"""Bounded, read-only Flare search and event access with locally replayable provenance."""

import asyncio
import hashlib
import json
import re
import time
from urllib.parse import urlencode

import httpx

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import encoded
from dark_mcp.models import limit_value, now, parse_date

API = "https://api.flare.io"


def date_field(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_date(value)
        return parsed.isoformat() if parsed else None
    except ValueError:
        return None


def uid_value(uid: str) -> str:
    if not re.fullmatch(r"[^\s/]+/[^\s/]+/\S+", uid) or len(uid) > 1000:
        raise ValueError("Use a Flare event UID returned by search")
    return uid


class FlareConnector:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.source_id = f"provider:flare:{settings.flare_tenant_id or 'default'}"
        self.store.register(self.source_id, "provider", API, "Flare API", {"provider": "flare"})
        self.token, self.token_until = None, 0
        self.lock = asyncio.Lock()
        self.last_request = 0

    async def _json(self, method: str, path: str, *, body=None, auth_key=False):
        key = self.settings.flare_api_key
        if not key:
            raise DataAccessError("provider_not_configured", "Set DARK_MCP_FLARE_API_KEY for licensed Flare access")
        self.store.cooldown(self.source_id)
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if auth_key:
            headers["Authorization"] = key.get_secret_value()
        else:
            if not self.token or self.token_until <= time.monotonic():
                token_body = {"tenant_id": self.settings.flare_tenant_id} if self.settings.flare_tenant_id else {}
                result = await self._json("POST", "/tokens/generate", body=token_body, auth_key=True)
                if not isinstance(result.get("token"), str) or not result["token"]:
                    raise DataAccessError("provider_schema_error", "Flare returned no usable API token")
                self.token, self.token_until = result["token"], time.monotonic() + 3300
            headers["Authorization"] = "Bearer " + self.token
        delay = 1 - (time.monotonic() - self.last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        self.last_request = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.request_timeout):
                async with httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=self.settings.request_timeout
                ) as client:
                    async with client.stream(method, API + path, json=body, headers=headers) as response:
                        if response.status_code == 429:
                            raw = response.headers.get("retry-after", "60")
                            wait = int(raw) if raw.isdigit() and len(raw) <= 8 else 60
                            self.store.cooldown(self.source_id, max(1, wait))
                            raise DataAccessError(
                                "provider_rate_limited", "Flare requires a wait", retry_after=max(1, wait)
                            )
                        if response.status_code in {401, 403}:
                            self.token = None
                            raise DataAccessError(
                                "provider_access_denied", "Flare credentials or API entitlement rejected"
                            )
                        if response.status_code == 404:
                            raise DataAccessError("provider_event_unavailable", "Flare event was not returned")
                        if not 200 <= response.status_code < 300:
                            raise DataAccessError("provider_http_error", f"Flare returned HTTP {response.status_code}")
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise DataAccessError("unsupported_encoding", "Flare returned unsupported compression")
                        if response.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                            raise DataAccessError("provider_schema_error", "Flare returned a non-JSON response")
                        data = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            data.extend(chunk)
                            if len(data) > self.settings.max_response_bytes:
                                raise DataAccessError("response_too_large", "Flare response exceeded the size bound")
                        result = json.loads(data)
                        if not isinstance(result, dict):
                            raise ValueError
                        return result
        except (httpx.RequestError, TimeoutError) as exc:
            raise DataAccessError("provider_unavailable", "Flare connection failed or timed out") from exc
        except (ValueError, UnicodeError) as exc:
            raise DataAccessError("provider_schema_error", "Flare returned invalid JSON") from exc

    async def search(
        self,
        query: str,
        types: list[str] | None = None,
        since: str | None = None,
        limit: int = 20,
        cursor: dict | None = None,
    ):
        limit_value(limit, 100)
        if not query.strip() or len(query) > 500:
            raise ValueError("Use a nonempty query up to 500 characters")
        if types is not None and (len(types) > 20 or any(not re.fullmatch(r"[a-z_]{1,80}", t) for t in types)):
            raise ValueError("Check Flare event type names")
        since = parse_date(since).isoformat() if since else None
        body = {"query": {"type": "query_string", "query_string": query}, "size": limit, "order": "asc", "filters": {}}
        if types:
            body["filters"]["type"] = types
        if since:
            body["filters"]["estimated_created_at"] = {"gte": since}
        fingerprint = hashlib.sha256(encoded({"source_id": self.source_id, **body}).encode()).hexdigest()
        if cursor is not None:
            if (
                cursor.get("search_fingerprint") != fingerprint
                or not isinstance(cursor.get("next"), str)
                or not 1 <= len(cursor["next"]) <= 16000
            ):
                raise ValueError("Resume with the same query, types, since, limit and tenant")
            body["from"] = cursor["next"]
        attempt = self.store.begin_attempt(self.source_id, {"operation": "search", "parameters": body})
        try:
            async with self.lock:
                result = await self._json("POST", "/firework/v4/events/global/_search", body=body)
            if (
                not isinstance(result.get("items"), list)
                or len(result["items"]) > limit
                or result.get("next") is not None
                and not isinstance(result["next"], str)
            ):
                raise DataAccessError("provider_schema_error", "Flare search response has an unexpected shape")
            items = []
            for hit in result["items"]:
                metadata = hit.get("metadata") if isinstance(hit, dict) else None
                if not isinstance(metadata, dict) or not isinstance(metadata.get("uid"), str):
                    raise DataAccessError("provider_schema_error", "Flare search hit has no UID")
                try:
                    uid_value(metadata["uid"])
                except ValueError as exc:
                    raise DataAccessError("provider_schema_error", "Flare search hit has an invalid UID") from exc
            for hit in result["items"]:
                metadata = hit["metadata"]
                # Search hits contain snippets, not original event bodies. Keep them in a separate evidence identity.
                text = json.dumps(hit.get("highlights", {}), ensure_ascii=False)
                snapshot = {
                    "title": metadata["uid"],
                    "text": text[: self.settings.max_text_chars],
                    "record_scope": "search_hit",
                    "event_uid": metadata["uid"],
                    "provider": "flare",
                    "provider_metadata": metadata,
                    "published_at": None,
                    "access_state": "search_hit_unverified",
                    "truncated": len(text) > self.settings.max_text_chars,
                }
                evidence = self.store.observe(
                    self.source_id,
                    "provider",
                    self.source_id + ":search:" + metadata["uid"],
                    API + "/firework/v2/activities/?" + urlencode({"uid": metadata["uid"]}),
                    snapshot,
                )
                items.append({"metadata": metadata, "highlights_text": snapshot["text"], "evidence": evidence})
            next_value = result.get("next")
            next_cursor = {"next": next_value, "search_fingerprint": fingerprint} if next_value else None
            self.store.finish_attempt(
                attempt, "search_page_returned", {"items": len(items), "has_next": bool(next_cursor)}
            )
            return {
                "results": items,
                "next_cursor": next_cursor,
                "source_id": self.source_id,
                "coverage": "One Flare search page; hits are snippets. Use read_flare_event for original event text. "
                "since filters provider estimated_created_at, not independently verified publication. "
                "An empty page can still have a next cursor. Search consumes the provider's quota.",
            }
        except DataAccessError as exc:
            self.store.finish_attempt(attempt, exc.code, exc.as_dict())
            raise

    async def read(self, uid: str):
        uid = uid_value(uid)
        attempt = self.store.begin_attempt(self.source_id, {"operation": "read", "event_uid": uid})
        try:
            async with self.lock:
                response = await self._json("GET", "/firework/v2/activities/?" + urlencode({"uid": uid}))
            activity = response.get("activity", response)
            if not isinstance(activity, dict):
                raise DataAccessError("provider_schema_error", "Flare returned no event object")
            data, header, metadata = (
                activity.get("data", activity),
                activity.get("header", {}),
                activity.get("metadata", {}),
            )
            if not all(isinstance(x, dict) for x in [data, header, metadata]):
                raise DataAccessError("provider_schema_error", "Flare event structure is unsupported")
            if any(value and value != uid for value in (header.get("uid"), activity.get("uid"))):
                raise DataAccessError("provider_schema_error", "Flare returned a different event UID")
            text = data.get("content") or data.get("description")
            preview = False
            if not isinstance(text, str) or not text.strip():
                text, preview = header.get("content_preview", ""), True
            if not isinstance(text, str) or not text.strip():
                raise DataAccessError(
                    "provider_body_unavailable", "Event has no supported text body; no full thread implied"
                )
            original_url = data.get("url") or data.get("browser_url")
            posted_raw = data.get("posted_at")
            timestamp = now()
            snapshot = {
                "provider": "flare",
                "event_uid": uid,
                "record_scope": "preview" if preview else "event_body",
                "title": str(data.get("title") or header.get("title") or "")[:500],
                "text": text[: self.settings.max_text_chars],
                "truncated": len(text) > self.settings.max_text_chars,
                "author": data.get("actor_name") or data.get("actor") or header.get("actor"),
                "original_url": original_url,
                "thread_id": data.get("topic_id") or header.get("parent_id"),
                "parent_uid": header.get("parent_uid"),
                "source_name": header.get("source_name") or header.get("source") or metadata.get("source"),
                "event_type": header.get("type") or uid.split("/")[0],
                "published_at": date_field(posted_raw),
                "published_at_raw": posted_raw,
                "provider_first_seen_at": date_field(metadata.get("first_crawled_at")),
                "provider_last_seen_at": date_field(metadata.get("last_crawled_at")),
                "provider_estimated_created_at": date_field(metadata.get("estimated_created_at")),
                "provider_metadata": metadata,
                "provider_payload_sha256": hashlib.sha256(encoded(response).encode()).hexdigest(),
                "access_state": "preview_only_unverified" if preview else "content_returned_unverified",
            }
            # Provider timestamps remain distinct from local observation and original publication time.
            url = (
                original_url
                if isinstance(original_url, str)
                else API + "/firework/v2/activities/?" + urlencode({"uid": uid})
            )
            evidence = self.store.observe(
                self.source_id,
                "provider",
                self.source_id + ":event:" + uid,
                url,
                snapshot,
                timestamp,
                provenance={"transport": "flare_api", "event_uid": uid},
            )
            self.store.finish_attempt(attempt, snapshot["access_state"], {"event_uid": uid, "evidence": evidence})
            return {
                "event": {**snapshot, "collected_at": timestamp, "evidence": evidence},
                "coverage": "One provider event; original text when available, otherwise an explicitly marked preview. "
                "Source identity, timestamps and claims are supplied by the provider, not independently verified. "
                "Parent threads and replies are not automatically fetched.",
            }
        except DataAccessError as exc:
            self.store.finish_attempt(attempt, exc.code, exc.as_dict())
            raise

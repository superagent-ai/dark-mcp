"""Bounded reads of Telegram's public HTML previews; separate from account/API collection."""

import asyncio
import hashlib
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from dark_mcp.errors import DataAccessError
from dark_mcp.models import limit_value, now, parse_date
from dark_mcp.references import reference_host
from dark_mcp.telegram import channel_key

USER_AGENT = "dark-mcp/0.21 (+https://github.com/superagent-ai/dark-mcp)"
SCOPE = "telegram_web"
COVERAGE = (
    "Visible text/metadata from one public Telegram HTML preview, not account history or global search. "
    "Public handles can be reassigned; no numeric channel identity or actor identity is authenticated. "
    "Media, attachments, private channels, unsupported messages and unvisited history remain gaps. "
    "Quotes/forward labels are unverified references, not independent corroboration."
)


def post_identity(value: str, handle: str) -> int | None:
    try:
        parts = urlsplit(value)
        match = re.fullmatch(r"/([A-Za-z][A-Za-z0-9_]{3,31})/([1-9][0-9]{0,15})", parts.path)
        if parts.scheme != "https" or parts.netloc != "t.me" or parts.query or parts.fragment:
            return None
        return int(match[2]) if match and match[1].lower() == handle else None
    except (ValueError, TypeError):
        return None


def body_links(body) -> list[dict]:
    links, seen = [], set()
    for anchor in body.select("a[href]"):
        target = anchor.get("href")
        if reference_host(target) is None or target in seen:
            continue
        seen.add(target)
        links.append({"url": target, "label": anchor.get_text(" ", strip=True)[:500]})
        if len(links) >= 50:
            break
    return links


def parse_preview(
    document: str, handle: str, limit: int = 20, message_id: int | None = None, before_id: int = 0
) -> dict:
    """Use explicit widget bodies and matching footer permalinks; never turn reply quotes into post text."""
    limit_value(limit)
    soup = BeautifulSoup(document, "html.parser")
    title_tag = soup.select_one('meta[property="og:title"]')
    title = title_tag.get("content", "")[:300] if title_tag else ""
    candidates = soup.select(".tgme_widget_message[data-post]")
    posts, seen, rejected = [], set(), 0
    for widget in candidates[:200]:
        identity = widget.get("data-post", "")
        pid = post_identity("https://t.me/" + identity, handle)
        if pid is None or pid in seen:
            rejected += 1
            continue
        # Matching footer metadata is required; body/quote links cannot establish this post's identity or date.
        footers = [
            anchor
            for anchor in widget.select("a.tgme_widget_message_date[href]")
            if anchor.find_parent(class_="tgme_widget_message") is widget
            and post_identity(anchor["href"], handle) == pid
        ]
        if not footers:
            rejected += 1
            continue
        seen.add(pid)
        date_values = {node["datetime"] for anchor in footers for node in anchor.select("time[datetime]")}
        published_at = None
        if len(date_values) == 1:
            try:
                parsed = parse_date(next(iter(date_values)))
                published_at = parsed.isoformat() if parsed else None
            except ValueError:
                pass
        bodies = [
            node
            for node in widget.select(".tgme_widget_message_text.js-message_text")
            if node.find_parent(class_="tgme_widget_message") is widget
            and node.find_parent(class_="tgme_widget_message_reply") is None
        ]
        # Ambiguous bodies and placeholders stay explicit rather than silently selecting a claimed original.
        body = bodies[0] if len(bodies) == 1 else None
        if body:
            for br in body.select("br"):
                br.replace_with("\n")
        text = body.get_text("", strip=False).strip() if body else ""
        errors = [
            node.get_text(" ", strip=True)[:300]
            for node in widget.select(".tgme_widget_message_error")
            if node.find_parent(class_="tgme_widget_message") is widget
        ]
        quotes = []
        for quote in widget.select("a.tgme_widget_message_reply[href]")[:10]:
            quotes.append(
                {
                    "url": quote["href"] if reference_host(quote["href"]) else None,
                    "text": quote.get_text(" ", strip=True)[:1000],
                    "verification": "public_preview_quote_unverified",
                }
            )
        forward = widget.select_one(".tgme_widget_message_forwarded_from")
        forward_links = body_links(forward) if forward else []
        posts.append(
            {
                "message_id": pid,
                "handle": handle,
                "url": f"https://t.me/{handle}/{pid}",
                "text": text[:16000],
                "truncated": len(text) > 16000,
                "published_at": published_at,
                "published_at_raw": next(iter(date_values)) if len(date_values) == 1 else None,
                "timestamp_basis": "matching_widget_footer_explicit_offset"
                if published_at
                else "unresolved_widget_date",
                "date_conflict": len(date_values) > 1,
                "links": body_links(body) if body else [],
                "link_capture": {"basis": "public_html_body_anchors", "maximum_links": 50},
                "quotes": quotes,
                "forward": {
                    "label": forward.get_text(" ", strip=True)[:500],
                    "links": forward_links,
                    "verification": "public_preview_header_unverified",
                }
                if forward
                else None,
                "has_media": bool(
                    widget.select_one(
                        ".tgme_widget_message_photo_wrap,.tgme_widget_message_video_player,"
                        ".tgme_widget_message_document_wrap,.tgme_widget_message_voice_player"
                    )
                ),
                "content_errors": errors,
                "body_state": "unavailable"
                if errors or len(bodies) > 1
                else "visible_text"
                if text
                else "no_visible_text",
            }
        )
    posts.sort(key=lambda post: post["message_id"], reverse=True)
    out_of_range = sum(post["message_id"] >= before_id for post in posts) if before_id else 0
    if before_id:
        posts = [post for post in posts if post["message_id"] < before_id]
    if message_id is not None:
        posts = [post for post in posts if post["message_id"] == message_id]
    retained = posts[:limit]
    older = []
    for anchor in soup.select("a.tme_messages_more[href]"):
        target = urlsplit(urljoin("https://t.me", anchor["href"]))
        query = parse_qs(target.query)
        if (
            target.scheme == "https"
            and target.netloc == "t.me"
            and target.path.lower() == f"/s/{handle}"
            and (
                set(query) == {"before"}
                and len(query["before"]) == 1
                and re.fullmatch(r"[1-9][0-9]{0,15}", query["before"][0])
            )
        ):
            older.append(int(query["before"][0]))
    next_before = None
    if retained and message_id is None:
        minimum = min(post["message_id"] for post in retained)
        if len(posts) > limit:
            next_before = minimum  # Do not skip parsed-but-omitted posts by using the page's older link.
        elif len(set(older)) == 1 and older[0] <= minimum:
            next_before = older[0]
    if not candidates:
        raise DataAccessError(
            "telegram_web_preview_unavailable", "No public message widgets returned; preview may be gated"
        )
    if not retained:
        if out_of_range:
            raise DataAccessError(
                "telegram_web_pagination_ignored", "No public posts inside the requested history bound"
            )
        raise DataAccessError("telegram_web_post_unavailable", "No matching public post identity and footer returned")
    return {
        "channel": {"handle": handle, "title": title, "numeric_id": None},
        "results": retained,
        "next_before_id": next_before,
        "parsed_posts": len(posts),
        "omitted_posts": max(0, len(posts) - limit),
        "rejected_widgets": rejected,
        "unexamined_widgets": max(0, len(candidates) - 200),
        "out_of_range_posts": out_of_range,
        "visible_text_posts": sum(post["body_state"] == "visible_text" for post in retained),
        "unavailable_posts": sum(post["body_state"] == "unavailable" for post in retained),
        "posts_without_visible_text": sum(post["body_state"] == "no_visible_text" for post in retained),
        "coverage": COVERAGE,
    }


class TelegramWeb:
    def __init__(self, settings, catalog, store):
        self.settings, self.catalog, self.store = settings, catalog, store
        self.robots = None
        self.robots_until = 0

    def allowed(self, channel: str) -> str:
        if not self.settings.telegram_web_enabled:
            raise DataAccessError("telegram_web_disabled", "Enable public Telegram web reads in local configuration")
        handle = channel_key(channel)
        if handle.startswith("-100"):
            raise ValueError("Public web previews require a public username, not a private/numeric locator")
        allowed = {channel_key(item) for item in self.settings.telegram_channels}
        allowed.update(channel_key(item) for item in self.settings.telegram_web_channels)
        if self.settings.telegram_catalog_sources:
            allowed.update(self.catalog.telegram_targets())
        if handle not in allowed and self.settings.telegram_web_directory_sources:
            with self.store.connect() as db:
                directory_lead = db.execute(
                    "SELECT 1 FROM sources WHERE kind='telegram_directory' "
                    "AND json_extract(metadata,'$.locator_type')='public_handle' "
                    "AND json_extract(metadata,'$.locator')=? LIMIT 1",
                    (handle,),
                ).fetchone()
            if directory_lead:
                return handle
        if handle not in allowed:
            raise DataAccessError("channel_not_allowed", "Public preview requires an explicitly permitted channel")
        return handle

    async def _pace(self):
        try:
            self.store.cooldown(SCOPE + "_spacing")
        except DataAccessError as error:
            if error.retry_after > 5:
                raise
            await asyncio.sleep(error.retry_after)
        self.store.cooldown(SCOPE + "_spacing", 1)

    async def _get(self, client, url: str, *, robots: bool = False) -> tuple[int, bytes]:
        await self._pace()
        client.cookies.clear()
        async with client.stream("GET", url) as response:
            if response.status_code == 429:
                raw = response.headers.get("retry-after", "60")
                try:
                    seconds = (
                        int(raw)
                        if raw.isdigit()
                        else max(
                            1, int((parsedate_to_datetime(raw).astimezone(UTC) - datetime.now(UTC)).total_seconds())
                        )
                    )
                except (ValueError, TypeError, OverflowError):
                    seconds = 60
                raise DataAccessError(
                    "telegram_web_rate_limited",
                    "Public Telegram web endpoint requires a wait",
                    retry_after=max(1, seconds),
                )
            if robots and response.status_code in {401, 403, 404}:
                return response.status_code, b""
            if response.is_redirect or response.status_code >= 400:
                raise DataAccessError("telegram_web_http_error", f"Public preview returned HTTP {response.status_code}")
            mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if mime not in {"text/html", "text/plain", "application/xhtml+xml"}:
                raise DataAccessError("telegram_web_unsupported_content", "Expected public preview HTML or robots text")
            raw = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                raw.extend(chunk)
                if len(raw) > (64000 if robots else self.settings.max_response_bytes):
                    raise DataAccessError("response_too_large", "Public Telegram response exceeded configured bounds")
            return response.status_code, bytes(raw)

    async def _rules(self, client):
        if self.robots is not None and time.monotonic() < self.robots_until:
            return self.robots
        status, raw = await self._get(client, "https://t.me/robots.txt", robots=True)
        rules = RobotFileParser()
        rules.parse(["User-agent: *", "Disallow: /"] if status in {401, 403} else raw.decode("utf-8").splitlines())
        self.robots, self.robots_until = rules, time.monotonic() + 300
        return rules

    async def read(self, channel: str, limit: int = 20, before_id: int = 0, message_id: int | None = None) -> dict:
        handle = self.allowed(channel)
        limit_value(limit)
        if not 0 <= before_id <= 10**16 - 1 or (
            message_id is not None and (not 1 <= message_id <= 10**16 - 1 or before_id)
        ):
            raise ValueError("Check before_id and message_id; direct reads cannot also paginate")
        url = (
            f"https://t.me/{handle}/{message_id}?embed=1"
            if message_id is not None
            else f"https://t.me/s/{handle}" + (f"?before={before_id}" if before_id else "")
        )
        source_id = "telegram-web:" + handle
        self.store.register(
            source_id,
            SCOPE,
            f"https://t.me/s/{handle}",
            handle,
            {"handle": handle, "collection_method": "telegram_public_html", "coverage": COVERAGE},
        )
        token = self.store.claim_connector(SCOPE, self.settings.request_timeout + 15)
        attempt = self.store.begin_attempt(source_id, {"url": url, "limit": limit, "method": "public_html"})
        try:
            async with asyncio.timeout(self.settings.request_timeout):
                async with httpx.AsyncClient(
                    timeout=self.settings.request_timeout,
                    follow_redirects=False,
                    trust_env=False,
                    headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                ) as client:
                    rules = await self._rules(client)
                    if not rules.can_fetch(USER_AGENT, url):
                        raise DataAccessError("robots_denied", "Public Telegram robots rules disallow this path")
                    delay = rules.crawl_delay(USER_AGENT) or rules.crawl_delay("*") or 1
                    rate = rules.request_rate(USER_AGENT) or rules.request_rate("*")
                    if rate and rate.requests > 0:
                        delay = max(delay, rate.seconds / rate.requests)
                    if delay > 1:
                        await asyncio.sleep(min(delay, self.settings.request_timeout))
                    _, raw = await self._get(client, url)
            result = parse_preview(raw.decode("utf-8"), handle, limit, message_id, before_id)
            result.update(source_id=source_id, collected_at=now(), requested_url=url)
            acquisition = {
                "method": "telegram_public_html",
                "network_read": True,
                "source_identity_verified": False,
                "account_used": False,
            }
            provenance = {**acquisition, "requested_url": url, "document_sha256": hashlib.sha256(raw).hexdigest()}
            result["acquisition"] = provenance
            for post in result["results"]:
                post["acquisition"] = acquisition
                if post["body_state"] == "unavailable":
                    continue
                snapshot = {**post, "title": "", "access_state": "content_returned_unverified"}
                post["evidence"] = self.store.observe(
                    source_id,
                    SCOPE,
                    f"{handle}:{post['message_id']}",
                    post["url"],
                    snapshot,
                    result["collected_at"],
                    provenance=provenance,
                )
            result["access_state"] = (
                "content_returned_unverified" if result["visible_text_posts"] else "preview_only_unverified"
            )
            self.store.finish_attempt(
                attempt,
                result["access_state"],
                {
                    "url": url,
                    "visible_text_posts": sum(post["body_state"] == "visible_text" for post in result["results"]),
                    "posts": len(result["results"]),
                    "next_before_id": result["next_before_id"],
                },
            )
            return result
        except asyncio.CancelledError:
            self.store.finish_attempt(attempt, "cancelled", {"url": url, "content_read_complete": False})
            raise
        except (httpx.HTTPError, TimeoutError, UnicodeError) as exc:
            error = DataAccessError("telegram_web_unavailable", "Public Telegram preview request failed or timed out")
            self.store.finish_attempt(attempt, error.code, error.as_dict())
            raise error from exc
        except DataAccessError as error:
            self.store.record_retry(SCOPE, error)
            self.store.finish_attempt(attempt, error.code, error.as_dict())
            raise
        finally:
            self.store.release_connector(SCOPE, token)

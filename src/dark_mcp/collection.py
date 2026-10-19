"""Observe all MCP content reads and drive bounded, restart-safe collection jobs."""

import json
from urllib.parse import urlsplit

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore, identifier
from dark_mcp.models import OnionPage, limit_value, parse_date
from dark_mcp.onion import onion_url
from dark_mcp.sources import SourceCatalog, page_access_state
from dark_mcp.telegram import channel_key


class CollectionService:
    def __init__(self, settings, catalog: SourceCatalog, store: EvidenceStore, onion, telegram):
        self.settings, self.catalog, self.store = settings, catalog, store
        self.onion, self.telegram = onion, telegram
        for source in catalog.sources.values():
            store.register(source["id"], source["kind"], source["locator"], source["name"], source)

    def onion_source(self, url: str) -> str:
        url = onion_url(url)
        host = urlsplit(url).hostname
        source = next(
            (
                s
                for s in self.catalog.sources.values()
                if s["kind"] == "onion" and urlsplit(s["locator"]).hostname == host
            ),
            None,
        )
        if source:
            return source["id"]
        source_id = identifier("onion", host)
        self.store.register(source_id, "onion", url, host, {"discovery": "operator_or_agent_supplied_url"})
        return source_id

    def telegram_source(self, channel: str) -> str:
        key = channel_key(channel)
        source = next(
            (
                s
                for s in self.catalog.sources.values()
                if s["kind"] == "telegram" and s["locator"] and s["locator"].lower() == key
            ),
            None,
        )
        if source:
            return source["id"]
        source_id = "telegram-locator:" + key
        self.store.register(source_id, "telegram", key, key)
        return source_id

    def observe_onion(self, page: OnionPage, collection_id: str | None = None) -> dict:
        snapshot = page.model_dump(exclude={"collected_at", "evidence"})
        snapshot["access_state"] = page_access_state(page)
        source_id = self.onion_source(page.url)
        return self.store.observe(source_id, "onion", page.url, page.url, snapshot, page.collected_at, collection_id)

    def import_legacy(self, index):
        with self.store.connect() as db:
            done = db.execute("SELECT 1 FROM migrations WHERE name='legacy-onions-v1'").fetchone()
        if done:
            return
        with index.connect() as db:
            pages = db.execute("SELECT * FROM pages").fetchall()
        for row in pages:
            page = OnionPage(**{**dict(row), "links": json.loads(row["links"])})
            self.observe_onion(page, "legacy-import")
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO migrations VALUES('legacy-onions-v1',datetime('now'))")

    async def fetch(self, url: str, collection_id: str | None = None) -> OnionPage:
        source_id = self.onion_source(url)
        attempt = self.store.begin_attempt(source_id, {"url": url, "collection_id": collection_id})
        try:
            page = await self.onion.fetch(url)
            page.access_state = page_access_state(page)
            page.evidence = self.observe_onion(page, collection_id)
            self.store.finish_attempt(
                attempt,
                page.access_state,
                {
                    "requested_url": url,
                    "resolved_url": page.url,
                    "pages": 1,
                    "truncated": page.truncated,
                    "evidence": page.evidence,
                    "next_page_urls": page.next_page_urls,
                    "coverage": "One page; no inference about unvisited threads or history",
                },
            )
            return page
        except DataAccessError as exc:
            self.store.finish_attempt(attempt, exc.code, {"url": url, **exc.as_dict()})
            raise

    def persist_messages(self, result: dict, locator: str, source_id: str, collection_id: str | None = None) -> dict:
        observed = result.get("channel")
        if observed:
            canonical = "telegram-channel:" + observed["id"]
            self.store.register(
                canonical,
                "telegram",
                locator,
                observed.get("title") or observed["id"],
                {"channel": observed, "collection_locator": locator},
            )
            self.store.bind_identity(source_id, canonical, observed)
        source = self.catalog.sources.get(source_id, {})
        expected = source.get("research", {}).get("channel_id")
        result["identity_changed_since_catalog"] = bool(expected and observed and expected != observed["id"])
        for post in result.get("results", []):
            channel = post["channel"]
            canonical = "telegram-channel:" + channel["id"]
            self.store.register(
                canonical,
                "telegram",
                locator,
                channel.get("title") or channel["id"],
                {"channel": channel, "collection_locator": locator, "thread_root": result.get("thread_root")},
            )
            snapshot = {
                key: post.get(key)
                for key in ["text", "published_at", "edited_at", "truncated", "has_media", "reply_to_message_id"]
            }
            snapshot.update(
                title="", access_state="content_returned_unverified", message_id=post["id"], channel_id=channel["id"]
            )
            stable_url = f"https://t.me/c/{channel['id'].removeprefix('-100')}/{post['id']}"
            post["evidence"] = self.store.observe(
                canonical,
                "telegram",
                f"{channel['id']}:{post['id']}",
                stable_url,
                snapshot,
                result.get("collected_at"),
                collection_id,
                {
                    "observed_url": post["url"],
                    "requested_source_id": source_id,
                    "collection_locator": locator,
                    "thread_root": result.get("thread_root"),
                },
            )
        return result

    async def messages(
        self,
        channel: str,
        limit: int = 20,
        before_id: int = 0,
        query: str | None = None,
        since: str | None = None,
        after_id: int = 0,
        oldest_first: bool = False,
        thread_id: int | None = None,
        collection_id: str | None = None,
    ) -> dict:
        source_id = self.telegram_source(channel)
        options = {"channel": channel, "limit": limit, "before_id": before_id, "query": query, "since": since}
        # Preserve the original connector contract for ordinary reads.
        if after_id or oldest_first:
            options.update(after_id=after_id, oldest_first=oldest_first)
        if thread_id is not None:
            options["thread_id"] = thread_id
        attempt = self.store.begin_attempt(source_id, options)
        try:
            self.store.cooldown("telegram")
            result = await self.telegram.messages(**options)
            self.persist_messages(result, channel, source_id, collection_id)
            state = "posts_returned" if result["results"] else "no_matching_posts"
            self.store.finish_attempt(
                attempt,
                state,
                {
                    "options": options,
                    "returned": len(result["results"]),
                    "next_before_id": result.get("next_before_id"),
                    "next_after_id": result.get("next_after_id"),
                    "since_boundary_reached": result.get("since_boundary_reached"),
                    "scanned_count": result.get("scanned_count"),
                    "channel": result.get("channel"),
                    "coverage": result.get("coverage"),
                    "thread_root": result.get("thread_root"),
                },
            )
            return result
        except DataAccessError as exc:
            if exc.retry_after is not None:
                self.store.cooldown("telegram", exc.retry_after)
            self.store.finish_attempt(attempt, exc.code, {"options": options, **exc.as_dict()})
            raise
        except ValueError:
            self.store.finish_attempt(attempt, "invalid_arguments", {"options": options})
            raise

    async def posts(self, **kwargs):
        self.store.cooldown("telegram")
        try:
            result = await self.telegram.posts(**kwargs)
        except DataAccessError as exc:
            if exc.retry_after is not None:
                self.store.cooldown("telegram", exc.retry_after)
            raise
        for post in result["results"]:
            locator = post["channel"]["username"] or post["channel"]["id"]
            source_id = self.telegram_source(locator)
            self.persist_messages(
                {"results": [post], "channel": post["channel"], "collected_at": result["collected_at"]},
                locator,
                source_id,
            )
        return result

    async def channels(self, **kwargs):
        self.store.cooldown("telegram")
        try:
            return await self.telegram.channels(**kwargs)
        except DataAccessError as exc:
            if exc.retry_after is not None:
                self.store.cooldown("telegram", exc.retry_after)
            raise

    async def message(self, channel: str, message_id: int, collection_id: str | None = None) -> dict:
        source_id = self.telegram_source(channel)
        attempt = self.store.begin_attempt(source_id, {"message_id": message_id, "direct_lookup": True})
        try:
            self.store.cooldown("telegram")
            result = await self.telegram.message(channel, message_id)
            self.persist_messages(result, channel, source_id, collection_id)
            self.store.finish_attempt(
                attempt,
                "posts_returned" if result["results"] else "post_unavailable",
                {"message_id": message_id, "direct_lookup": True, "channel": result["channel"]},
            )
            return result
        except DataAccessError as exc:
            if exc.retry_after is not None:
                self.store.cooldown("telegram", exc.retry_after)
            self.store.finish_attempt(attempt, exc.code, exc.as_dict())
            raise

    async def thread(self, channel: str, message_id: int, limit: int = 20, before_id: int = 0) -> dict:
        root = await self.message(channel, message_id)
        replies = await self.messages(channel, limit=limit, before_id=before_id, thread_id=message_id)
        return {"root": root, **replies, "coverage": "One root lookup and one page of accessible discussion replies"}

    async def sync_changes(self, channel: str, limit: int = 100) -> dict:
        source_id = self.telegram_source(channel)
        stored = self.store.source(source_id)
        canonical = stored["canonical_id"]
        pts = self.store.telegram_pts(canonical) if canonical else None
        expected = (stored["identity"] or {}).get("id")
        attempt = self.store.begin_attempt(source_id, {"update_pts": pts, "limit": limit})
        try:
            self.store.cooldown("telegram")
            result = await self.telegram.changes(channel, pts=pts, expected_id=expected, limit=limit)
            self.persist_messages(result, channel, source_id)
            canonical = "telegram-channel:" + result["channel"]["id"]
            result["deletion_observations"] = []
            for message_id in result["deleted_message_ids"]:
                evidence_id = identifier("ev", f"telegram:{result['channel']['id']}:{message_id}")
                try:
                    self.store.get(evidence_id)
                except DataAccessError:
                    continue
                result["deletion_observations"].append(
                    self.store.confirm_deleted(
                        evidence_id,
                        {"source": "Telegram UpdateDeleteChannelMessages", "next_pts": result["next_pts"]},
                    )
                )
            self.store.telegram_pts(canonical, result["next_pts"])
            state = (
                "updates_gap"
                if result["gap_detected"]
                else "updates_initialized"
                if result["initialized"]
                else "updates_synced"
            )
            self.store.finish_attempt(
                attempt,
                state,
                {
                    "next_pts": result["next_pts"],
                    "gap_detected": result["gap_detected"],
                    "has_more": result["has_more"],
                    "deleted_ids": result["deleted_message_ids"],
                },
            )
            return result
        except DataAccessError as exc:
            if exc.retry_after is not None:
                self.store.cooldown("telegram", exc.retry_after)
            self.store.finish_attempt(attempt, exc.code, exc.as_dict())
            raise

    async def refresh(self, evidence_id: str) -> dict:
        item = self.store.get(evidence_id)
        if item["kind"] == "onion":
            return {"page": (await self.fetch(item["url"])).model_dump()}
        source = self.store.source(item["source_id"])
        locator = source["metadata"]["collection_locator"]
        if source["metadata"].get("thread_root"):
            raise DataAccessError(
                "thread_recheck_required",
                "Re-collect the discussion thread to observe edits; absence from a thread page never confirms deletion",
            )
        requested_source = self.telegram_source(locator)
        attempt = self.store.begin_attempt(requested_source, {"evidence_id": evidence_id, "direct_lookup": True})
        try:
            self.store.cooldown("telegram")
            result = await self.telegram.message(locator, item["snapshot"]["message_id"])
            # A username may have been reassigned. Never mark a different channel's missing ID as deletion.
            if result["channel"]["id"] != item["snapshot"]["channel_id"]:
                raise DataAccessError("source_identity_changed", "Channel identity changed; old evidence retained")
            self.persist_messages(result, locator, requested_source)
            if not result["results"]:
                result["availability_observation"] = self.store.unavailable(
                    evidence_id, "Previously observed post was not returned by direct lookup; deletion or access change"
                )
            self.store.finish_attempt(
                attempt,
                "posts_returned" if result["results"] else "post_unavailable",
                {"evidence_id": evidence_id, "direct_lookup": True},
            )
            return result
        except DataAccessError as exc:
            if exc.retry_after is not None:
                self.store.cooldown("telegram", exc.retry_after)
            self.store.finish_attempt(attempt, exc.code, exc.as_dict())
            raise

    async def crawl(self, seeds: list[str], max_pages: int = 5) -> dict:
        limit_value(max_pages, 10)
        if not 1 <= len(seeds) <= 10:
            raise ValueError("Use 1-10 seeds")
        queue = list(dict.fromkeys(onion_url(seed) for seed in seeds))
        hosts = {urlsplit(url).hostname for url in queue}
        visited, indexed, errors = set(), [], []
        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                page = await self.fetch(url)
                indexed.append(page.url)
                if page.access_state == "content_returned_unverified":
                    queue.extend(
                        link
                        for link in [*page.next_page_urls, *page.links]
                        if urlsplit(link).hostname in hosts and link not in visited
                    )
                    queue = list(dict.fromkeys(queue))[:100]
            except DataAccessError as exc:
                errors.append({"url": url, **exc.as_dict()["error"]})
        return {
            "indexed": indexed,
            "errors": errors,
            "attempted": len(visited),
            "coverage": "Bounded same-host crawl; all returned pages preserved as evidence",
        }

    def start(
        self,
        source_id: str,
        mode: str = "history",
        query: str | None = None,
        since: str | None = None,
        url: str | None = None,
        thread_id: int | None = None,
    ) -> dict:
        source = self.store.source(source_id)
        if not source["locator"]:
            raise DataAccessError("source_access_required", "This source has no durable collection locator")
        if mode not in {"history", "updates"}:
            raise ValueError("Choose history or updates")
        parse_date(since)
        if query is not None and (not query.strip() or len(query) > 500):
            raise ValueError("Check query")
        if thread_id is not None and (thread_id <= 0 or query is not None):
            raise ValueError("Thread reads cannot use server-side query filtering")
        options = {"mode": mode, "query": query, "since": since, "thread_id": thread_id}
        if source["kind"] == "onion":
            if mode != "history" or query is not None or since is not None or thread_id is not None:
                raise ValueError("Onion jobs paginate explicit page links; re-fetch evidence separately for changes")
            target = onion_url(url or source["locator"])
            if urlsplit(target).hostname != urlsplit(source["locator"]).hostname:
                raise DataAccessError("source_url_mismatch", "Choose a URL on this source host")
            checkpoint = {"queue": [target], "visited": [], "pages_collected": 0}
        else:
            if url is not None:
                raise ValueError("Telegram jobs do not accept URL overrides")
            self.telegram._allowed_channel(source["locator"])
            checkpoint = {"before_id": 0, "after_id": None, "pages_collected": 0}
        return self.store.create_job(source_id, source["kind"], options, checkpoint)

    async def next(self, collection_id: str, max_items: int = 20, max_pages: int = 1) -> dict:
        limit_value(max_items)
        limit_value(max_pages, 5)
        existing = self.store.job(collection_id)
        if existing["status"] == "complete":
            return {"collection": existing, "results": [], "coverage": "Previously exhausted observed pagination"}
        job, token = self.store.claim(collection_id, (self.settings.request_timeout + 15) * 2 * max_pages + 30)
        source = self.store.source(job["source_id"])
        checkpoint, options = job["checkpoint"], job["options"]
        results, status = [], "ready"
        error = None
        try:
            for _ in range(max_pages):
                if job["kind"] == "onion":
                    if not checkpoint["queue"]:
                        status = "complete"
                        break
                    url = checkpoint["queue"][0]
                    page = await self.fetch(url, collection_id)
                    results.append({"page": page.model_dump()})
                    if page.access_state != "content_returned_unverified":
                        raise DataAccessError("source_gated", "Source returned a gate or empty page; cursor retained")
                    checkpoint["visited"].append(url)
                    if page.url not in checkpoint["visited"]:
                        checkpoint["visited"].append(page.url)
                    checkpoint["queue"].pop(0)
                    checkpoint["queue"] = list(
                        dict.fromkeys(
                            [
                                *checkpoint["queue"],
                                *(
                                    u
                                    for u in page.next_page_urls
                                    if urlsplit(u).hostname == urlsplit(source["locator"]).hostname
                                    and u not in checkpoint["visited"]
                                ),
                            ]
                        )
                    )
                    status = "ready" if checkpoint["queue"] else "complete"
                else:
                    args = {
                        "channel": source["locator"],
                        "query": options["query"],
                        "since": options["since"],
                        "thread_id": options["thread_id"],
                        "collection_id": collection_id,
                    }
                    if options["thread_id"] and not checkpoint.get("root_collected"):
                        root = await self.message(source["locator"], options["thread_id"], collection_id)
                        results.append({"root": root})
                        checkpoint["root_collected"] = True
                    if options["mode"] == "updates" and checkpoint["after_id"] is None:
                        baseline = await self.messages(**{**args, "query": None, "since": None}, limit=1)
                        checkpoint["after_id"] = max((p["id"] for p in baseline["results"]), default=0)
                        results.append(
                            {"baseline": baseline, "coverage": "Initialized high-water mark; not a backfill"}
                        )
                        status = "idle"
                    elif options["mode"] == "updates":
                        batch = await self.messages(
                            **args, limit=max_items, after_id=checkpoint["after_id"], oldest_first=True
                        )
                        checkpoint["after_id"] = batch.get("next_after_id") or checkpoint["after_id"]
                        results.append(batch)
                        status = "ready" if batch.get("page_full") else "idle"
                    else:
                        batch = await self.messages(**args, limit=max_items, before_id=checkpoint["before_id"])
                        results.append(batch)
                        cursor = batch.get("next_before_id")
                        checkpoint["before_id"] = cursor or checkpoint["before_id"]
                        status = "ready" if cursor else "complete"
                checkpoint["pages_collected"] += 1
                if status in {"complete", "idle"}:
                    break
        except DataAccessError as exc:
            error = exc.as_dict()["error"]
            status = "waiting" if exc.retry_after is not None else "blocked"
        finally:
            # Release a job even if cancelled; observed evidence is idempotent when replayed after a crash.
            self.store.checkpoint(collection_id, token, checkpoint, status, error)
        result = {
            "collection": self.store.job(collection_id),
            "results": results,
            "coverage": "Only recorded pages/posts; complete means observed cursor exhausted, not all-source coverage",
        }
        if error:
            result.update(ok=False, error=error)
        return result

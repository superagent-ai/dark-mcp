"""Observe all MCP content reads and drive bounded, restart-safe collection jobs."""

import json
from urllib.parse import urlsplit

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore, identifier
from dark_mcp.forums import thread_identity
from dark_mcp.models import OnionPage, limit_value, parse_date
from dark_mcp.onion import onion_url
from dark_mcp.sections import SectionPlan
from dark_mcp.sources import SourceCatalog, page_access_state
from dark_mcp.telegram import channel_key


class CollectionService:
    def __init__(self, settings, catalog: SourceCatalog, store: EvidenceStore, onion, telegram, forums=None):
        self.settings, self.catalog, self.store = settings, catalog, store
        self.onion, self.telegram = onion, telegram
        self.forums = forums
        for source in catalog.sources.values():
            store.register(source["id"], source["kind"], source["locator"], source["name"], source)

    def onion_source(self, url: str) -> str:
        url = onion_url(url)
        host = urlsplit(url).hostname
        source = next(
            (
                s
                for s in self.catalog.sources.values()
                if (s["kind"] == "onion" or s.get("transport") == "tor")
                and host in s.get("allowed_hosts", [urlsplit(s["locator"]).hostname])
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

    async def forum_page(self, source_id: str, url: str | None = None, limit: int = 20, collection_id=None):
        if self.forums is None:
            raise DataAccessError("forum_not_configured", "Public forum collector is unavailable")
        source_metadata = self.forums.source(source_id)
        attempt = self.store.begin_attempt(source_id, {"url": url, "limit": limit, "collection_id": collection_id})
        cooldown_key = ("onion:" if source_metadata.get("transport") == "tor" else "forum:") + source_id
        try:
            self.store.cooldown(cooldown_key)
            page = await self.forums.fetch(source_id, url, limit)
            snapshot = page.model_dump(exclude={"collected_at", "evidence"})
            page.evidence = self.store.observe(
                source_id, "forum", page.url, page.url, snapshot, page.collected_at, collection_id
            )
            for post in page.posts:
                post_snapshot = {
                    **post.model_dump(exclude={"evidence"}),
                    "title": page.title,
                    "access_state": "content_returned_unverified",
                    "thread_url": post.url,
                    "thread_key": page.thread_key,
                    "transport": page.transport,
                    "access_context": page.access_context,
                }
                post.evidence = self.store.observe(
                    source_id,
                    "forum",
                    source_id + ":post:" + post.id,
                    post.url,
                    post_snapshot,
                    page.collected_at,
                    collection_id,
                    {"page_url": page.url, "observed_url": post.url},
                )
            self.store.finish_attempt(
                attempt,
                page.access_state,
                {
                    "url": page.url,
                    "page_type": page.page_type,
                    "posts": len(page.posts),
                    "thread_listings": len(page.threads),
                    "thread_listing_scope": page.thread_listing_scope,
                    "omitted_threads": page.omitted_threads,
                    "hidden_posts": sum(p.hidden_content for p in page.posts),
                    "truncated": page.truncated,
                    "next_page_urls": page.next_page_urls,
                    "evidence": page.evidence,
                },
            )
            return page
        except DataAccessError as exc:
            if exc.retry_after is not None:
                self.store.cooldown(cooldown_key, exc.retry_after)
            self.store.finish_attempt(attempt, exc.code, exc.as_dict())
            raise

    async def forum_threads(
        self, source_id: str, url: str | None = None, query: str | None = None, limit=20, exclude_pinned: bool = False
    ):
        limit_value(limit)
        if query is not None and (not query.strip() or len(query) > 500):
            raise ValueError("Use a nonempty local title filter up to 500 characters")
        page = await self.forum_page(source_id, url, limit)
        results = [row for row in page.threads if query is None or query.casefold().strip() in row["title"].casefold()]
        excluded = sum(row.get("pinned") is True for row in results) if exclude_pinned else 0
        if exclude_pinned:
            results = [row for row in results if row.get("pinned") is not True]
        return {
            "page": page.model_dump(),
            "results": results[:limit],
            "omitted_matches": max(0, len(results) - limit),
            "excluded_pinned_matches": excluded,
            "unknown_pin_status_matches": sum(row.get("pinned") is None for row in results),
            "coverage": "Local title filter on one public index page; follow next_page_urls. Not a site-wide search.",
        }

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
            self.store.cooldown("onion:" + source_id)
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
            if exc.retry_after is not None:
                self.store.cooldown("onion:" + source_id, exc.retry_after)
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
        if item["kind"] == "provider":
            raise DataAccessError(
                "historical_evidence_only",
                "Legacy provider evidence remains replayable; collect its original source directly",
            )
        if item["kind"] == "onion":
            return {"page": (await self.fetch(item["url"])).model_dump()}
        if item["kind"] == "forum":
            url = item["snapshot"].get("thread_url") or item["url"]
            page = await self.forum_page(item["source_id"], url, limit=100)
            result = {
                "page": page.model_dump(),
                "coverage": "Refetched the observed page. Absence from this page does not prove deletion.",
            }
            post_id = item["snapshot"].get("id")
            if post_id and not any(post.id == post_id for post in page.posts):
                result["availability_observation"] = self.store.unavailable(
                    evidence_id, "Previously observed forum post was not returned; access, pagination or content change"
                )
            return result
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
        max_threads: int = 100,
        max_index_pages: int = 3,
        max_thread_pages: int = 3,
        exclude_pinned: bool = False,
    ) -> dict:
        source = self.store.source(source_id)
        if source["metadata"].get("configuration_origin") == "local_operator":
            self.catalog.get(source_id)  # Removed configurations remain historical evidence, not active collectors.
        if source["kind"] == "provider":
            raise DataAccessError("historical_evidence_only", "Legacy provider sources have no active collector")
        if not source["locator"]:
            raise DataAccessError("source_access_required", "This source has no durable collection locator")
        if mode not in {"history", "updates", "section"}:
            raise ValueError("Choose history, updates or section")
        limit_value(max_threads, 500)
        limit_value(max_index_pages, 50)
        limit_value(max_thread_pages, 20)
        if mode != "section" and (max_threads, max_index_pages, max_thread_pages) != (100, 3, 3):
            raise ValueError("Thread discovery bounds require section mode")
        if type(exclude_pinned) is not bool or (exclude_pinned and mode != "section"):
            raise ValueError("Pinned-thread exclusion requires section mode")
        parse_date(since)
        if query is not None and (not query.strip() or len(query) > 500):
            raise ValueError("Check query")
        if thread_id is not None and (thread_id <= 0 or query is not None):
            raise ValueError("Thread reads cannot use server-side query filtering")
        options = {"mode": mode, "query": query, "since": since, "thread_id": thread_id}
        if mode == "section":
            if source["metadata"].get("collection_method") != "forum_page":
                raise DataAccessError(
                    "section_mode_requires_forum", "Section discovery requires a structured forum collector"
                )
            from dark_mcp.forums import source_url

            target = source_url(source["metadata"], url or source["locator"])
            if thread_identity(target) is not None or since is not None or thread_id is not None:
                raise ValueError(
                    "Section mode needs an index URL; query is a local title filter and dates are not filtered"
                )
            options.update(
                max_threads=max_threads,
                max_index_pages=max_index_pages,
                max_thread_pages=max_thread_pages,
                exclude_pinned=exclude_pinned,
                max_total_pages=max_index_pages + max_threads * max_thread_pages,
            )
            checkpoint = {"queue": [target], "visited": [], "pages_collected": 0}
            SectionPlan(source["metadata"], options, checkpoint)
        elif source["kind"] in {"onion", "forum"}:
            if mode != "history" or query is not None or since is not None or thread_id is not None:
                raise ValueError(
                    "Onion/forum jobs paginate explicit page links; re-fetch evidence separately for changes"
                )
            if source["metadata"].get("collection_method") == "forum_page":
                from dark_mcp.forums import source_url

                target = source_url(source["metadata"], url or source["locator"])
            else:
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
            return {
                "collection": existing,
                "results": [],
                "coverage": (
                    "Previously finished section bounds; inspect failed_pages and deferred_urls for gaps"
                    if existing["options"]["mode"] == "section"
                    else "Previously exhausted observed pagination"
                ),
            }
        job, token = self.store.claim(collection_id, (self.settings.request_timeout + 15) * 2 * max_pages + 30)
        source = self.store.source(job["source_id"])
        checkpoint, options = job["checkpoint"], job["options"]
        results, status = [], "ready"
        error = None
        try:
            for _ in range(max_pages):
                if job["kind"] in {"onion", "forum"}:
                    if not checkpoint["queue"]:
                        status = "complete"
                        break
                    url = checkpoint["queue"][0]
                    planner = (
                        SectionPlan(source["metadata"], options, checkpoint) if options["mode"] == "section" else None
                    )
                    if planner is not None and checkpoint["pages_collected"] >= options["max_total_pages"]:
                        for pending in checkpoint["queue"]:
                            planner.deferred(pending, "total_page_bound")
                        checkpoint["queue"] = []
                        planner.state["thread_owners"] = {}
                        status = "complete"
                        break
                    try:
                        page = (
                            await self.forum_page(source["id"], url, max_items, collection_id)
                            if source["metadata"].get("collection_method") == "forum_page"
                            else await self.fetch(url, collection_id)
                        )
                    except DataAccessError as exc:
                        if (
                            planner is None
                            or thread_identity(url) is None
                            or exc.retry_after is not None
                            or exc.code
                            in {
                                "forum_session_expired",
                                "forum_session_unavailable",
                                "invalid_forum_session",
                                "forum_auth_required",
                                "forum_unavailable",
                                "tor_unavailable",
                                "robots_unavailable",
                                "http_500",
                                "http_502",
                                "http_503",
                                "http_504",
                            }
                        ):
                            raise
                        planner.failure(url, exc.as_dict()["error"])
                        results.append({"failed_url": url, **exc.as_dict()})
                        checkpoint["pages_collected"] += 1
                        status = "ready" if checkpoint["queue"] else "complete"
                        if status == "complete":
                            break
                        continue
                    results.append({"page": page.model_dump()})
                    if source["metadata"].get("collection_method") == "forum_page" and page.omitted_posts:
                        raise DataAccessError(
                            "forum_page_incomplete",
                            "Post bounds omitted visible posts; cursor retained. "
                            "Retry with a higher max_items or text bound.",
                        )
                    if page.access_state != "content_returned_unverified":
                        if planner is None or thread_identity(url) is None:
                            raise DataAccessError(
                                "source_gated", "Source returned a gate or empty page; cursor retained"
                            )
                        planner.failure(
                            url,
                            {
                                "code": page.access_state,
                                "message": "Source body unavailable; retained as a coverage gap",
                            },
                        )
                    elif planner is not None:
                        checkpoint["visited"].append(url)
                        planner.advance(page)
                    else:
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
                                        if urlsplit(u).hostname
                                        in source["metadata"].get(
                                            "allowed_hosts", [urlsplit(source["locator"]).hostname]
                                        )
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
            "coverage": (
                "Section collection completed or resumed within configured bounds; inspect section failed_pages and "
                "deferred_urls for uncollected material. Hidden posts remain partial evidence."
                if options["mode"] == "section"
                else "Only recorded pages/posts; complete means observed cursor exhausted, not all-source coverage"
            ),
        }
        if error:
            result.update(ok=False, error=error)
        return result

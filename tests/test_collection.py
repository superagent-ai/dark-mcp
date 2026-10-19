from copy import deepcopy
from unittest.mock import AsyncMock

from mcp import Client

from dark_mcp.collection import CollectionService
from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.index import OnionIndex
from dark_mcp.models import OnionPage, now
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

SOURCE = "telegram:breachdetect"
CHANNEL = {"id": "-100123", "username": "breachdetect", "title": "Test channel", "url": "https://t.me/breachdetect"}


def post(message_id, text="Superagent.sh offer", channel=None):
    return {
        "id": message_id,
        "text": text,
        "channel": channel or CHANNEL,
        "url": f"https://t.me/breachdetect/{message_id}",
        "published_at": "2026-10-18T00:00:00Z",
        "edited_at": None,
        "has_media": False,
        "truncated": False,
        "reply_to_message_id": None,
    }


class Feed:
    def __init__(self):
        self.posts = {i: post(i) for i in range(101, 106)}
        self.calls = []
        self.error = None
        self.deleted = []

    async def messages(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        ids = sorted(self.posts, reverse=not kwargs.get("oldest_first"))
        ids = [
            i for i in ids if (not kwargs.get("before_id") or i < kwargs["before_id"]) and i > kwargs.get("after_id", 0)
        ][: kwargs["limit"]]
        return {
            "results": deepcopy([self.posts[i] for i in ids]),
            "channel": CHANNEL,
            "next_before_id": min(ids) if len(ids) == kwargs["limit"] else None,
            "next_after_id": max(ids) if ids else None,
            "page_full": len(ids) == kwargs["limit"],
            "scanned_count": len(ids),
            "collected_at": now(),
            "coverage": "Fixture feed",
        }

    async def message(self, channel, message_id):
        return {
            "results": [deepcopy(self.posts[message_id])] if message_id in self.posts else [],
            "channel": CHANNEL,
            "collected_at": now(),
        }

    def _allowed_channel(self, channel):
        assert channel == "breachdetect"

    async def changes(self, channel, pts, expected_id, limit):
        return {
            "channel": CHANNEL,
            "results": [],
            "next_pts": (pts or 0) + 10,
            "deleted_message_ids": self.deleted if pts is not None else [],
            "gap_detected": False,
            "initialized": pts is None,
            "has_more": False,
            "collected_at": now(),
        }


def service(settings, feed=None, onion=None):
    return CollectionService(
        settings, SourceCatalog(), EvidenceStore(settings.prepare_data_dir()), onion or AsyncMock(), feed or Feed()
    )


async def test_backfill_resumes_after_restart_without_skipping_and_saves_bounds(settings):
    feed = Feed()
    one = service(settings, feed)
    job = one.start(SOURCE, query="Superagent", since="2026-10-01T00:00:00Z")
    page = await one.next(job["id"], max_items=2)
    assert [p["id"] for p in page["results"][0]["results"]] == [105, 104]
    two = service(settings, feed)
    page = await two.next(job["id"], max_items=2)
    assert [p["id"] for p in page["results"][0]["results"]] == [103, 102]
    page = await two.next(job["id"], max_items=2)
    assert page["collection"]["status"] == "complete"
    assert len(two.store.search("superagent.sh")["results"]) == 5
    assert feed.calls[1]["before_id"] == 104
    assert feed.calls[1]["query"] == "Superagent"
    health = two.store.health(SOURCE)["results"][0]
    assert health["last_content_at"]
    assert two.store.health("telegram-channel:" + CHANNEL["id"])["results"][0]["last_content_at"]
    assert health["last_attempt"]["options"]["since"] == "2026-10-01T00:00:00Z"


async def test_update_job_drains_new_posts_oldest_first_without_skipping_backlog(settings):
    feed = Feed()
    collector = service(settings, feed)
    job = collector.start(SOURCE, mode="updates")
    initial = await collector.next(job["id"], max_items=2)
    assert initial["collection"]["checkpoint"]["after_id"] == 105
    feed.posts.update({i: post(i) for i in range(106, 112)})
    seen = []
    for _ in range(4):
        result = await collector.next(job["id"], max_items=2)
        seen.extend(p["id"] for p in result["results"][0]["results"])
    assert seen == [106, 107, 108, 109, 110, 111]
    assert result["collection"]["status"] == "idle"
    assert all(call.get("oldest_first") for call in feed.calls[1:])


async def test_failed_request_keeps_cursor_and_cooldown_blocks_network_after_restart(settings):
    feed = Feed()
    collector = service(settings, feed)
    job = collector.start(SOURCE)
    await collector.next(job["id"], max_items=2)
    feed.error = DataAccessError("telegram_rate_limited", "wait", retry_after=60)
    failed = await collector.next(job["id"], max_items=2)
    assert failed["collection"]["status"] == "waiting"
    assert failed["collection"]["checkpoint"]["before_id"] == 104
    before = len(feed.calls)
    other = service(settings, feed)
    failed = await other.next(job["id"], max_items=2)
    assert failed["error"]["code"] == "collection_rate_limited"
    assert len(feed.calls) == before


async def test_onion_pagination_gate_retry_and_search_retains_old_content(settings):
    catalog = SourceCatalog()
    source = catalog.get("onion:b333e6836938")
    root = source["url"]
    raw = AsyncMock()
    raw.fetch.side_effect = [
        OnionPage(url=root, title="Thread", text="Superagent.sh claim", next_page_urls=[root + "page2"]),
        OnionPage(url=root + "page2", title="Verify Humanity", text="Verify Humanity"),
        OnionPage(url=root + "page2", title="Thread replies", text="Superagent.sh follow-up"),
    ]
    collector = service(settings, onion=raw)
    job = collector.start(source["id"])
    page = await collector.next(job["id"])
    assert page["collection"]["checkpoint"]["queue"] == [root + "page2"]
    page = await collector.next(job["id"])
    assert page["error"]["code"] == "source_gated"
    assert page["collection"]["checkpoint"]["queue"] == [root + "page2"]
    assert not collector.store.search("Humanity")["results"]
    assert collector.store.search("Humanity", include_gates=True)["results"]
    page = await collector.next(job["id"])
    assert page["collection"]["status"] == "complete"
    assert len(collector.store.search("superagent.sh")["results"]) == 2


async def test_direct_missing_explicit_delete_update_edits_and_identity_guard(settings):
    feed = Feed()
    collector = service(settings, feed)
    result = await collector.messages("breachdetect", limit=1)
    evidence_id = result["results"][0]["evidence"]["evidence_id"]
    first_revision = result["results"][0]["evidence"]["revision_id"]
    feed.posts[105]["text"] = "Withdrawn offer"
    changed = await collector.refresh(evidence_id)
    assert changed["results"][0]["evidence"]["change_type"] == "edited"
    assert collector.store.get(evidence_id, first_revision)["snapshot"]["text"] == "Superagent.sh offer"
    del feed.posts[105]
    missing = await collector.refresh(evidence_id)
    assert missing["availability_observation"]["deletion_confirmed"] is False
    await collector.sync_changes("breachdetect")
    feed.deleted = [105]
    result = await collector.sync_changes("breachdetect")
    assert result["deletion_observations"][0]["deletion_confirmed"]
    assert collector.store.get(evidence_id)["availability"] == "deleted"


def test_legacy_cache_import_is_idempotent_and_not_fresh_health(settings, onion):
    index = OnionIndex(settings.prepare_data_dir())
    index.put(OnionPage(url=onion, title="Old", text="Superagent.sh", collected_at="2026-10-01T00:00:00Z"))
    collector = service(settings)
    collector.import_legacy(index)
    collector.import_legacy(index)
    item = collector.store.search("superagent.sh")["results"][0]
    assert item["collected_at"] == "2026-10-01T00:00:00+00:00"
    assert collector.store.counts()["observations"] == 1
    assert collector.store.health(item["source_id"])["results"][0]["last_attempt_at"] is None


async def test_actual_mcp_tools_collect_restart_search_revisions_and_profiles(settings, monkeypatch):
    feed = Feed()
    monkeypatch.setattr("dark_mcp.telegram.TelegramConnector.messages", lambda _, **kw: feed.messages(**kw))
    settings.telegram_catalog_sources = True
    async with Client(create_server(settings)) as client:

        async def call(tool_name, **arguments):
            return (await client.call_tool(tool_name, arguments)).structured_content

        created = await call("start_collection", source_id=SOURCE)
        collection_id = created["collection"]["id"]
        page = await call("collect_next", collection_id=collection_id, max_items=2)
        ev = page["results"][0]["results"][0]["evidence"]
        profile = await call(
            "save_company_profile",
            profile_id="superagent",
            name="Superagent",
            identifiers=["superagent.sh"],
            references=["https://superagent.sh"],
        )
        assert profile["verification"].startswith("caller_provided")
    async with Client(create_server(settings)) as client:

        async def call(tool_name, **arguments):
            return (await client.call_tool(tool_name, arguments)).structured_content

        resumed = await call("collect_next", collection_id=collection_id, max_items=2)
        assert resumed["results"][0]["results"][0]["id"] == 103
        found = await call("search_evidence", query="SUPERAGENT.sh")
        assert len(found["results"]) == 4
        exact = await call("get_evidence", evidence_id=ev["evidence_id"], revision_id=ev["revision_id"])
        assert exact["evidence"]["snapshot"]["text"] == "Superagent.sh offer"
        assert (await call("get_exact_duplicates", evidence_id=ev["evidence_id"]))["results"]
        assert (await call("get_company_profile", profile_id="superagent"))["document"]["name"] == "Superagent"
        assert (await call("list_evidence_changes"))["results"]
        assert (await call("get_source_health", source_id=SOURCE))["results"][0]["last_content_at"]

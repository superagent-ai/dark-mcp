from unittest.mock import AsyncMock

import pytest
from mcp import Client

from dark_mcp.coverage import CorpusCoverage
from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.server import create_server


def post(store, source, pid, root=False, thread="/threads/request.1/", **extra):
    return store.observe(
        source,
        "forum",
        source + ":" + pid,
        "https://example.test/" + pid,
        {"id": pid, "thread_key": thread, "is_thread_root": root, "text": "Claim", **extra},
    )


def test_inventory_does_not_inflate_posts_for_refresh_history_or_aggregate_pages(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    store.register("a", "onion", None, "Tor forum")
    store.register("b", "forum", None, "Other forum")
    root = post(store, "a", "1", True, published_at="2026-01-01T00:00:00Z")
    post(store, "a", "1", True, published_at="2026-01-01T00:00:00Z")
    post(store, "a", "2", hidden_content=True, truncated=True)
    store.observe(
        "a",
        "forum",
        "page",
        "https://example.test/threads/request.1/",
        {"text": "Claim\nClaim", "page_type": "thread", "posts": [{"hidden_content": True}]},
    )
    store.observe("a", "forum", "legacy", "https://example.test/legacy", {"text": "Unindexed"})
    store.observe(
        "a", "onion", "root", "https://example.test/", {"text": "Challenge", "access_state": "challenge_suspected"}
    )
    # Same thread key on a second source is a distinct discussion. It has no explicit root.
    post(store, "b", "1")
    post(store, "b", "2", root=None)
    store.unavailable(root["evidence_id"], "direct recheck returned no post")
    coverage = CorpusCoverage(EvidenceStore(settings.data_dir)).get(kind="forum", limit=1)
    summary = coverage["summary"]
    assert summary["sources_with_items"] == 2
    assert summary["items"] == summary["stored_revisions"] == 6
    assert summary["unreported_acquisition_forum_items"] == 6
    assert summary["native_forum_items"] == summary["operator_import_forum_items"] == 0
    assert summary["indexed_forum_posts"] == 4
    assert summary["forum_page_items"] == summary["unindexed_forum_items"] == 1
    assert summary["indexed_forum_threads"] == 2
    assert summary["threads_without_explicit_root"] == 1
    assert summary["hidden_forum_post_items"] == 1  # Not also counted from the aggregate page.
    assert summary["truncated_items"] == summary["unavailable_items"] == 1
    assert summary["items_without_published_at"] == 5
    assert summary["oldest_published_at"] == "2026-01-01T00:00:00+00:00"
    assert coverage["results"][0]["catalog_kind"] == "onion"  # Evidence kind and transport differ.
    assert coverage["next_offset"] == 1
    second = CorpusCoverage(store).get(kind="forum", offset=1, limit=1)
    assert second["summary"] == summary and second["results"][0]["source_id"] == "b"
    assert second["observations_through_seq"] == coverage["observations_through_seq"]
    assert second["next_offset"] is None
    all_kinds = CorpusCoverage(store).get(source_id="a")["summary"]
    assert all_kinds["items"] == 5 and all_kinds["default_search_excluded_items"] == 1


def test_latest_revision_flags_and_membership_not_old_claims_or_old_thread(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    store.register("a", "forum", None, "Forum")
    post(store, "a", "1", True, hidden_content=True, truncated=True)
    post(store, "a", "2")
    earlier = CorpusCoverage(store).get()
    # A moved root does not establish a saved root in its former thread.
    post(store, "a", "1", True, thread="/threads/moved.2/", hidden_content=False)
    latest = CorpusCoverage(store).get()
    assert latest["observations_through_seq"] > earlier["observations_through_seq"]
    result = latest["summary"]
    assert result["items"] == 2 and result["stored_revisions"] == 3
    assert result["indexed_forum_threads"] == 2 and result["threads_without_explicit_root"] == 1
    assert result["hidden_forum_post_items"] == result["truncated_items"] == 0


def test_empty_inventory_and_scoped_zero_are_explicit(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    store.register("empty", "onion", None, "Never collected")
    coverage = CorpusCoverage(store)
    assert coverage.get()["results"] == []
    scoped = coverage.get(source_id="empty", kind="forum")
    assert scoped["summary"]["items"] == 0
    assert scoped["results"][0]["source_id"] == "empty"
    assert scoped["results"][0]["items"] == 0
    assert scoped["results"][0]["first_recorded_at"] is None
    for args in ({"kind": "tor"}, {"offset": -1}, {"limit": 101}):
        with pytest.raises(ValueError):
            coverage.get(**args)
    with pytest.raises(DataAccessError):
        coverage.get(source_id="unknown")


async def test_real_mcp_coverage_is_local_and_negative_search_exposes_filters(settings, monkeypatch):
    from dark_mcp.forums import ForumConnector
    from dark_mcp.onion import OnionConnector
    from dark_mcp.telegram import TelegramConnector

    network = AsyncMock(side_effect=AssertionError("No network allowed"))
    monkeypatch.setattr(ForumConnector, "fetch", network)
    monkeypatch.setattr(OnionConnector, "fetch", network)
    monkeypatch.setattr(TelegramConnector, "messages", network)
    async with Client(create_server(settings)) as client:
        result = (
            await client.call_tool(
                "get_corpus_coverage",
                {
                    "source_id": "onion:91e4e785a3af",
                    "kind": "forum",
                },
            )
        ).structured_content
        assert result["ok"] and result["summary"]["items"] == 0
        assert result["results"][0]["catalog_kind"] == "onion"
        search = (
            await client.call_tool(
                "search_evidence",
                {
                    "query": "Absent",
                    "kind": "forum",
                    "include_history": False,
                    "collected_since": "2026-10-19T00:00:00Z",
                },
            )
        ).structured_content
        assert search["results"] == []
        assert search["corpus_scope"]["evidence_kind"] == "forum"
        assert not search["corpus_scope"]["include_history"]
        assert search["corpus_scope"]["collected_since"] == "2026-10-19T00:00:00+00:00"
        assert search["corpus_scope"]["inventory_tool"] == "get_corpus_coverage"
        assert not search["corpus_scope"]["inventory_is_search_denominator"]
        network.assert_not_awaited()

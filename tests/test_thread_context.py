import json
from unittest.mock import AsyncMock

import pytest
import respx
from mcp import Client

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.forums import ForumConnector
from dark_mcp.server import create_server
from dark_mcp.thread_context import ThreadContext, forum_thread_key

SOURCE = "forum:hard-tm"
ORIGIN = "https://hard-tm.com"
PINNED = "https://8.8.8.8"


def store_for(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    store.register(SOURCE, "forum", ORIGIN, "Fixture forum")
    store.register("other", "forum", "https://other.example", "Other fixture")
    return store


def save(store, pid="11", text="Original alleged access", thread="/threads/access.42", source=SOURCE, **extra):
    return store.observe(
        source,
        "forum",
        source + ":post:" + pid,
        ORIGIN + thread + "/post-" + pid,
        {
            "id": pid,
            "thread_key": thread,
            "thread_url": ORIGIN + thread,
            "url": ORIGIN + thread + "/post-" + pid,
            "title": "Fixture claim",
            "text": text,
            "author": "fixture-author",
            "published_at": "2025-01-01T00:00:00Z",
            **extra,
        },
    )


def at(store, evidence, timestamp):
    with store.connect() as db:
        db.execute(
            "UPDATE observations SET observed_at=? WHERE evidence_id=? AND revision_id=?",
            (timestamp, evidence["evidence_id"], evidence["revision_id"]),
        )


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("/threads/slug.42/page-2", "xenforo:42"),
        ("https://mirror.example/threads/42/post-31", "xenforo:42"),
        ("/showthread.php?tid=42&pid=31", "mybb:tid:42"),
        ("tid:042", "mybb:tid:42"),
        ("/Thread-Original", "mybb:slug:/Thread-Original"),
        ("/forums/access.42/", None),
        ("http://[invalid", None),
    ],
)
def test_only_explicit_source_thread_identity_is_normalized(value, key):
    assert forum_thread_key(value) == key


def test_context_joins_mirror_aliases_but_not_other_sources_or_same_title(settings):
    store = store_for(settings)
    root = save(store, is_thread_root=True, post_number=1)
    reply = save(store, pid="12", text="Reply clarification", thread="/threads/42", is_thread_root=False, post_number=2)
    save(store, pid="13", text="Unrelated thread", thread="/threads/43")
    save(store, text="Same handle and title on another source", source="other")
    context = ThreadContext(store, 60000).get(reply["evidence_id"], limit=1)
    assert context["root_observed"] and context["total_stored_posts"] == 2 and context["next_offset"] == 1
    assert context["results"][0]["evidence_id"] == root["evidence_id"]
    rest = ThreadContext(store, 60000).get(root["evidence_id"], offset=1)
    assert rest["results"][0]["evidence_id"] == reply["evidence_id"]
    assert "Unrelated" not in json.dumps(context) and "another source" not in json.dumps(context)


def test_observation_cutoff_excludes_later_edits_and_replies_even_with_old_source_dates(settings):
    store = store_for(settings)
    first = save(store, text="Original allegation", is_thread_root=True)
    at(store, first, "2026-10-01T08:00:00+00:00")
    edited = save(store, text="Claim withdrawn", is_thread_root=True)
    at(store, edited, "2026-10-03T08:00:00+00:00")
    reply = save(store, pid="12", text="Later reply", is_thread_root=False)
    at(store, reply, "2026-10-03T09:00:00+00:00")
    context = ThreadContext(store, 60000)
    past = context.get(first["evidence_id"], as_of="2026-10-02T00:00:00Z")
    assert past["total_stored_posts"] == 1 and past["results"][0]["text"] == "Original allegation"
    assert past["results"][0]["revision_id"] == first["revision_id"]
    assert past["cutoff_basis"] == "local_observation_recording_time"
    current = context.get(first["evidence_id"])
    assert current["total_stored_posts"] == 2 and current["results"][0]["text"] == "Claim withdrawn"
    with pytest.raises(DataAccessError) as exc:
        context.get(first["evidence_id"], as_of="2026-09-30T00:00:00Z")
    assert exc.value.code == "evidence_not_observed"
    with pytest.raises(DataAccessError):
        context.get(reply["evidence_id"], as_of="2026-10-02T00:00:00Z")


def test_context_retains_missing_root_hidden_truncated_and_unavailable_posts(settings):
    store = store_for(settings)
    reply = save(store, pid="12", text="Visible part only", hidden_content=True, truncated=True, is_thread_root=False)
    at(store, reply, "2026-10-01T08:00:00+00:00")
    store.unavailable(reply["evidence_id"], "Source no longer readable")
    context = ThreadContext(store, 60000)
    current = context.get(reply["evidence_id"])
    assert not current["root_observed"]
    post = current["results"][0]
    assert post["hidden_content"] and post["truncated"]
    assert post["availability_at_observation"] == "unavailable" and post["text"] == "Visible part only"
    past = context.get(reply["evidence_id"], as_of="2026-10-02T00:00:00Z")["results"][0]
    assert past["availability_at_observation"] == "observed"
    assert "unavailable" not in json.dumps(past)


def test_moved_post_does_not_reappear_from_old_thread_membership(settings):
    store = store_for(settings)
    seed = save(store)
    moved = save(store, pid="12", text="Reply")
    save(store, pid="12", text="Moved to unrelated thread", thread="/threads/43")
    assert ThreadContext(store, 60000).get(seed["evidence_id"])["total_stored_posts"] == 1
    assert ThreadContext(store, 60000).get(moved["evidence_id"])["thread_key"] == "xenforo:43"


def test_context_text_bound_is_explicit_and_exact_evidence_is_unchanged(settings):
    store = store_for(settings)
    item = save(store, text="A" * 10000, is_thread_root=True, author="X" * 2000)
    save(store, pid="12", text="B" * 10000, is_thread_root=False)
    view = ThreadContext(store, 1500).get(item["evidence_id"])
    assert sum(len(row["text"]) for row in view["results"]) == 1500
    assert all(row["context_text_truncated"] for row in view["results"])
    assert store.get(item["evidence_id"])["snapshot"]["text"] == "A" * 10000
    assert view["results"][0]["snapshot_hash"] == item["snapshot_hash"]
    assert len(view["results"][0]["author"]) == 500 and view["results"][0]["author_display_truncated"]


def test_existing_forum_snapshots_are_indexed_without_rewriting_revisions_or_hashes(settings):
    store = store_for(settings)
    root = save(store, is_thread_root=True)
    reply = save(store, pid="12", is_thread_root=False)
    before = store.get(root["evidence_id"])
    with store.connect() as db:
        db.execute("DELETE FROM migrations WHERE name='forum-context-v1'")
        db.execute("DROP TABLE forum_thread_members")
    restarted = EvidenceStore(settings.data_dir)
    view = ThreadContext(restarted, 60000).get(reply["evidence_id"])
    assert view["total_stored_posts"] == 2
    assert restarted.get(root["evidence_id"])["snapshot_hash"] == before["snapshot_hash"]
    assert restarted.get(root["evidence_id"])["revision_id"] == before["revision_id"]
    assert len(restarted.history(root["evidence_id"])["results"]) == 1


@respx.mock
async def test_real_mcp_reconstructs_collected_thread_after_restart_without_network(settings, monkeypatch):
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/threads/42/").respond(
        text="""<title>Fixture claim</title>
      <article class="message--post" data-content="post-11" data-author="seller" data-position="0">
      <div class="message-body"><div class="bbWrapper">Initial allegation</div></div></article>
      <a rel="next" href="/threads/42/page-2">Next</a>"""
    )
    respx.get(PINNED + "/threads/42/page-2").respond(
        text="""<title>Fixture claim</title>
      <article class="message--post" data-content="post-12" data-author="reply-author" data-position="1">
      <div class="message-body"><div class="bbWrapper">Contradictory reply</div></div></article>"""
    )
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool(
                "start_collection",
                {
                    "source_id": SOURCE,
                    "url": ORIGIN + "/threads/42/",
                },
            )
        ).structured_content["collection"]
        result = (
            await client.call_tool("collect_next", {"collection_id": job["id"], "max_pages": 2})
        ).structured_content
        root = result["results"][0]["page"]["posts"][0]["evidence"]
        seed = result["results"][1]["page"]["posts"][0]["evidence"]
    calls = len(respx.calls)
    async with Client(create_server(settings)) as client:
        view = (await client.call_tool("get_collected_thread", {"evidence_id": seed["evidence_id"]})).structured_content
        assert view["ok"] and view["root_observed"] and view["total_stored_posts"] == 2
        assert [row["post_number"] for row in view["results"]] == [1, 2]
        assert view["results"][0]["evidence_id"] == root["evidence_id"]
        assert view["results"][1]["text"] == "Contradictory reply"
        invalid = (
            await client.call_tool("get_collected_thread", {"evidence_id": seed["evidence_id"], "as_of": "2026-10-01"})
        ).structured_content
        assert invalid["error"]["code"] == "invalid_arguments"
    assert len(respx.calls) == calls


def test_historical_context_does_not_expose_future_title_url_or_availability(settings):
    store = store_for(settings)
    root = save(store, is_thread_root=True)
    at(store, root, "2026-10-01T08:00:00+00:00")
    updated = save(store, text="Future contents", title="Future title", url=ORIGIN + "/threads/future-title.42/post-11")
    at(store, updated, "2026-10-03T08:00:00+00:00")
    store.unavailable(root["evidence_id"], "Later failure")
    view = ThreadContext(store, 60000).get(root["evidence_id"], as_of="2026-10-02T00:00:00Z")
    assert view["results"][0]["revision_id"] == root["revision_id"]
    assert "Future" not in json.dumps(view) and "future-title" not in json.dumps(view)
    assert "unavailable" not in json.dumps(view)


async def test_page_anchor_and_unsupported_evidence_report_local_coverage(settings):
    store = store_for(settings)
    save(store, is_thread_root=True)
    page = store.observe(
        SOURCE,
        "forum",
        ORIGIN + "/threads/42/",
        ORIGIN + "/threads/42/",
        {
            "thread_key": "/threads/42",
            "text": "Aggregate page body",
            "title": "Forum thread page",
        },
    )
    unsupported = store.observe(SOURCE, "onion", "generic", "http://unused.onion", {"text": "Generic page"})
    async with Client(create_server(settings)) as client:
        view = (await client.call_tool("get_collected_thread", {"evidence_id": page["evidence_id"]})).structured_content
        assert view["ok"] and view["total_stored_posts"] == 1
        assert view["results"][0]["text"] == "Original alleged access"
        error = (
            await client.call_tool("get_collected_thread", {"evidence_id": unsupported["evidence_id"]})
        ).structured_content
        assert error["error"]["code"] == "thread_context_unavailable"

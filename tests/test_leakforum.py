from unittest.mock import AsyncMock

import pytest
import respx
from mcp import Client

from dark_mcp.errors import DataAccessError
from dark_mcp.forums import ForumConnector, parse_forum, source_url
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

ORIGIN = "https://leakforum.io"
THREAD = ORIGIN + "/Thread-Fixture-request"
PINNED = "https://8.8.8.8"


def post(pid, number, text):
    return f"""<article class="post-article" id="post_{pid}">
    <div class="post-username"><a>fixture-member</a></div>
    <span class="post_date"><span title="10-19-2026, 12:48 AM">Today</span></span>
    <div class="right pagnum"><small><a href="Thread-Fixture-request?pid={pid}#pid{pid}">#{number}</a></small></div>
    <div class="post_body">{text}</div>
    <button onclick="quick_quote({pid}, 'fixture-member', 1792368000)">Quote</button></article>"""


def test_leakforum_header_metadata_and_catalog_scope():
    source = SourceCatalog().get("forum:leakforum")
    assert source["forum_engine"] == "mybb" and source["transport"] == "direct_https"
    assert source["checked_at"] == source["research"]["forum_access"]["checked_at"]
    assert source["checked_at"] != SourceCatalog().get("forum:cracked")["checked_at"]
    doc = post(111, 1, 'Company.example claim <a href="https://example.test/proof">Reference</a>')
    doc += post(112, 2, '<div class="hidden-content">You must register to view this content</div>')
    page = parse_forum(source, THREAD, doc, 60000, 100)
    assert [p.post_number for p in page.posts] == [1, 2]
    assert [p.is_thread_root for p in page.posts] == [True, False]
    assert page.posts[0].url == THREAD + "?pid=111"
    assert page.posts[0].author == "fixture-member"
    assert page.posts[0].timestamp_basis == "source_unix_timestamp"
    assert page.published_at == page.posts[0].published_at
    assert page.posts[1].hidden_content
    assert page.posts[0].links == [{"url": "https://example.test/proof", "label": "Reference"}]
    for url in [
        "https://leakforum.st/Thread-Fixture-request",
        ORIGIN + "/member.php?action=register",
        ORIGIN + "/showthread.php?tid=1&action=lastpost",
        ORIGIN + "/attachment.php?aid=1",
    ]:
        with pytest.raises(DataAccessError):
            source_url(source, url)


@respx.mock
async def test_leakforum_section_to_original_replies_restarts_and_replays_over_mcp(settings, monkeypatch):
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/Forum-Buyer-Requests").respond(text=f'<a href="{THREAD}">Fixture request</a>')
    respx.get(PINNED + "/Thread-Fixture-request?page=2").respond(text=post(112, 2, "Unverified reply"))
    respx.get(PINNED + "/Thread-Fixture-request?pid=111").respond(text=post(111, 1, "Company.example access claim"))
    respx.get(PINNED + "/Thread-Fixture-request").respond(
        text=post(111, 1, "Company.example access claim") + '<a class="pagination_next" href="?page=2">Next</a>'
    )
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool(
                "start_collection",
                {
                    "source_id": "forum:leakforum",
                    "mode": "section",
                    "max_threads": 1,
                    "max_index_pages": 1,
                    "max_thread_pages": 2,
                },
            )
        ).structured_content["collection"]
        discovery = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert discovery["collection"]["checkpoint"]["queue"] == [THREAD]
    async with Client(create_server(settings)) as client:
        first = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        root = first["results"][0]["page"]["posts"][0]["evidence"]
        assert first["collection"]["checkpoint"]["queue"] == [THREAD + "?page=2"]
    async with Client(create_server(settings)) as client:
        last = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert last["collection"]["status"] == "complete"
        context = (
            await client.call_tool(
                "get_collected_thread",
                {
                    "evidence_id": root["evidence_id"],
                },
            )
        ).structured_content
        assert context["root_observed"] and [p["post_number"] for p in context["results"]] == [1, 2]
        saved = (
            await client.call_tool(
                "get_evidence",
                {
                    "evidence_id": root["evidence_id"],
                    "revision_id": root["revision_id"],
                },
            )
        ).structured_content["evidence"]
        assert saved["snapshot_hash"] == root["snapshot_hash"]
        refreshed = (
            await client.call_tool(
                "refresh_evidence",
                {
                    "evidence_id": root["evidence_id"],
                },
            )
        ).structured_content
        assert refreshed["ok"] and refreshed["page"]["posts"][0]["evidence"]["change_type"] == "unchanged"

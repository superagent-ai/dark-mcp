from unittest.mock import AsyncMock

import pytest
import respx
from mcp import Client

from dark_mcp.errors import DataAccessError
from dark_mcp.forums import ForumConnector, parse_forum, source_url
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

SOURCE = "forum:bf-st"
ORIGIN = "https://bf.st"
THREAD = ORIGIN + "/Thread-Fixture-access-request"
PINNED = "https://8.8.8.8"


def post(pid, number, text, *, edited=False, relative=False):
    date = '<span title="Oct 19, 2026, 10:14 AM">5 hours ago</span>' if relative else "Oct 17, 2026, 07:15 PM"
    notice = (
        '<span class="post_edit"><span class="edited_post">'
        "(This post was last modified: Oct 18, 2026, 01:47 PM by "
        '<a href="User-fixture-editor">fixture-editor</a>.)</span></span>'
        if edited
        else '<span class="post_edit"></span>'
    )
    return f'''<div class="post" id="post_{pid}">
    <div class="post__author scaleimages"><div class="post__author-info"><div class="post__user">
    <div class="post__user-profile largetext"><a href="User-fixture-author">fixture-author</a></div>
    <a class="post__user-avatar" href="User-fixture-author"></a></div></div></div>
    <div class="post__content"><div class="post_head"><div class="float_right"><strong>
    <div class="float_right"><strong><a href="{THREAD}?pid={pid}#pid{pid}">#{number}</a></strong></div>
    </strong></div><span class="post_date">{date} {notice}</span></div>
    <div class="post_body">{text}</div></div></div>'''


def test_observed_source_scope_does_not_authenticate_historical_brand_or_mirrors():
    catalog = SourceCatalog()
    source = catalog.get(SOURCE)
    assert source["allowed_hosts"] == ["bf.st"] and source["transport"] == "direct_https"
    assert source["forum_engine"] == "mybb" and source["collection_method"] == "forum_page"
    assert source["research"]["forum_access"]["verification"]["source_identity_verified"] is False
    assert catalog.get("forum:breachforums")["collection_method"] == "metadata_only"
    assert "onion" in source["research"]["forum_access"]["advertised_mirrors"][0]["url"]
    for url in [
        "https://breachforums.rs/Thread-Fixture-access-request",
        ORIGIN + "/member.php?action=register",
        THREAD + "?action=lastpost",
        ORIGIN + "/attachment.php?aid=42",
        source["research"]["forum_access"]["advertised_mirrors"][0]["url"],
    ]:
        with pytest.raises(DataAccessError):
            source_url(source, url)


def test_bem_author_nested_counter_and_separate_publication_edit_notices():
    page = parse_forum(SourceCatalog().get(SOURCE), THREAD, post(42, 1, "Visible original", edited=True), 60000, 100)
    root = page.posts[0]
    assert root.author == "fixture-author" and root.post_number == 1 and root.is_thread_root
    assert root.url == THREAD + "?pid=42"
    assert root.published_at_raw == "Oct 17, 2026, 07:15 PM"
    assert "last modified" in root.edited_at_raw and "fixture-editor" in root.edited_at_raw
    assert root.published_at is None and root.edited_at is None and page.published_at is None
    assert root.timestamp_basis == "unresolved_source_text"


def test_reply_counter_ignores_quoted_body_headers_and_retains_unresolved_time_hint():
    body = """Clarification <blockquote><div class="post_head"><div class="float_right">
    <a href="?pid=9">#1</a></div></div></blockquote><div class="hidden-content">Login to view</div>"""
    page = parse_forum(SourceCatalog().get(SOURCE), THREAD + "?page=2", post(52, 11, body, relative=True), 60000, 100)
    reply = page.posts[0]
    assert reply.post_number == 11 and reply.is_thread_root is False and reply.hidden_content
    assert reply.published_at is None and reply.published_at_raw == "5 hours ago"
    assert reply.published_at_hint == "Oct 19, 2026, 10:14 AM"


def test_conflicting_nested_header_positions_remain_unknown():
    html = post(42, 1, "Original").replace(
        '<span class="post_date">',
        '<div class="float_right"><a href="?pid=42">#2</a></div><span class="post_date">',
    )
    item = parse_forum(SourceCatalog().get(SOURCE), THREAD, html, 60000, 100).posts[0]
    assert item.post_number is None and item.is_thread_root is None


@respx.mock
async def test_packaged_source_section_pagination_restart_replay_and_refresh(settings, monkeypatch):
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())
    respx.get(PINNED + "/robots.txt").respond(404)
    index = '<span id="tid_17"><a href="Thread-Fixture-access-request">Fixture company request</a></span>'
    respx.get(PINNED + "/Forum-Buyers-Place").respond(text=index)
    respx.get(PINNED + "/Thread-Fixture-access-request?page=2").respond(text=post(43, 2, "Reply clarification"))
    respx.get(PINNED + "/Thread-Fixture-access-request?pid=42").respond(text=post(42, 1, "Acme.example request"))
    respx.get(PINNED + "/Thread-Fixture-access-request").respond(
        text=post(42, 1, "Acme.example request") + '<a class="pagination_next" href="?page=2">Next</a>'
    )
    async with Client(create_server(settings)) as client:
        start = await client.call_tool(
            "start_collection",
            {"source_id": SOURCE, "url": ORIGIN + "/Forum-Buyers-Place", "mode": "section", "max_threads": 1},
        )
        job = start.structured_content["collection"]
        discovery = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert discovery["collection"]["checkpoint"]["queue"] == [THREAD]
        first = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        root = first["results"][0]["page"]["posts"][0]["evidence"]
    async with Client(create_server(settings)) as client:
        last = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert last["collection"]["status"] == "complete"
        context = (
            await client.call_tool("get_collected_thread", {"evidence_id": root["evidence_id"]})
        ).structured_content
        assert context["root_observed"] and context["total_stored_posts"] == 2
        assert [p["post_number"] for p in context["results"]] == [1, 2]
        assert context["results"][0]["acquisition"]["method"] == "native_fetch"
        exact = (
            await client.call_tool(
                "get_evidence", {"evidence_id": root["evidence_id"], "revision_id": root["revision_id"]}
            )
        ).structured_content
        assert exact["evidence"]["snapshot_hash"] == root["snapshot_hash"]
        refreshed = (
            await client.call_tool("refresh_evidence", {"evidence_id": root["evidence_id"]})
        ).structured_content
        assert refreshed["ok"]
        positive = (
            await client.call_tool("search_evidence", {"query": "Acme.example", "source_id": SOURCE})
        ).structured_content
        assert any(hit["evidence_id"] == root["evidence_id"] for hit in positive["results"])

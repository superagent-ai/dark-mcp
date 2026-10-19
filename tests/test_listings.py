from unittest.mock import AsyncMock

import respx
from mcp import Client

from dark_mcp.forums import ForumConnector, parse_forum
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

ORIGIN = "https://leakforum.io"
PINNED = "https://8.8.8.8"


def mybb_row(tid, slug):
    return f"""<tr class="inline_row"><td class="forumdisplay_sticky">
    <span id="tid_{tid}"><a href="Thread-{slug}">{slug}</a></span>
    <div class="author"><a>original-author</a><span class="thread-date">
    <span title="07-01-2022, 04:12 AM">4 years ago</span></span></div></td>
    <td class="lastpost"><div class="last-poster_data"><a>bumping-author</a>
    <time datetime="2026-10-19T12:00:00+0300">Today</time></div></td></tr>"""


def listing_document():
    return (
        """<a href="Thread-Unrelated-sidebar">Unrelated sidebar</a><table>
    <tr><td class="trow_sep">Important Threads</td></tr>"""
        + mybb_row(1, "pinned")
        + """
    <tr><td class="trow_sep">Normal Threads</td></tr>"""
        + mybb_row(2, "normal")
        + """
    <tr><td class="trow_sep">Custom group</td></tr>"""
        + mybb_row(3, "unknown")
        + "</table>"
    )


def test_mybb_metadata_uses_headings_not_misleading_theme_classes_and_ignores_sidebar():
    source = SourceCatalog().get("forum:leakforum")
    page = parse_forum(source, ORIGIN + "/Forum-Buyer-Requests", listing_document(), 60000, 100)
    assert page.thread_listing_scope == "recognized_thread_rows"
    assert [row["title"] for row in page.threads] == ["pinned", "normal", "unknown"]
    assert [row["pinned"] for row in page.threads] == [True, False, None]
    assert all(row["author"] == "original-author" for row in page.threads)
    normal = page.threads[1]
    assert normal["source_thread_id"] == "2" and normal["listing_basis"] == "mybb_subject_row"
    assert normal["published_at"] is None and normal["published_at_raw"] == "07-01-2022, 04:12 AM"
    assert normal["last_activity_at"] == "2026-10-19T09:00:00+00:00"


def xf_row(tid):
    return f"""<div class="structItem--thread js-threadListItem-{tid}" data-author="creator">
    <div class="structItem-cell--main"><div class="structItem-title">
    <a href="/forums/sell_all/?prefix_id=1">Prefix</a><a href="/threads/topic.{tid}/">Topic {tid}</a></div>
    <div class="structItem-startDate"><time datetime="2022-09-16T12:00:00+0300">2022</time></div></div>
    <time class="structItem-latestDate" datetime="2026-10-19T12:00:00+0300">Today</time></div>"""


def test_xenforo_source_creation_and_activity_remain_distinct_and_pin_groups_are_explicit():
    source = SourceCatalog().get("forum:hard-tm")
    doc = '<a href="/threads/sidebar.99/">Sidebar</a>'
    doc += '<div class="structItemContainer-group--sticky structItemContainer-group">'
    doc += xf_row(1) + '</div><div class="structItemContainer-group js-threadList">' + xf_row(2) + "</div>"
    doc += xf_row(3)
    page = parse_forum(source, "https://hard-tm.com/forums/sell_all/", doc, 60000, 100)
    assert len(page.threads) == 3 and [r["pinned"] for r in page.threads] == [True, False, None]
    assert all(row["listing_basis"] == "xenforo_thread_row" and row["author"] == "creator" for row in page.threads)
    assert page.threads[0]["published_at"] == "2022-09-16T09:00:00+00:00"
    assert page.threads[0]["last_activity_at"] == "2026-10-19T09:00:00+00:00"
    assert page.threads[0]["source_thread_id"] == "1"


def test_unscoped_links_have_unknown_pin_status_and_row_cap_reports_omissions():
    source = SourceCatalog().get("forum:leakforum")
    fallback = parse_forum(source, ORIGIN + "/", '<a href="Thread-Fallback">Fallback</a>', 60000, 100)
    assert fallback.thread_listing_scope == "unscoped_links" and fallback.threads[0]["pinned"] is None
    doc = "<table>" + "".join(mybb_row(n, "topic-" + str(n)) for n in range(1, 105)) + "</table>"
    bounded = parse_forum(source, ORIGIN + "/Forum-Buyer-Requests", doc, 60000, 100)
    assert len(bounded.threads) == 100 and bounded.omitted_threads == 4 and bounded.truncated
    assert all(row["pinned"] is None for row in bounded.threads)
    from dark_mcp.sections import SectionPlan

    checkpoint = {"queue": [bounded.url], "visited": [], "pages_collected": 0}
    plan = SectionPlan(source, {"max_threads": 1, "max_index_pages": 1, "max_thread_pages": 1}, checkpoint)
    plan.advance(bounded)
    assert checkpoint["section"]["index_rows_omitted"] == 4


@respx.mock
async def test_mcp_pinned_filter_retains_full_index_evidence_and_resumes_unpinned_selection(settings, monkeypatch):
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/Forum-Buyer-Requests").respond(text=listing_document())
    respx.get(PINNED + "/Thread-normal").respond(
        text="""<article id="post_42">
    <div class="pagnum"><a href="Thread-normal?pid=42">#1</a></div>
    <div class="post_body">Original request</div></article>"""
    )
    async with Client(create_server(settings)) as client:
        listed = (
            await client.call_tool(
                "list_forum_threads",
                {
                    "source_id": "forum:leakforum",
                    "exclude_pinned": True,
                },
            )
        ).structured_content
        assert listed["excluded_pinned_matches"] == 1 and listed["unknown_pin_status_matches"] == 1
        assert [r["title"] for r in listed["results"]] == ["normal", "unknown"]
        assert len(listed["page"]["threads"]) == 3  # Filtering does not discard source evidence.
        health = (await client.call_tool("get_source_health", {"source_id": "forum:leakforum"})).structured_content
        assert health["results"][0]["last_attempt"]["thread_listing_scope"] == "recognized_thread_rows"
        assert health["results"][0]["last_attempt"]["thread_listings"] == 3
        job = (
            await client.call_tool(
                "start_collection",
                {
                    "source_id": "forum:leakforum",
                    "mode": "section",
                    "exclude_pinned": True,
                    "max_threads": 1,
                    "max_index_pages": 1,
                    "max_thread_pages": 1,
                },
            )
        ).structured_content["collection"]
        result = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        state = result["collection"]
        assert state["options"]["exclude_pinned"]
        assert state["checkpoint"]["queue"] == [ORIGIN + "/Thread-normal"]
        assert state["checkpoint"]["section"]["deferred_urls"][0]["reason"] == "pinned_thread_excluded"
        invalid = (
            await client.call_tool(
                "start_collection",
                {
                    "source_id": "forum:leakforum",
                    "mode": "history",
                    "exclude_pinned": True,
                },
            )
        ).structured_content
        assert invalid["error"]["code"] == "invalid_arguments"
    async with Client(create_server(settings)) as client:
        finished = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert finished["collection"]["status"] == "complete"
        assert finished["results"][0]["page"]["posts"][0]["text"] == "Original request"

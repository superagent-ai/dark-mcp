from unittest.mock import AsyncMock

import pytest
import respx
from bs4 import BeautifulSoup
from mcp import Client

from dark_mcp.forums import ForumConnector, last_thread_page
from dark_mcp.models import ForumPage, ForumPost
from dark_mcp.sections import SectionPlan
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

XF = "https://hard-tm.com/threads/example.42/"
MYBB = "https://bf.st/Thread-example"


def xf_nav(maximum=8, linked=8):
    return f'''<ul class="pageNav-main"><li><a href="{XF}page-2">2</a></li>
    <li class="pageNav-page--skipEnd"><input class="js-pageJumpPage" max="{maximum}"/></li>
    <li><a href="{XF}page-{linked}">{linked}</a></li></ul>'''


def test_declared_last_page_requires_an_actual_same_thread_read_link():
    source = SourceCatalog().get("forum:hard-tm")
    result = last_thread_page(source, XF, BeautifulSoup(xf_nav(), "html.parser"))
    assert result["last_page_url"] == XF + "page-8" and result["last_page_number"] == 8
    assert result["last_page_basis"] == "xenforo_declared_page_maximum_matching_link"
    missing = last_thread_page(source, XF, BeautifulSoup(xf_nav(linked=3), "html.parser"))
    assert missing["last_page_url"] is None and missing["last_page_number"] == 8
    assert missing["last_page_basis"] == "declared_total_without_read_link"
    undeclared = last_thread_page(
        source, XF, BeautifulSoup(xf_nav().replace('class="js-pageJumpPage"', ""), "html.parser")
    )
    assert undeclared["last_page_url"] is None and undeclared["last_page_number"] is None


def test_mybb_declared_total_and_explicit_last_markers_keep_source_urls():
    source = SourceCatalog().get("forum:bf-st")
    nav = f'<div class="pagination"><span class="pages">Pages (20):</span><a href="{MYBB}?page=20">20</a></div>'
    captured = last_thread_page(source, MYBB, BeautifulSoup(nav, "html.parser"))
    assert captured["last_page_url"] == MYBB + "?page=20" and captured["last_page_number"] == 20
    explicit = '<div class="pagination"><a class="pagination_last" href="?page=30">Last</a></div>'
    captured = last_thread_page(source, MYBB, BeautifulSoup(explicit, "html.parser"))
    assert captured["last_page_number"] == 30 and captured["last_page_basis"] == "explicit_last_page_link"
    numbered = (
        '<div class="pagination"><span class="pages">Pages (2):</span>'
        '<a href="showthread.php?tid=9&amp;page=2">2</a></div>'
    )
    captured = last_thread_page(source, "https://bf.st/showthread.php?tid=9", BeautifulSoup(numbered, "html.parser"))
    assert captured["last_page_url"] == "https://bf.st/showthread.php?tid=9&page=2"


@pytest.mark.parametrize(
    "replacement",
    [
        "https://hard-tm.com/threads/other.99/page-8",
        "https://other.example/threads/example.42/page-8",
        XF + "page-8?pid=5",
        XF + "page-8?foo=bar",
        "https://user:secret@hard-tm.com/threads/example.42/page-8",
        "https://hard-tm.com/threads/example.42/reply",
        "https://hard-tm.com/attachments/proof.zip",
    ],
)
def test_other_thread_actions_and_ambiguous_links_do_not_become_last_page_targets(replacement):
    source = SourceCatalog().get("forum:hard-tm")
    nav = xf_nav().replace(XF + "page-8", replacement)
    result = last_thread_page(source, XF, BeautifulSoup(nav, "html.parser"))
    assert result["last_page_url"] is None


def test_conflicting_totals_quoted_pagination_and_previous_page_links_are_not_trusted():
    source = SourceCatalog().get("forum:hard-tm")
    conflicted = last_thread_page(source, XF, BeautifulSoup(xf_nav() + xf_nav(maximum=9, linked=9), "html.parser"))
    assert conflicted["last_page_conflict"] and conflicted["last_page_url"] is None
    quoted = '<article data-content="post-99"><div class="message-body">' + xf_nav() + "</div></article>"
    assert last_thread_page(source, XF, BeautifulSoup(quoted, "html.parser"))["last_page_url"] is None
    before_current = last_thread_page(source, XF + "page-9", BeautifulSoup(xf_nav(), "html.parser"))
    assert before_current["last_page_conflict"] and before_current["last_page_url"] is None
    on_last = last_thread_page(source, XF + "page-8", BeautifulSoup(xf_nav(), "html.parser"))
    assert on_last["last_page_url"] == XF + "page-8"


def post(pid, root=False):
    return ForumPost(id=str(pid), url=XF + f"post-{pid}", text="Observed post", is_thread_root=root)


def plan():
    source = SourceCatalog().get("forum:hard-tm")
    options = {"thread_page_order": "root_then_last", "max_threads": 1, "max_index_pages": 1, "max_thread_pages": 2}
    checkpoint = {"queue": [XF], "visited": []}
    planner = SectionPlan(source, options, checkpoint)
    planner.state["thread_owners"][XF] = XF
    return planner, checkpoint


def page(url=XF, **kwargs):
    return ForumPage(
        source_id="forum:hard-tm",
        url=url,
        title="Example",
        text="Observed post",
        page_type="thread",
        posts=[post(1, root=url == XF)],
        access_state="content_returned_unverified",
        coverage="Fixture",
        **kwargs,
    )


def test_plan_samples_root_and_last_preserves_gaps_and_stops_at_observed_boundary():
    planner, checkpoint = plan()
    planner.advance(
        page(
            last_page_url=XF + "page-8",
            last_page_number=8,
            last_page_basis="source_pagination",
            next_page_urls=[XF + "page-2"],
        )
    )
    assert checkpoint["queue"] == [XF + "page-8"]
    sample = planner.state["thread_samples"][XF]
    assert sample["root_observed"] and sample["uncollected_middle_pages"] == {"from": 2, "through": 7}
    assert planner.state["deferred_urls"][0]["reason"] == "middle_pages_not_sampled"
    planner.advance(page(XF + "page-8", next_page_urls=[XF + "page-9"], last_page_number=9))
    assert not checkpoint["queue"] and sample["last_page_collected"] and sample["later_pagination_observed"]
    assert sample["last_page_number_on_sampled_page"] == 9
    assert planner.state["deferred_urls"][-1]["reason"] == "pagination_after_sample_boundary"


def test_missing_last_link_failure_and_redirect_changes_are_explicit():
    planner, checkpoint = plan()
    planner.advance(page(next_page_urls=[XF + "page-2"]))
    assert not checkpoint["queue"]
    assert planner.state["thread_samples"][XF]["state"] == "last_page_not_explicit"
    planner, checkpoint = plan()
    planner.advance(page(last_page_url=XF + "page-8", last_page_number=8))
    planner.failure(XF + "page-8", {"code": "http_403"})
    assert planner.state["thread_samples"][XF]["state"] == "sample_page_failed"
    planner, checkpoint = plan()
    planner.advance(page(last_page_url=XF + "page-8", last_page_number=8))
    planner.advance(page())  # Last-page request redirected back to the root.
    sample = planner.state["thread_samples"][XF]
    assert sample["state"] == "sample_page_changed" and not sample["last_page_collected"]
    planner, checkpoint = plan()
    planner.advance(page(XF + "page-3", last_page_url=XF + "page-8", last_page_number=8))
    assert planner.state["thread_samples"][XF]["state"] == "root_page_changed" and not checkpoint["queue"]
    planner, checkpoint = plan()
    planner.advance(page("https://hard-tm.com/threads/different.99/"))
    assert planner.state["thread_samples"][XF]["state"] == "thread_identity_changed" and not checkpoint["queue"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_id": "forum:hard-tm", "mode": "history", "thread_page_order": "root_then_last"},
        {"source_id": "forum:hard-tm", "mode": "section", "thread_page_order": "newest_first"},
        {"source_id": "forum:hard-tm", "mode": "section", "thread_page_order": "root_then_last", "max_thread_pages": 1},
        {"source_id": "onion:a9bcde64e044", "mode": "section", "thread_page_order": "root_then_last"},
    ],
)
async def test_invalid_sampling_options_fail_before_source_access(settings, monkeypatch, kwargs):
    fetch = AsyncMock(side_effect=AssertionError("Invalid options must not fetch"))
    monkeypatch.setattr(ForumConnector, "fetch", fetch)
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("start_collection", kwargs)).structured_content
        assert result["error"]["code"] == "invalid_arguments"
    fetch.assert_not_awaited()


@respx.mock
async def test_protocol_root_then_last_job_resumes_without_reading_middle_pages(settings, monkeypatch):
    addresses = AsyncMock(return_value=["8.8.8.8"])
    monkeypatch.setattr("dark_mcp.forums.public_addresses", addresses)
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())
    respx.get("https://8.8.8.8/robots.txt").respond(404)
    respx.get("https://8.8.8.8/forums/sell_all/").respond(text=f'<a href="{XF}">Access claim</a>')
    root_html = (
        '<article class="message--post" data-content="post-1"><a class="message-attribution-gadget" href="'
        + XF
        + 'post-1">#1</a><div class="message-body"><div class="bbWrapper">Original offer</div></div></article>'
    )
    respx.get("https://8.8.8.8/threads/example.42/").respond(text=root_html + xf_nav())
    last_html = (
        root_html.replace("post-1", "post-80").replace("#1", "#80").replace("Original offer", "Recent closing reply")
    )
    respx.get("https://8.8.8.8/threads/example.42/page-8").respond(text=last_html + xf_nav())
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool(
                "start_collection",
                {
                    "source_id": "forum:hard-tm",
                    "mode": "section",
                    "url": "https://hard-tm.com/forums/sell_all/",
                    "max_threads": 1,
                    "max_index_pages": 1,
                    "max_thread_pages": 2,
                    "thread_page_order": "root_then_last",
                },
            )
        ).structured_content["collection"]
        first = (
            await client.call_tool("collect_next", {"collection_id": job["id"], "max_pages": 2})
        ).structured_content
        assert first["collection"]["checkpoint"]["queue"] == [XF + "page-8"]
    async with Client(create_server(settings)) as client:
        last = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert last["collection"]["status"] == "complete"
        assert last["results"][0]["page"]["posts"][0]["text"] == "Recent closing reply"
        assert last["collection"]["checkpoint"]["section"]["thread_samples"][XF]["last_page_collected"]
        hits = (await client.call_tool("search_evidence", {"query": "Recent closing reply"})).structured_content
        assert hits["results"]
    assert not any("page-2" in str(call.request.url) for call in respx.calls)

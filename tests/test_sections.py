from unittest.mock import AsyncMock

import pytest
import respx
from mcp import Client

from dark_mcp.forums import ForumConnector
from dark_mcp.server import create_server

ORIGIN = "https://hard-tm.com"
PINNED = "https://8.8.8.8"
INDEX = ORIGIN + "/forums/sell_all/"


def post(pid, text, position=0):
    return f'''<title>Fixture Access Offer</title><article class="message--post" data-content="post-{pid}"
      data-position="{position}" data-author="fixture-seller"><div class="message-attribution-main">
      <a href="/threads/access-one.1/post-{pid}"><time datetime="2026-10-19T08:00:00Z">Today</time></a></div>
      <div class="message-body"><div class="bbWrapper">{text}</div></div></article>'''


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())


@respx.mock
async def test_section_enters_original_threads_after_restart_and_retains_gaps_and_bounds(settings, network):
    respx.get(PINNED + "/robots.txt").respond(404)
    index = respx.get(PINNED + "/forums/sell_all/").respond(
        text="""<title>Access listings</title>
      <a href="/threads/access-one.1/post-11">Access one</a>
      <a href="/threads/access-gated.2/">Access gated</a>
      <a href="/threads/unrelated.3/">Unrelated</a>
      <a href="/threads/access-deferred.4/">Access deferred</a>
      <a rel="next" href="/forums/sell_all/page-2">Next</a>"""
    )
    original = respx.get(PINNED + "/threads/access-one.1/").respond(
        text=post(11, "Acme.example alleged access") + '<a rel="next" href="/threads/access-one.1/page-2">Next</a>'
    )
    gated = respx.get(PINNED + "/threads/access-gated.2/").respond(
        text="<title>Login</title>Log in to view this thread"
    )
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool(
                "start_collection",
                {
                    "source_id": "forum:hard-tm",
                    "mode": "section",
                    "url": INDEX,
                    "query": "Access",
                    "max_threads": 2,
                    "max_index_pages": 1,
                    "max_thread_pages": 1,
                },
            )
        ).structured_content["collection"]
        first = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert first["collection"]["status"] == "ready" and index.call_count == 1
        assert first["collection"]["checkpoint"]["queue"] == [
            ORIGIN + "/threads/access-one.1/",
            ORIGIN + "/threads/access-gated.2/",
        ]
    async with Client(create_server(settings)) as client:
        result = (
            await client.call_tool("collect_next", {"collection_id": job["id"], "max_pages": 5})
        ).structured_content
        assert result["ok"] and result["collection"]["status"] == "complete"
        section = result["collection"]["checkpoint"]["section"]
        assert (
            len(section["threads_selected"]) == 2
            and section["failed_pages"][0]["error"]["code"] == "login_gate_suspected"
        )
        assert {row["reason"] for row in section["deferred_urls"]} == {
            "thread_selection_bound",
            "index_page_bound",
            "thread_page_bound",
        }
        hits = (
            await client.call_tool("search_evidence", {"query": "Acme.example", "kind": "forum"})
        ).structured_content
        assert any("post-11" in h["url"] for h in hits["results"])
    assert original.call_count == gated.call_count == 1
    assert all(
        "unrelated" not in str(c.request.url)
        and "deferred" not in str(c.request.url)
        and "page-2" not in str(c.request.url)
        for c in respx.calls
    )


@respx.mock
async def test_section_thread_reply_pagination_collects_both_roots_and_replies(settings, network):
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/forums/sell_all/").respond(text='<a href="/threads/access-one.1/">Access</a>')
    respx.get(PINNED + "/threads/access-one.1/").respond(
        text=post(11, "Root claim") + '<a rel="next" href="/threads/access-one.1/page-2">Next</a>'
    )
    respx.get(PINNED + "/threads/access-one.1/page-2").respond(text=post(12, "Reply context", position=1))
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool(
                "start_collection",
                {
                    "source_id": "forum:hard-tm",
                    "mode": "section",
                    "url": INDEX,
                    "max_threads": 1,
                    "max_index_pages": 1,
                    "max_thread_pages": 2,
                },
            )
        ).structured_content["collection"]
        result = (
            await client.call_tool("collect_next", {"collection_id": job["id"], "max_pages": 5})
        ).structured_content
        assert result["collection"]["status"] == "complete"
        posts = [p for r in result["results"] for p in r["page"]["posts"]]
        assert [p["id"] for p in posts] == ["11", "12"]
        assert [p["is_thread_root"] for p in posts] == [True, False]
        assert result["collection"]["checkpoint"]["section"]["failed_pages"] == []


@respx.mock
async def test_section_rate_limit_keeps_current_url_and_persisted_cooldown_prevents_more_requests(settings, network):
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/forums/sell_all/").respond(429, headers={"Retry-After": "3600"})
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool("start_collection", {"source_id": "forum:hard-tm", "mode": "section", "url": INDEX})
        ).structured_content["collection"]
        first = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert first["collection"]["status"] == "waiting"
        assert first["collection"]["checkpoint"]["queue"] == [INDEX]
        assert first["collection"]["checkpoint"]["section"]["failed_pages"] == []
    count = len(respx.calls)
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert result["error"]["code"] == "collection_rate_limited" and len(respx.calls) == count


@respx.mock
async def test_section_records_unavailable_page_and_continues_other_original_threads(settings, network):
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/forums/sell_all/").respond(
        text="""<a href="/threads/missing.1/">Access missing</a>
        <a href="/threads/access-one.1/">Access readable</a>"""
    )
    respx.get(PINNED + "/threads/missing.1/").respond(404)
    respx.get(PINNED + "/threads/access-one.1/").respond(text=post(11, "Readable claim"))
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool("start_collection", {"source_id": "forum:hard-tm", "mode": "section", "url": INDEX})
        ).structured_content["collection"]
        result = (
            await client.call_tool("collect_next", {"collection_id": job["id"], "max_pages": 5})
        ).structured_content
        assert result["collection"]["status"] == "complete"
        assert result["collection"]["checkpoint"]["section"]["failed_pages"][0]["error"]["code"] == "http_404"
        assert result["results"][-1]["page"]["posts"][0]["text"] == "Readable claim"


@pytest.mark.parametrize(
    "arguments",
    [
        {"source_id": "telegram:breachdetect", "mode": "section"},
        {"source_id": "forum:hard-tm", "mode": "section", "url": ORIGIN + "/threads/1/"},
        {"source_id": "forum:hard-tm", "mode": "section", "since": "2026-10-01T00:00:00Z"},
        {"source_id": "forum:hard-tm", "mode": "history", "max_threads": 5},
    ],
)
async def test_section_scope_and_bounds_are_explicit(settings, arguments):
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("start_collection", arguments)).structured_content
        assert result["ok"] is False


@respx.mock
async def test_unavailable_section_index_retains_cursor_for_retry(settings, network):
    respx.get(PINNED + "/robots.txt").respond(404)
    route = respx.get(PINNED + "/forums/sell_all/").respond(503)
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool("start_collection", {"source_id": "forum:hard-tm", "mode": "section", "url": INDEX})
        ).structured_content["collection"]
        failed = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert failed["ok"] is False and failed["collection"]["status"] == "blocked"
        assert failed["collection"]["checkpoint"]["queue"] == [INDEX]
        assert failed["collection"]["checkpoint"]["pages_collected"] == 0
        route.respond(text='<a href="/threads/access-one.1/">Access</a>')
        retried = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert retried["ok"] and retried["collection"]["status"] == "ready"


@respx.mock
async def test_thread_transient_failure_is_not_consumed_as_permanent_gap(settings, network):
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/forums/sell_all/").respond(text='<a href="/threads/access-one.1/">Access</a>')
    route = respx.get(PINNED + "/threads/access-one.1/").respond(503)
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool("start_collection", {"source_id": "forum:hard-tm", "mode": "section", "url": INDEX})
        ).structured_content["collection"]
        await client.call_tool("collect_next", {"collection_id": job["id"]})
        failed = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert failed["error"]["code"] == "http_503"
        assert failed["collection"]["checkpoint"]["queue"] == [ORIGIN + "/threads/access-one.1/"]
        assert failed["collection"]["checkpoint"]["section"]["failed_pages"] == []
        route.respond(text=post(11, "Recovered claim"))
        restored = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert restored["collection"]["status"] == "complete"


@respx.mock
async def test_section_redirect_retains_selected_thread_page_bound(settings, network):
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/forums/sell_all/").respond(text='<a href="/threads/1/">Access</a>')
    respx.get(PINNED + "/threads/1/").respond(302, headers={"Location": "/threads/access-one.1/"})
    respx.get(PINNED + "/threads/access-one.1/").respond(
        text=post(11, "Root claim") + '<a rel="next" href="/threads/access-one.1/page-2">Next</a>'
    )
    respx.get(PINNED + "/threads/access-one.1/page-2").respond(
        text=post(12, "Reply", position=1) + '<a rel="next" href="/threads/access-one.1/page-3">Next</a>'
    )
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool(
                "start_collection",
                {
                    "source_id": "forum:hard-tm",
                    "mode": "section",
                    "url": INDEX,
                    "max_threads": 1,
                    "max_index_pages": 1,
                    "max_thread_pages": 2,
                },
            )
        ).structured_content["collection"]
        result = (
            await client.call_tool(
                "collect_next",
                {
                    "collection_id": job["id"],
                    "max_pages": 5,
                },
            )
        ).structured_content
        section = result["collection"]["checkpoint"]["section"]
        assert section["thread_pages"] == {ORIGIN + "/threads/1/": 2}
        assert section["deferred_urls"] == [
            {
                "url": ORIGIN + "/threads/access-one.1/page-3",
                "reason": "thread_page_bound",
            }
        ]
        assert result["collection"]["status"] == "complete"
    assert all("page-3" not in str(c.request.url) for c in respx.calls)

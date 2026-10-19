from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest
import respx
from mcp import Client

from dark_mcp.coverage import CorpusCoverage
from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.forum_search import NO_RESULTS, ForumSearch, matched_post_url, parse_results
from dark_mcp.forum_search_urls import initial_search_url, search_url
from dark_mcp.forums import ForumConnector, source_url
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

SOURCE = "onion:91e4e785a3af"
SID = "a" * 32


def result_url(source, page=1):
    return source["native_search"]["endpoint"] + "?" + urlencode(dict(action="results", sid=SID, page=page))


def row(source, pid, body="Acme.example search excerpt", raw_date="10-19-2026, 01:00 PM"):
    return f'''<tr class="inline_row"><td>status</td><td></td><td>
    <span class="smalltext">Thread: <a href="Thread-Acme?highlight=Acme">Acme request</a><br>
    Post: <a href="Thread-Acme?pid={pid}?highlight=Acme#pid{pid}">Short title</a></span><br>
    <table><tr><td><span><em>{body}</em></span></td></tr></table></td>
    <td><a href="{source["locator"]}User-Test">Test</a></td><td>Requests</td>
    <td>5</td><td>5000</td><td>{raw_date}</td></tr>'''


def document(source, *ids, page=1, next_page=None, body="Acme.example search excerpt"):
    pagination = (
        (
            f'<div class="pagination"><span class="pagination_current">{page}</span>'
            f'<a href="{result_url(source, next_page)}">Next</a></div>'
        )
        if next_page
        else ""
    )
    return (
        "<html><title>Search</title><table>"
        + "".join(row(source, p, body) for p in ids)
        + "</table>"
        + pagination
        + "</html>"
    )


def setup(settings):
    catalog = SourceCatalog()
    source = catalog.get(SOURCE)
    forums = ForumConnector(settings, catalog)
    forums.fetch_document = AsyncMock(return_value=(result_url(source), document(source, 1, 2, 3), {"state": "public"}))
    store = EvidenceStore(settings.prepare_data_dir())
    return source, forums, store, ForumSearch(settings, forums, store)


def test_exact_native_routes_bind_redirect_sid_and_preserve_normal_action_guard():
    source = SourceCatalog().get(SOURCE)
    context = {"query": "Acme & cloud"}
    initial = initial_search_url(source, context["query"])
    assert "Acme+%26+cloud" in initial
    assert search_url(source, result_url(source), context)
    assert context["sid"] == SID
    for value in (
        initial,
        result_url(source).replace(SID, "b" * 32),
        result_url(source) + "&action=delete",
        result_url(source).replace("search.php", "member.php"),
        result_url(source) + "&order=asc",
        result_url(source) + "#fragment",
        result_url(source).replace(source["allowed_hosts"][0], "evil.onion"),
    ):
        with pytest.raises(DataAccessError):
            search_url(source, value, context)
    with pytest.raises(DataAccessError):
        source_url(source, initial)
    with pytest.raises(DataAccessError):
        initial_search_url(SourceCatalog().get("forum:niflheim"), "Acme")


def test_excerpts_identity_dates_and_pid_normalization_are_explicit():
    source = SourceCatalog().get(SOURCE)
    result = parse_results(source, result_url(source), document(source, 7), {"query": "Acme", "sid": SID}, 1000)
    item = result["results"][0]
    assert item["url"] == source["locator"] + "showthread.php?pid=7"
    assert item["matched_post_id"] == "7" and item["text"] == "Acme.example search excerpt"
    assert item["published_at"] is None and item["published_at_raw"] == "10-19-2026, 01:00 PM"
    assert item["reported_author"] == "Test"
    assert item["reported_author_identity"]["identity_verified"] is False
    assert item["content_scope"] == "source_search_excerpt"
    for bad in (
        "https://evil.example/Thread-X?pid=7#pid7",
        "Thread-X?pid=7#pid8",
        "member.php?pid=7#pid7",
        "Thread-X?pid=7&action=delete#pid7",
    ):
        with pytest.raises((DataAccessError, ValueError)):
            matched_post_url(source, source["locator"], bad)


@pytest.mark.parametrize(
    "markup,code",
    [
        ("<title>Just a moment...</title><p>Verify humanity</p>", "challenge_suspected"),
        ("<title>Login</title><p>Please log in</p>", "login_gate_suspected"),
        (
            '<table><td class="trow1">An invalid search was specified. Please go back and try again.</td></table>',
            "forum_search_expired",
        ),
        ("<p>Unknown template with no matches visible</p>", "forum_search_layout_unrecognized"),
    ],
)
def test_gates_and_expired_cache_are_not_negative_results(markup, code):
    source = SourceCatalog().get(SOURCE)
    with pytest.raises(DataAccessError) as error:
        parse_results(source, result_url(source), markup, {"query": "Acme", "sid": SID}, 1000)
    assert error.value.code == code


def test_negative_pagination_and_foreign_cache_binding():
    source = SourceCatalog().get(SOURCE)
    negative = '<table><tr><td class="trow1">' + NO_RESULTS + "</td></tr></table>"
    assert parse_results(source, result_url(source), negative, {"query": "Acme", "sid": SID}, 1000)["negative_result"]
    page = document(source, 1, page=1, next_page=2)
    parsed = parse_results(source, result_url(source), page, {"query": "Acme", "sid": SID}, 1000)
    assert parsed["next_url"] == result_url(source, 2) and not parsed["source_result_window_exhausted"]
    foreign = page.replace(SID, "b" * 32)
    bad = parse_results(source, result_url(source), foreign, {"query": "Acme", "sid": SID}, 1000)
    assert bad["next_url"] is None and bad["unrecognized_pagination_links"] == 1
    assert not bad["source_result_window_exhausted"]
    with pytest.raises(DataAccessError):
        parse_results(source, result_url(source, 2), page, {"query": "Acme", "sid": SID}, 1000)


def test_native_mybb_pagination_uid_zero_and_page_jump_toggle():
    source = SourceCatalog().get(SOURCE)
    html = document(source, 1, next_page=2)
    html = html.replace("&page=2", "&uid=0&page=2")
    html += '<div class="pagination"><a href="javascript:void(0)">Jump</a></div>'
    result = parse_results(source, result_url(source), html, {"query": "Acme", "sid": SID}, 1000)
    assert result["next_url"] == result_url(source, 2).replace("&page=2", "&uid=0&page=2")
    assert result["unrecognized_pagination_links"] == 0
    with pytest.raises(DataAccessError):
        search_url(source, result_url(source, 2) + "&uid=123", {"query": "Acme", "sid": SID})


async def test_pending_rows_survive_restart_keep_exact_versions_and_do_not_use_network(settings):
    source, forums, store, reader = setup(settings)
    first = await reader.search(SOURCE, "Acme", limit=1)
    assert len(first["results"]) == 1 and first["next_cursor"]
    assert first["cached_results_remaining"] == 2 and not first["source_result_window_exhausted"]
    with store.connect() as db:
        remaining = db.execute("SELECT id FROM items WHERE url LIKE '%pid=2'").fetchone()[0]
    old = store.get(remaining)
    store.observe(
        SOURCE, "forum_search", old["stable_key"], old["url"], {**old["snapshot"], "text": "Later different excerpt"}
    )
    store.cooldown("onion:" + SOURCE, 120)
    second = await ForumSearch(settings, forums, EvidenceStore(settings.data_dir)).search(
        SOURCE, "Acme", limit=2, cursor=first["next_cursor"]
    )
    assert [r["matched_post_id"] for r in second["results"]] == ["2", "3"]
    assert second["results"][0]["text"] == "Acme.example search excerpt"
    assert second["acquisition"]["network_read"] is False
    assert second["next_cursor"] is None and second["source_result_window_exhausted"]
    assert forums.fetch_document.call_count == 1
    assert len(store.search("Acme", kind="forum_search")["results"]) == 3
    assert store.search("Acme", kind="forum")["results"] == []
    assert CorpusCoverage(store).get(kind="forum_search")["summary"]["indexed_forum_posts"] == 0
    with pytest.raises(DataAccessError):
        await reader.search(SOURCE, "Other", cursor=first["next_cursor"])


async def test_remote_next_page_and_repeated_page_are_explicit(settings):
    source, forums, store, reader = setup(settings)
    forums.fetch_document.return_value = (result_url(source), document(source, 1, 2, next_page=2), {})
    first = await reader.search(SOURCE, "Acme", limit=2)
    forums.fetch_document.return_value = (result_url(source, 2), document(source, 3, page=2), {})
    second = await reader.search(SOURCE, "Acme", limit=2, cursor=first["next_cursor"])
    assert [r["matched_post_id"] for r in second["results"]] == ["3"]
    assert second["acquisition"]["network_read"] is True and second["next_cursor"] is None
    forums.fetch_document.return_value = (result_url(source, 2), document(source, 1, 2, page=2), {})
    with pytest.raises(DataAccessError) as error:
        await reader.search(SOURCE, "Acme", cursor=first["next_cursor"])
    assert error.value.code == "forum_search_repeated_page"
    with pytest.raises(DataAccessError) as error:
        await reader.search(SOURCE, "Another query")
    assert error.value.code == "collection_rate_limited"


async def test_native_html_flood_wait_is_saved_for_search_and_original_reads(settings):
    source, forums, store, reader = setup(settings)
    forums.fetch_document.return_value = (
        result_url(source),
        '<table><td class="trow1">Sorry, but you can only perform one search every 60 seconds. '
        "Please wait another 47 seconds before attempting to search again.</td></table>",
        {},
    )
    with pytest.raises(DataAccessError) as error:
        await reader.search(SOURCE, "Acme")
    assert error.value.code == "forum_search_rate_limited" and error.value.retry_after == 47
    with pytest.raises(DataAccessError):
        store.cooldown("onion:" + SOURCE)
    with pytest.raises(DataAccessError):
        await reader.search(SOURCE, "Acme")
    assert forums.fetch_document.call_count == 1


async def test_mcp_search_index_replay_and_refresh_do_not_call_telegram(settings, monkeypatch):
    source = SourceCatalog().get(SOURCE)
    fake = AsyncMock(return_value=(result_url(source), document(source, 8, 9), {}))
    monkeypatch.setattr(ForumConnector, "fetch_document", fake)
    async with Client(create_server(settings)) as client:
        result = (
            await client.call_tool("search_forum_posts", {"source_id": SOURCE, "query": "Acme", "limit": 1})
        ).structured_content
        assert result["ok"] and result["next_cursor"]
        eid = result["results"][0]["evidence"]["evidence_id"]
        refreshed = (await client.call_tool("refresh_evidence", {"evidence_id": eid})).structured_content
        assert refreshed["error"]["code"] == "forum_search_repeat_required"
        searched = (
            await client.call_tool("search_evidence", {"query": "Acme", "kind": "forum_search"})
        ).structured_content
        assert searched["ok"] and len(searched["results"]) == 2
    async with Client(create_server(settings)) as client:
        replay = (
            await client.call_tool(
                "search_forum_posts", {"source_id": SOURCE, "query": "Acme", "cursor": result["next_cursor"]}
            )
        ).structured_content
        assert replay["ok"] and replay["acquisition"]["network_read"] is False
        assert replay["results"][0]["matched_post_id"] == "9"
    assert fake.call_count == 1


@respx.mock
async def test_https_search_transport_pins_host_obeys_robots_and_binds_redirect(settings, monkeypatch):
    catalog = SourceCatalog()
    source = catalog.sources["forum:bf-st"]
    source["native_search"] = {"engine": "mybb_get", "endpoint": "https://bf.st/search.php"}
    forums = ForumConnector(settings, catalog)
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(forums, "_pace", AsyncMock())
    robots = respx.get("https://8.8.8.8/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /search.php")
    )
    route = respx.get(url__startswith="https://8.8.8.8/search.php").mock(
        return_value=httpx.Response(200, text="unexpected")
    )
    target = initial_search_url(source, "Acme")
    with pytest.raises(DataAccessError) as error:
        await forums.fetch_document(source["id"], target, search={"query": "Acme"})
    assert error.value.code == "robots_denied" and route.call_count == 0
    robots.mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /"))
    forums.robots_cache.clear()
    route.mock(
        side_effect=[
            httpx.Response(302, headers={"location": result_url(source)}),
            httpx.Response(200, text=document(source, 1)),
        ]
    )
    final, html, _ = await forums.fetch_document(source["id"], target, search={"query": "Acme"})
    assert final == result_url(source) and "Acme" in html
    assert all(call.request.headers["host"] == "bf.st" for call in route.calls)


async def test_negative_result_retains_query_document_hash_and_access_in_attempt(settings):
    import hashlib
    import json

    source, forums, store, reader = setup(settings)
    html = '<table><td class="trow1">' + NO_RESULTS + "</td></table>"
    forums.fetch_document.return_value = (initial_search_url(source, "Acme"), html, {"state": "public"})
    result = await reader.search(SOURCE, "Acme")
    assert result["negative_result"] and result["results"] == [] and result["next_cursor"] is None
    assert result["acquisition"]["document_text_sha256"] == hashlib.sha256(html.encode()).hexdigest()
    with store.connect() as db:
        attempt = db.execute("SELECT * FROM attempts").fetchone()
    details = json.loads(attempt["detail"])
    assert details["query"] == "Acme" and details["method"] == "source_native_forum_search"
    assert details["acquisition"]["access_context"]["state"] == "public"
    assert details["negative_result"] is True

from unittest.mock import AsyncMock

import httpx
import pytest
from mcp import Client

from dark_mcp.errors import DataAccessError
from dark_mcp.forums import parse_forum, source_url
from dark_mcp.onion import OnionConnector
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

SOURCE = "onion:a9bcde64e044"
HOST = "http://exiliow4ctlzrvaglkgwqnpxdlvrxmdgvuy2hkbzqoziebfim6q5hwid.onion"
THREAD = HOST + "/42/request"


def question(text="Acme.example account request"):
    return f"""<title>Fixture request</title><div class="qa-q-view" id="q42">
      <div class="qa-q-view-content qa-post-content"><div itemprop="text">{text}</div></div>
      <a class="qa-user-link">question-author</a>
      <time itemprop="dateCreated" datetime="2026-10-01T12:00:00+0000">1 Out</time></div>"""


def answer():
    return """<div class="qa-a-list-item" id="a43">
      <div class="qa-a-item-content qa-post-content"><div itemprop="text">Reply clarification
      <a href="https://external.example/attachment.zip">Reference only</a></div></div>
      <a class="qa-user-link">answer-author</a>
      <time itemprop="dateCreated" datetime="2026-10-02T12:00:00+0000">2 Out</time>
      <span class="qa-a-item-what">selecionada</span><span class="qa-a-item-when">
      <time itemprop="dateModified" datetime="2026-10-04T12:00:00+0000">4 Out</time></span>
      <div class="qa-c-list-item" id="c44"><div class="qa-c-item-content">Comment on answer</div>
      <a class="qa-user-link">comment-author</a>
      <time itemprop="dateCreated" datetime="2026-10-03T12:00:00+0000">3 Out</time></div></div>"""


@pytest.mark.parametrize(
    "path",
    [
        "/login",
        "/register",
        "/ask",
        "/admin",
        "/user/example",
        "/marketplace",
        "/qa-plugin/file.bin",
        "/42/request?qa=delete",
        "/42/request?show=43&show=44",
        "/42/request?show=abc",
        "/hacking?show=43",
        "/42/request/edit",
        "/42/../admin",
        "/42/request%2Fdelete",
    ],
)
def test_q2a_read_routes_never_enable_actions_or_arbitrary_paths(path):
    with pytest.raises(DataAccessError):
        source_url(SourceCatalog().get(SOURCE), HOST + path)


def test_q2a_routes_are_scoped_to_source_and_preserve_only_numeric_read_queries():
    source = SourceCatalog().get(SOURCE)
    assert source_url(source, THREAD + "?show=43#answer") == THREAD + "?show=43"
    assert source_url(source, HOST + "/questions/hacking?start=20").endswith("?start=20")
    with pytest.raises(DataAccessError):
        source_url(SourceCatalog().get("onion:91e4e785a3af"), THREAD)
    spear = SourceCatalog().get("onion:91e4e785a3af")
    with pytest.raises(DataAccessError):
        source_url(spear, spear["locator"] + "42/request")
    with pytest.raises(DataAccessError):
        source_url(SourceCatalog().get("forum:hard-tm"), "https://hard-tm.com/42/request")


def test_q2a_retains_authorship_thread_hierarchy_dates_and_selection_without_inventing_edit():
    source = SourceCatalog().get(SOURCE)
    page = parse_forum(source, THREAD, question() + answer(), 60000, 100)
    assert page.thread_key == "q2a:42" and page.access_state == "content_returned_unverified"
    assert [p.id for p in page.posts] == ["42", "43", "44"]
    assert [p.author for p in page.posts] == ["question-author", "answer-author", "comment-author"]
    assert [p.post_type for p in page.posts] == ["question", "answer", "comment"]
    assert [p.reply_to_post_id for p in page.posts] == [None, "42", "43"]
    assert page.posts[0].is_thread_root is True and page.posts[0].post_number == 1
    assert page.posts[1].post_number is None
    assert page.posts[1].published_at == "2026-10-02T12:00:00+00:00"
    assert page.posts[1].edited_at is None
    assert page.posts[1].source_events == [
        {"label": "selecionada", "at": "2026-10-04T12:00:00+00:00", "raw": "2026-10-04T12:00:00+0000"}
    ]
    assert "Comment on answer" not in page.posts[1].text
    assert page.posts[1].links[0]["url"] == "https://external.example/attachment.zip"


def test_q2a_dates_do_not_borrow_from_nested_comments_and_unresolved_dates_stay_unknown():
    html = question().replace('<time itemprop="dateCreated" datetime="2026-10-01T12:00:00+0000">1 Out</time>', "")
    html = (
        html[:-6] + '<div class="qa-c-list-item" id="c44"><div class="qa-c-item-content">Comment</div>'
        '<time itemprop="dateCreated" datetime="2026-10-03T12:00:00+0000">3 Out</time></div></div>'
    )
    page = parse_forum(SourceCatalog().get(SOURCE), THREAD, html, 60000, 100)
    assert page.posts[0].published_at is None and page.published_at is None
    assert page.posts[1].published_at == "2026-10-03T12:00:00+00:00"
    undated = parse_forum(
        SourceCatalog().get(SOURCE), THREAD, question().replace('datetime="2026-10-01T12:00:00+0000"', ""), 60000, 100
    )
    assert undated.posts[0].published_at is None
    assert undated.posts[0].published_at_raw == "1 Out"


def test_q2a_index_title_filter_is_local_and_pagination_follows_only_explicit_forward_links():
    html = """<title>Public questions</title>
      <div class="qa-q-item-title"><a href="./42/request?show=43">Acme account request</a></div>
      <a class="qa-page-next" href="?start=20">próxima »</a>
      <a rel="next" href="/questions/vazamento-de-dados?start=20">Other category</a>
      <a href="/login">Login</a><a href="https://other.example">Offsite</a>"""
    page = parse_forum(SourceCatalog().get(SOURCE), HOST + "/hacking", html, 60000, 100)
    assert page.threads == [{"url": THREAD + "?show=43", "title": "Acme account request"}]
    assert page.next_page_urls == [HOST + "/hacking?start=20"]
    reply = parse_forum(
        SourceCatalog().get(SOURCE),
        THREAD,
        question() + '<a class="qa-page-next" href="?start=10">Next</a>',
        60000,
        100,
    )
    assert reply.next_page_urls == [THREAD + "?start=10"]


def test_q2a_bounds_hidden_content_and_scripts_are_explicit():
    page = parse_forum(
        SourceCatalog().get(SOURCE), THREAD, question("conteúdo oculto<script>execute me</script>") + answer(), 60000, 1
    )
    assert page.posts[0].hidden_content and "execute me" not in page.posts[0].text
    assert page.omitted_posts == 2 and page.truncated


async def test_real_mcp_q2a_section_resume_context_and_refresh_never_fetch_attachments(settings, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET" and request.url.host == httpx.URL(HOST).host
        if request.url.path == "/robots.txt":
            return httpx.Response(302, headers={"Location": "/"})
        if request.url.path == "/":
            return httpx.Response(200, text="<title>Forum homepage</title>")
        if request.url.path == "/hacking":
            return httpx.Response(
                200, text='<div class="qa-q-item-title"><a href="./42/request?show=43">Acme request</a></div>'
            )
        assert request.url.path == "/42/request"
        return httpx.Response(200, text=question() + answer())

    monkeypatch.setattr(
        OnionConnector, "client", lambda self: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(OnionConnector, "_pace", AsyncMock())
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool(
                "start_collection",
                {
                    "source_id": SOURCE,
                    "mode": "section",
                    "url": HOST + "/hacking",
                    "max_threads": 1,
                },
            )
        ).structured_content["collection"]
        first = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert first["collection"]["checkpoint"]["queue"] == [THREAD]
    async with Client(create_server(settings)) as client:
        result = (
            await client.call_tool("collect_next", {"collection_id": job["id"], "max_items": 100})
        ).structured_content
        assert result["collection"]["status"] == "complete"
        seed = result["results"][0]["page"]["posts"][1]["evidence"]["evidence_id"]
        context = (await client.call_tool("get_collected_thread", {"evidence_id": seed})).structured_content
        assert context["root_observed"] and context["total_stored_posts"] == 3
        assert context["results"][2]["reply_to_post_id"] == "43"
        refreshed = (await client.call_tool("refresh_evidence", {"evidence_id": seed})).structured_content
        assert refreshed["ok"]
    assert all("attachment" not in str(r.url) for r in requests)


def test_other_question_widget_is_not_misattributed_and_index_clipping_is_explicit():
    unrelated = question("Wrong question widget").replace('id="q42"', 'id="q99"')
    page = parse_forum(SourceCatalog().get(SOURCE), THREAD, question() + unrelated, 60000, 100)
    assert len(page.posts) == 1 and "Wrong question" not in page.text
    index = '<div class="qa-q-item-title"><a href="./42/request">Fixture request</a></div>' + "X" * 500
    clipped = parse_forum(SourceCatalog().get(SOURCE), HOST + "/hacking", index, 100, 100)
    assert clipped.truncated and len(clipped.text) == 100

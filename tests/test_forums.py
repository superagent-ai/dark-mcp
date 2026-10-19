import asyncio
import socket
from unittest.mock import AsyncMock

import pytest
import respx
from mcp import Client

from dark_mcp.errors import DataAccessError
from dark_mcp.forums import ForumConnector, forum_url, parse_forum, public_addresses
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

HOST = "https://8.8.8.8"
THREAD = "https://hard-tm.com/threads/185218/"


def xf_post(pid, text, author="seller", hidden=False):
    placeholder = '<div class="bbCodeBlock--hide">You must register to view this content.</div>' if hidden else ""
    return f'''<article class="message--post" data-content="post-{pid}" data-author="{author}">
      <div class="message-attribution-main"><a href="/threads/185218/post-{pid}">
        <time datetime="2026-10-18T10:00:00+0300">Yesterday</time></a></div>
      <article class="message-body"><div class="bbWrapper">{text}{placeholder}
      <script>throw Error('never execute')</script><div class="signature">Unrelated signature</div></div></article>
      </article>'''


def page(body, next_url=None):
    return (
        "<title>Acme company listing</title>"
        + body
        + (f'<a class="pageNav-jump--next" href="{next_url}">Next</a>' if next_url else "")
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://hard-tm.com/threads/1/",
        "https://127.0.0.1/threads/1/",
        "https://hard-tm.com.evil.example/threads/1/",
        "https://user:secret@hard-tm.com/threads/1/",
        "https://hard-tm.com:444/threads/1/",
        "https://hard-tm.com/threads/%2e%2e/admin.php",
        "https://hard-tm.com/threads/../admin.php",
        "https://hard-tm.com/threads/%252e%252e/admin.php",
        "https://hard-tm.com/threads/a/reply",
        "https://hard-tm.com/threads/a/vote",
        "https://hard-tm.com/threads/a/custom-action",
        "https://hard-tm.com/search/",
        "https://hard-tm.com/attachments/1.zip",
        "https://hard-tm.com/showthread.php?action=delete&tid=1",
        "https://hard-tm.com/threads/1/?page=1&page=2",
        "https://hard-tm.com/threads/1/?page=1%0d%0aX-Test:1",
    ],
)
def test_forum_urls_restrict_hosts_actions_credentials_and_ambiguous_queries(url):
    with pytest.raises(DataAccessError):
        forum_url(url, ["hard-tm.com"])


async def test_dns_mixed_public_private_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ]
        ),
    )
    with pytest.raises(DataAccessError, match="public internet"):
        await public_addresses("hard-tm.com")


def test_parser_preserves_authors_dates_quotes_hidden_markers_and_thread_scope():
    source = SourceCatalog().get("forum:hard-tm")
    html = page(
        xf_post(1, 'Acme exposure <blockquote data-source="post:9">Quoted allegation</blockquote>', hidden=True)
        + xf_post(2, "A public reply", "reader"),
        "/threads/185218/page-2",
    )
    result = parse_forum(source, THREAD, html, 60000, 20)
    assert len(result.posts) == 2
    assert result.posts[0].author == "seller"
    assert result.posts[0].published_at == "2026-10-18T07:00:00+00:00"
    assert result.posts[0].hidden_content
    assert result.posts[0].quotes[0]["post_id"] == "9"
    assert "never execute" not in result.text and "Unrelated signature" not in result.text
    assert result.posts[0].url == "https://hard-tm.com/threads/185218/post-1"
    assert result.next_page_urls == ["https://hard-tm.com/threads/185218/page-2"]
    assert result.published_at is None
    bounded = parse_forum(source, THREAD, html, 60000, 1)
    assert bounded.omitted_posts == 1 and bounded.truncated
    changed_page = parse_forum(source, THREAD + "page-2", html, 60000, 20)
    assert changed_page.posts[0].url == result.posts[0].url


def test_index_pagination_does_not_follow_different_threads_and_handles_mybb_rewrites():
    source = SourceCatalog().get("forum:cracked")
    html = """<title>Other Leaks</title><a href="Thread-Acme-claim">Acme claim</a>
      <div class="pagination"><a class="pagination_current" href="forumdisplay.php?fid=314">1</a>
      <a class="pagination_next" href="forumdisplay.php?fid=314&amp;page=2">Next</a></div>"""
    result = parse_forum(source, source["locator"], html, 60000, 20)
    assert result.threads[0]["title"] == "Acme claim"
    assert result.next_page_urls == ["https://cracked.st/forumdisplay.php?fid=314&page=2"]
    # On a thread, that same breadcrumb pagination must not collect the forum index as replies.
    result = parse_forum(source, "https://cracked.st/Thread-Acme-claim", html, 60000, 20)
    assert result.next_page_urls == [] and result.access_state == "thread_body_unavailable"


def test_mybb_post_dates_use_epoch_metadata_without_executing_scripts():
    source = SourceCatalog().get("forum:cracked")
    html = """<title>Acme listing</title><div class="post-set" id="post_42">
       <div class="post-username"><a>seller</a></div>
       <span class="post_date"><span title="Yesterday">1 day ago</span></span>
       <script>quick_quote(42,'seller',1792310400);</script><div class="post_body">Acme account offer
       <div class="hidden-content">You must login to view this content.</div></div></div>"""
    result = parse_forum(source, "https://cracked.st/Thread-Acme-listing", html, 60000, 20)
    assert result.posts[0].published_at and result.posts[0].published_at_raw == "Yesterday"
    assert result.posts[0].hidden_content and result.posts[0].author == "seller"
    assert result.posts[0].url.endswith("?pid=42")


def test_visible_advertisement_with_registration_only_links_is_partial():
    source = SourceCatalog().get("forum:hard-tm")
    result = parse_forum(
        source, THREAD, page(xf_post(1, "Public product details. You must be registered for see links")), 60000, 20
    )
    assert result.posts[0].hidden_content
    assert "Public product details" in result.posts[0].text
    assert result.access_state == "content_returned_unverified"


def test_niflheim_login_placeholder_is_hidden_content_even_with_public_replies():
    source = SourceCatalog().get("forum:niflheim")
    html = page(
        xf_post(1, 'To view the content, you need to <a href="/login/">Sign In</a> or Register.')
        + xf_post(2, "very helpful info", "reader")
    )
    result = parse_forum(source, "https://niflheim.world/threads/example.1/", html, 60000, 20)
    assert result.posts[0].hidden_content
    assert not result.posts[1].hidden_content
    assert len(result.posts) == 2


@pytest.fixture
def public_network(monkeypatch):
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())


@respx.mock
async def test_https_is_pinned_to_checked_ip_with_original_host_and_sni(settings, public_network):
    respx.get(HOST + "/robots.txt").respond(text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"})
    route = respx.get(HOST + "/threads/185218/").respond(text=page(xf_post(1, "Acme listing")))
    result = await ForumConnector(settings, SourceCatalog()).fetch("forum:hard-tm", THREAD)
    assert len(result.posts) == 1
    request = route.calls.last.request
    assert request.headers["host"] == "hard-tm.com"
    assert request.extensions["sni_hostname"] == "hard-tm.com"
    assert "authorization" not in request.headers and "cookie" not in request.headers


@respx.mock
async def test_robots_restriction_and_cross_host_redirect_do_not_reach_target(settings, public_network):
    robots = respx.get(HOST + "/robots.txt").respond(text="User-agent: *\nDisallow: /threads/")
    connector = ForumConnector(settings, SourceCatalog())
    with pytest.raises(DataAccessError, match="robots.txt"):
        await connector.fetch("forum:hard-tm", THREAD)
    assert len(respx.calls) == 1
    robots.respond(text="User-agent: *\nAllow: /")
    respx.get(HOST + "/threads/185218/").respond(302, headers={"location": "https://127.0.0.1/threads/1/"})
    connector = ForumConnector(settings, SourceCatalog())
    with pytest.raises(DataAccessError, match="catalog hosts"):
        await connector.fetch("forum:hard-tm", THREAD)
    assert len(respx.calls) == 3


@respx.mock
async def test_binary_and_oversized_forum_responses_are_not_collected(settings, public_network):
    respx.get(HOST + "/robots.txt").respond(text="User-agent: *\nAllow: /")
    route = respx.get(HOST + "/threads/185218/").respond(content=b"binary", headers={"content-type": "application/zip"})
    with pytest.raises(DataAccessError, match="HTML/text"):
        await ForumConnector(settings, SourceCatalog()).fetch("forum:hard-tm", THREAD)
    settings.max_response_bytes = 1024
    route.respond(text="x" * 2000)
    with pytest.raises(DataAccessError, match="response bound"):
        await ForumConnector(settings, SourceCatalog()).fetch("forum:hard-tm", THREAD)


@respx.mock
async def test_real_mcp_collection_resume_search_and_refresh_preserve_post_evidence(settings, public_network):
    respx.get(HOST + "/robots.txt").respond(text="User-agent: *\nAllow: /")
    first = page(xf_post(1, "Acme.example access claim") + xf_post(2, "First public reply"), "/threads/185218/page-2")
    respx.get(HOST + "/threads/185218/").respond(text=first)
    respx.get(HOST + "/threads/185218/page-2").respond(text=page(xf_post(3, "Second public reply")))
    post_route = respx.get(HOST + "/threads/185218/post-1").respond(text=first)
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool("start_collection", {"source_id": "forum:hard-tm", "url": THREAD})
        ).structured_content["collection"]
        # A too-small post bound retains the page instead of silently dropping its second post.
        bounded = (
            await client.call_tool("collect_next", {"collection_id": job["id"], "max_items": 1})
        ).structured_content
        assert bounded["error"]["code"] == "forum_page_incomplete"
        assert bounded["collection"]["checkpoint"]["pages_collected"] == 0
        result = (
            await client.call_tool("collect_next", {"collection_id": job["id"], "max_items": 20})
        ).structured_content
        assert result["collection"]["status"] == "ready"
        hits = (
            await client.call_tool("search_evidence", {"query": "Acme.example", "kind": "forum"})
        ).structured_content["results"]
        post_id = next(h["evidence_id"] for h in hits if "/post-1" in h["url"])
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert result["collection"]["status"] == "complete"
        assert result["results"][0]["page"]["posts"][0]["id"] == "3"
        refresh = (await client.call_tool("refresh_evidence", {"evidence_id": post_id})).structured_content
        assert refresh["ok"]
        history = (await client.call_tool("get_evidence_history", {"evidence_id": post_id})).structured_content
        assert len(history["results"]) == 1
        post_route.respond(text=page(xf_post(2, "Only a reply is visible now")))
        missing = (await client.call_tool("refresh_evidence", {"evidence_id": post_id})).structured_content
        assert missing["availability_observation"]["availability"] == "unavailable"
        assert missing["availability_observation"]["deletion_confirmed"] is False
        saved = (await client.call_tool("get_evidence", {"evidence_id": post_id})).structured_content["evidence"]
        assert saved["snapshot"]["text"] == "Acme.example access claim"
        post_route.respond(text=first)
        restored = (await client.call_tool("refresh_evidence", {"evidence_id": post_id})).structured_content
        assert restored["page"]["posts"][0]["evidence"]["change_type"] == "restored"

from unittest.mock import AsyncMock

import httpx
import pytest

from dark_mcp.errors import DataAccessError
from dark_mcp.index import OnionIndex
from dark_mcp.onion import OnionConnector, onion_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "https://example.com/",
        "http://example.onion/",
        "http://" + "a" * 56 + ".onion/",
        "http://localhost.onion/",
    ],
)
def test_refuses_non_onion_or_invalid_addresses(url):
    with pytest.raises(DataAccessError, match="v3 onion"):
        onion_url(url)


def test_refuses_credentials_ports_and_controls(onion):
    for url in [onion.replace("://", "://user:password@"), onion[:-1] + ":8080/", onion + "\n"]:
        with pytest.raises(DataAccessError):
            onion_url(url)
    assert onion_url(onion + "page#fragment") == onion + "page"


def connector_for(settings, monkeypatch, handler):
    connector = OnionConnector(settings, OnionIndex(settings.prepare_data_dir()))
    monkeypatch.setattr(
        connector,
        "client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        ),
    )
    monkeypatch.setattr(connector, "_pace", AsyncMock())
    return connector


async def test_fetch_extracts_text_and_updates_search(settings, monkeypatch, onion):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            text=(
                "<title>Example company</title><script>secret script</script>"
                '<p>Acme incident report</p><a href="/evidence">evidence</a>'
                '<a href="http://127.0.0.1/">blocked link</a>'
            ),
            headers={"content-type": "text/html"},
        )

    connector = connector_for(settings, monkeypatch, handler)
    page = await connector.fetch(onion)
    assert "secret script" not in page.text
    assert page.links == [onion + "evidence"]
    assert connector.index.search("Acme incident")["results"][0]["url"] == onion


async def test_refuses_clearnet_redirect_before_request(settings, monkeypatch, onion):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    connector = connector_for(settings, monkeypatch, handler)
    with pytest.raises(DataAccessError) as exc:
        await connector.fetch(onion)
    assert exc.value.code == "invalid_onion_url"
    assert all(url.startswith(onion) for url in seen)


async def test_redirect_checks_destination_robots(settings, monkeypatch, onion, other_onion):
    def handler(request):
        if str(request.url) == onion + "robots.txt":
            return httpx.Response(404)
        if str(request.url) == other_onion + "robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /", headers={"content-type": "text/plain"})
        if str(request.url) == onion:
            return httpx.Response(302, headers={"Location": other_onion})
        pytest.fail("Disallowed redirect destination was fetched")

    connector = connector_for(settings, monkeypatch, handler)
    with pytest.raises(DataAccessError) as exc:
        await connector.fetch(onion)
    assert exc.value.code == "robots_denied"


async def test_response_limit_prevents_indexing(settings, monkeypatch, onion):
    settings.max_response_bytes = 1024

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="x" * 1025, headers={"content-type": "text/plain"})

    connector = connector_for(settings, monkeypatch, handler)
    with pytest.raises(DataAccessError) as exc:
        await connector.fetch(onion)
    assert exc.value.code == "response_too_large"
    assert connector.index.count() == 0


async def test_rejects_unexpected_compression(settings, monkeypatch, onion):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"")

    connector = connector_for(settings, monkeypatch, handler)
    with pytest.raises(DataAccessError) as exc:
        await connector.fetch(onion)
    assert exc.value.code == "unsupported_encoding"
    assert connector.index.count() == 0


async def test_crawl_bounds_same_host_and_page_count(settings, monkeypatch, onion, other_onion):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.url.host == httpx.URL(onion).host
        return httpx.Response(
            200,
            text=f'<a href="/next">next</a><a href="{other_onion}">other</a>',
            headers={"content-type": "text/html"},
        )

    connector = connector_for(settings, monkeypatch, handler)
    result = await connector.crawl([onion], max_pages=2)
    assert result["attempted"] == 2
    assert len(result["indexed"]) == 2


def test_client_requires_proxy_and_ignores_environment(settings, monkeypatch):
    options = {}

    def factory(**kwargs):
        options.update(kwargs)
        return object()

    monkeypatch.setattr("dark_mcp.onion.httpx.AsyncClient", factory)
    OnionConnector(settings, OnionIndex(settings.prepare_data_dir())).client()
    assert options["proxy"].startswith("socks5h://")
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False


async def test_explicit_pagination_and_publication_metadata_are_not_arbitrary_links(settings, monkeypatch, onion):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            text=(
                '<title>Thread</title><meta property="article:published_time" content="2026-10-18T09:00:00+02:00">'
                '<time datetime="2026-10-19T00:00:00Z">Reply date</time>'
                '<a rel="next" href="?page=2">2</a><a href="/members">Members</a>'
                '<a href="/thread?page=3">Next page »</a><p>Acme discussion</p>'
            ),
            headers={"content-type": "text/html"},
        )

    page = await connector_for(settings, monkeypatch, handler).fetch(onion)
    assert page.next_page_urls == [onion + "?page=2", onion + "thread?page=3"]
    assert page.published_at == "2026-10-18T07:00:00+00:00"


@pytest.mark.parametrize(
    ("title", "text"),
    [
        ("DarkForest", "Stay logged in for 1 hour Register I forgot my password"),
        ("Проверка браузера…", "Это займёт всего мгновение."),
    ],
)
async def test_known_gate_shapes_are_not_content_success(settings, monkeypatch, onion, title, text):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=f"<title>{title}</title><p>{text}</p>", headers={"content-type": "text/html"})

    page = await connector_for(settings, monkeypatch, handler).fetch(onion)
    assert page.access_state in {"login_gate_suspected", "challenge_suspected"}

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from mcp import Client
from typer.testing import CliRunner

from dark_mcp.cli import app
from dark_mcp.errors import DataAccessError
from dark_mcp.forum_sessions import ForumSessions
from dark_mcp.forums import ForumConnector
from dark_mcp.onion import OnionConnector
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

SOURCE = "forum:hard-tm"
ORIGIN = "https://hard-tm.com"
THREAD = ORIGIN + "/threads/42/"
PINNED = "https://8.8.8.8"
SECRET = "fixture-session-value-not-real"


def cookies_file(tmp_path, rows=None):
    p = tmp_path / "operator-cookies.json"
    p.write_text(
        json.dumps(
            rows
            if rows is not None
            else [{"name": "account_session", "value": SECRET, "domain": "hard-tm.com", "path": "/", "secure": True}]
        )
    )
    return p


def content(text):
    return f"""<title>Fixture access claim</title><article class="message--post" data-content="post-42"
      data-author="fixture-seller" data-position="0"><div class="message-attribution-main">
      <a href="/threads/42/post-42"><time datetime="2026-10-19T08:00:00Z">Today</time></a></div>
      <div class="message-body"><div class="bbWrapper">{text}</div></div></article>"""


def test_exact_host_secure_path_scope_metadata_and_private_storage(settings, tmp_path):
    vault = ForumSessions(settings, SourceCatalog())
    file = cookies_file(
        tmp_path,
        [{"name": "private_session", "value": SECRET, "domain": ".hard-tm.com", "path": "/threads", "secure": True}],
    )
    meta = vault.import_file(SOURCE, file)
    assert meta["state"] == "configured" and meta["authentication_verified"] is False
    assert SECRET not in json.dumps(meta) and "private_session" not in json.dumps(meta)
    assert vault.path.stat().st_mode & 0o777 == 0o600
    assert vault.path.parent.stat().st_mode & 0o777 == 0o700
    assert vault.headers(SOURCE, THREAD) == {"Cookie": "private_session=" + SECRET}
    assert vault.headers(SOURCE, ORIGIN + "/threads-extra") == {}
    assert vault.headers(SOURCE, ORIGIN + "/robots.txt") == {}
    assert vault.headers(SOURCE, "https://hard-tm.su/threads/42/") == {}
    with pytest.raises(DataAccessError):
        vault.headers(SOURCE, "https://attacker.example/threads/42/")


@pytest.mark.parametrize(
    "row",
    [
        {"name": "sid", "value": SECRET, "domain": "other.example"},
        {"name": "bad name", "value": SECRET},
        {"name": "sid", "value": "injection\r\nHeader: value"},
        {"name": "sid", "value": "one; second=two"},
        {"name": "sid", "value": SECRET, "path": "relative"},
        {"name": "sid", "value": SECRET, "expires": float("inf")},
    ],
)
def test_bad_or_cross_host_cookie_exports_never_get_saved(settings, tmp_path, row):
    vault = ForumSessions(settings, SourceCatalog())
    with pytest.raises(DataAccessError):
        vault.import_file(SOURCE, cookies_file(tmp_path, [row]))
    assert not vault.path.exists()


@pytest.mark.parametrize("expiry", [0, 1])
def test_expired_cookie_export_is_not_a_session_cookie(settings, tmp_path, expiry):
    with pytest.raises(DataAccessError, match="No usable"):
        ForumSessions(settings, SourceCatalog()).import_file(
            SOURCE, cookies_file(tmp_path, [{"name": "sid", "value": SECRET, "expires": expiry}])
        )


def test_session_expiry_rotation_origin_restrictions_and_removal_survive_restart(settings, tmp_path):
    vault = ForumSessions(settings, SourceCatalog())
    initial = vault.import_file(SOURCE, cookies_file(tmp_path), ttl_hours=1)
    vault.update(
        SOURCE,
        THREAD,
        ["account_session=rotated-fixture; Path=/; Secure", "attacker=bad; Domain=other.example; Path=/"],
    )
    other = ForumSessions(settings, SourceCatalog())
    assert other.headers(SOURCE, THREAD) == {"Cookie": "account_session=rotated-fixture"}
    assert other.status(SOURCE)["expires_at"] == initial["expires_at"]
    with vault.connect() as db:
        db.execute("UPDATE sessions SET expires_at=?", ((datetime.now(UTC) - timedelta(seconds=5)).isoformat(),))
    assert other.status(SOURCE)["state"] == "expired"
    with pytest.raises(DataAccessError, match="expired"):
        other.headers(SOURCE, THREAD)
    assert other.remove(SOURCE)["state"] == "public" and other.headers(SOURCE, THREAD) == {}


def test_cli_provisions_and_removes_session_without_echoing_secrets(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("dark_mcp.cli.Settings", lambda: settings)
    runner = CliRunner()
    imported = runner.invoke(app, ["forum-session-import", SOURCE, str(cookies_file(tmp_path))])
    assert imported.exit_code == 0 and SECRET not in imported.output
    assert json.loads(imported.output)["state"] == "configured"
    status = runner.invoke(app, ["forum-session-status", SOURCE])
    assert status.exit_code == 0 and SECRET not in status.output
    removed = runner.invoke(app, ["forum-session-remove", SOURCE])
    assert removed.exit_code == 0 and json.loads(removed.output)["state"] == "public"


@respx.mock
async def test_real_mcp_authenticated_read_uses_pinned_host_and_keeps_secrets_out_of_evidence(
    settings, tmp_path, monkeypatch
):
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/threads/42/").mock(
        side_effect=lambda request: httpx.Response(
            200,
            text=content("Acme.example original claim")
            if request.headers.get("cookie")
            else content("To view the content, you need to Sign In or Register."),
        )
    )
    vault = ForumSessions(settings, SourceCatalog())
    async with Client(create_server(settings)) as client:
        public = (await client.call_tool("read_forum_thread", {"source_id": SOURCE, "url": THREAD})).structured_content
        assert public["page"]["posts"][0]["hidden_content"]
        vault.import_file(SOURCE, cookies_file(tmp_path))
        authenticated = (
            await client.call_tool("read_forum_thread", {"source_id": SOURCE, "url": THREAD})
        ).structured_content
        page = authenticated["page"]
        assert page["access_context"]["credentials_attached"] is True
        assert page["posts"][0]["text"] == "Acme.example original claim"
        assert not page["posts"][0]["hidden_content"]
        saved = (
            await client.call_tool("get_evidence", {"evidence_id": page["posts"][0]["evidence"]["evidence_id"]})
        ).structured_content
        assert SECRET not in json.dumps(saved)
        assert saved["evidence"]["snapshot"]["access_context"]["authentication_verified"] is False
        status = (await client.call_tool("get_forum_access_status", {"source_id": SOURCE})).structured_content
        assert SECRET not in json.dumps(status)
    requests = [call.request for call in respx.calls if call.request.url.path != "/robots.txt"]
    assert requests[-1].url.host == "8.8.8.8" and requests[-1].headers["host"] == "hard-tm.com"
    assert requests[-1].headers["cookie"] == "account_session=" + SECRET
    assert all("cookie" not in call.request.headers for call in respx.calls if call.request.url.path == "/robots.txt")


@respx.mock
async def test_session_expiry_fails_before_network_and_signin_redirect_is_not_followed(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())
    vault = ForumSessions(settings, SourceCatalog())
    vault.import_file(SOURCE, cookies_file(tmp_path))
    with vault.connect() as db:
        db.execute("UPDATE sessions SET expires_at=?", ((datetime.now(UTC) - timedelta(seconds=5)).isoformat(),))
    with pytest.raises(DataAccessError, match="expired"):
        await ForumConnector(settings, SourceCatalog()).fetch(SOURCE, THREAD)
    assert not respx.calls
    vault.import_file(SOURCE, cookies_file(tmp_path))
    respx.get(PINNED + "/robots.txt").respond(404)
    respx.get(PINNED + "/threads/42/").respond(302, headers={"Location": "/login/"})
    with pytest.raises(DataAccessError) as error:
        await ForumConnector(settings, SourceCatalog()).fetch(SOURCE, THREAD)
    assert error.value.code == "forum_auth_required"
    assert len(respx.calls) == 2


async def test_tor_session_is_sent_only_to_catalog_origin_not_robots_or_referenced_market(
    settings, tmp_path, monkeypatch, other_onion
):
    source = SourceCatalog().get("onion:91e4e785a3af")
    file = cookies_file(
        tmp_path, [{"name": "sid", "value": SECRET, "domain": source["allowed_hosts"][0], "secure": False}]
    )
    ForumSessions(settings, SourceCatalog()).import_file(source["id"], file)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.headers.get("cookie") == "sid=" + SECRET
        return httpx.Response(
            200,
            text='<article id="post_42"><div class="post_body">'
            f'Claim <a href="{other_onion}">Market</a></div></article>',
        )

    monkeypatch.setattr(
        OnionConnector, "client", lambda self: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(OnionConnector, "_pace", AsyncMock())
    async with Client(create_server(settings)) as client:
        result = (
            await client.call_tool(
                "read_forum_thread", {"source_id": source["id"], "url": source["locator"] + "Thread-fixture"}
            )
        ).structured_content
        assert result["ok"] and result["page"]["access_context"]["credentials_attached"]
        assert SECRET not in json.dumps(result)
    assert all(str(request.url).startswith(source["locator"]) for request in requests)
    assert all("cookie" not in request.headers for request in requests if request.url.path == "/robots.txt")


@respx.mock
async def test_catalog_mirror_redirect_does_not_leak_cookie_through_shared_pinned_ip(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())
    ForumSessions(settings, SourceCatalog()).import_file(SOURCE, cookies_file(tmp_path))
    respx.get(PINNED + "/robots.txt").respond(404, headers={"Set-Cookie": "robots_cookie=not-for-content; Path=/"})

    def handler(request):
        if request.headers["host"] == "hard-tm.com":
            assert request.headers.get("cookie") == "account_session=" + SECRET
            return httpx.Response(
                302,
                headers={
                    "Location": "https://hard-tm.su/threads/42/",
                    "Set-Cookie": "rotated=fixture; Path=/; Secure",
                },
            )
        assert request.headers["host"] == "hard-tm.su" and "cookie" not in request.headers
        return httpx.Response(200, text=content("Mirror visible body"))

    respx.get(PINNED + "/threads/42/").mock(side_effect=handler)
    result = await ForumConnector(settings, SourceCatalog()).fetch(SOURCE, THREAD)
    assert result.access_context["state"] == "public"
    assert result.access_context["credentials_attached"] is False
    assert result.url == "https://hard-tm.su/threads/42/"


def test_local_removal_clears_cookie_bytes_from_session_database(settings, tmp_path):
    vault = ForumSessions(settings, SourceCatalog())
    vault.import_file(SOURCE, cookies_file(tmp_path))
    assert SECRET.encode() in vault.path.read_bytes()
    vault.remove(SOURCE)
    assert SECRET.encode() not in vault.path.read_bytes()


async def test_tor_response_rotation_is_not_sent_to_robots_on_redirect(settings, tmp_path, monkeypatch):
    source = SourceCatalog().get("onion:91e4e785a3af")
    file = cookies_file(tmp_path, [{"name": "sid", "value": SECRET, "domain": source["allowed_hosts"][0]}])
    ForumSessions(settings, SourceCatalog()).import_file(source["id"], file)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/robots.txt":
            assert "cookie" not in request.headers
            return httpx.Response(404)
        if request.url.path == "/Thread-old":
            return httpx.Response(302, headers={"Location": "/Thread-fixture", "Set-Cookie": "sid=rotated; Path=/"})
        assert request.headers.get("cookie") == "sid=rotated"
        return httpx.Response(200, text='<article id="post_42"><div class="post_body">Claim</div></article>')

    monkeypatch.setattr(
        OnionConnector, "client", lambda self: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(OnionConnector, "_pace", AsyncMock())
    async with Client(create_server(settings)) as client:
        result = (
            await client.call_tool(
                "read_forum_thread",
                {
                    "source_id": source["id"],
                    "url": source["locator"] + "Thread-old",
                },
            )
        ).structured_content
        assert result["ok"] and result["page"]["posts"][0]["text"] == "Claim"
    assert sum(request.url.path == "/robots.txt" for request in requests) == 2


@pytest.mark.parametrize("first_tool", ["fetch_onion", "read_forum_thread"])
async def test_tor_rate_limit_is_persisted_and_shared_between_generic_and_structured_reads(
    settings,
    monkeypatch,
    first_tool,
):
    source = SourceCatalog().get("onion:91e4e785a3af")
    requests = []

    def handler(request):
        requests.append(request)
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(429, headers={"Retry-After": "3600"})
        )

    monkeypatch.setattr(
        OnionConnector, "client", lambda self: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(OnionConnector, "_pace", AsyncMock())
    url = source["locator"] + "Thread-fixture"
    arguments = {"url": url}
    if first_tool == "read_forum_thread":
        arguments["source_id"] = source["id"]
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool(first_tool, arguments)).structured_content
        assert result["error"]["code"] == "onion_rate_limited"
    count = len(requests)
    next_tool = "read_forum_thread" if first_tool == "fetch_onion" else "fetch_onion"
    arguments = {"url": url}
    if next_tool == "read_forum_thread":
        arguments["source_id"] = source["id"]
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool(next_tool, arguments)).structured_content
        assert result["error"]["code"] == "collection_rate_limited" and len(requests) == count

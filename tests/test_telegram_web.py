import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from mcp import Client

from dark_mcp.coverage import CorpusCoverage
from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.open_telegram import import_directory
from dark_mcp.references import ReferenceSearch
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog
from dark_mcp.telegram import TelegramConnector
from dark_mcp.telegram_web import TelegramWeb, parse_preview

HANDLE = "fixture_channel"
ROOT = f"https://t.me/s/{HANDLE}"
ROBOTS = "https://t.me/robots.txt"


def widget(pid, text="Synthetic body", *, quote="", date="2026-10-19T08:00:00+00:00", footer_id=None):
    return f'''<div class="tgme_widget_message" data-post="{HANDLE}/{pid}"><div class="tgme_widget_message_bubble">
    {quote}<div class="tgme_widget_message_text js-message_text">{text}</div>
    <a class="tgme_widget_message_date" href="https://t.me/{HANDLE}/{footer_id or pid}">
    <time datetime="{date}">08:00</time></a></div></div>'''


def page(*widgets, before=1):
    return (
        '<meta property="og:title" content="Fixture">'
        + "".join(widgets)
        + (f'<a class="tme_messages_more" href="/s/{HANDLE}?before={before}">Older</a>' if before else "")
    )


@pytest.fixture
def web(settings, monkeypatch):
    settings.telegram_web_enabled = True
    settings.telegram_channels = [HANDLE]
    monkeypatch.setattr(TelegramWeb, "_pace", AsyncMock())
    return TelegramWeb(settings, SourceCatalog(settings), EvidenceStore(settings.prepare_data_dir()))


def html_response(document):
    return httpx.Response(200, text=document, headers={"Content-Type": "text/html"})


def test_preview_separates_quotes_keeps_inline_company_text_and_link_destinations():
    quote = """<a class="tgme_widget_message_reply" href="https://t.me/other_channel/1">
    <div class="tgme_widget_message_text js-message_reply_text">QuotedCorp.example allegation</div></a>"""
    document = page(
        widget(12, 'A<span>cme</span>.example<br>See <a href="https://company.example/path">short</a>', quote=quote)
    )
    result = parse_preview(document, HANDLE)
    post = result["results"][0]
    assert post["text"] == "Acme.example\nSee short"
    assert "QuotedCorp" not in post["text"] and "QuotedCorp" in post["quotes"][0]["text"]
    assert post["links"] == [{"url": "https://company.example/path", "label": "short"}]
    assert post["published_at"] == "2026-10-19T08:00:00+00:00"
    assert post["timestamp_basis"] == "matching_widget_footer_explicit_offset"


def test_local_limit_pagination_does_not_skip_parsed_posts():
    result = parse_preview(page(widget(7), widget(8), widget(9), before=7), HANDLE, limit=1)
    assert [p["message_id"] for p in result["results"]] == [9]
    assert result["next_before_id"] == 9 and result["omitted_posts"] == 2
    assert parse_preview(page(widget(7), widget(8), before=7), HANDLE)["next_before_id"] == 7
    assert parse_preview(page(widget(7), before=99), HANDLE)["next_before_id"] is None


def test_history_bound_excludes_newer_posts_and_stops_ignored_pagination():
    result = parse_preview(page(widget(7), widget(8), widget(9), before=7), HANDLE, before_id=9)
    assert [p["message_id"] for p in result["results"]] == [8, 7]
    assert result["out_of_range_posts"] == 1 and result["next_before_id"] == 7
    with pytest.raises(DataAccessError) as error:
        parse_preview(page(widget(8), widget(9), before=8), HANDLE, before_id=8)
    assert error.value.code == "telegram_web_pagination_ignored"


@respx.mock
async def test_web_only_allowlist_survives_server_restart_without_native_permission(web, monkeypatch):
    monkeypatch.setenv("DARK_MCP_TELEGRAM_WEB_CHANNELS", '["@Fixture_Channel"]')
    settings = type(web.settings)(
        _env_file=None,
        data_dir=web.settings.data_dir,
        telegram_web_enabled=True,
        telegram_channels=[],
        telegram_catalog_sources=False,
        telegram_web_directory_sources=False,
    )
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    route = respx.get(ROOT).mock(return_value=html_response(page(widget(5, "Acme.example"))))
    with pytest.raises(DataAccessError):
        TelegramConnector(settings, web.catalog)._allowed_channel(HANDLE)
    for _ in range(2):
        async with Client(create_server(settings)) as client:
            status = (await client.call_tool("get_status")).structured_content
            assert status["telegram_web_channels"] == ["@Fixture_Channel"]
            result = (
                await client.call_tool("read_telegram_public_preview", {"channel": HANDLE, "limit": 1})
            ).structured_content
            assert result["ok"] and result["results"][0]["text"] == "Acme.example"
            denied = (
                await client.call_tool("read_telegram_public_preview", {"channel": "unlisted_channel"})
            ).structured_content
            assert denied["error"]["code"] == "channel_not_allowed"
            private = (
                await client.call_tool("read_telegram_public_preview", {"channel": "-10012345"})
            ).structured_content
            assert private["error"]["code"] == "invalid_arguments"
    assert route.call_count == 2


def test_imported_directory_needs_web_only_opt_in_and_never_grants_native_access(web, tmp_path):
    file = tmp_path / "sources.md"
    file.write_text(
        "|Telegram|Status|Name|Type|\n|---|---|---|---|\n"
        "|https://t.me/other_channel|OFFLINE|Unverified lead|Access|\n"
        "|https://t.me/+12345678abcd|VALID|Invite|Unknown|\n"
    )
    import_directory(web.settings, file, "fixture-v1", "telegram_threat_actors")
    with pytest.raises(DataAccessError):
        web.allowed("other_channel")
    web.settings.telegram_web_directory_sources = True
    assert web.allowed("other_channel") == "other_channel"
    with pytest.raises(DataAccessError):
        web.allowed("unlisted_channel")
    with pytest.raises(ValueError):
        web.allowed("+12345678abcd")
    with pytest.raises(DataAccessError):
        TelegramConnector(web.settings, web.catalog)._allowed_channel("other_channel")


async def test_directory_tool_reports_configured_web_permission_without_claiming_access(web, tmp_path):
    file = tmp_path / "sources.md"
    file.write_text(
        "|Telegram|Status|Name|Type|\n|---|---|---|---|\n|https://t.me/other_channel|OFFLINE|Research lead|Access|\n"
    )
    import_directory(web.settings, file, "fixture-v1", "telegram_threat_actors")
    web.settings.telegram_web_directory_sources = True
    async with Client(create_server(web.settings)) as client:
        result = (await client.call_tool("list_open_telegram_sources", {"dataset": "deepdarkcti"})).structured_content
        lead = result["results"][0]
        assert lead["metadata"]["collection_enabled"] is False  # Import alone grants no access.
        assert lead["public_web_permission"] == {"permitted_by_configuration": True, "access_verified": False}


def test_foreign_widget_footer_mismatches_and_unresolved_dates_are_not_authenticated():
    foreign = widget(8, "Foreign claim").replace(f"{HANDLE}/8", "other_channel/8")
    result = parse_preview(page(foreign, widget(9, footer_id=10), widget(11, date="2026-10-19 08:00:00")), HANDLE)
    assert result["rejected_widgets"] == 2 and len(result["results"]) == 1
    assert result["results"][0]["published_at"] is None
    with pytest.raises(DataAccessError):
        parse_preview("<h1>View in Telegram / Login</h1>", HANDLE)
    with pytest.raises(DataAccessError):
        parse_preview(page(widget(3)), HANDLE, message_id=4)


@respx.mock
async def test_public_collection_replay_references_and_native_isolation(web):
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    route = respx.get(ROOT).mock(
        return_value=html_response(page(widget(5, 'Acme.example <a href="https://company.example/">short</a>')))
    )
    # A native account cooldown does not clear or change, and there is no implicit fallback call from that connector.
    web.store.cooldown("telegram", 3600)
    deadline = web.store.connector_status("telegram")["retry_at"]
    first = await web.read(HANDLE)
    item = first["results"][0]["evidence"]
    assert item["change_type"] == "new"
    route.mock(
        return_value=html_response(
            page(widget(5, 'Acme.example <a href="https://company.example/">short</a>')) + "<p>Views changed</p>"
        )
    )
    second = await web.read(HANDLE)
    assert second["results"][0]["evidence"]["revision_id"] == item["revision_id"]
    assert second["results"][0]["evidence"]["change_type"] == "unchanged"
    assert first["acquisition"]["document_sha256"] != second["acquisition"]["document_sha256"]
    replay = web.store.get(item["evidence_id"])
    assert replay["collected_at"] == first["collected_at"]
    assert replay["provenance_collected_at"] == second["collected_at"]
    assert replay["provenance"]["document_sha256"] == second["acquisition"]["document_sha256"]
    assert len(web.store.search("Acme.example", kind="telegram_web")["results"]) == 1
    assert web.store.search("Acme.example", kind="telegram")["results"] == []
    assert ReferenceSearch(web.store).search(host="company.example", kind="telegram_web")["results"]
    assert CorpusCoverage(web.store).get(kind="telegram_web")["summary"]["items"] == 1
    assert web.store.connector_status("telegram")["retry_at"] == deadline
    assert web.store.connector_status("telegram_web")["lease_active"] is False


@respx.mock
async def test_http_flood_wait_is_persisted_without_retries_or_deadline_extension(web):
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    route = respx.get(ROOT).mock(return_value=httpx.Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(DataAccessError) as error:
        await web.read(HANDLE)
    assert error.value.code == "telegram_web_rate_limited"
    deadline = web.store.connector_status("telegram_web")["retry_at"]
    for _ in range(2):
        with pytest.raises(DataAccessError) as blocked:
            await web.read(HANDLE)
        assert blocked.value.code == "collection_rate_limited" and blocked.value.retry_at == deadline
    assert route.call_count == 1
    assert web.store.connector_status("telegram_web")["lease_active"] is False


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_robots_denial_prevents_content_fetch(web, status):
    respx.get(ROBOTS).mock(return_value=httpx.Response(status))
    route = respx.get(ROOT).mock(return_value=html_response(page(widget(5))))
    with pytest.raises(DataAccessError) as error:
        await web.read(HANDLE)
    assert error.value.code == "robots_denied" and route.call_count == 0
    assert web.store.counts()["items"] == 0


@respx.mock
async def test_permitted_handles_only_no_private_invites_redirects_or_attachments(web):
    for target in ("other_channel", "https://evil.example/", "+12345678", "-100123456"):
        with pytest.raises((ValueError, DataAccessError)):
            await web.read(target)
    assert not respx.calls
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    respx.get(ROOT).mock(return_value=httpx.Response(302, headers={"Location": "https://evil.example/"}))
    with pytest.raises(DataAccessError):
        await web.read(HANDLE)
    assert len(respx.calls) == 2


@respx.mock
async def test_size_and_content_type_failures_store_no_body_claims(web):
    web.settings.max_response_bytes = 1024
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    route = respx.get(ROOT).mock(return_value=html_response(page(widget(5, "X" * 2000))))
    with pytest.raises(DataAccessError) as large:
        await web.read(HANDLE)
    assert large.value.code == "response_too_large"
    route.mock(
        return_value=httpx.Response(200, content=b"binary", headers={"Content-Type": "application/octet-stream"})
    )
    with pytest.raises(DataAccessError) as binary:
        await web.read(HANDLE)
    assert binary.value.code == "telegram_web_unsupported_content"
    assert web.store.counts()["items"] == 0 and not web.store.connector_status("telegram_web")["lease_active"]


@respx.mock
async def test_shared_lease_excludes_concurrent_read_and_releases_after_cancellation(web):
    entered = asyncio.Event()
    wait = asyncio.Event()
    started = []

    async def slow(request):
        started.append(request.url)
        entered.set()
        await wait.wait()
        return html_response(page(widget(1)))

    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    respx.get(ROOT).mock(side_effect=slow)
    task = asyncio.create_task(web.read(HANDLE))
    await entered.wait()
    other = TelegramWeb(web.settings, web.catalog, EvidenceStore(web.settings.data_dir))
    with pytest.raises(DataAccessError) as error:
        await other.read(HANDLE)
    assert error.value.code == "connector_busy" and len(started) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert web.store.connector_status("telegram_web")["lease_active"] is False


@respx.mock
async def test_real_mcp_public_read_direct_refresh_search_and_disabled_native(settings, monkeypatch):
    settings.telegram_web_enabled = True
    settings.telegram_channels = [HANDLE]
    monkeypatch.setattr(TelegramWeb, "_pace", AsyncMock())
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    respx.get(ROOT).mock(return_value=html_response(page(widget(12, "Acme.example public allegation"))))
    direct = respx.get(f"https://t.me/{HANDLE}/12?embed=1").mock(
        return_value=html_response(widget(12, "Changed allegation"))
    )
    async with Client(create_server(settings)) as client:
        read = (await client.call_tool("read_telegram_public_preview", {"channel": HANDLE})).structured_content
        assert read["ok"] and read["results"][0]["published_at"]
        evidence_id = read["results"][0]["evidence"]["evidence_id"]
        hits = (
            await client.call_tool("search_evidence", {"query": "Acme.example", "kind": "telegram_web"})
        ).structured_content
        assert hits["results"][0]["evidence_id"] == evidence_id
        refreshed = (await client.call_tool("refresh_evidence", {"evidence_id": evidence_id})).structured_content
        assert refreshed["ok"] and direct.call_count == 1
        assert refreshed["results"][0]["evidence"]["change_type"] == "edited"
        native = (await client.call_tool("read_telegram_channel", {"channel": HANDLE})).structured_content
        assert native["error"]["code"] == "telegram_disabled"


@respx.mock
async def test_media_only_and_unavailable_placeholders_do_not_become_body_claims(web):
    media = widget(3, "").replace(
        "</div>\n    <a",
        '<a class="tgme_widget_message_photo_wrap" href="https://media.example/pic"></a></div>\n    <a',
    )
    unavailable = widget(4, "").replace(
        'class="tgme_widget_message_bubble"',
        'class="tgme_widget_message_bubble"><div class="tgme_widget_message_error">View in Telegram</div><div',
    )
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    respx.get(ROOT).mock(return_value=html_response(page(media, unavailable)))
    result = await web.read(HANDLE)
    states = {p["message_id"]: p for p in result["results"]}
    assert states[4]["body_state"] == "unavailable" and "evidence" not in states[4]
    assert states[3]["body_state"] == "no_visible_text" and states[3]["has_media"]
    assert not web.store.search("View in Telegram")["results"]
    assert len(respx.calls) == 2  # No media URL was requested.

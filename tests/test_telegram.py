from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from telethon import errors, functions, types

from dark_mcp.errors import DataAccessError
from dark_mcp.telegram import TelegramConnector


def entity():
    return types.Channel(
        id=123,
        title="Research channel",
        photo=types.ChatPhotoEmpty(),
        date=datetime.now(UTC),
        broadcast=True,
        username="research",
    )


def message(identifier, date):
    return SimpleNamespace(
        id=identifier, date=date, message="Acme incident claim", peer_id=types.PeerChannel(123), media=None
    )


def configure(settings):
    settings.telegram_enabled = True
    settings.telegram_api_id = 123
    settings.telegram_api_hash = SecretStr("test-secret")
    settings.telegram_channels = ["research"]
    settings.session_path.touch(mode=0o600)


async def test_disabled_and_non_allowed_channels_do_not_connect(settings, monkeypatch):
    client = AsyncMock()
    factory = AsyncMock(return_value=client)
    monkeypatch.setattr("dark_mcp.telegram.TelegramClient", factory)
    connector = TelegramConnector(settings)
    with pytest.raises(DataAccessError) as exc:
        await connector.messages("research")
    assert exc.value.code == "telegram_disabled"
    configure(settings)
    with pytest.raises(DataAccessError) as exc:
        await connector.messages("unapproved")
    assert exc.value.code == "channel_not_allowed"
    factory.assert_not_called()


async def test_session_required_and_global_search_disabled(settings):
    configure(settings)
    settings.session_path.unlink()
    with pytest.raises(DataAccessError) as exc:
        await TelegramConnector(settings).channels("acme")
    assert exc.value.code == "telegram_not_authenticated"
    settings.session_path.touch()
    with pytest.raises(DataAccessError) as exc:
        await TelegramConnector(settings).channels("acme")
    assert exc.value.code == "global_search_disabled"


async def test_channel_search_since_and_pagination(settings, monkeypatch):
    configure(settings)
    connector = TelegramConnector(settings)
    calls = {}

    class FakeClient:
        async def get_entity(self, target):
            assert target == "research"
            return entity()

        async def iter_messages(self, target, **kwargs):
            calls.update(kwargs)
            for day, identifier in [(18, 20), (17, 19), (16, 18)]:
                yield message(identifier, datetime(2026, 10, day, tzinfo=UTC))

    @asynccontextmanager
    async def session(**kwargs):
        yield FakeClient()

    monkeypatch.setattr(connector, "session", session)
    result = await connector.messages("@research", query="Acme", limit=2, before_id=30, since="2026-10-17T00:00:00Z")
    assert [item["id"] for item in result["results"]] == [20, 19]
    assert result["next_before_id"] == 19
    assert result["results"][0]["url"] == "https://t.me/research/20"
    assert calls == {"limit": 2, "offset_id": 30, "search": "Acme"}


async def test_public_search_does_not_spend_stars(settings, monkeypatch):
    connector = TelegramConnector(settings)
    requests = []

    class FakeClient:
        async def __call__(self, request):
            requests.append(request)
            return SimpleNamespace(query_is_free=False)

    @asynccontextmanager
    async def session(**kwargs):
        yield FakeClient()

    monkeypatch.setattr(connector, "session", session)
    with pytest.raises(DataAccessError) as exc:
        await connector.posts("Acme")
    assert exc.value.code == "telegram_search_quota"
    assert len(requests) == 1
    assert isinstance(requests[0], functions.channels.CheckSearchPostsFloodRequest)


async def test_public_search_uses_numeric_cursor_and_returns_provenance(settings, monkeypatch):
    connector = TelegramConnector(settings)
    requests = []

    class FakeClient:
        async def get_input_entity(self, target):
            assert target == -100123
            return types.InputPeerChannel(channel_id=123, access_hash=456)

        async def __call__(self, request):
            requests.append(request)
            if isinstance(request, functions.channels.CheckSearchPostsFloodRequest):
                return SimpleNamespace(query_is_free=True)
            assert request.allow_paid_stars is None
            assert request.offset_id == 90
            assert request.offset_rate == 100
            return SimpleNamespace(
                chats=[entity()], messages=[message(20, datetime(2026, 10, 18, tzinfo=UTC))], next_rate=200
            )

    @asynccontextmanager
    async def session(**kwargs):
        yield FakeClient()

    monkeypatch.setattr(connector, "session", session)
    result = await connector.posts("Acme", cursor={"channel": "-100123", "offset_id": 90, "offset_rate": 100})
    assert result["results"][0]["url"] == "https://t.me/research/20"
    assert result["next_cursor"] == {"offset_rate": 200, "offset_id": 20, "channel": "-100123"}


async def test_flood_wait_returns_retry_and_disconnects(settings, monkeypatch):
    configure(settings)
    client = AsyncMock()
    client.connect.side_effect = errors.FloodWaitError(request=None, capture=42)
    monkeypatch.setattr("dark_mcp.telegram.TelegramClient", lambda *a, **k: client)
    with pytest.raises(DataAccessError) as exc:
        await TelegramConnector(settings).messages("research")
    assert exc.value.code == "telegram_rate_limited"
    assert exc.value.retry_after == 42
    client.disconnect.assert_awaited_once()

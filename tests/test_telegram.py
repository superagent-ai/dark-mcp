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
            return SimpleNamespace(query_is_free=False, remains=0)

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
    assert len(requests) == 1


@pytest.mark.parametrize(("query_is_free", "remains"), [(False, 1), (True, 0)])
async def test_public_search_uses_available_daily_slot_or_cached_query(
    settings,
    monkeypatch,
    query_is_free,
    remains,
):
    connector = TelegramConnector(settings)

    class FakeClient:
        async def __call__(self, request):
            if isinstance(request, functions.channels.CheckSearchPostsFloodRequest):
                return SimpleNamespace(query_is_free=query_is_free, remains=remains)
            assert request.allow_paid_stars is None
            return SimpleNamespace(chats=[], messages=[])

    @asynccontextmanager
    async def session(**kwargs):
        yield FakeClient()

    monkeypatch.setattr(connector, "session", session)
    result = await connector.posts("Acme")
    assert result["results"] == []
    assert result["next_cursor"] is None


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


def test_collectible_public_alias_is_used_for_evidence_links():
    channel = entity()
    channel.username = None
    channel.usernames = [types.Username(username="breaches", active=True)]
    result = TelegramConnector._message(message(20, datetime(2026, 10, 18, tzinfo=UTC)), channel)
    assert result["url"] == "https://t.me/breaches/20"
    assert result["channel"]["url"] == "https://t.me/breaches"


async def test_incremental_reads_request_oldest_new_ids_and_keep_boundary_progress(settings, monkeypatch):
    configure(settings)
    connector = TelegramConnector(settings)
    calls = []

    class FakeClient:
        async def get_entity(self, target):
            return entity()

        async def iter_messages(self, target, **kwargs):
            calls.append(kwargs)
            for identifier, day in [(101, 16), (102, 17), (103, 18)]:
                yield message(identifier, datetime(2026, 10, day, tzinfo=UTC))

    @asynccontextmanager
    async def session(**kwargs):
        yield FakeClient()

    monkeypatch.setattr(connector, "session", session)
    result = await connector.messages(
        "research", limit=2, after_id=100, oldest_first=True, since="2026-10-17T00:00:00Z"
    )
    assert [m["id"] for m in result["results"]] == [102]
    assert result["next_after_id"] == 102
    assert result["page_full"]
    assert calls[0]["reverse"] and calls[0]["min_id"] == 100


async def test_thread_replies_use_actual_discussion_peer_and_cannot_be_searched(settings, monkeypatch):
    configure(settings)
    connector = TelegramConnector(settings)

    class FakeClient:
        async def get_entity(self, target):
            return entity()

        async def iter_messages(self, target, **kwargs):
            assert kwargs["reply_to"] == 10
            reply = message(20, datetime(2026, 10, 18, tzinfo=UTC))
            reply.peer_id = types.PeerChannel(999)
            yield reply

    @asynccontextmanager
    async def session(**kwargs):
        yield FakeClient()

    monkeypatch.setattr(connector, "session", session)
    result = await connector.messages("research", thread_id=10)
    assert result["results"][0]["url"] == "https://t.me/c/999/20"
    assert result["results"][0]["channel"]["id"] == "-100999"
    assert result["thread_root"]["message_id"] == 10
    with pytest.raises(ValueError):
        await connector.messages("research", thread_id=10, query="Acme")


async def test_update_cursor_uses_explicit_delete_events_and_marks_gap(settings, monkeypatch):
    configure(settings)
    connector = TelegramConnector(settings)
    responses = [
        SimpleNamespace(full_chat=SimpleNamespace(pts=100)),
        types.updates.ChannelDifference(
            pts=102,
            new_messages=[],
            other_updates=[
                types.UpdateDeleteChannelMessages(channel_id=123, messages=[20], pts=102, pts_count=1),
                types.UpdateDeleteChannelMessages(channel_id=999, messages=[99], pts=102, pts_count=1),
            ],
            chats=[],
            users=[],
            final=True,
        ),
        types.updates.ChannelDifferenceTooLong(
            dialog=SimpleNamespace(pts=500), messages=[], chats=[], users=[], final=True
        ),
    ]

    class FakeClient:
        async def get_entity(self, target):
            return entity()

        async def __call__(self, request):
            return responses.pop(0)

    @asynccontextmanager
    async def session(**kwargs):
        yield FakeClient()

    monkeypatch.setattr(connector, "session", session)
    baseline = await connector.changes("research")
    assert baseline["initialized"] and baseline["next_pts"] == 100
    result = await connector.changes("research", pts=100, expected_id="-100123")
    assert result["deleted_message_ids"] == [20]
    gap = await connector.changes("research", pts=102, expected_id="-100123")
    assert gap["gap_detected"] and not gap["deleted_message_ids"]
    with pytest.raises(DataAccessError) as exc:
        await connector.changes("research", pts=500, expected_id="-100999")
    assert exc.value.code == "source_identity_changed"


@pytest.mark.parametrize("stage", ["preflight", "search"])
async def test_premium_requirement_is_an_entitlement_error_with_durable_mcp_attempt(settings, monkeypatch, stage):
    from mcp import Client

    from dark_mcp.evidence import EvidenceStore
    from dark_mcp.server import create_server

    configure(settings)
    settings.telegram_global_search = True
    client = AsyncMock()
    client.is_user_authorized.return_value = True
    failure = errors.PremiumAccountRequiredError(request=None)
    client.side_effect = [failure] if stage == "preflight" else [SimpleNamespace(query_is_free=True), failure]
    factories = []

    def factory(*args, **kwargs):
        factories.append(kwargs)
        return client

    monkeypatch.setattr("dark_mcp.telegram.TelegramClient", factory)
    async with Client(create_server(settings)) as mcp:
        result = (await mcp.call_tool("search_telegram_posts", {"query": "Acme", "limit": 10})).structured_content
        assert result["error"]["code"] == "telegram_premium_required"
        assert "retry_after" not in result["error"] and "retry_at" not in result["error"]
        health = (await mcp.call_tool("get_source_health", {"source_id": "telegram-public-search"})).structured_content
        entry = health["results"][0]
        assert entry["last_access_state"] == "telegram_premium_required"
        assert entry["last_attempt"]["options"] == {"query": "Acme", "limit": 10}
        assert entry["last_successful_collection_at"] is None
    assert client.await_count == (1 if stage == "preflight" else 2)
    client.disconnect.assert_awaited_once()
    assert factories[0]["request_retries"] == 0 and factories[0]["flood_sleep_threshold"] == 0
    if stage == "search":
        assert client.call_args.args[0].allow_paid_stars is None
    status = EvidenceStore(settings.data_dir).connector_status("telegram")
    assert status["cooldown_active"] is False and status["lease_active"] is False


async def test_empty_public_search_is_a_saved_observation_not_a_channel_or_absence_proof(settings, monkeypatch):
    from mcp import Client

    from dark_mcp.models import now
    from dark_mcp.server import create_server

    mock = AsyncMock(
        return_value={
            "results": [],
            "next_cursor": None,
            "collected_at": now(),
            "coverage": "Bounded public search; not exhaustive",
        }
    )
    monkeypatch.setattr(TelegramConnector, "posts", mock)
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("search_telegram_posts", {"query": "Acme", "limit": 10})).structured_content
        assert result["ok"] and result["results"] == []
        assert result["search_observation"]["source_id"] == "telegram-public-search"
        health = (
            await client.call_tool("get_source_health", {"source_id": "telegram-public-search"})
        ).structured_content["results"][0]
        assert health["last_access_state"] == "no_matching_posts"
        assert health["last_attempt"]["returned_items"] == 0
        assert health["last_attempt"]["coverage"] == "Bounded public search; not exhaustive"
        assert health["last_successful_collection_at"] and health["last_content_at"] is None

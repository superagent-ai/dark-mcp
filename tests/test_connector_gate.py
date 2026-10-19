import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from mcp import Client
from pydantic import SecretStr
from telethon import errors

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.server import create_server
from dark_mcp.telegram import TelegramConnector

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


def configured(settings):
    settings.telegram_enabled = True
    settings.telegram_api_id = 123
    settings.telegram_api_hash = SecretStr("test-only-not-a-real-credential")
    settings.telegram_global_search = True
    settings.telegram_channels = ["research", "breachdetect"]
    settings.session_path.touch(mode=0o600)
    return settings


@pytest.mark.parametrize("kind", ["telegram", "forum:fixture", "onion:fixture"])
def test_blocked_checks_do_not_extend_absolute_deadline_and_longer_provider_waits_win(settings, monkeypatch, kind):
    clock = ["2026-10-19T10:00:00.100000+00:00"]
    monkeypatch.setattr("dark_mcp.evidence.now", lambda: clock[0])
    store = EvidenceStore(settings.prepare_data_dir())
    error = DataAccessError("telegram_rate_limited", "Wait", retry_after=42)
    store.record_retry(kind, error)
    original = error.retry_at
    assert original == "2026-10-19T10:00:42.100000+00:00"
    clock[0] = "2026-10-19T10:00:00.200000+00:00"
    for _ in range(3):
        with pytest.raises(DataAccessError) as exc:
            store.cooldown(kind)
        assert exc.value.retry_after == 42 and exc.value.retry_at == original
        store.record_retry(kind, exc.value)
        assert store.connector_status(kind)["retry_at"] == original
    # Re-recording the same native error after disconnect also preserves its deadline.
    store.record_retry(kind, error)
    assert store.connector_status(kind)["retry_at"] == original
    store.record_retry(kind, DataAccessError("telegram_rate_limited", "Shorter", retry_after=1))
    assert store.connector_status(kind)["retry_at"] == original
    store.record_retry(kind, DataAccessError("telegram_rate_limited", "Longer", retry_after=60))
    assert store.connector_status(kind)["retry_at"] == "2026-10-19T10:01:00.200000+00:00"
    clock[0] = "2026-10-19T10:01:01+00:00"
    store.cooldown(kind)
    assert not store.connector_status(kind)["cooldown_active"]


def test_session_lease_expiry_token_ownership_and_busy_are_separate_from_provider_cooldown(settings, monkeypatch):
    clock = ["2026-10-19T10:00:00+00:00"]
    monkeypatch.setattr("dark_mcp.evidence.now", lambda: clock[0])
    first = EvidenceStore(settings.prepare_data_dir())
    other = EvidenceStore(settings.data_dir)
    token = first.claim_connector("telegram", 60)
    with pytest.raises(DataAccessError) as exc:
        other.claim_connector("telegram", 60)
    assert exc.value.code == "connector_busy" and exc.value.retry_after == 60
    other.record_retry("telegram", exc.value)
    assert other.connector_status("telegram")["retry_at"] is None
    assert "token" not in json.dumps(other.connector_status("telegram"))
    first.release_connector("telegram", "wrong-owner")
    assert other.connector_status("telegram")["lease_active"]
    clock[0] = "2026-10-19T10:01:01+00:00"
    replacement = other.claim_connector("telegram", 60)
    first.release_connector("telegram", token)
    assert other.connector_status("telegram")["lease_active"]
    other.release_connector("telegram", replacement)
    assert not other.connector_status("telegram")["lease_active"]
    first.cooldown("telegram", 42)
    with pytest.raises(DataAccessError) as exc:
        other.claim_connector("telegram", 60)
    assert exc.value.code == "collection_rate_limited"


@pytest.mark.parametrize("wait_error", [errors.FloodWaitError, errors.FloodPremiumWaitError])
async def test_queued_caller_cannot_construct_client_after_floodwait_and_deadline_precedes_disconnect(
    settings, monkeypatch, wait_error
):
    configured(settings)
    store = EvidenceStore(settings.prepare_data_dir())
    connector = TelegramConnector(settings, store=store)
    entered, release = asyncio.Event(), asyncio.Event()
    client = AsyncMock()

    async def connect():
        entered.set()
        await release.wait()
        raise wait_error(request=None, capture=42)

    async def disconnect():
        status = store.connector_status("telegram")
        assert status["cooldown_active"] and status["lease_active"]

    client.connect.side_effect = connect
    client.disconnect.side_effect = disconnect
    factory = Mock(return_value=client)
    monkeypatch.setattr("dark_mcp.telegram.TelegramClient", factory)

    async def read():
        async with connector.session():
            raise AssertionError("A FloodWait session cannot yield a client")

    first = asyncio.create_task(read())
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(read())
    await asyncio.sleep(0)
    release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)
    assert [item.code for item in outcomes] == ["telegram_rate_limited", "collection_rate_limited"]
    assert outcomes[0].retry_at == outcomes[1].retry_at
    assert factory.call_count == 1 and client.connect.await_count == 1
    assert not store.connector_status("telegram")["lease_active"]


async def test_independent_connector_instances_share_lease_without_creating_account_cooldown(settings, monkeypatch):
    configured(settings)
    store = EvidenceStore(settings.prepare_data_dir())
    one, two = (
        TelegramConnector(settings, store=store),
        TelegramConnector(settings, store=EvidenceStore(settings.data_dir)),
    )
    client = AsyncMock()
    client.is_user_authorized.return_value = True
    factory = Mock(return_value=client)
    monkeypatch.setattr("dark_mcp.telegram.TelegramClient", factory)
    async with one.session():
        with pytest.raises(DataAccessError) as exc:
            async with two.session():
                raise AssertionError("A second connector must not enter the session")
        assert exc.value.code == "connector_busy"
        assert store.connector_status("telegram")["retry_at"] is None
    async with two.session():
        pass
    assert factory.call_count == 2 and not store.connector_status("telegram")["lease_active"]


async def test_constructor_errors_and_cancelled_operations_release_only_their_lease(settings, monkeypatch):
    configured(settings)
    store = EvidenceStore(settings.prepare_data_dir())
    connector = TelegramConnector(settings, store=store)
    monkeypatch.setattr("dark_mcp.telegram.TelegramClient", Mock(side_effect=OSError("fixture")))
    with pytest.raises(DataAccessError) as exc:
        async with connector.session():
            pass
    assert exc.value.code == "telegram_unavailable" and not store.connector_status("telegram")["lease_active"]
    client = AsyncMock()
    client.is_user_authorized.return_value = True
    monkeypatch.setattr("dark_mcp.telegram.TelegramClient", Mock(return_value=client))
    with pytest.raises(asyncio.CancelledError):
        async with connector.session():
            raise asyncio.CancelledError
    client.disconnect.assert_awaited_once()
    assert not store.connector_status("telegram")["lease_active"]


async def test_all_mcp_telegram_entrypoints_block_without_connecting_or_sliding_deadline(settings, monkeypatch):
    configured(settings)
    store = EvidenceStore(settings.prepare_data_dir())
    deadline = store.cooldown("telegram", 60)
    factory = Mock(side_effect=AssertionError("No native Telegram client may be constructed"))
    monkeypatch.setattr("dark_mcp.telegram.TelegramClient", factory)
    source = "telegram-channel:-100123"
    store.register(source, "telegram", "research", "Fixture", {"collection_locator": "research"})
    saved = store.observe(
        source,
        "telegram",
        "-100123:42",
        "https://t.me/c/123/42",
        {
            "text": "Fixture",
            "message_id": 42,
            "channel_id": "-100123",
        },
    )
    operations = [
        ("search_telegram_channels", {"query": "research"}),
        ("read_telegram_channel", {"channel": "research"}),
        ("search_telegram_messages", {"channel": "research", "query": "company"}),
        ("read_telegram_thread", {"channel": "research", "message_id": 42}),
        ("sync_telegram_changes", {"channel": "research"}),
        ("search_telegram_posts", {"query": "company"}),
        ("refresh_evidence", {"evidence_id": saved["evidence_id"]}),
        ("collect_source", {"source_id": "telegram:breachdetect"}),
    ]
    for _ in range(2):
        async with Client(create_server(settings)) as client:
            status = (await client.call_tool("get_status", {})).structured_content
            assert status["telegram_access"]["cooldown_active"]
            assert status["telegram_access"]["retry_at"] == deadline
            assert not status["telegram_access"]["network_read"]
            for name, arguments in operations:
                result = (await client.call_tool(name, arguments)).structured_content
                assert result["error"]["code"] == "collection_rate_limited", (name, result)
                assert result["error"]["retry_at"] == deadline
            job = (
                await client.call_tool("start_collection", {"source_id": "telegram:breachdetect"})
            ).structured_content["collection"]
            result = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
            assert result["collection"]["status"] == "waiting"
            assert result["collection"]["checkpoint"]["before_id"] == 0
            assert store.connector_status("telegram")["retry_at"] == deadline
    factory.assert_not_called()


async def test_independent_python_processes_observe_shared_lease_and_release(settings):
    code = """
import json,sys
from pathlib import Path
from dark_mcp.evidence import EvidenceStore
from dark_mcp.errors import DataAccessError
store=EvidenceStore(Path(sys.argv[1]))
try:
 token=store.claim_connector('telegram',60)
except DataAccessError as error:
 print(json.dumps(error.as_dict()),flush=True)
else:
 print(json.dumps({'claimed':True}),flush=True)
 if sys.argv[2]=='hold':sys.stdin.readline()
 store.release_connector('telegram',token)
"""
    directory = str(settings.prepare_data_dir())
    env = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT)}
    first = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        directory,
        "hold",
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        line = await asyncio.wait_for(first.stdout.readline(), 5)
        assert json.loads(line)["claimed"]
        second = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            directory,
            "once",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(second.communicate(), 5)
        assert second.returncode == 0, stderr.decode()
        assert json.loads(stdout)["error"]["code"] == "connector_busy"
        first.stdin.write(b"release\n")
        await first.stdin.drain()
        await asyncio.wait_for(first.wait(), 5)
        assert first.returncode == 0
        assert not EvidenceStore(settings.data_dir).connector_status("telegram")["lease_active"]
    finally:
        if first.returncode is None:
            first.kill()
            await first.wait()

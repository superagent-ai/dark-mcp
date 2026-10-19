import asyncio
import csv
import io
import json
import zipfile
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from mcp import Client
from typer.testing import CliRunner

from dark_mcp.cli import app
from dark_mcp.collection import CollectionService
from dark_mcp.coverage import CorpusCoverage
from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.open_telegram import import_archive, import_directory, list_open_sources
from dark_mcp.open_telegram_fetch import fetch_open_artifacts
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog
from dark_mcp.telegram import TelegramConnector


def archive(tmp_path, rows, filename="posts/cyber/fixture_channel.csv"):
    target = tmp_path / "fixture.zip"
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=["Channel ID", "URL", "Post ID", "date", "message", "peer_channel"])
    writer.writeheader()
    for index, row in enumerate(rows, 1):
        writer.writerow(
            {
                "Channel ID": "fixture_channel",
                "URL": "https://t.me/fixture_channel",
                "Post ID": index,
                "date": "2024-01-02 07:40:16",
                "peer_channel": "123456",
                **row,
            }
        )
    with zipfile.ZipFile(target, "w") as z:
        z.writestr(filename, text.getvalue())
    return target


def test_archive_resume_replay_native_isolation_dates_and_acquisition(settings, tmp_path):
    file = archive(
        tmp_path,
        [
            {"message": "Acme.example access claim"},
            {"message": ""},
            {"message": "Acme.example followup", "date": "2024-01-03T08:00:00+00:00"},
        ],
    )
    one = import_archive(settings, file, "darkgram", "fixture-v1", limit=2)
    assert one["next_offset"] == 2
    assert one["counts"] == {"scanned": 2, "new": 1, "empty_text_skipped": 1}
    two = import_archive(settings, file, "darkgram", "fixture-v1", offset=one["next_offset"])
    assert two["imported_rows"] == 1 and two["next_offset"] is None
    store = EvidenceStore(settings.data_dir)
    store.register("native", "telegram", "fixture_channel", "Native")
    native = store.observe(
        "native", "telegram", "123456:1", "https://t.me/fixture_channel/1", {"text": "Acme.example native observation"}
    )
    before = store.counts()
    replay = import_archive(settings, file, "darkgram", "fixture-v1")
    assert replay["counts"]["unchanged"] == 2
    assert store.counts()["revisions"] == before["revisions"]
    assert len(store.search("Acme.example", kind="telegram")["results"]) == 1
    hits = store.search("Acme.example", kind="telegram_archive")["results"]
    assert len(hits) == 2 and native["evidence_id"] not in {h["evidence_id"] for h in hits}
    snapshots = [store.get(h["evidence_id"])["snapshot"] for h in hits]
    naive = next(s for s in snapshots if s["message_id"] == "1")
    assert naive["published_at"] is None and naive["published_at_raw"] == "2024-01-02 07:40:16"
    assert naive["timestamp_basis"] == "dataset_timezone_or_date_unresolved"
    assert all(h["matched_revision_first_recorded_at"] > "2025" for h in hits)
    assert (
        len(store.search("Acme.example", kind="telegram_archive", published_since="2024-01-01T00:00:00Z")["results"])
        == 1
    )
    assert all(h["acquisition"]["artifact_sha256"] == one["artifact_sha256"] for h in hits)
    assert all(h["acquisition"]["network_read"] is False for h in hits)
    assert CorpusCoverage(store).get(kind="telegram_archive")["summary"]["items"] == 2


async def test_archive_cannot_refresh_through_native_connector(settings, tmp_path):
    file = archive(tmp_path, [{"message": "Synthetic claim"}])
    import_archive(settings, file, "darkgram", "fixture-v1")
    store = EvidenceStore(settings.data_dir)
    item = store.search("Synthetic")["results"][0]
    telegram = AsyncMock()
    service = CollectionService(settings, SourceCatalog(), store, None, telegram)
    with pytest.raises(DataAccessError) as error:
        await service.refresh(item["evidence_id"])
    assert error.value.code == "archive_reimport_required"
    telegram.message.assert_not_called()


@pytest.mark.parametrize("member", ["../fixture.csv", "/posts/cyber/x.csv", "posts/payload.exe", "replies/x.csv"])
def test_archive_rejects_unsupported_members_without_writes(settings, tmp_path, member):
    file = archive(tmp_path, [{"message": "Synthetic"}], filename=member)
    with pytest.raises(DataAccessError):
        import_archive(settings, file, "darkgram", "fixture-v1")
    assert EvidenceStore(settings.data_dir).counts()["items"] == 0


def test_archive_category_bounds_truncation_and_bad_identifiers(settings, tmp_path):
    settings.max_text_chars = 100
    file = archive(
        tmp_path,
        [
            {"message": "X" * 101},
            {"message": "bad", "Channel ID": "+some_invite", "peer_channel": "invalid"},
            {"message": "bad post", "Post ID": "1.5"},
        ],
    )
    assert import_archive(settings, file, "darkgram", "v1", category="other")["imported_rows"] == 0
    result = import_archive(settings, file, "darkgram", "v1", category="cyber")
    assert result["counts"]["invalid_identity_skipped"] == 2
    item = EvidenceStore(settings.data_dir).search("XXX")["results"][0]
    assert item["truncated"]
    assert len(EvidenceStore(settings.data_dir).get(item["evidence_id"])["snapshot"]["text"]) == 100


def test_legacy_darkgram_csv_preserves_filename_identity_basis_without_evaluating_fields(settings, tmp_path):
    file = tmp_path / "legacy.zip"
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=["id", "date", "message", "peer_id"])
    writer.writeheader()
    writer.writerow(
        {
            "id": 1,
            "date": "2024-01-01 00:00:00",
            "message": "Synthetic access claim",
            "peer_id": "__import__('os').environ.clear()",
        }
    )
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("posts/cyber/fixture_channel.csv", text.getvalue())
    assert import_archive(settings, file, "darkgram", "legacy-v1")["imported_rows"] == 1
    store = EvidenceStore(settings.data_dir)
    item = store.search("Synthetic")["results"][0]
    snapshot = store.get(item["evidence_id"])["snapshot"]
    assert snapshot["reported_channel_id"] == ""
    assert snapshot["acquisition"]["handle_basis"] == "dataset_member_filename"
    assert snapshot["reported_handle"] == "fixture_channel" and snapshot["published_at"] is None


def test_integral_csv_ids_preserve_raw_value_but_fractional_ids_stay_invalid(settings, tmp_path):
    file = archive(
        tmp_path,
        [
            {"message": "Synthetic claim", "Post ID": "1730.0"},
            {"message": "Invalid fractional identity", "Post ID": "1730.5"},
        ],
    )
    result = import_archive(settings, file, "darkgram", "fixture-v1")
    assert result["imported_rows"] == 1 and result["counts"]["invalid_identity_skipped"] == 1
    store = EvidenceStore(settings.data_dir)
    hit = store.search("Synthetic")["results"][0]
    snapshot = store.get(hit["evidence_id"])["snapshot"]
    assert snapshot["message_id"] == "1730" and snapshot["message_id_raw"] == "1730.0"
    assert snapshot["url"] == "https://t.me/fixture_channel/1730"


def test_tgdataset_epoch_and_forward_dates_do_not_backdate_the_observed_copy(settings, tmp_path):
    file = tmp_path / "shard.json"
    file.write_text(
        json.dumps(
            {
                "123": {
                    "username": None,
                    "text_messages": {
                        "7": {
                            "message": "Acme.example unverified claim",
                            "date": 1704067200,
                            "is_forwarded": True,
                            "forwarded_from_id": 999,
                            "forwarded_message_date": 1600000000,
                        }
                    },
                }
            }
        )
    )
    result = import_archive(settings, file, "tgdataset", "fixture-v1")
    assert result["imported_rows"] == 1
    store = EvidenceStore(settings.data_dir)
    hit = store.search("Acme.example")["results"][0]
    snapshot = store.get(hit["evidence_id"])["snapshot"]
    assert snapshot["published_at"] == "2024-01-01T00:00:00+00:00"
    assert snapshot["forward"]["forwarded_message_date"] == 1600000000
    assert snapshot["url"] is None
    assert snapshot["acquisition"]["license"] == "CC-BY-4.0"


def directory(tmp_path):
    file = tmp_path / "directory.md"
    file.write_text(
        "|Telegram|Status|Threat Actor Name|Type of attacks|\n|---|---|---|---|\n"
        "|https://t.me/fixture_channel|VALID|Research assertion|Access sales|\n"
        "|https://t.me/+12345678abcd|EXPIRED|Expired lead|Unknown|\n"
        "|https://t.me/share/url?url=example|VALID|Action|Unknown|\n"
        "|https://example.com/fixture|VALID|Other site|Unknown|\n"
    )
    return file


def test_directory_is_searchable_but_cannot_grant_native_access(settings, tmp_path):
    catalog = SourceCatalog(settings)
    original = catalog.telegram_targets()
    result = import_directory(settings, directory(tmp_path), "fixture-v1", "telegram_threat_actors")
    assert result["counts"]["rows"] == 2
    assert result["counts"]["unsupported_locator_skipped"] == 2
    store = EvidenceStore(settings.data_dir)
    leads = list_open_sources(store, dataset="deepdarkcti", limit=1)
    assert leads["total"] == 2 and leads["next_offset"] == 1
    assert len(list_open_sources(store, query="EXPIRED")["results"]) == 1
    assert all(not row["metadata"]["collection_enabled"] for row in list_open_sources(store)["results"])
    assert SourceCatalog(settings).telegram_targets() == original
    settings.telegram_catalog_sources = True
    with pytest.raises(DataAccessError):
        TelegramConnector(settings, SourceCatalog(settings))._allowed_channel("fixture_channel")
    import_directory(settings, directory(tmp_path), "fixture-v1", "telegram_threat_actors")
    assert list_open_sources(store)["total"] == 2
    assert store.counts()["items"] == 0


def test_duplicate_directory_locators_preserve_conflicting_upstream_labels(settings, tmp_path):
    file = directory(tmp_path)
    with file.open("a") as stream:
        stream.write("|https://t.me/fixture_channel|OFFLINE|Different attribution|Other|\n")
    result = import_directory(settings, file, "fixture-v1", "telegram_threat_actors")
    assert result["counts"]["rows"] == 3 and result["counts"]["unique_leads"] == 2
    row = list_open_sources(EvidenceStore(settings.data_dir), query="Different attribution")["results"][0]
    assert row["metadata"]["reported_status"] == "MULTIPLE_UPSTREAM_STATUSES"
    assert row["metadata"]["reported_statuses"] == ["OFFLINE", "VALID"]
    assert len(row["metadata"]["reported_labels"]) == 2


async def test_real_mcp_search_replay_coverage_and_errors_without_telegram(settings, tmp_path):
    import_archive(settings, archive(tmp_path, [{"message": "Acme.example synthetic access offer"}]), "darkgram", "v1")
    import_directory(settings, directory(tmp_path), "fixture-v1", "telegram_threat_actors")
    async with Client(create_server(settings)) as client:
        sources = (await client.call_tool("list_open_telegram_sources", {"dataset": "darkgram"})).structured_content
        assert sources["ok"] and sources["total"] == 1
        search = (
            await client.call_tool("search_evidence", {"query": "Acme.example", "kind": "telegram_archive"})
        ).structured_content
        evidence_id = search["results"][0]["evidence_id"]
        exact = (await client.call_tool("get_evidence", {"evidence_id": evidence_id})).structured_content
        assert exact["evidence"]["snapshot"]["text"] == "Acme.example synthetic access offer"
        refresh = (await client.call_tool("refresh_evidence", {"evidence_id": evidence_id})).structured_content
        assert refresh["error"]["code"] == "archive_reimport_required"
        coverage = (await client.call_tool("get_corpus_coverage", {"kind": "telegram_archive"})).structured_content
        assert coverage["summary"]["items"] == 1
        bad = (await client.call_tool("list_open_telegram_sources", {"kind": "native"})).structured_content
        assert not bad["ok"]


def test_cli_local_imports(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("dark_mcp.cli.Settings", lambda: settings)
    result = CliRunner().invoke(
        app, ["telegram-archive-import", str(archive(tmp_path, [{"message": "Synthetic"}])), "darkgram", "fixture-v1"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["imported_rows"] == 1
    result = CliRunner().invoke(app, ["telegram-directory-import", str(directory(tmp_path)), "fixture-v1"])
    assert result.exit_code == 0, result.output


@respx.mock
async def test_pinned_download_checksums_cache_and_private_storage(settings, monkeypatch):
    import hashlib

    from dark_mcp.open_telegram_fetch import SNAPSHOTS

    payload = b"synthetic research artifact"
    monkeypatch.setitem(
        SNAPSHOTS,
        "darkgram",
        {
            "repository": "SayakSR/DarkGram",
            "commit": "fixture-v1",
            "files": {"data/posts.zip": hashlib.sha256(payload).hexdigest()},
        },
    )
    url = "https://raw.githubusercontent.com/SayakSR/DarkGram/fixture-v1/data/posts.zip"
    route = respx.get(url).mock(return_value=httpx.Response(200, content=payload))
    result = await fetch_open_artifacts(settings, "darkgram")
    from pathlib import Path

    file = Path(result["files"][0]["path"])
    assert await asyncio.to_thread(file.read_bytes) == payload
    assert (await asyncio.to_thread(file.stat)).st_mode & 0o777 == 0o600
    assert not result["imported"] and result["telegram_requests"] == 0
    assert (await fetch_open_artifacts(settings, "darkgram"))["files"][0]["cached"]
    assert route.call_count == 1
    await asyncio.to_thread(file.unlink)
    route.mock(return_value=httpx.Response(200, content=b"unexpected replacement"))
    with pytest.raises(DataAccessError) as error:
        await fetch_open_artifacts(settings, "darkgram")
    assert error.value.code == "open_artifact_unavailable"
    assert not await asyncio.to_thread(file.exists)


async def test_imports_survive_stdio_restart_without_account(settings, tmp_path):
    import os
    import sys
    from pathlib import Path

    from mcp import StdioServerParameters

    import_archive(settings, archive(tmp_path, [{"message": "Acme.example synthetic claim"}]), "darkgram", "v1")
    env = {
        **os.environ,
        "DARK_MCP_DATA_DIR": str(settings.data_dir),
        "DARK_MCP_TELEGRAM_ENABLED": "false",
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "dark_mcp", "serve"], env=env)
    async with Client(params) as client:
        search = (
            await client.call_tool("search_evidence", {"query": "Acme.example", "kind": "telegram_archive"})
        ).structured_content
        hit = search["results"][0]
    async with Client(params) as client:
        exact = (await client.call_tool("get_evidence", {"evidence_id": hit["evidence_id"]})).structured_content
        assert exact["evidence"]["content_hash"] == hit["content_hash"]
        assert exact["evidence"]["snapshot"]["acquisition"]["network_read"] is False

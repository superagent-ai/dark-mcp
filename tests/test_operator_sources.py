import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from mcp import Client
from typer.testing import CliRunner

from dark_mcp import cli
from dark_mcp.collection import CollectionService
from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.forum_sessions import ForumSessions
from dark_mcp.forums import ForumConnector
from dark_mcp.operator_sources import OperatorSources, normalize
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

ORIGIN = "https://private-forum.example.test"
SID = "operator:forum:research"


def manifest(tmp_path, **changes):
    value = {
        "slug": "research",
        "name": "Operator research",
        "locator": ORIGIN + "/Forum-research",
        "forum_engine": "mybb",
        **changes,
    }
    file = tmp_path / "source.json"
    file.write_text(json.dumps(value))
    return file


def test_registry_is_private_idempotent_scoped_and_separate_from_packaged_research(settings, tmp_path, onion):
    registry = OperatorSources(settings)
    assert registry.sources() == [] and not registry.path.exists()
    config = manifest(tmp_path)
    result = registry.import_file(config)
    assert result == registry.import_file(config) and not result["access_verified"]
    assert registry.path.stat().st_mode & 0o777 == 0o600
    assert registry.path.parent.stat().st_mode & 0o777 == 0o700
    catalog = SourceCatalog(settings)
    assert catalog.summary()["operator_configured_sources"] == 1
    assert catalog.summary()["packaged_sources"] == 174
    assert len(SourceCatalog().sources) == 174  # The packaged snapshot is unchanged.
    detail = catalog.detail(SID)
    assert detail["source"]["configuration_origin"] == "local_operator"
    assert detail["research_references"] == [] and detail["provider_research"] == []
    assert detail["source"]["checked_at"] is None
    with pytest.raises(DataAccessError) as exc:
        registry.import_file(manifest(tmp_path, name="Changed identity"))
    assert exc.value.code == "operator_source_exists"
    with pytest.raises(DataAccessError) as exc:
        registry.import_file(manifest(tmp_path, slug="other"))
    assert exc.value.code == "operator_source_conflict"
    with pytest.raises(DataAccessError):
        registry.import_file(manifest(tmp_path, slug="builtin", locator="https://leakforum.io/"))
    tor = registry.import_file(manifest(tmp_path, slug="tor", locator=onion, allowed_hosts=[onion.split("/")[2]]))
    cat = SourceCatalog(settings)
    store = EvidenceStore(settings.prepare_data_dir())
    collection = CollectionService(settings, cat, store, None, None)
    assert collection.onion_source(onion) == tor["source_id"]  # Generic/structured Tor share source cooldowns.
    assert registry.remove(SID) and not registry.remove(SID)
    with pytest.raises(DataAccessError):
        registry.import_file(manifest(tmp_path, locator="https://different.example.test/"))
    assert registry.import_file(manifest(tmp_path))["source_id"] == SID
    # Even a registry reset cannot reassign an identity already used by saved evidence.
    registry.path.unlink()
    registry.import_file(manifest(tmp_path, locator="https://different.example.test/"))
    with pytest.raises(DataAccessError) as exc:
        create_server(settings)
    assert exc.value.code == "source_identity_conflict"
    assert store.source(SID)["locator"].startswith(ORIGIN)


@pytest.mark.parametrize(
    "change",
    [
        {"slug": "../escape"},
        {"name": "bad\nname"},
        {"forum_engine": "ipb"},
        {"locator": "http://example.test/"},
        {"locator": "https://127.0.0.1/"},
        {"locator": "https://user:secret@example.test/"},
        {"allowed_hosts": ["*.example.test"]},
        {"collection_urls": [ORIGIN + "/showthread.php?tid=1&action=delete"]},
        {"cookies": [{"name": "session", "value": "must-not-be-echoed"}]},
    ],
)
def test_operator_source_rejects_credentials_actions_and_unsupported_configuration(change):
    with pytest.raises(DataAccessError) as exc:
        normalize({"slug": "research", "name": "Source", "locator": ORIGIN, "forum_engine": "mybb", **change})
    assert "must-not-be-echoed" not in str(exc.value) and "secret" not in str(exc.value)


def test_cli_invalid_input_does_not_echo_secrets_and_registry_symlinks_are_rejected(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    result = CliRunner().invoke(cli.app, ["source-import", str(manifest(tmp_path, cookie="secret-sentinel"))])
    assert result.exit_code == 1 and "secret-sentinel" not in result.output
    registry = OperatorSources(settings)
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.symlink_to(tmp_path / "missing-target")
    with pytest.raises(DataAccessError):
        registry.sources()


def test_source_file_and_active_configuration_bounds_include_reactivation(settings, tmp_path):
    registry = OperatorSources(settings)
    file = manifest(tmp_path)
    file.write_text(" " * 32001 + file.read_text())
    with pytest.raises(DataAccessError):
        registry.import_file(file)
    assert not registry.path.exists()
    with registry.connect(create=True) as db:
        for n in range(100):
            config = {
                "slug": f"source-{n}",
                "name": "Fixture",
                "forum_engine": "mybb",
                "locator": f"https://source-{n}.example.test/",
            }
            config = normalize(config)
            config.pop("transport")
            db.execute(
                "INSERT INTO configurations VALUES(?,?,?,1)",
                (f"operator:forum:source-{n}", json.dumps(config, sort_keys=True), "2026-10-19T00:00:00Z"),
            )
    with pytest.raises(DataAccessError) as exc:
        registry.import_file(manifest(tmp_path))
    assert exc.value.code == "operator_source_limit" and len(registry.sources()) == 100
    registry.remove("operator:forum:source-0")
    registry.import_file(manifest(tmp_path))
    with pytest.raises(DataAccessError) as exc:
        registry.import_file(
            manifest(tmp_path, slug="source-0", name="Fixture", locator="https://source-0.example.test/")
        )
    assert exc.value.code == "operator_source_limit"


@respx.mock
async def test_local_source_and_exact_origin_session_collect_after_restart_then_remove_without_losing_evidence(
    settings,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["source-import", str(manifest(tmp_path))]).exit_code == 0
    cookies = tmp_path / "cookies.json"
    cookies.write_text(
        json.dumps([{"domain": "private-forum.example.test", "name": "session", "value": "fixture-only"}])
    )
    assert runner.invoke(cli.app, ["forum-session-import", SID, str(cookies)]).exit_code == 0
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(ForumConnector, "_pace", AsyncMock())

    def robots(request):
        assert "cookie" not in request.headers
        return httpx.Response(404)

    def thread(request):
        assert request.headers["Cookie"] == "session=fixture-only"
        return httpx.Response(
            200,
            text="""<article id="post_42"><div class="post_body">
        Private fixture original</div><div class="pagnum"><a href="Thread-fixture?pid=42">#1</a></div></article>""",
        )

    respx.get("https://8.8.8.8/robots.txt").mock(side_effect=robots)
    respx.get("https://8.8.8.8/Thread-fixture").mock(side_effect=thread)
    async with Client(create_server(settings)) as client:
        job = (
            await client.call_tool("start_collection", {"source_id": SID, "url": ORIGIN + "/Thread-fixture"})
        ).structured_content["collection"]
    async with Client(create_server(settings)) as client:
        read = (await client.call_tool("collect_next", {"collection_id": job["id"]})).structured_content
        assert read["ok"] and read["collection"]["status"] == "complete"
        item = read["results"][0]["page"]["posts"][0]["evidence"]
        status = (await client.call_tool("get_forum_access_status", {"source_id": SID})).structured_content
        assert not status["access"]["authentication_verified"]  # Fixture transport does not verify a real account.
    removed = runner.invoke(cli.app, ["source-remove", SID])
    assert removed.exit_code == 0 and json.loads(removed.output)["local_sessions_removed"] == 1
    assert OperatorSources(settings).sources() == []
    assert ForumSessions(settings, SourceCatalog()).remove_source_sessions(SID) == 0
    async with Client(create_server(settings)) as client:
        old = (await client.call_tool("get_evidence", {"evidence_id": item["evidence_id"]})).structured_content
        assert old["evidence"]["snapshot_hash"] == item["snapshot_hash"]
        missing = (await client.call_tool("start_collection", {"source_id": SID})).structured_content
        assert missing["error"]["code"] == "source_not_found"
        refresh = (await client.call_tool("refresh_evidence", {"evidence_id": item["evidence_id"]})).structured_content
        assert refresh["error"]["code"] == "source_not_found"

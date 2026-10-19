import pytest
from mcp import Client

from dark_mcp.evidence import EvidenceStore
from dark_mcp.open_telegram import import_directory, list_open_sources
from dark_mcp.server import create_server
from dark_mcp.source_observations import public_web_observations


def saved_post(store, handle, key="1", text="Synthetic source body", date="2026-10-18T00:00:00Z"):
    sid = "telegram-web:" + handle
    store.register(sid, "telegram_web", "https://t.me/s/" + handle, handle)
    return store.observe(
        sid,
        "telegram_web",
        handle + ":" + key,
        f"https://t.me/{handle}/{key}",
        {"text": text, "published_at": date, "access_state": "content_returned_unverified"},
    )


def directory(settings, tmp_path):
    path = tmp_path / "directory.md"
    path.write_text(
        "|Telegram|Status|Name|Type|\n|---|---|---|---|\n"
        "|https://t.me/recent_channel|OFFLINE|Recent source|Access|\n"
        "|https://t.me/recent_two|ONLINE|Another recent source|Access|\n"
        "|https://t.me/fail_channel|ONLINE|Failed source|Access|\n"
        "|https://t.me/never_channel|ONLINE|Untested source|Access|\n"
        "|https://t.me/media_channel|ONLINE|Media only|Access|\n"
        "|https://t.me/+aValidInvite12345|VALID|Private lead|Unknown|\n"
    )
    import_directory(settings, path, "fixture-v1", "telegram_threat_actors")


def test_local_facts_preserve_failed_refresh_and_directory_claims_without_inventing_access(settings, tmp_path):
    directory(settings, tmp_path)
    store = EvidenceStore(settings.prepare_data_dir())
    saved_post(store, "recent_channel", key="1", date="2025-01-01T00:00:00Z")
    latest = saved_post(store, "recent_channel", key="2")
    store.unavailable(latest["evidence_id"], "Synthetic missing post")
    for handle, state in (("recent_channel", "telegram_web_identity_mismatch"), ("fail_channel", "robots_denied")):
        sid = "telegram-web:" + handle
        if handle == "fail_channel":
            store.register(sid, "telegram_web", "https://t.me/s/" + handle, handle)
        attempt = store.begin_attempt(sid, {"network_read": False, "fixture": True})
        store.finish_attempt(attempt, state, {"fixture": True})
    saved_post(store, "media_channel", text="")
    before = store.counts()
    results = list_open_sources(store)["results"]
    leads = {row["metadata"]["locator"]: row for row in results}
    observed = leads["recent_channel"]["public_web_observation"]
    assert leads["recent_channel"]["metadata"]["reported_status"] == "OFFLINE"
    assert observed["match_basis"] == "same_public_handle_unverified"
    assert not observed["network_read"] and not observed["current_access_verified"]
    assert observed["latest_attempt"]["state"] == "telegram_web_identity_mismatch"
    assert observed["saved_inventory"]["posts"] == observed["saved_inventory"]["posts_with_text"] == 2
    assert observed["saved_inventory"]["unavailable_or_deleted_posts"] == 1
    assert observed["saved_inventory"]["newest_text_publication"] == "2026-10-18T00:00:00+00:00"
    assert (
        observed["saved_inventory"]["last_positive_post_recorded_at"] == store.get(latest["evidence_id"])["last_seen"]
    )
    failed = leads["fail_channel"]["public_web_observation"]
    assert failed["latest_attempt"]["state"] == "robots_denied" and failed["saved_inventory"]["posts"] == 0
    assert leads["never_channel"]["public_web_observation"] is None
    assert leads["+aValidInvite12345"]["public_web_observation"] is None
    assert leads["media_channel"]["public_web_observation"]["saved_inventory"]["newest_text_publication"] is None
    assert store.counts() == before


def test_reported_text_date_filter_precedes_pagination_and_excludes_media_and_unknown_dates(settings, tmp_path):
    directory(settings, tmp_path)
    store = EvidenceStore(settings.prepare_data_dir())
    saved_post(store, "recent_channel")
    saved_post(store, "recent_two")
    saved_post(store, "media_channel", text="")
    saved_post(store, "never_channel", date=None)
    first = list_open_sources(store, limit=1, web_text_since="2026-10-01T00:00:00Z")
    second = list_open_sources(store, offset=1, limit=1, web_text_since="2026-10-01T00:00:00Z")
    assert first["total"] == second["total"] == 2
    assert first["next_offset"] == 1 and second["next_offset"] is None
    assert first["results"][0]["id"] != second["results"][0]["id"]
    assert list_open_sources(store, web_text_since="2027-01-01T00:00:00Z")["total"] == 0
    with pytest.raises(ValueError):
        list_open_sources(store, web_text_since="2026-10-01")
    with pytest.raises(ValueError):
        list_open_sources(store, kind="telegram_archive", web_text_since="2026-10-01T00:00:00Z")


def test_handle_lookup_batches_large_directories_and_ignores_invalid_private_or_native_locators(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    handles = ["sample_" + str(i) for i in range(450)]
    for handle in (handles[0], handles[201], handles[-1]):
        saved_post(store, handle)
    store.register("telegram-web:-1001234", "telegram_web", "fixture", "Unsupported locator")
    results = public_web_observations(store, [*handles, "+privateInvite", "-1001234", "invalid' SQL", None, 42])
    assert len(results) == 3
    assert all(result["saved_inventory"]["posts"] == 1 for result in results.values())


async def test_mcp_reports_saved_facts_across_restart_without_granting_permissions_or_network(settings, tmp_path):
    directory(settings, tmp_path)
    store = EvidenceStore(settings.prepare_data_dir())
    saved_post(store, "recent_channel")
    for _ in range(2):
        async with Client(create_server(settings)) as client:
            result = (
                await client.call_tool(
                    "list_open_telegram_sources",
                    {"kind": "telegram_directory", "web_text_since": "2026-10-01T00:00:00Z"},
                )
            ).structured_content
            assert result["ok"] and result["total"] == 1
            lead = result["results"][0]
            assert lead["metadata"]["collection_enabled"] is False
            assert lead["public_web_permission"] == {"permitted_by_configuration": False, "access_verified": False}
            assert lead["public_web_observation"]["saved_inventory"]["posts_with_text"] == 1
            assert lead["public_web_observation"]["latest_attempt"] is None
            assert not lead["public_web_observation"]["network_read"]

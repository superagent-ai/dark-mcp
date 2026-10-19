import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import Client

from dark_mcp.errors import DataAccessError
from dark_mcp.models import OnionPage
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog, collect_source, page_access_state
from dark_mcp.telegram import TelegramConnector


def test_complete_research_is_packaged_without_drift():
    script = Path(__file__).parents[1] / "scripts/build_source_catalog.py"
    spec = importlib.util.spec_from_file_location("build_source_catalog", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    catalog = SourceCatalog()
    assert catalog.document == module.build_catalog()
    assert catalog.summary()["by_kind"] == {"forum": 17, "onion": 30, "telegram": 125}
    assert len(catalog.sources) == 172
    assert len(catalog.document["web_mirror_context"]) == 8
    # Both temporary previews are retained with their original expiry and numeric locator.
    assert catalog.get("telegram-invite:bradmax-cloud")["locator"] == "-1002380538610"
    assert catalog.get("telegram-invite:observercl0ud")["research"]["preview"]["expires_at"]
    for source in catalog.sources.values():
        assert source["research"]
        assert source["access_state"]


def test_catalog_pagination_filters_and_provenance():
    catalog = SourceCatalog()
    ids, offset = [], 0
    while True:
        page = catalog.list(limit=13, offset=offset)
        ids.extend(source["id"] for source in page["results"])
        offset = page["next_offset"]
        if offset is None:
            break
    assert len(set(ids)) == len(ids) == 172
    assert catalog.list(kind="onion")["total"] == 30
    assert catalog.list(kind="telegram", access_state="unavailable_to_session")["total"] == 29
    assert catalog.list(query="breachdetect")["total"] >= 1
    source = catalog.detail("telegram:breachdetect")["source"]
    assert source["research"]["discovery_references"]
    assert source["research"]["evidence_posts"]
    assert "research" not in catalog.list(limit=1)["results"][0]


def test_catalog_telegram_opt_in_does_not_allow_arbitrary_channels(settings):
    connector = TelegramConnector(settings)
    with pytest.raises(DataAccessError, match="Add this channel"):
        connector._allowed_channel("breachdetect")
    settings.telegram_catalog_sources = True
    assert connector._allowed_channel("@breachdetect") == "breachdetect"
    assert connector._allowed_channel("-1002380538610") == -1002380538610
    with pytest.raises(DataAccessError):
        connector._allowed_channel("unlisted_channel")


async def test_onion_collection_checks_host_and_reports_gate():
    catalog = SourceCatalog()
    source = catalog.list(kind="onion")["results"][0]
    onion, telegram = AsyncMock(), AsyncMock()
    onion.fetch.return_value = OnionPage(url=source["url"], title="Verify Humanity", text="Verify Humanity")
    result = await collect_source(catalog, onion, telegram, source["id"])
    assert result["ok"]
    assert result["access_state"] == "challenge_suspected"
    assert result["historical_access_state"] == source["access_state"]
    other = catalog.list(kind="onion")["results"][1]
    result = await collect_source(catalog, onion, telegram, source["id"], url=other["url"])
    assert result["error"]["code"] == "source_url_mismatch"
    assert onion.fetch.await_count == 1
    telegram.messages.assert_not_awaited()


async def test_telegram_collection_forwards_pagination_and_rate_limit():
    catalog = SourceCatalog()
    onion, telegram = AsyncMock(), AsyncMock()
    telegram.messages.return_value = {"results": [{"id": 99}], "next_before_id": 99}
    result = await collect_source(
        catalog,
        onion,
        telegram,
        "telegram:breachdetect",
        query="Acme",
        limit=2,
        before_id=101,
        since="2026-10-17T00:00:00Z",
    )
    assert result["access_state"] == "posts_returned"
    assert result["next_before_id"] == 99
    telegram.messages.assert_awaited_once_with(
        channel="breachdetect",
        query="Acme",
        limit=2,
        before_id=101,
        since="2026-10-17T00:00:00Z",
    )
    telegram.messages.side_effect = DataAccessError("telegram_rate_limited", "Wait", retry_after=123)
    result = await collect_source(catalog, onion, telegram, "telegram:breachdetect")
    assert not result["ok"]
    assert result["source_id"] == "telegram:breachdetect"
    assert result["error"]["retry_after_seconds"] == 123
    onion.fetch.assert_not_awaited()


async def test_metadata_only_candidate_does_not_join_or_connect():
    onion, telegram = AsyncMock(), AsyncMock()
    result = await collect_source(SourceCatalog(), onion, telegram, "telegram-invite:harmony-cloud")
    assert result["error"]["code"] == "source_access_required"
    telegram.messages.assert_not_awaited()
    onion.fetch.assert_not_awaited()


@pytest.mark.parametrize(
    ("title", "text", "state"),
    [
        ("", "", "empty_document"),
        ("Login", "Log in to access this forum", "login_gate_suspected"),
        ("Forum", "Acme claim", "content_returned_unverified"),
    ],
)
def test_page_state_is_not_an_allegation_verdict(title, text, state):
    assert page_access_state(OnionPage(url="unused", title=title, text=text)) == state


async def test_catalog_tools_over_mcp_preserve_structured_errors(settings):
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("list_sources", {"kind": "onion", "limit": 2})).structured_content
        assert result["total"] == 30
        assert result["next_offset"] == 2
        result = (await client.call_tool("get_source", {"source_id": "telegram:breachdetect"})).structured_content
        assert result["source"]["research"]["evidence_posts"]
        result = (await client.call_tool("collect_source", {"source_id": "telegram:breachdetect"})).structured_content
        assert result["error"]["code"] == "telegram_disabled"
        result = (await client.call_tool("get_source", {"source_id": "missing"})).structured_content
        assert result["error"]["code"] == "source_not_found"
        result = (await client.call_tool("list_sources", {"kind": "invalid"})).structured_content
        assert result["error"]["code"] == "invalid_arguments"
        serialized = json.dumps((await client.call_tool("get_status", {})).structured_content)
        assert "172" in serialized
        assert "telegram_api_hash" not in serialized


def test_vendor_leads_distinguish_collection_examples_from_current_access():
    catalog = SourceCatalog()
    assert catalog.summary()["by_collection_method"]["metadata_only"] == 27
    assert catalog.summary()["vendor_research"]["providers_reviewed"] == 8
    assert catalog.summary()["access_snapshot_dates"]["onion"] == "2026-10-18"
    lead = catalog.detail("forum:damagelib")
    assert lead["source"]["locator"] is None
    assert lead["source"]["checked_at"] is None
    assert lead["source"]["researched_on"] == "2026-10-19"
    assert lead["vendor_research_references"]
    assert any(row["id"] == "flare" for row in lead["provider_research"])
    assert all(not row["current_api_coverage_verified"] for row in lead["source"]["vendor_evidence"])
    assert catalog.list(kind="forum", query="DamageLib")["total"] == 1
    # Vendor research enriches existing onion entries instead of inventing additional working mirrors.
    ramp = next(row for row in catalog.sources.values() if row["family"] == "RAMP (Dark)")
    assert ramp["access_state"] == "timeout"
    assert ramp["checked_at"].startswith("2026-10-18")
    assert ramp["status_context"][0]["state"] == "reported_seized"
    assert catalog.get("forum:leakbase")["status_context"][0]["reference_ids"] == ["leakbase-enforcement"]
    assert not catalog.list(kind="forum", query="BHF")["results"][0]["locator"]
    assert "BFD" in catalog.get("forum:bhf")["research"]["identity_notes"][0]


async def test_vendor_forum_leads_are_discoverable_but_never_sent_to_a_connector(settings):
    catalog, onion, telegram = SourceCatalog(), AsyncMock(), AsyncMock()
    for source in catalog.list(kind="forum", limit=100)["results"]:
        if source["collection_method"] == "metadata_only":
            result = await collect_source(catalog, onion, telegram, source["id"])
            assert result["error"]["code"] == "source_access_required"
    onion.fetch.assert_not_awaited()
    telegram.messages.assert_not_awaited()
    async with Client(create_server(settings)) as client:
        leads = (await client.call_tool("list_sources", {"kind": "forum"})).structured_content
        assert leads["ok"] and leads["total"] == 17
        source = (await client.call_tool("get_source", {"source_id": "forum:darknetarmy"})).structured_content
        assert "not a valid Tor v3" in source["source"]["research"]["identity_notes"][0]
        result = (await client.call_tool("start_collection", {"source_id": "forum:bhf"})).structured_content
        assert result["error"]["code"] == "source_access_required"

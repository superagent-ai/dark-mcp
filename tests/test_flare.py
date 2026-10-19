import json
from unittest.mock import AsyncMock

import pytest
import respx
from mcp import Client
from pydantic import SecretStr

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.flare import API, FlareConnector
from dark_mcp.server import create_server

UID = "forum_post/example/42"


def event(text="Offer alleges superagent.sh access", *, wrapper=True):
    activity = {
        "data": {
            "content": text,
            "posted_at": "2026-10-18T09:00:00+02:00",
            "actor_name": "seller",
            "url": "https://forum.example/thread/42#post42",
            "topic_id": "7",
        },
        "header": {
            "uid": UID,
            "type": "forum_post",
            "title": "Unverified access offer",
            "parent_uid": "forum_topic/example/7",
            "source": "example",
        },
        "metadata": {
            "first_crawled_at": "2026-10-18T07:05:00Z",
            "last_crawled_at": "2026-10-18T08:05:00Z",
            "estimated_created_at": "2026-10-18T06:59:00Z",
        },
    }
    return {"activity": activity} if wrapper else activity


@pytest.fixture
def licensed(settings, monkeypatch):
    settings.flare_api_key = SecretStr("fixture-key-not-real")
    settings.flare_tenant_id = 123
    monkeypatch.setattr("dark_mcp.flare.asyncio.sleep", AsyncMock())
    return settings


def token_route():
    return respx.post(API + "/tokens/generate").respond(json={"token": "fixture-token-not-real"})


@respx.mock
async def test_real_mcp_search_read_restart_refresh_and_exact_replay(licensed):
    auth = token_route()
    search = respx.post(API + "/firework/v4/events/global/_search").respond(
        json={
            "items": [
                {
                    "metadata": {"uid": UID, "type": "forum_post"},
                    "highlights": {"content": ["superagent.sh alleged access"]},
                }
            ],
            "next": "opaque-cursor",
        }
    )
    read = respx.get(API + "/firework/v2/activities/", params={"uid": UID}).respond(json=event())
    async with Client(create_server(licensed)) as client:
        hit = (await client.call_tool("search_flare_events", {"query": "superagent.sh"})).structured_content
        assert hit["ok"] and hit["next_cursor"]
        value = (await client.call_tool("read_flare_event", {"uid": UID})).structured_content
        assert value["ok"]
        e = value["event"]
        assert e["text"].startswith("Offer alleges") and e["record_scope"] == "event_body"
        assert e["published_at"] == "2026-10-18T07:00:00+00:00"
        assert e["provider_first_seen_at"] == "2026-10-18T07:05:00+00:00"
        assert e["provider_estimated_created_at"] == "2026-10-18T06:59:00+00:00"
        assert e["collected_at"] != e["provider_first_seen_at"]
        assert e["parent_uid"] == "forum_topic/example/7"
        original = e["evidence"]
        assert original["evidence_id"] != hit["results"][0]["evidence"]["evidence_id"]
        assert "fixture-key" not in json.dumps((await client.call_tool("get_status", {})).structured_content)
    assert auth.call_count == 1
    assert json.loads(auth.calls[0].request.content) == {"tenant_id": 123}
    assert auth.calls[0].request.headers["Authorization"] == "fixture-key-not-real"
    assert search.calls[0].request.headers["Authorization"] == "Bearer fixture-token-not-real"
    read.respond(json=event("Updated superagent.sh allegation"))
    async with Client(create_server(licensed)) as client:
        updated = (
            await client.call_tool("refresh_evidence", {"evidence_id": original["evidence_id"]})
        ).structured_content
        assert updated["event"]["evidence"]["change_type"] == "edited"
        replay = (
            await client.call_tool(
                "get_evidence", {"evidence_id": original["evidence_id"], "revision_id": original["revision_id"]}
            )
        ).structured_content
        assert replay["evidence"]["snapshot"]["text"] == "Offer alleges superagent.sh access"
        hits = (
            await client.call_tool(
                "search_evidence", {"query": "superagent.sh", "kind": "provider", "include_history": False}
            )
        ).structured_content
        assert len(hits["results"]) == 2


@respx.mock
async def test_cursor_continues_empty_page_and_rejects_changed_search_before_network(licensed):
    token_route()
    route = respx.post(API + "/firework/v4/events/global/_search").respond(json={"items": [], "next": "continue"})
    connector = FlareConnector(licensed, EvidenceStore(licensed.prepare_data_dir()))
    first = await connector.search("acme.example", types=["forum_post"], since="2026-10-01T00:00:00Z")
    assert first["results"] == [] and first["next_cursor"]
    with pytest.raises(ValueError, match="same query"):
        await connector.search("different.example", cursor=first["next_cursor"])
    assert route.call_count == 1
    await connector.search(
        "acme.example", types=["forum_post"], since="2026-10-01T00:00:00Z", cursor=first["next_cursor"]
    )
    assert json.loads(route.calls.last.request.content)["from"] == "continue"


@respx.mock
async def test_rate_limit_survives_new_connector_without_more_requests(licensed):
    token_route()
    respx.get(API + "/firework/v2/activities/", params={"uid": UID}).respond(429, headers={"Retry-After": "3600"})
    store = EvidenceStore(licensed.prepare_data_dir())
    with pytest.raises(DataAccessError) as err:
        await FlareConnector(licensed, store).read(UID)
    assert err.value.retry_after == 3600
    count = len(respx.calls)
    with pytest.raises(DataAccessError) as err:
        await FlareConnector(licensed, store).read(UID)
    assert err.value.retry_after > 3500 and len(respx.calls) == count


@pytest.mark.parametrize("status", [302, 401, 403, 404, 500])
@respx.mock
async def test_provider_errors_do_not_follow_redirects_or_expose_response_body(licensed, status):
    token_route()
    respx.get(API + "/firework/v2/activities/", params={"uid": UID}).respond(
        status, text="fixture-secret-in-error", headers={"Location": "http://127.0.0.1/"}
    )
    with pytest.raises(DataAccessError) as err:
        await FlareConnector(licensed, EvidenceStore(licensed.prepare_data_dir())).read(UID)
    assert "fixture-secret" not in json.dumps(err.value.as_dict())
    assert len(respx.calls) == 2


@respx.mock
async def test_missing_credentials_and_malformed_search_do_not_masquerade_as_no_matches(settings, licensed):
    store = EvidenceStore(licensed.prepare_data_dir())
    licensed.flare_api_key = None
    with pytest.raises(DataAccessError, match="DARK_MCP_FLARE_API_KEY"):
        await FlareConnector(licensed, store).search("acme.example")
    assert not respx.calls
    licensed.flare_api_key = SecretStr("fixture-key-not-real")
    token_route()
    respx.post(API + "/firework/v4/events/global/_search").respond(json={"items": [{"metadata": {}}]})
    with pytest.raises(DataAccessError, match="no UID"):
        await FlareConnector(licensed, store).search("acme.example")
    assert store.search("acme.example", kind="provider")["results"] == []


@respx.mock
async def test_flat_event_preview_and_unknown_time_remain_explicit(licensed):
    token_route()
    value = event(wrapper=False)
    value["data"] = {"posted_at": "2026-10-18T09:00:00"}  # Source timezone is unknown.
    value["header"]["content_preview"] = "Partial superagent.sh mention"
    respx.get(API + "/firework/v2/activities/", params={"uid": UID}).respond(json=value)
    result = await FlareConnector(licensed, EvidenceStore(licensed.prepare_data_dir())).read(UID)
    assert result["event"]["record_scope"] == "preview"
    assert result["event"]["published_at"] is None
    assert result["event"]["published_at_raw"] == "2026-10-18T09:00:00"
    assert result["event"]["provider_first_seen_at"]


@respx.mock
async def test_provider_bound_and_wrong_uid_are_explicit_failures(licensed):
    token_route()
    route = respx.get(API + "/firework/v2/activities/", params={"uid": UID})
    value = event()
    value["activity"]["header"]["uid"] = "forum_post/other/9"
    route.respond(json=value)
    connector = FlareConnector(licensed, EvidenceStore(licensed.prepare_data_dir()))
    with pytest.raises(DataAccessError, match="different event UID"):
        await connector.read(UID)
    licensed.max_response_bytes = 1024
    route.respond(text="x" * 1025, headers={"Content-Type": "application/json"})
    with pytest.raises(DataAccessError, match="size bound"):
        await connector.read(UID)


@respx.mock
async def test_refresh_never_reads_stored_event_through_a_different_tenant(licensed):
    token_route()
    respx.get(API + "/firework/v2/activities/", params={"uid": UID}).respond(json=event())
    async with Client(create_server(licensed)) as client:
        original = (await client.call_tool("read_flare_event", {"uid": UID})).structured_content
    count = len(respx.calls)
    licensed.flare_tenant_id = 456
    async with Client(create_server(licensed)) as client:
        result = (
            await client.call_tool("refresh_evidence", {"evidence_id": original["event"]["evidence"]["evidence_id"]})
        ).structured_content
    assert result["error"]["code"] == "provider_scope_mismatch" and len(respx.calls) == count

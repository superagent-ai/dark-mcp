import json
import os
import sys

from mcp import Client, StdioServerParameters

from dark_mcp import __version__
from dark_mcp.server import create_server


async def test_real_mcp_initialization_tools_and_structured_errors(settings):
    async with Client(create_server(settings)) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names == {
            "get_status",
            "search_flare_events",
            "read_flare_event",
            "list_sources",
            "get_source",
            "collect_source",
            "start_collection",
            "collect_next",
            "get_collection",
            "list_collections",
            "search_evidence",
            "get_evidence",
            "get_evidence_history",
            "list_evidence_changes",
            "get_exact_duplicates",
            "refresh_evidence",
            "get_source_health",
            "save_company_profile",
            "get_company_profile",
            "add_evidence_relation",
            "get_evidence_relations",
            "read_telegram_thread",
            "sync_telegram_changes",
            "list_forum_threads",
            "read_forum_thread",
            "fetch_onion",
            "crawl_onion",
            "search_onion",
            "search_onion_cache",
            "search_telegram_channels",
            "read_telegram_channel",
            "search_telegram_messages",
            "search_telegram_posts",
        }
        result = await client.call_tool("search_onion", {"query": "Acme"})
        assert result.structured_content["error"]["code"] == "search_not_configured"
        result = await client.call_tool("read_telegram_channel", {"channel": "research"})
        assert result.structured_content["error"]["code"] == "telegram_disabled"
        result = await client.call_tool("fetch_onion", {"url": "http://127.0.0.1/"})
        assert result.structured_content["error"]["code"] == "invalid_onion_url"
        serialized = json.dumps((await client.call_tool("get_status", {})).structured_content)
        assert "telegram_api_hash" not in serialized
        assert "session_path" not in serialized


async def test_stdio_entry_point(tmp_path):
    env = {**os.environ, "DARK_MCP_DATA_DIR": str(tmp_path), "DARK_MCP_TELEGRAM_ENABLED": "false"}
    # The explicit src path also permits running directly from an uninstalled checkout.
    env["PYTHONPATH"] = str(__import__("pathlib").Path(__file__).parents[1] / "src")
    params = StdioServerParameters(command=sys.executable, args=["-m", "dark_mcp", "serve"], env=env)
    async with Client(params) as client:
        result = await client.call_tool("get_status", {})
        assert result.structured_content["ok"] is True
        assert result.structured_content["indexed_onion_pages"] == 0


async def test_stdio_evidence_migration_and_profile_survive_process_restart(tmp_path, onion):
    from pathlib import Path

    from dark_mcp.index import OnionIndex
    from dark_mcp.models import OnionPage

    OnionIndex(tmp_path).put(
        OnionPage(
            url=onion, title="Fixture", text="Acme.example synthetic evidence", collected_at="2026-10-01T00:00:00Z"
        )
    )
    env = {
        **os.environ,
        "DARK_MCP_DATA_DIR": str(tmp_path),
        "DARK_MCP_TELEGRAM_ENABLED": "false",
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "dark_mcp", "serve"], env=env)
    async with Client(params) as client:
        result = (await client.call_tool("search_evidence", {"query": "acme.example"})).structured_content
        assert result["ok"] and len(result["results"]) == 1
        evidence_id = result["results"][0]["evidence_id"]
        result = (
            await client.call_tool(
                "save_company_profile", {"profile_id": "fixture", "name": "Acme", "identifiers": ["acme.example"]}
            )
        ).structured_content
        assert result["ok"]
    async with Client(params) as client:
        result = (await client.call_tool("get_evidence", {"evidence_id": evidence_id})).structured_content
        assert result["evidence"]["snapshot"]["text"] == "Acme.example synthetic evidence"
        profile = (await client.call_tool("get_company_profile", {"profile_id": "fixture"})).structured_content
        assert profile["document"]["identifiers"] == ["acme.example"]
        status = (await client.call_tool("get_status", {})).structured_content
        assert status["evidence_store"]["observations"] == 1
        assert status["version"] == __version__

import json
import os
import sys

from mcp import Client, StdioServerParameters

from dark_mcp.server import create_server


async def test_real_mcp_initialization_tools_and_structured_errors(settings):
    async with Client(create_server(settings)) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names == {
            "get_status",
            "list_sources",
            "get_source",
            "collect_source",
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

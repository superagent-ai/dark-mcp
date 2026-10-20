import base64
import gzip
import json
from pathlib import Path

import httpx
import pytest

from dark_mcp.client import AILClient, AILError
from dark_mcp.config import Settings
from dark_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings():
    return Settings(ail_url="https://ail.test", ail_api_key="local-test-token", _env_file=None)


async def test_search_uses_ail_and_keeps_source_data(settings, respx_mock):
    evidence = {
        "results": [{"type": "item", "id": "crawled/2026/10/20/example.gz", "result": "untrusted text"}],
        "pagination": {"total": 1},
    }
    route = respx_mock.post("https://ail.test/api/v1/dark-mcp/search").mock(
        return_value=httpx.Response(200, json=evidence)
    )
    result = await create_server(settings).call_tool("ail_search", {"query": "superagent.sh"})
    assert result.structured_content["data"] == evidence
    assert json.loads(route.calls.last.request.content)["search"] == "superagent.sh"
    assert json.loads(route.calls.last.request.content)["substring"] is False
    assert route.calls.last.request.headers["X-AIL-AUTH"] == "local-test-token"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
        "http://169.254.169.254",
        "file:///etc/passwd",
        "https://user:secret@example.com",
    ],
)
async def test_crawl_rejects_nonpublic_or_credential_urls(settings, respx_mock, url):
    result = await create_server(settings).call_tool("ail_crawl_url", {"url": url})
    assert result.structured_content["ok"] is False
    assert not respx_mock.calls


async def test_crawl_is_explicit_bounded_and_async(settings, respx_mock):
    route = respx_mock.post("https://ail.test/api/v1/add/crawler/task").mock(
        return_value=httpx.Response(200, json={"uuid": "task"})
    )
    result = await create_server(settings).call_tool("ail_crawl_url", {"url": "https://example.com/thread/1"})
    assert result.structured_content["data"] == {"uuid": "task"}
    assert json.loads(route.calls.last.request.content) == {
        "url": "https://example.com/thread/1",
        "depth": 0,
        "har": False,
        "screenshot": False,
        "javascript": False,
    }


async def test_import_uses_genuine_feeder_contract(settings, respx_mock):
    route = respx_mock.post("https://ail.test/api/v1/import/json/item").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    await create_server(settings).call_tool("ail_import_text", {"text": "operator test control"})
    payload = json.loads(route.calls.last.request.content)
    assert gzip.decompress(base64.b64decode(payload["data"])) == b"operator test control"
    assert payload["source"] == "operator-submission"
    assert payload["meta"] == {}


async def test_operator_import_cannot_select_native_telegram_feeder(settings, respx_mock):
    route = respx_mock.post("https://ail.test/api/v1/import/json/item").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    await create_server(settings).call_tool("ail_import_text", {"text": "caller text", "source": "telegram"})
    assert json.loads(route.calls.last.request.content)["source"] == "operator-telegram"


@pytest.mark.parametrize("gid", ["item::../../etc/passwd", "item::/etc/passwd", "item::", "missing-id"])
async def test_object_paths_cannot_escape_native_store(settings, respx_mock, gid):
    result = await create_server(settings).call_tool("ail_get_object", {"global_id": gid})
    assert result.structured_content["ok"] is False
    assert not respx_mock.calls


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(302, headers={"Location": "https://other.test"}), "ail_redirect_rejected"),
        (httpx.Response(401, json={"secret": "must-never-echo"}), "ail_authentication_failed"),
        (httpx.Response(200, text="<html>sign in</html>"), "ail_invalid_response"),
        (httpx.Response(200, json={"status": "error", "reason": "must-never-echo"}), "ail_backend_error"),
    ],
)
async def test_errors_are_honest_and_do_not_echo_secrets(settings, respx_mock, response, code):
    respx_mock.get("https://ail.test/api/v1/dark-mcp/status").mock(return_value=response)
    result = await create_server(settings).call_tool("ail_status", {})
    assert result.structured_content["error"]["code"] == code
    assert "must-never-echo" not in str(result)


async def test_response_size_bound(settings, respx_mock):
    settings.max_response_bytes = 1024
    respx_mock.get("https://ail.test/api/v1/ping").mock(return_value=httpx.Response(200, content=b"x" * 1025))
    with pytest.raises(AILError, match="byte bound"):
        await AILClient(settings).request("GET", "api/v1/ping")


async def test_missing_configuration_fails_explicitly(tmp_path):
    settings = Settings(ail_api_key_file=tmp_path / "missing", _env_file=None)
    result = await create_server(settings).call_tool("ail_status", {})
    assert result.structured_content["error"]["code"] == "ail_not_configured"


async def test_protocol_stdio_lists_tools():
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dark_mcp", "serve"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {tool.name for tool in result.tools}
            assert {"ail_search", "ail_get_object", "ail_crawl_url", "ail_status"} <= names
            search = next(tool for tool in result.tools if tool.name == "ail_search")
            assert "substring" not in search.input_schema.get("properties", {})
            assert not any("password" in str(tool.input_schema).lower() for tool in result.tools)

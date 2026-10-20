"""Verify retained original-source objects through a fresh MCP stdio connection."""

import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/research/ail-validation-2026-10-20"


async def main():
    os.umask(0o077)
    original = json.loads((OUT / "receipts.json").read_text())
    report = {"checked_at": datetime.now(UTC).isoformat(), "calls": [], "sources": [], "queries": {}}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dark_mcp", "serve"],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        report["tools"] = [tool.name for tool in (await session.list_tools()).tools]

        async def call(name, args):
            result = (await session.call_tool(name, args)).structured_content
            report["calls"].append({"tool": name, "arguments": args, "result": result})
            assert result and result.get("ok"), f"Failed MCP tool: {name}"
            return result["data"]

        for _ in range(20):
            result = (await session.call_tool("ail_status", {})).structured_content
            if result and result.get("ok"):
                break
            await asyncio.sleep(2)
        else:
            raise RuntimeError("AIL did not restart")
        report["status"] = await call("ail_status", {})
        assert report["status"]["search"]["health"] == "available"
        for capture in original["captures"]:
            assert len(capture["objects"]) == 1, "Source capture missing or ambiguous"
            gid = capture["objects"][0]["data"]["global_id"]
            obj = await call("ail_get_object", {"global_id": gid})
            assert obj.get("url") == capture["url"], "Source URL mismatch"
            body = obj["content"]
            assert len(body) > 1000
            report["sources"].append(
                {
                    "url": obj["url"],
                    "global_id": gid,
                    "body_chars": len(body),
                    "returned_content_sha256": hashlib.sha256(body.encode()).hexdigest(),
                    "telegram_message_text_blocks": len(re.findall(r'class="[^\"]*tgme_widget_message_text', body)),
                    "opencode_literal_mentions": len(re.findall("opencode", body, re.I)),
                    "superagent_domain_literal_mentions": len(re.findall(re.escape("superagent.sh"), body, re.I)),
                }
            )
        for query in ("opencode", "opencode.ai", "superagent.sh", "superagent"):
            result = await call("ail_search", {"query": query})
            rows = []
            for hit in result.get("results", []):
                obj = await call("ail_get_object", {"global_id": hit["global_id"]})
                body = obj.get("content", "")
                rows.append(
                    {
                        "global_id": hit["global_id"],
                        "url": obj.get("url"),
                        "literal_match": query.casefold() in body.casefold(),
                        "source": obj.get("source"),
                    }
                )
            report["queries"][query] = {"results": rows, "pagination": result.get("pagination")}
        assert any(row["literal_match"] for row in report["queries"]["opencode"]["results"])
        report["chat_instances"] = await call("ail_chat_instances", {})
        # A plain imported item is stored but is not an indexed crawled/chat/forum object upstream.
        marker = original.get("control", {}).get("marker")
        report["operator_control"] = {
            "full_text_indexed": False,
            "reason": "Upstream excludes ordinary non-crawled items",
        }
        if marker:
            for path in (ROOT / "data/ail/objects/PASTES/operator-e2e-control").glob("**/*.gz"):
                gid = "item::" + str(path.relative_to(ROOT / "data/ail/objects/PASTES"))
                obj = await call("ail_get_object", {"global_id": gid})
                if marker in obj.get("content", ""):
                    report["operator_control"].update(ingestion_and_object_read_pass=True, global_id=gid)
                    break
            assert report["operator_control"].get("ingestion_and_object_read_pass")
    async with httpx.AsyncClient() as client:
        response = await client.get("http://127.0.0.1:7000/api/v1/dark-mcp/status")
        assert response.status_code == 401
        report["unauthenticated_status"] = response.status_code
    (OUT / "verified.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "source_pages": len(report["sources"]),
                "telegram_text_blocks": sum(row["telegram_message_text_blocks"] for row in report["sources"]),
                "queries": {q: len(v["results"]) for q, v in report["queries"].items()},
                "tools": len(report["tools"]),
                "unauthenticated_status": 401,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

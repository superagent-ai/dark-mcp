"""Bounded live MCP→AIL validation. Writes private receipts; never treats controls as source findings."""

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    "http://spear4h2potiyk43oilufhuq2jqbioyaizcs7xttlyw75s5ykwxpxlid.onion/Thread-Buying-LLM-Cloud-API-Keys-Collection-%E2%80%94-OpenAI-Claude-Gemini-AWS-Azure-More",
    "https://t.me/s/ThreatMarket",
    "https://t.me/s/breaches",
    "https://t.me/s/vxunderground",
]


async def main():
    os.umask(0o077)
    out = ROOT / "data/research/ail-validation-2026-10-20"
    out.mkdir(parents=True, exist_ok=True)
    receipt = {"started_at": datetime.now(UTC).isoformat(), "calls": [], "captures": [], "queries": {}}

    def save():
        (out / "receipts.json").write_text(json.dumps(receipt, indent=2))

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dark_mcp", "serve"],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()

        async def call(name, args):
            result = await session.call_tool(name, args)
            data = result.structured_content
            receipt["calls"].append(
                {"at": datetime.now(UTC).isoformat(), "tool": name, "arguments": args, "result": data}
            )
            save()
            return data

        status = await call("ail_status", {})
        if not status or not status.get("ok") or status["data"]["search"]["health"] != "available":
            raise RuntimeError("AIL index unavailable; see private receipt")
        for url in SOURCES:
            baseline = await call("ail_domain_captures", {"domain": urlsplit(url).hostname})
            previous_ids = {item["global_id"] for item in baseline.get("data", {}).get("items", [])}
            started = time.time()
            queued = await call("ail_crawl_url", {"url": url, "depth": 0, "javascript": False})
            capture = {"url": url, "requested_at": datetime.now(UTC).isoformat(), "queued": queued, "objects": []}
            receipt["captures"].append(capture)
            save()
            if not queued.get("ok"):
                continue
            deadline = time.monotonic() + 210
            while time.monotonic() < deadline:
                history = await call("ail_domain_captures", {"domain": urlsplit(url).hostname})
                if history.get("ok"):
                    native = history["data"]
                    current = [
                        item
                        for item in native.get("items", [])
                        if item.get("collected_epoch", 0) >= int(started) and item["global_id"] not in previous_ids
                    ]
                    if current:
                        for item in current:
                            obj = await call("ail_get_object", {"global_id": item["global_id"]})
                            capture["objects"].append(obj)
                        break
                    if any(
                        entry["epoch"] >= int(started) and not entry.get("status")
                        for entry in native.get("history", [])
                    ):
                        capture["native_failure"] = True
                        break
                await asyncio.sleep(5)
            capture["finished_at"] = datetime.now(UTC).isoformat()
            save()
            print(
                f"Source checked: {urlsplit(url).hostname}; saved root objects: {len(capture['objects'])}", flush=True
            )
        # Allow upstream async batching to settle, bounded and visible through status.
        await asyncio.sleep(8)
        for query in ("opencode", "opencode.ai", "superagent.sh", "superagent"):
            result = await call("ail_search", {"query": query})
            receipt["queries"][query] = result
            if result.get("ok"):
                for hit in result["data"].get("results", []):
                    await call("ail_get_object", {"global_id": hit["global_id"]})
        # The known source mention is the positive search control; no synthetic threat data is indexed.
        receipt["final_status"] = await call("ail_status", {})
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        save()
        print("Live validation receipts saved locally; source findings require content review.")


if __name__ == "__main__":
    asyncio.run(main())

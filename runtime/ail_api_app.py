# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authenticated JSON access to upstream AIL UI libraries, using AIL's own storage/indexes."""

import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from blueprints.api_rest import get_auth_from_header, token_required
from flask import jsonify, request
from Flask_server import app
from lib import ail_queues, ail_users, crawlers, search_engine
from lib.objects import Domains, Items, ail_objects


@app.get("/api/v1/dark-mcp/status")
@token_required("user")
def dark_mcp_status():
    return jsonify(
        {
            "backend": "AIL Framework",
            "compatibility_api": 1,
            "upstream": json.loads(Path(__file__).with_name("upstream.json").read_text()),
            "search": search_engine.get_meilisearch_status(),
            "crawler": {
                "lacus_configured": bool(crawlers.get_lacus_url()),
                "connected": crawlers.is_lacus_connected(),
            },
            "workers": ail_queues.get_modules_queues_stats(),
        }
    )


@app.post("/api/v1/dark-mcp/search")
@token_required("user")
def dark_mcp_search():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("search"), str) or not 1 <= len(data["search"]) <= 500:
        return jsonify({"status": "error", "reason": "Invalid search"}), 400
    if not isinstance(data.get("indexes", []), list):
        return jsonify({"status": "error", "reason": "Invalid indexes"}), 400
    if data.get("substring"):
        return jsonify({"status": "error", "reason": "Experimental substring mode failed live validation"}), 400
    data = {key: data[key] for key in ("search", "indexes", "page", "sort", "substring") if key in data}
    data["user_id"] = ail_users.get_token_user(get_auth_from_header())
    result, status = search_engine.api_search(data)
    if status != 200:
        return jsonify(result), status
    results, pagination = result
    for result in results:
        result["global_id"] = f"{result['type']}:{result.get('subtype', '')}:{result['id']}"
    return jsonify({"results": results, "pagination": pagination})


@app.get("/api/v1/dark-mcp/object")
@token_required("user")
def dark_mcp_object():
    gid = request.args.get("gid", "")
    parts = gid.split(":", 2)
    if (
        len(parts) != 3
        or not parts[0]
        or not parts[2]
        or parts[2].startswith("/")
        or any(p in {"..", "."} for p in parts[2].split("/"))
    ):
        return jsonify({"status": "error", "reason": "Invalid global ID"}), 400
    result, status = ail_objects.api_get_object_global_id(gid)
    if status == 200:
        result["global_id"] = gid
        if parts[0] == "item":
            result.update(Items.Item(parts[2]).get_meta(options={"content", "url", "crawler", "parent"}))
    return jsonify(result), status


@app.get("/api/v1/dark-mcp/domain")
@token_required("user")
def dark_mcp_domain():
    domain_id = request.args.get("domain", "")
    if not domain_id or len(domain_id) > 253 or urlsplit("http://" + domain_id).hostname != domain_id:
        return jsonify({"status": "error", "reason": "Use a domain hostname"}), 400
    domain = Domains.Domain(domain_id)
    if not domain.exists():
        return jsonify({"domain": domain_id, "history": [], "items": []})
    # Upstream history is retained even after temporary crawler tasks are removed.
    history = domain.get_history(status=True, root=True)
    items = []
    for entry in history[-20:]:
        if entry.get("root"):
            item = Items.Item(entry["root"])
            if item.exists():
                meta = item.get_meta(options={"url", "crawler"})
                meta.update(global_id=item.get_global_id(), collected_epoch=entry["epoch"])
                items.append(meta)
    return jsonify(
        {"domain": domain_id, "history": history[-20:], "items": items, "limit": 20, "total_captures": len(history)}
    )


@app.get("/api/v1/dark-mcp/crawl")
@token_required("user")
def dark_mcp_crawl():
    try:
        task_id = str(UUID(request.args.get("uuid", "")))
    except ValueError:
        return jsonify({"status": "error", "reason": "Invalid UUID"}), 400
    task = crawlers.CrawlerTask(task_id)
    if not task.exists():
        return jsonify({"status": "error", "reason": "Unknown task"}), 404
    capture = task.get_capture()
    return jsonify(
        {
            "uuid": task_id,
            "url": task.get_url(),
            "queued": task.is_in_queue(),
            "status": task.get_status(),
            "capture": capture,
            "capture_status": crawlers.CrawlerCapture(capture).get_status() if capture else None,
        }
    )

"""Build the distributable registry from the complete, dated research snapshots."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_catalog() -> dict:
    telegram = json.loads((ROOT / "sources/telegram-research-2026-10-18.json").read_text())
    onion = json.loads((ROOT / "sources/onion-research-2026-10-18.json").read_text())
    sources = []
    for row in telegram["public_channels"]:
        sources.append(
            {
                "id": "telegram:" + row["requested_handle"].lower(),
                "kind": "telegram",
                "name": row["observed_title"] or row["requested_handle"],
                "locator": row["requested_handle"],
                "url": row["source_url"],
                "family": row["coverage_cluster"],
                "source_class": row["source_class"],
                "access_state": row["status"],
                "checked_at": row["checked_at"],
                "assessment": row["assessment"],
                "collection_method": "telegram_channel",
                "research": row,
            }
        )
    previews = {row["channel_id"]: row for row in telegram["conditional_previews"]}
    for row in telegram["invite_metadata"]:
        preview = next(
            (
                p
                for p in previews.values()
                if p["name"].lower().replace(" ", "") == (row.get("title") or "").lower().replace(" ", "")
            ),
            None,
        )
        channel = row.get("channel") or {}
        locator = channel.get("username") or channel.get("id") or (preview or {}).get("channel_id")
        slug = re.sub(r"[^a-z0-9]+", "-", row["label"].lower()).strip("-")
        sources.append(
            {
                "id": "telegram-invite:" + slug,
                "kind": "telegram",
                "name": row.get("title") or row["label"],
                "locator": locator,
                "url": channel.get("url"),
                "family": slug,
                "source_class": "Invite-derived candidate",
                "access_state": (
                    "temporary_preview" if row.get("result_type") == "ChatInvitePeek" else "access_unconfirmed"
                ),
                "checked_at": row.get("collected_at"),
                "assessment": (preview or {}).get(
                    "assessment", "Invite metadata only; history access and current identity are unverified."
                ),
                "collection_method": "telegram_channel" if locator else "metadata_only",
                "research": {**row, "preview": preview},
            }
        )
    for row in onion["sources"]:
        digest = hashlib.sha256(row["url"].encode()).hexdigest()[:12]
        urls = list(dict.fromkeys([row["url"], *(check["url"] for check in row["depth_checks"])]))
        sources.append(
            {
                "id": "onion:" + digest,
                "kind": "onion",
                "name": row["name"],
                "locator": row["url"],
                "url": row["url"],
                "family": row["source_family"],
                "source_class": row["source_class"],
                "access_state": row["access_state"],
                "checked_at": row["checked_at"],
                "assessment": row["assessment"],
                "collection_method": "onion_page",
                "collection_urls": urls,
                "research": row,
            }
        )
    vendor = json.loads((ROOT / "sources/vendor-source-research-2026-10-19.json").read_text())
    for row in vendor["forums"]:
        matches = [
            source
            for source in sources
            if source["kind"] == "onion" and source["family"] in row["existing_onion_families"]
        ]
        if row["existing_onion_families"] and not matches:
            raise ValueError(f"Unmatched existing forum family: {row['slug']}")
        for source in matches:
            # Preserve the original access check and timestamps; vendor research is not a new fetch.
            source["research"] = {**source["research"], "vendor_research": row}
            source["vendor_evidence"] = row["vendor_evidence"]
            source["status_context"] = row["status_context"]
            source["research_priority"] = row["research_priority"]
        if not matches:
            sources.append(
                {
                    "id": "forum:" + row["slug"],
                    "kind": "forum",
                    "name": row["name"],
                    "locator": None,
                    "url": None,
                    "family": row["slug"],
                    "source_class": row["source_class"],
                    "access_state": row["access_state"],
                    "checked_at": None,
                    "researched_on": row["researched_on"],
                    "assessment": row["rationale"] + " Metadata only; no collector or current access verified.",
                    "research_priority": row["research_priority"],
                    "vendor_evidence": row["vendor_evidence"],
                    "status_context": row["status_context"],
                    "collection_method": "metadata_only",
                    "research": row,
                }
            )
    access = json.loads((ROOT / "sources/forum-access-2026-10-19.json").read_text())
    for row in access["sources"]:
        existing = next((source for source in sources if source["id"] == row["id"]), None)
        if existing is None:
            existing = {"id": row["id"], "kind": "forum", "source_class": "forum", "research": {}}
            sources.append(existing)
        existing.update(
            {
                **row,
                "url": row["locator"],
                "collection_method": "forum_page",
                "access_state": row["access_state"],
                "checked_at": row.get("checked_at", access["checked_at"]),
                "research": {**existing["research"], "forum_access": row},
            }
        )
    priority = json.loads((ROOT / "sources/priority-forum-access-2026-10-19.json").read_text())
    for row in priority["sources"]:
        existing = next(source for source in sources if source["id"] == row["source_id"])
        existing.update(access_state=row["access_state"], checked_at=row["checked_at"], assessment=row["assessment"])
        existing["research"]["latest_priority_access_check"] = row
    tor_access = json.loads((ROOT / "sources/onion-forum-access-2026-10-19.json").read_text())
    for row in tor_access["sources"]:
        existing = next(source for source in sources if source["id"] == row["id"])
        existing.update(
            {
                **row,
                "collection_method": "forum_page",
                "checked_at": row.get("checked_at", tor_access["checked_at"]),
                "research": {**existing["research"], "tor_forum_access": row},
            }
        )
    return {
        "schema_version": 2,
        "snapshot_date": "2026-10-19",
        "access_snapshot_dates": {
            "telegram": "2026-10-18",
            "onion": "2026-10-18",
            "forum": "2026-10-19",
            "priority_forums": "2026-10-19",
        },
        "sources": sorted(sources, key=lambda source: source["id"]),
        "references": {
            "telegram": telegram["research_references"],
            "onion": onion["research_references"],
            "forum": vendor["references"],
        },
        "vendor_research": {
            **{key: value for key, value in vendor.items() if key != "forums"},
            "forum_families_reviewed": len(vendor["forums"]),
        },
        "web_mirror_context": onion["public_web_mirror_checks"],
    }


if __name__ == "__main__":
    target = ROOT / "src/dark_mcp/resources/source-catalog.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_catalog(), indent=2, ensure_ascii=False) + "\n")

"""Local collection facts for public Telegram handles, separate from directory assertions."""

import json
import re


def public_web_observations(store, handles: list[str]) -> dict:
    source_ids = sorted(
        {
            "telegram-web:" + handle.lower()
            for handle in handles
            if isinstance(handle, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", handle)
        }
    )
    results = {}
    with store.connect() as db:
        db.execute("BEGIN")
        for start in range(0, len(source_ids), 200):
            batch = source_ids[start : start + 200]
            placeholders = ",".join("?" for _ in batch)
            registered = db.execute(
                f"SELECT id FROM sources WHERE kind='telegram_web' AND id IN ({placeholders})", batch
            ).fetchall()
            for row in registered:
                results[row["id"]] = {
                    "source_id": row["id"],
                    "match_basis": "same_public_handle_unverified",
                    "network_read": False,
                    "current_access_verified": False,
                    "latest_attempt": None,
                    "saved_inventory": {
                        "posts": 0,
                        "posts_with_text": 0,
                        "newest_reported_publication": None,
                        "newest_text_publication": None,
                        "first_post_recorded_at": None,
                        "last_positive_post_recorded_at": None,
                        "unavailable_or_deleted_posts": 0,
                    },
                }
            attempts = db.execute(
                "SELECT source_id,id,started_at,finished_at,state,detail FROM ("
                "SELECT *,row_number() OVER (PARTITION BY source_id ORDER BY started_at DESC,rowid DESC) n "
                f"FROM attempts WHERE source_id IN ({placeholders})) WHERE n=1",
                batch,
            ).fetchall()
            for row in attempts:
                if row["source_id"] in results:
                    result = dict(row)
                    result.pop("source_id")
                    result["detail"] = json.loads(result["detail"])
                    results[row["source_id"]]["latest_attempt"] = result
            inventory = db.execute(
                "SELECT i.source_id,count(*) posts,"
                "sum(CASE WHEN trim(r.text)<>'' THEN 1 ELSE 0 END) posts_with_text,"
                "max(r.published_at) newest_reported_publication,"
                "max(CASE WHEN trim(r.text)<>'' THEN r.published_at END) newest_text_publication,"
                "min(i.first_seen) first_post_recorded_at,max(i.last_seen) last_positive_post_recorded_at,"
                "sum(CASE WHEN i.availability<>'observed' THEN 1 ELSE 0 END) unavailable_or_deleted_posts "
                "FROM items i JOIN revisions r ON r.id=i.latest_revision_id "
                f"WHERE i.kind='telegram_web' AND i.source_id IN ({placeholders}) GROUP BY i.source_id",
                batch,
            ).fetchall()
            for row in inventory:
                if row["source_id"] in results:
                    results[row["source_id"]]["saved_inventory"] = {
                        key: value for key, value in dict(row).items() if key != "source_id"
                    }
    return results

import json

import pytest
from mcp import Client

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.server import create_server


def seed(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    store.register("fixture", "telegram", "fixture", "Synthetic relation source")
    records = [
        store.observe("fixture", "telegram", str(i), "https://t.me/fixture/" + str(i), {"text": "Original " + str(i)})
        for i in range(3)
    ]
    return store, records


def change(store, key, text):
    return store.observe("fixture", "telegram", str(key), "https://t.me/fixture/" + str(key), {"text": text})


def test_latest_save_binds_all_endpoints_and_support_without_drifting_after_edits(settings):
    store, rows = seed(settings)
    a, b, c = [r["evidence_id"] for r in rows]
    saved = store.relate(a, b, "possible_repost", [a, c, c])
    assert saved["revision_binding_status"] == "bound" and saved["verification"] == "caller_asserted"
    bindings = saved["revision_bindings"]
    assert bindings["basis"] == "latest_revisions_at_save"
    assert {r["evidence_id"] for r in bindings["references"]} == {a, b, c}
    assert len(bindings["references"]) == 3
    for old in rows:
        ref = next(r for r in bindings["references"] if r["evidence_id"] == old["evidence_id"])
        assert ref["revision_id"] == old["revision_id"]
        assert ref["content_hash"] == old["content_hash"] and ref["snapshot_hash"] == old["snapshot_hash"]
        assert ref["revision_first_recorded_at"] == store.get(old["evidence_id"])["first_seen"]
    change(store, 0, "Later withdrawal")
    store.unavailable(b, "Synthetic unavailable post")
    restarted = EvidenceStore(settings.data_dir)
    relationship = restarted.relations(a)["results"][0]
    assert relationship["revision_bindings"] == bindings
    assert restarted.get(a, rows[0]["revision_id"])["snapshot"]["text"] == "Original 0"
    assert not restarted.get(a, rows[0]["revision_id"])["is_latest_revision"]


def test_explicit_historical_versions_are_preserved_and_bad_maps_do_not_write(settings):
    store, rows = seed(settings)
    a, b, c = [r["evidence_id"] for r in rows]
    change(store, 0, "Updated text")
    change(store, 0, "Original 0")  # Same body hash as an older version does not make it that version.
    mapping = {r["evidence_id"]: r["revision_id"] for r in rows}
    saved = store.relate(a, b, "historical_context", [c], revision_ids=mapping)
    assert saved["revision_bindings"]["basis"] == "caller_selected_revisions"
    assert saved["revision_bindings"]["references"][0]["revision_id"] == rows[0]["revision_id"]
    assert store.get(a)["revision_id"] != rows[0]["revision_id"]
    for bad in ({a: rows[0]["revision_id"]}, {**mapping, "unrelated": 1}, {**mapping, a: 0}, {**mapping, a: True}):
        with pytest.raises(ValueError):
            store.relate(a, b, "invalid", [c], revision_ids=bad)
    with pytest.raises(DataAccessError) as wrong:
        store.relate(a, b, "invalid", [c], revision_ids={**mapping, a: rows[1]["revision_id"]})
    assert wrong.value.code == "revision_not_found"
    assert len(store.relations(a)["results"]) == 1


def test_legacy_relationship_upgrade_preserves_rows_and_never_guesses_versions(settings):
    store, rows = seed(settings)
    a, b = [r["evidence_id"] for r in rows[:2]]
    store.relate(a, b, "possible_repost", [a], note="Legacy assertion")
    with store.connect() as db:
        original = [tuple(r) for r in db.execute("SELECT * FROM revisions")]
        observations = [tuple(r) for r in db.execute("SELECT * FROM observations")]
        legacy = tuple(
            db.execute("SELECT id,from_id,to_id,relation,note,supporting_ids,created_at FROM relations").fetchone()
        )
        db.execute(
            "CREATE TABLE legacy_relations (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "from_id TEXT NOT NULL REFERENCES items(id),to_id TEXT NOT NULL REFERENCES items(id),"
            "relation TEXT NOT NULL,note TEXT NOT NULL,supporting_ids TEXT NOT NULL,created_at TEXT NOT NULL)"
        )
        db.execute("INSERT INTO legacy_relations VALUES(?,?,?,?,?,?,?)", legacy)
        db.execute("DROP TABLE relations")
        db.execute("ALTER TABLE legacy_relations RENAME TO relations")
    for _ in range(2):
        restarted = EvidenceStore(settings.data_dir)
        result = restarted.relations(a)["results"][0]
        assert result["revision_binding_status"] == "legacy_unbound" and result["revision_bindings"] is None
        assert result["note"] == "Legacy assertion" and result["supporting_ids"] == json.loads(legacy[5])
        with restarted.connect() as db:
            assert [tuple(r) for r in db.execute("SELECT * FROM revisions")] == original
            assert [tuple(r) for r in db.execute("SELECT * FROM observations")] == observations
            assert (
                tuple(
                    db.execute(
                        "SELECT id,from_id,to_id,relation,note,supporting_ids,created_at FROM relations"
                    ).fetchone()
                )
                == legacy
            )


async def test_mcp_exact_relation_versions_and_hashes_replay_across_restart(settings):
    store, rows = seed(settings)
    a, b = [r["evidence_id"] for r in rows[:2]]
    async with Client(create_server(settings)) as client:
        saved = (
            await client.call_tool(
                "add_evidence_relation",
                {
                    "from_id": a,
                    "to_id": b,
                    "relation": "possible_repost",
                    "supporting_ids": [a, b],
                    "revision_ids": {a: rows[0]["revision_id"], b: rows[1]["revision_id"]},
                },
            )
        ).structured_content
        assert saved["ok"] and saved["revision_binding_status"] == "bound"
    change(store, 0, "Later source edit")
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("get_evidence_relations", {"evidence_id": a})).structured_content
        assert result["ok"] and result["results"][0]["revision_bindings"] == saved["revision_bindings"]
        for ref in result["results"][0]["revision_bindings"]["references"]:
            exact = (
                await client.call_tool(
                    "get_evidence", {"evidence_id": ref["evidence_id"], "revision_id": ref["revision_id"]}
                )
            ).structured_content
            assert exact["ok"] and exact["evidence"]["content_hash"] == ref["content_hash"]
            assert exact["evidence"]["snapshot_hash"] == ref["snapshot_hash"]

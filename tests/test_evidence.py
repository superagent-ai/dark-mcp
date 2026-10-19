import pytest

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore


def setup_store(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    store.register("a", "telegram", "research", "Research")
    store.register("b", "onion", "unused", "Other source")
    return store


def observe(store, text="Superagent.sh alleged offer", key="1", source="a", **kwargs):
    return store.observe(
        source,
        "telegram",
        key,
        "https://t.me/research/" + key,
        {"text": text, "title": "", "published_at": "2026-10-01T00:00:00Z", **kwargs},
    )


def test_revisions_are_immutable_reverts_recorded_duplicates_and_history_search(settings):
    store = setup_store(settings)
    first = observe(store)
    repeated = observe(store)
    assert repeated["revision_id"] == first["revision_id"]
    assert repeated["change_type"] == "unchanged"
    changed = observe(store, "Withdrawn claim")
    assert changed["change_type"] == "edited"
    assert store.get(first["evidence_id"], first["revision_id"])["snapshot"]["text"].startswith("Superagent")
    results = store.search("superagent.sh")["results"]
    assert len(results) == 1 and not results[0]["is_latest_revision"]
    assert store.search("superagent.sh", include_history=False)["results"] == []
    reverted = observe(store)
    assert reverted["revision_id"] > changed["revision_id"]
    copy = observe(store, key="2", source="b")
    assert store.duplicates(first["evidence_id"])["results"][0]["evidence_id"] == copy["evidence_id"]
    assert len(store.history(first["evidence_id"])["results"]) == 3
    events = store.changes(limit=2)
    assert [row["change_type"] for row in events["results"]] == ["new", "edited"]
    assert events["has_more"]
    assert store.changes(events["next_after_seq"])["results"][0]["change_type"] == "edited"


def test_literal_unicode_search_filters_gates_sql_syntax_and_recent_reobservation(settings):
    store = setup_store(settings)
    first = store.observe(
        "a",
        "telegram",
        "1",
        "https://t.me/research/1",
        {"text": 'Straße SUPERAGENT.sh "quote"', "title": ""},
        "2026-10-01T00:00:00Z",
    )
    assert store.search("STRASSE")["results"]
    assert store.search('"quote"')["results"]
    assert store.search("sh")["results"]  # Short queries deliberately do not use the trigram index.
    assert not store.search('" OR 1=1; DROP TABLE items; --')["results"]
    assert not store.search("superagent.ai")["results"]
    assert not store.search("superagent.sh", collected_since="2026-10-18T00:00:00Z")["results"]
    store.observe(
        "a",
        "telegram",
        "1",
        "https://t.me/research/1",
        {"text": 'Straße SUPERAGENT.sh "quote"', "title": ""},
        "2026-10-19T00:00:00Z",
    )
    assert store.search("superagent.sh", collected_since="2026-10-18T00:00:00Z")["results"]
    assert not store.search("superagent.sh", published_since="2026-10-01T00:00:00Z")["results"]
    observe(store, "Superagent login", key="gate", access_state="login_gate_suspected")
    assert len(store.search("superagent")["results"]) == 1
    assert len(store.search("superagent", include_gates=True)["results"]) == 2
    with pytest.raises(ValueError):
        store.search("x", published_since="2026-10-19")
    with pytest.raises(DataAccessError):
        store.get(first["evidence_id"], 99999)


def test_missing_is_not_deleted_explicit_deletion_and_restoration_preserve_content(settings):
    store = setup_store(settings)
    item = observe(store)
    result = store.unavailable(item["evidence_id"], "direct lookup returned nothing")
    assert not result["deletion_confirmed"]
    assert store.get(item["evidence_id"])["availability"] == "unavailable"
    assert store.search("Superagent")["results"][0]["availability"] == "unavailable"
    assert observe(store)["change_type"] == "restored"
    store.confirm_deleted(item["evidence_id"], {"source": "explicit server update"})
    assert store.get(item["evidence_id"])["availability"] == "deleted"
    assert store.confirm_deleted(item["evidence_id"], {})["change_type"] == "unchanged"
    assert store.get(item["evidence_id"])["snapshot"]["text"]
    assert observe(store)["change_type"] == "restored"


def test_leases_cooldown_and_checkpoints_survive_new_store_instance(settings):
    store = setup_store(settings)
    job = store.create_job("a", "telegram", {"mode": "history"}, {"before_id": 0})
    _, token = store.claim(job["id"], 60)
    other = EvidenceStore(settings.data_dir)
    with pytest.raises(DataAccessError) as exc:
        other.claim(job["id"], 60)
    assert exc.value.code == "collection_busy"
    store.checkpoint(job["id"], token, {"before_id": 90}, "ready")
    resumed, token = other.claim(job["id"], 60)
    assert resumed["checkpoint"] == {"before_id": 90}
    other.checkpoint(job["id"], token, resumed["checkpoint"], "ready")
    _, token = other.claim(job["id"], 60)
    other.checkpoint(job["id"], token, resumed["checkpoint"], "complete")
    with pytest.raises(DataAccessError) as exc:
        store.claim(job["id"], 60)
    assert exc.value.code == "collection_complete"
    store.cooldown("telegram", 120)
    with pytest.raises(DataAccessError) as exc:
        other.cooldown("telegram")
    assert exc.value.retry_after > 0


def test_profiles_and_relations_never_assert_independent_verification(settings):
    store = setup_store(settings)
    one, two = observe(store), observe(store, key="2")
    profile = store.save_profile("superagent", {"name": "Superagent", "identifiers": ["superagent.sh"]})
    store.save_profile("superagent", {"name": "Superagent", "identifiers": ["superagent.sh", "superagent-ai"]})
    assert len(store.profile("superagent", profile["revision"])["document"]["identifiers"]) == 1
    store.relate(one["evidence_id"], two["evidence_id"], "possible_repost", [one["evidence_id"]])
    assert store.relations(two["evidence_id"])["results"][0]["verification"] == "caller_asserted"
    with pytest.raises(ValueError):
        store.relate(one["evidence_id"], two["evidence_id"], "possible_repost", [])


def test_search_labels_repeated_thread_title_hits_and_body_previews(settings):
    store = setup_store(settings)
    root = observe(store, "Superagent.sh access allegation", key="root", title="Superagent.sh thread")
    reply = observe(store, "bump", key="reply", title="Superagent.sh thread")
    body = observe(store, "superagent.sh mentioned here", key="body", title="Other discussion")
    results = {r["evidence_id"]: r for r in store.search("SUPERAGENT.sh")["results"]}
    assert results[root["evidence_id"]]["matched_fields"] == ["title", "body"]
    assert results[reply["evidence_id"]]["matched_fields"] == ["title"]
    assert results[reply["evidence_id"]]["excerpt_basis"] == "body_preview"
    assert results[reply["evidence_id"]]["excerpt"] == "bump"
    assert results[body["evidence_id"]]["matched_fields"] == ["body"]
    assert results[body["evidence_id"]]["excerpt_basis"] == "body_match_context"


def test_search_recording_times_do_not_backdate_new_company_text_or_refresh_from_old_publication(settings, monkeypatch):
    store = setup_store(settings)
    monkeypatch.setattr("dark_mcp.evidence.now", lambda: "2026-10-01T08:00:00+00:00")
    first = observe(store, "General discussion")
    monkeypatch.setattr("dark_mcp.evidence.now", lambda: "2026-10-19T08:00:00+00:00")
    edited = observe(store, "Superagent.sh newly added", published_at="2026-10-01T00:00:00Z")
    monkeypatch.setattr("dark_mcp.evidence.now", lambda: "2026-10-20T08:00:00+00:00")
    repeated = observe(store, "Superagent.sh newly added", published_at="2026-10-01T00:00:00Z")
    assert repeated["revision_id"] == edited["revision_id"]
    monkeypatch.setattr("dark_mcp.evidence.now", lambda: "2026-10-21T08:00:00+00:00")
    store.unavailable(first["evidence_id"], "Missing on direct check")
    hit = store.search("superagent.sh")["results"][0]
    assert hit["evidence_first_recorded_at"] == "2026-10-01T08:00:00+00:00"
    assert hit["matched_revision_first_recorded_at"] == "2026-10-19T08:00:00+00:00"
    assert hit["matched_revision_last_recorded_at"] == "2026-10-20T08:00:00+00:00"
    assert hit["published_at"] == "2026-10-01T00:00:00+00:00"
    assert hit["availability"] == "unavailable"


def test_search_labels_a_literal_spanning_title_body_boundary_without_claiming_body_match(settings):
    store = setup_store(settings)
    observe(store, "root text", title="Company")
    hit = store.search("company\nroot")["results"][0]
    assert hit["matched_fields"] == ["title_body_boundary"]
    assert hit["excerpt_basis"] == "body_preview"


async def test_search_match_context_and_recorded_times_are_returned_over_real_mcp(settings):
    from mcp import Client

    from dark_mcp.server import create_server

    store = setup_store(settings)
    item = observe(store, "Reply without a company name", title="Superagent.sh claim")
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("search_evidence", {"query": "superagent.sh"})).structured_content
        assert result["ok"] and len(result["results"]) == 1
        hit = result["results"][0]
        assert hit["evidence_id"] == item["evidence_id"] and hit["matched_fields"] == ["title"]
        assert hit["matched_revision_first_recorded_at"] == hit["matched_revision_last_recorded_at"]


def test_search_excerpt_offsets_follow_original_unicode_text_after_casefold_expansion(settings):
    store = setup_store(settings)
    observe(store, "ß" * 1000 + " MARKER.EXAMPLE matching body")
    hit = store.search("marker.example")["results"][0]
    assert hit["matched_fields"] == ["body"] and "MARKER.EXAMPLE" in hit["excerpt"]
    assert len(hit["excerpt"]) <= 600
    assert store.search("SS")["results"][0]["excerpt"].startswith("ß")

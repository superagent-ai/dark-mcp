import json
from unittest.mock import AsyncMock

import pytest
from mcp import Client

from dark_mcp.evidence import EvidenceStore
from dark_mcp.references import ReferenceSearch, canonical_host
from dark_mcp.server import create_server


def store_for(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    store.register("a", "forum", "https://forum.example", "Fixture source")
    store.register("b", "onion", "https://other.example", "Other fixture")
    return store


def save(store, links, pid="42", source="a", **extra):
    return store.observe(
        source,
        "forum",
        source + ":post:" + pid,
        "https://forum.example/Thread-original?pid=" + pid,
        {
            "id": pid,
            "url": "https://forum.example/Thread-original?pid=" + pid,
            "thread_key": "/Thread-original",
            "is_thread_root": True,
            "text": "See the attached reference",
            "title": "Unverified claim",
            "links": links,
            **extra,
        },
    )


def test_reference_only_company_domain_is_found_without_pretending_body_match(settings):
    store = store_for(settings)
    item = save(store, [{"url": "https://legora.com/security", "label": "Details"}])
    assert not store.search("legora")["results"]
    result = ReferenceSearch(store).search(query="legora", host="LEGORA.COM.", kind="forum")
    assert len(result["results"]) == 1 and result["reference_followed"] is False
    hit = result["results"][0]
    assert hit["evidence_id"] == item["evidence_id"] and hit["revision_id"] == item["revision_id"]
    assert hit["matched_fields"] == ["host", "url"]
    assert hit["content_hash"] == item["content_hash"] and hit["snapshot_hash"] == item["snapshot_hash"]
    assert hit["source_url"].endswith("?pid=42") and hit["source_url_basis"] == "selected_revision_snapshot"
    assert hit["matched_revision_first_recorded_at"] and hit["matched_revision_last_recorded_at"]
    assert result["scope"]["host"] == "legora.com"


def test_latest_history_removed_reference_and_old_recorded_url_are_separate(settings):
    store = store_for(settings)
    original = save(store, [{"url": "https://legora.com/security", "label": "Old label"}])
    latest = save(store, [], url="https://forum.example/Thread-moved?pid=42")
    search = ReferenceSearch(store)
    assert not search.search(host="legora.com")["results"]
    old = search.search(host="legora.com", include_history=True)["results"][0]
    assert old["revision_id"] == original["revision_id"] and not old["is_latest_revision"]
    assert old["source_url"] == "https://forum.example/Thread-original?pid=42"
    assert store.get(latest["evidence_id"])["snapshot"]["links"] == []
    # A later matching label is selected per item/URL without duplicating older references.
    save(store, [{"url": "https://legora.com/security", "label": "New label"}])
    hits = search.search(host="legora.com", include_history=True)["results"]
    assert len(hits) == 1 and hits[0]["label"] == "New label" and hits[0]["is_latest_revision"]


def test_host_boundaries_idna_ip_literals_and_casefolded_labels(settings):
    store = store_for(settings)
    urls = [
        "https://legora.com/",
        "https://api.legora.com/",
        "https://legora.com.evil.example/",
        "https://notlegora.com/",
        "https://straße.de/",
        "https://strasse.de/",
        "https://8.8.8.8/",
        "https://a.8.8.8.8/",
        "http://[2001:db8::1]/",
    ]
    for i, url in enumerate(urls):
        save(store, [{"url": url, "label": "Straße reference"}], pid=str(i + 1))
    search = ReferenceSearch(store)
    assert len(search.search(host="legora.com")["results"]) == 1
    assert len(search.search(host="legora.com", include_subdomains=True)["results"]) == 2
    assert len(search.search(host="straße.de")["results"]) == 1
    assert search.search(host="straße.de")["results"][0]["host"] == "xn--strae-oqa.de"
    assert search.search(host="strasse.de")["results"][0]["host"] == "strasse.de"
    assert len(search.search(host="8.8.8.8", include_subdomains=True)["results"]) == 1
    assert search.search(host="[2001:db8::1]")["results"][0]["host"] == "2001:db8::1"
    label_hits = search.search(query="STRASSE")["results"]
    assert len(label_hits) == len(urls) and all("label" in h["matched_fields"] for h in label_hits)


def test_scope_pagination_short_queries_and_unavailable_state(settings):
    store = store_for(settings)
    for i in range(3):
        save(store, [{"url": "https://example.com/" + str(i), "label": "AI"}], pid=str(i + 1))
    other = save(store, [{"url": "https://example.com/other", "label": "AI"}], source="b")
    store.unavailable(other["evidence_id"], "Source no longer readable")
    search = ReferenceSearch(store)
    first = search.search(query="ai", source_id="a", limit=2)
    second = search.search(query="ai", source_id="a", limit=2, offset=first["next_offset"])
    assert len(first["results"]) == 2 and len(second["results"]) == 1 and second["next_offset"] is None
    assert first["observations_through_seq"] == second["observations_through_seq"]
    assert len({h["reference_url"] for h in first["results"] + second["results"]}) == 3
    hit = search.search(query="other")["results"][0]
    assert hit["current_availability"] == "unavailable"


def test_reference_order_uses_latest_positive_recording_even_for_an_unchanged_snapshot(settings):
    store = store_for(settings)
    first_links = [{"url": "https://example.com/first", "label": "Reference"}]
    first = save(store, first_links, pid="1")
    save(store, [{"url": "https://example.com/second", "label": "Reference"}], pid="2")
    again = save(store, first_links, pid="1")
    assert again["revision_id"] == first["revision_id"] and again["change_type"] == "unchanged"
    hits = ReferenceSearch(store).search(host="example.com")["results"]
    assert hits[0]["evidence_id"] == first["evidence_id"]


def test_reference_recording_times_do_not_borrow_older_item_or_publication_dates(settings):
    store = store_for(settings)
    first = save(store, [], published_at="2000-01-01T00:00:00Z")
    introduced = save(
        store, [{"url": "https://legora.com/", "label": "Reference"}], published_at="2000-01-01T00:00:00Z"
    )
    with store.connect() as db:
        db.execute("UPDATE items SET first_seen=? WHERE id=?", ("2026-10-01T00:00:00+00:00", first["evidence_id"]))
        db.execute(
            "UPDATE observations SET observed_at=? WHERE revision_id=?",
            ("2026-10-01T00:00:00+00:00", first["revision_id"]),
        )
        db.execute(
            "UPDATE observations SET observed_at=? WHERE revision_id=?",
            ("2026-10-03T00:00:00+00:00", introduced["revision_id"]),
        )
    hit = ReferenceSearch(store).search(host="legora.com")["results"][0]
    assert hit["evidence_first_recorded_at"] == "2026-10-01T00:00:00+00:00"
    assert hit["matched_revision_first_recorded_at"] == "2026-10-03T00:00:00+00:00"
    assert hit["published_at"] == "2000-01-01T00:00:00+00:00"


def test_partial_source_flags_are_retained_while_gate_snapshots_are_excluded(settings):
    store = store_for(settings)
    links = [{"url": "https://example.com/", "label": "Visible reference"}]
    save(store, links, hidden_content=True, truncated=True)
    save(store, links, pid="43", access_state="login_gate_suspected")
    results = ReferenceSearch(store).search(host="example.com")["results"]
    assert len(results) == 1 and results[0]["hidden_content"] and results[0]["truncated"]


def test_onion_navigation_refs_and_literal_punctuation_are_explicitly_scoped(settings):
    store = store_for(settings)
    store.observe(
        "b",
        "onion",
        "page",
        "https://page.example/",
        {"url": "https://page.example/", "links": ["https://example.com/original"], "text": "Navigation"},
    )
    save(store, [{"url": "https://other.example/", "label": 'A "quoted" 100%_verified reference'}])
    search = ReferenceSearch(store)
    assert len(search.search(host="example.com", kind="onion")["results"]) == 1
    assert not search.search(host="example.com", kind="forum")["results"]
    assert search.search(query='"quoted"')["results"][0]["matched_fields"] == ["label"]
    assert search.search(query="100%_verified")["results"][0]["matched_fields"] == ["label"]
    assert not search.search(query='" OR 1=1 --')["results"]


def test_backfill_and_reopen_preserve_immutable_evidence_and_index_once(settings):
    store = store_for(settings)
    ref = save(store, [{"url": "https://legora.com/", "label": "Details"}])
    before = store.get(ref["evidence_id"])
    counts = store.counts()
    # Simulate a pre-reference-index store: retain source evidence, remove only derived rows/marker.
    with store.connect() as db:
        db.execute("DELETE FROM evidence_references")
        db.execute("INSERT INTO reference_search(reference_search) VALUES('rebuild')")
        db.execute("DELETE FROM migrations WHERE name='reference-index-v1'")
    reopened = EvidenceStore(settings.data_dir)
    assert reopened.get(ref["evidence_id"])["snapshot_hash"] == before["snapshot_hash"]
    assert reopened.counts() == counts
    assert len(ReferenceSearch(reopened).search(query="legora")["results"]) == 1
    again = EvidenceStore(settings.data_dir)
    with again.connect() as db:
        assert db.execute("SELECT count(*) FROM evidence_references").fetchone()[0] == 1
    assert again.get(ref["evidence_id"])["snapshot"] == before["snapshot"]


def test_bounds_bad_urls_nested_copies_and_provider_metadata_are_excluded(settings):
    store = store_for(settings)
    links = [
        {"url": "https://user:private-secret@example.com/", "label": "Credentials"},
        {"url": "javascript:alert(1)", "label": "Script"},
        {"url": "https://example.com/\nsecret", "label": "Control"},
        {"url": "https://example.com:bad/", "label": "Invalid port"},
        {"url": "https://example.com/valid", "label": "Reference"},
        {"url": "https://example.com/valid", "label": "Duplicate"},
    ]
    save(store, links)
    store.observe("a", "forum", "aggregate", "https://forum.example/", {"posts": [{"links": links}], "text": "Page"})
    store.observe("a", "provider", "provider", "https://provider.example/1", {"links": links, "text": "Provider"})
    save(store, [{"url": f"https://bound.example/{i}", "label": "x" * 1000} for i in range(201)], pid="43")
    result = ReferenceSearch(store).search(host="example.com")
    assert len(result["results"]) == 1 and result["results"][0]["reference_url"] == "https://example.com/valid"
    assert "private-secret" not in json.dumps(result)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM evidence_references WHERE host='bound.example'").fetchone()[0] == 200
        assert db.execute("SELECT max(length(label)) FROM evidence_references").fetchone()[0] == 500


@pytest.mark.parametrize(
    "host",
    ["https://example.com/", "user@example.com", "example.com:443", "*.example.com", "[example.com]", "bad\nhost", ""],
)
def test_invalid_host_arguments_are_rejected(host):
    with pytest.raises(ValueError):
        canonical_host(host)


async def test_mcp_reference_search_is_local_reports_acquisition_and_structured_errors(settings, monkeypatch):
    from dark_mcp.forums import ForumConnector
    from dark_mcp.onion import OnionConnector
    from dark_mcp.telegram import TelegramConnector

    store = store_for(settings)
    saved = save(
        store, [{"url": "https://legora.com/", "label": "Details"}], acquisition={"method": "operator_html_import"}
    )
    native = AsyncMock(side_effect=AssertionError("References are never fetched"))
    monkeypatch.setattr(ForumConnector, "fetch", native)
    monkeypatch.setattr(OnionConnector, "fetch", native)
    monkeypatch.setattr(TelegramConnector, "messages", native)
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("search_references", {"host": "legora.com"})).structured_content
        assert result["ok"] and result["results"][0]["evidence_id"] == saved["evidence_id"]
        assert result["results"][0]["acquisition"]["method"] == "operator_html_import"
        for args in [
            {},
            {"query": "x\x00y"},
            {"host": "user:private-secret@example.com"},
            {"query": "x", "kind": "provider"},
        ]:
            error = (await client.call_tool("search_references", args)).structured_content
            assert error["error"]["code"] == "invalid_arguments" and "private-secret" not in json.dumps(error)
    native.assert_not_awaited()

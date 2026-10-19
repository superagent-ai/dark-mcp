import json

import pytest
from mcp import Client

from dark_mcp.evidence import EvidenceStore
from dark_mcp.server import create_server


def record(store, key="1", kind="telegram_web", text="A brief reply", quotes=None, **fields):
    store.register("quote-source", kind, "fixture", "Synthetic source")
    return store.observe(
        "quote-source",
        kind,
        key,
        "https://t.me/fixture/" + key,
        {"text": text, "title": "Discussion", "quotes": quotes or [], **fields},
    )


@pytest.mark.parametrize("kind", ["forum", "forum_feed", "telegram_web"])
def test_quote_only_names_are_opt_in_and_separate_from_body_and_attribution(settings, kind):
    store = EvidenceStore(settings.prepare_data_dir())
    quote = {"text": "An unverified Acme.example claim", "url": "https://t.me/unverified/5", "truncated": True}
    saved = record(store, kind=kind, quotes=[quote])
    assert not store.search("ACME.example")["results"]
    result = store.search("ACME.example", include_quotes=True)
    assert result["includes_quotes"] and result["corpus_scope"]["include_quotes"]
    hit = result["results"][0]
    assert hit["evidence_id"] == saved["evidence_id"] and hit["matched_fields"] == ["quotes"]
    assert hit["excerpt"] == "A brief reply" and hit["excerpt_basis"] == "body_preview"
    assert hit["matched_quotes"] == [
        {
            "ordinal": 0,
            "excerpt": quote["text"],
            "reference": {"url": quote["url"], "truncated": True},
            "attribution": "quoted_context_unverified",
        }
    ]
    assert hit["published_at"] is None
    assert store.get(saved["evidence_id"])["snapshot"]["text"] == "A brief reply"
    # A quote reference alone is not a body/quote-text match or verified origin.
    assert not store.search("unverified/5", include_quotes=True)["results"]


def test_quote_literal_matching_unicode_short_terms_and_no_cross_quote_concatenation(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    record(
        store,
        quotes=[
            {"text": "Straße " + "padding " * 100 + "QuOtEdCompany.example"},
            {"text": 'alpha "quoted"'},
            {"text": "omega"},
            {"text": 7},
            "unsupported legacy quote",
            {"text": ""},
        ],
    )
    assert store.search("STRASSE", include_quotes=True)["results"]
    assert store.search("ss", include_quotes=True)["results"]
    assert store.search('"quoted"', include_quotes=True)["results"]
    hit = store.search("quotedcompany.example", include_quotes=True)["results"][0]
    assert "QuOtEdCompany.example" in hit["matched_quotes"][0]["excerpt"]
    assert not store.search('" OR 1=1; DROP TABLE items; --', include_quotes=True)["results"]
    assert not store.search('quoted"\nomega', include_quotes=True)["results"]
    assert not store.search("Discussion\nStraße", include_quotes=True)["results"]


def test_body_quote_overlap_is_one_record_and_preserves_title_body_boundary_label(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    saved = record(store, text="Acme.example", quotes=[{"text": "Acme.example"}, {"text": "Acme.example again"}])
    hits = store.search("Acme.example", include_quotes=True)["results"]
    assert len(hits) == 1 and hits[0]["evidence_id"] == saved["evidence_id"]
    assert hits[0]["matched_fields"] == ["body", "quotes"] and len(hits[0]["matched_quotes"]) == 2
    record(store, key="2", text="beta", title="alpha", quotes=[{"text": "alpha\nbeta"}])
    assert store.search("alpha\nbeta", include_quotes=True)["results"][0]["matched_fields"] == [
        "title_body_boundary",
        "quotes",
    ]


def test_native_forward_labels_and_unstructured_metadata_are_not_inferred_quote_text(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    record(
        store,
        kind="telegram",
        quotes=[{"text": "Acme.example"}],
        forward={"origin_name": "Acme.example", "date": "2025-01-01T00:00:00Z"},
    )
    assert not store.search("Acme.example", include_quotes=True)["results"]


def test_quote_history_source_kind_gate_dates_and_pagination_filters(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    first = record(store, quotes=[{"text": "Acme.example"}], published_at="2026-01-01T00:00:00Z")
    record(store, quotes=[{"text": "Updated quote without the name"}], published_at="2026-01-01T00:00:00Z")
    hit = store.search("Acme.example", include_quotes=True)["results"][0]
    assert hit["revision_id"] == first["revision_id"] and not hit["is_latest_revision"]
    assert (
        hit["matched_revision_first_recorded_at"] == store.get(first["evidence_id"], first["revision_id"])["first_seen"]
    )
    assert not store.search("Acme.example", include_quotes=True, include_history=False)["results"]
    assert not store.search("Acme.example", include_quotes=True, kind="forum")["results"]
    assert not store.search("Acme.example", include_quotes=True, published_since="2026-02-01T00:00:00Z")["results"]
    assert not store.search("Acme.example", include_quotes=True, collected_since="2099-01-01T00:00:00Z")["results"]
    record(store, key="gate", quotes=[{"text": "Acme.example"}], access_state="login_gate_suspected")
    assert len(store.search("Acme.example", include_quotes=True)["results"]) == 1
    assert len(store.search("Acme.example", include_quotes=True, include_gates=True)["results"]) == 2
    page = store.search("Acme.example", include_quotes=True, include_gates=True, limit=1)
    assert page["next_offset"] == 1
    assert len(store.search("Acme.example", include_quotes=True, include_gates=True, offset=1)["results"]) == 1
    assert store.search("Acme.example", include_quotes=True, source_id="quote-source")["results"]


def test_migration_backfills_quotes_without_changing_saved_revisions_or_observations(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    saved = record(store, quotes=[{"text": "Acme.example"}])
    with store.connect() as db:
        original = [tuple(row) for row in db.execute("SELECT * FROM revisions")]
        observations = [tuple(row) for row in db.execute("SELECT * FROM observations")]
        db.execute("DROP TRIGGER quote_search_insert")
        db.execute("DROP TABLE quote_search")
        db.execute("DROP TABLE evidence_quotes")
        db.execute("DELETE FROM migrations WHERE name='quote-index-v1'")
    for _ in range(2):
        restarted = EvidenceStore(settings.data_dir)
        with restarted.connect() as db:
            assert [tuple(row) for row in db.execute("SELECT * FROM revisions")] == original
            assert [tuple(row) for row in db.execute("SELECT * FROM observations")] == observations
            assert db.execute("SELECT count(*) FROM evidence_quotes").fetchone()[0] == 1
        assert (
            restarted.search("Acme.example", include_quotes=True)["results"][0]["revision_id"] == saved["revision_id"]
        )
        assert json.loads(original[0][-1])["quotes"][0]["text"] == "Acme.example"


async def test_mcp_quote_search_and_exact_revision_replay_survive_restart(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    saved = record(store, quotes=[{"text": "Acme.example", "post_id": "44"}])
    for _ in range(2):
        async with Client(create_server(settings)) as client:
            ordinary = (await client.call_tool("search_evidence", {"query": "Acme.example"})).structured_content
            assert ordinary["ok"] and not ordinary["results"] and not ordinary["includes_quotes"]
            result = (
                await client.call_tool("search_evidence", {"query": "Acme.example", "include_quotes": True})
            ).structured_content
            assert result["ok"] and result["results"][0]["matched_fields"] == ["quotes"]
            exact = (
                await client.call_tool(
                    "get_evidence", {"evidence_id": saved["evidence_id"], "revision_id": saved["revision_id"]}
                )
            ).structured_content
            assert exact["evidence"]["snapshot"]["text"] == "A brief reply"
            assert exact["evidence"]["snapshot"]["quotes"][0]["post_id"] == "44"


async def test_real_forum_parser_quote_only_match_and_reprocessing_are_separate(settings, monkeypatch):
    from unittest.mock import AsyncMock

    from dark_mcp.forums import ForumConnector
    from dark_mcp.sources import SourceCatalog

    source_id = "onion:91e4e785a3af"
    url = SourceCatalog().get(source_id)["locator"] + "Thread-Discussion"
    html = """<title>Discussion</title><div id="post_1"><div class="post_head"><div class="float_right">
    <strong><a href="?pid=1">#2</a></strong></div></div><div class="post_body">
    <blockquote data-source="post:9">An unverified Acme.example claim</blockquote>Request for evidence
    </div></div>"""
    store = EvidenceStore(settings.prepare_data_dir())
    store.register(source_id, "onion", url, "Synthetic source")
    old = store.observe(
        source_id,
        "forum",
        source_id + ":post:1",
        url + "?pid=1",
        {
            "id": "1",
            "title": "Discussion",
            "text": "An unverified Acme.example claim\nRequest for evidence",
            "quotes": [{"post_id": "9", "text": "An unverified Acme.example claim"}],
        },
    )
    monkeypatch.setattr(ForumConnector, "fetch_document", AsyncMock(return_value=(url, html, {"state": "public"})))
    async with Client(create_server(settings)) as client:
        read = (await client.call_tool("read_forum_thread", {"source_id": source_id, "url": url})).structured_content
        assert read["ok"]
        post = read["page"]["posts"][0]
        assert post["text"] == "Request for evidence" and post["body_scope"] == "post_body_without_blockquotes_v1"
        assert post["evidence"]["processing_change"]["source_edit_verified"] is False
        fresh = (
            await client.call_tool(
                "search_evidence", {"query": "Acme.example", "kind": "forum", "include_history": False}
            )
        ).structured_content
        assert fresh["results"] == []
        quoted = (
            await client.call_tool(
                "search_evidence",
                {"query": "Acme.example", "kind": "forum", "include_quotes": True, "include_history": False},
            )
        ).structured_content
        assert len(quoted["results"]) == 1 and quoted["results"][0]["matched_fields"] == ["quotes"]
        legacy = (
            await client.call_tool(
                "get_evidence", {"evidence_id": old["evidence_id"], "revision_id": old["revision_id"]}
            )
        ).structured_content
        assert "Acme.example" in legacy["evidence"]["snapshot"]["text"]
        assert legacy["evidence"]["processing_change"] is None
        # Re-observe without a scope transition: the original revision's processing note survives.
        again = (await client.call_tool("read_forum_thread", {"source_id": source_id, "url": url})).structured_content
        assert again["page"]["posts"][0]["evidence"]["change_type"] == "unchanged"
    async with Client(create_server(settings)) as client:
        saved = (await client.call_tool("get_evidence", {"evidence_id": old["evidence_id"]})).structured_content
        assert saved["evidence"]["processing_change"]["kind"] == "body_scope_changed"
        assert saved["evidence"]["processing_change"]["previous_scope"] == "unreported"
        assert saved["evidence"]["snapshot"]["edited_at"] is None


def test_nested_and_omitted_forum_quotes_do_not_leak_back_into_body_or_links():
    from dark_mcp.forums import parse_forum
    from dark_mcp.sources import SourceCatalog

    source = SourceCatalog().get("forum:hard-tm")
    quotes = (
        '<blockquote data-source="post:9">'
        + ("x" * 600)
        + """<blockquote>NestedCompany</blockquote>
    <a href="https://quote.example/">quoted link</a><div class="hideContent">Quoted hidden content</div></blockquote>"""
    )
    quotes += "".join("<blockquote>OmittedCorp" + str(i) + "</blockquote>" for i in range(4))
    html = (
        '<title>Discussion</title><article class="message--post" data-content="post-1">'
        '<article class="message-body"><div class="bbWrapper">'
        + quotes
        + 'Own reply <a href="https://own.example/">own link</a></div></article></article>'
    )
    parsed = parse_forum(source, "https://hard-tm.com/threads/discussion.1/", html, 60000, 20)
    post = parsed.posts[0]
    assert post.text == "Own reply\nown link" and post.hidden_content is False
    assert len(post.quotes) == 3 and post.omitted_quotes == 2
    assert post.quotes[0]["truncated"] and post.quotes[0]["hidden_content"]
    assert post.quotes[0]["post_id"] == "9" and post.quotes[0]["links"][0]["url"] == "https://quote.example/"
    assert post.links == [{"url": "https://own.example/", "label": "own link"}]
    assert "NestedCompany" not in parsed.text and "OmittedCorp" not in parsed.text

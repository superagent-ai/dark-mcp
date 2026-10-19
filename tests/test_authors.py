from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup
from mcp import Client

from dark_mcp.authors import AuthorPosts, author_fields
from dark_mcp.evidence import EvidenceStore
from dark_mcp.forums import ForumConnector, parse_forum
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog


def anchor(html):
    return BeautifulSoup(html, "html.parser").a


def test_source_header_profile_ids_are_distinct_from_handle_only_urls():
    source = {"forum_engine": "mybb", "allowed_hosts": ["forum.example"]}
    captured = author_fields(
        source,
        "https://forum.example/Thread-x",
        "Seller",
        anchor('<a href="/member.php?action=profile&amp;uid=0012">Seller</a>'),
    )
    assert captured["author_id"] == "12" and captured["author_identity"]["id_basis"] == "profile_url"
    assert captured["author_url"].endswith("member.php?action=profile&uid=0012")
    handle = author_fields(
        source, "https://forum.example/Thread-x", "Seller", anchor('<a href="/User-Seller">Seller</a>')
    )
    assert handle["author_id"] is None and handle["author_url"] == "https://forum.example/User-Seller"
    q2a = author_fields(
        {**source, "forum_engine": "q2a"},
        "https://forum.example/12/topic",
        "123",
        anchor('<a href="/user/123">123</a>'),
    )
    assert q2a["author_id"] is None and q2a["author_url"].endswith("/user/123")


def test_conflicting_header_ids_and_handles_remain_unresolved():
    source = {"forum_engine": "xenforo", "allowed_hosts": ["forum.example"]}
    url = "https://forum.example/threads/1/"
    good = author_fields(source, url, "Seller", anchor('<a href="/members/seller.12/" data-user-id="12">Seller</a>'))
    assert good["author_id"] == "12" and good["author_identity"]["id_basis"] == "profile_url_and_attribute"
    conflict = author_fields(
        source, url, "Seller", anchor('<a href="/members/seller.12/" data-user-id="13">Seller</a>')
    )
    assert conflict["author_id"] is None and conflict["author_identity"]["id_conflict"]
    assert conflict["author_identity"]["profile_id"] == "12"
    renamed = author_fields(source, url, "Other", anchor('<a href="/members/seller.12/">Seller</a>'))
    assert renamed["author_id"] is None and renamed["author_identity"]["handle_conflict"]


@pytest.mark.parametrize(
    "href",
    [
        "https://other.example/members/seller.12/",
        "https://user:secret@forum.example/members/seller.12/",
        "/members/seller.12/?action=delete",
        "/members/seller.12/?foo=x",
        "/members/%2e%2e.12/",
        "javascript:alert(1)",
        "/logout/12/",
        "/members/seller.12/?action=profile&action=delete",
    ],
)
def test_unsafe_or_other_source_profile_links_do_not_supply_ids(href):
    source = {"forum_engine": "xenforo", "allowed_hosts": ["forum.example"]}
    value = author_fields(source, "https://forum.example/threads/1/", "Seller", anchor(f'<a href="{href}">Seller</a>'))
    assert value["author_id"] is None and value["author_url"] is None


def test_parser_ignores_profile_links_in_post_bodies_and_keeps_real_header_identity():
    source = SourceCatalog().get("forum:hard-tm")
    document = """<title>Discussion</title><article class="message--post" data-content="post-1" data-author="Seller">
    <div class="message-name"><a class="username" href="/members/seller.12/" data-user-id="12">Seller</a></div>
    <div class="message-body"><div class="bbWrapper">Post text
    <a data-class="profile_url" data-user-id="999" href="/members/decoy.999/">Decoy</a></div></div></article>"""
    page = parse_forum(source, "https://hard-tm.com/threads/1/", document, 10000, 20)
    assert page.posts[0].author == "Seller" and page.posts[0].author_id == "12"
    no_header = document.replace(
        '<div class="message-name"><a class="username" href="/members/seller.12/" data-user-id="12">Seller</a></div>',
        "",
    )
    page = parse_forum(source, "https://hard-tm.com/threads/1/", no_header, 10000, 20)
    assert page.posts[0].author == "Seller" and page.posts[0].author_id is None


def store_for(settings):
    store = EvidenceStore(settings.prepare_data_dir())
    for source in ("a", "b"):
        store.register(source, "forum", "https://" + source + ".example/", source)
    return store


def save(store, pid, author="Seller", author_id="12", source="a", **extra):
    return store.observe(
        source,
        "forum",
        source + ":post:" + pid,
        "https://" + source + ".example/threads/1/post-" + pid,
        {
            "id": pid,
            "author": author,
            "author_id": author_id,
            "author_url": "https://" + source + ".example/members/seller.12/",
            "text": "Access claim " + pid,
            "url": "https://" + source + ".example/threads/1/post-" + pid,
            "title": "Claim",
            "thread_key": "/threads/1/",
            "published_at": "2000-01-01T00:00:00Z",
            **extra,
        },
    )


def test_same_named_accounts_sources_renames_and_pagination_are_not_merged(settings):
    store = store_for(settings)
    save(store, "1")
    save(store, "2", author="Renamed")
    save(store, "3", author_id="13")
    save(store, "4", author_id=None)
    save(store, "5", source="b")
    query = AuthorPosts(store, 10000)
    by_id = query.get("a", author_id="0012", limit=1)
    assert by_id["total_stored_posts"] == 2 and by_id["next_offset"] == 1
    second = query.get("a", author_id="12", offset=1, limit=1)
    assert second["next_offset"] is None
    assert {p["author"] for p in by_id["results"] + second["results"]} == {"Seller", "Renamed"}
    handles = query.get("a", author="seller")
    assert handles["total_stored_posts"] == 3 and handles["distinct_native_author_ids"] == 2
    assert handles["posts_without_native_author_id"] == 1
    assert handles["match_basis"] == "casefolded_exact_handle_not_identity" and not handles["network_read"]
    assert query.get("b", author_id="12")["total_stored_posts"] == 1
    save(store, "6", author="Straße", author_id="14")
    assert query.get("a", author="STRASSE")["results"][0]["author_id"] == "14"


def test_recording_cutoff_selects_state_before_matching_and_excludes_later_authorship(settings):
    store = store_for(settings)
    old = save(store, "1")
    new = save(store, "1", author="Renamed", author_id="13")
    future = save(store, "2")
    with store.connect() as db:
        for ref, date in [
            (old, "2026-10-01T00:00:00+00:00"),
            (new, "2026-10-03T00:00:00+00:00"),
            (future, "2026-10-04T00:00:00+00:00"),
        ]:
            db.execute("UPDATE observations SET observed_at=? WHERE revision_id=?", (date, ref["revision_id"]))
        db.execute("UPDATE items SET first_seen=?", ("2026-10-01T00:00:00+00:00",))
    query = AuthorPosts(store, 10000)
    current = query.get("a", author="Seller")
    assert current["total_stored_posts"] == 1 and current["results"][0]["post_id"] == "2"
    past = query.get("a", author_id="12", as_of="2026-10-02T00:00:00Z")
    assert past["total_stored_posts"] == 1 and past["results"][0]["revision_id"] == old["revision_id"]
    assert not query.get("a", author_id="13", as_of="2026-10-02T00:00:00Z")["results"]
    assert not query.get("a", author="Seller", as_of="2026-09-30T00:00:00Z")["results"]
    assert query.get("a", author_id="13")["results"][0]["revision_id"] == new["revision_id"]
    save(store, "1", author="Renamed", author_id="13")  # Unchanged refresh recorded after the cutoff.
    selected = query.get("a", author_id="13", as_of="2026-10-04T00:00:00Z")["results"][0]
    assert selected["evidence_first_recorded_at"] == "2026-10-01T00:00:00+00:00"
    assert selected["matched_revision_first_recorded_at"] == "2026-10-03T00:00:00+00:00"
    assert selected["matched_revision_last_recorded_at"] == "2026-10-03T00:00:00+00:00"


def test_removed_author_metadata_conflicts_and_body_limits_are_explicit(settings):
    store = store_for(settings)
    old = save(store, "1")
    save(store, "1", author=None, author_id=None)
    save(store, "2", author_identity={"id_conflict": True}, hidden_content=True, truncated=True, text="x" * 20000)
    query = AuthorPosts(store, 100)
    assert not query.get("a", author_id="12")["results"]
    handles = query.get("a", author="seller")
    assert handles["total_stored_posts"] == 1 and handles["results"][0]["author_id"] is None
    hit = handles["results"][0]
    assert len(hit["text"]) == 100 and hit["context_text_truncated"] and hit["hidden_content"] and hit["truncated"]
    assert store.get(old["evidence_id"], old["revision_id"])["snapshot"]["author"] == "Seller"


def test_unavailable_state_and_backfill_do_not_infer_legacy_ids_or_change_revisions(settings):
    store = store_for(settings)
    old = save(store, "1", author_id=None)
    store.unavailable(old["evidence_id"], "Source no longer readable")
    counts = store.counts()
    with store.connect() as db:
        db.execute("DELETE FROM forum_author_members")
        db.execute("DELETE FROM migrations WHERE name='forum-author-index-v1'")
    for _ in range(2):
        reopened = EvidenceStore(settings.data_dir)
        assert reopened.counts() == counts
        query = AuthorPosts(reopened, 10000)
        assert not query.get("a", author_id="12")["results"]  # Existing URL alone does not invent an ID.
        hit = query.get("a", author="Seller")["results"][0]
        assert hit["snapshot_hash"] == old["snapshot_hash"] and hit["availability_at_observation"] == "unavailable"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"author": "x", "author_id": "12"},
        {"author_id": "0"},
        {"author_id": "user:12"},
        {"author": "\n"},
        {"author": "Seller", "offset": -1},
        {"author": "Seller", "as_of": "2026-10-01"},
    ],
)
def test_invalid_author_queries_are_rejected(settings, kwargs):
    with pytest.raises(ValueError):
        AuthorPosts(store_for(settings), 10000).get("a", **kwargs)


async def test_mcp_lookup_is_local_and_retains_exact_revision_and_acquisition(settings, monkeypatch):
    store = store_for(settings)
    saved = save(store, "1", acquisition={"method": "operator_html_import"})
    native = AsyncMock(side_effect=AssertionError("Local author lookup must not fetch a profile or forum"))
    monkeypatch.setattr(ForumConnector, "fetch", native)
    async with Client(create_server(settings)) as client:
        result = (
            await client.call_tool("get_collected_author_posts", {"source_id": "a", "author_id": "12"})
        ).structured_content
        assert result["ok"] and not result["network_read"]
        hit = result["results"][0]
        assert hit["evidence_id"] == saved["evidence_id"] and hit["snapshot_hash"] == saved["snapshot_hash"]
        assert hit["acquisition"]["method"] == "operator_html_import"
        exact = (
            await client.call_tool(
                "get_evidence", {"evidence_id": hit["evidence_id"], "revision_id": hit["revision_id"]}
            )
        ).structured_content
        assert exact["evidence"]["snapshot_hash"] == hit["snapshot_hash"]
        error = (await client.call_tool("get_collected_author_posts", {"source_id": "a"})).structured_content
        assert error["error"]["code"] == "invalid_arguments"
    native.assert_not_awaited()

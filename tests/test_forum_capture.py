import hashlib
import json
from unittest.mock import AsyncMock
from urllib.parse import urljoin

import pytest
from mcp import Client
from typer.testing import CliRunner

from dark_mcp import cli
from dark_mcp.collection import CollectionService
from dark_mcp.coverage import CorpusCoverage
from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.forum_capture import import_forum_capture
from dark_mcp.forums import ForumConnector, parse_forum
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog
from dark_mcp.thread_context import ThreadContext

SID = "forum:leakforum"
ORIGIN = "https://leakforum.io"
URL = ORIGIN + "/Thread-fixture"


def document(body="Fixture Acme access claim", extra=""):
    return f'''<title>Fixture thread</title><link rel="canonical" href="{URL}">
    <input type="hidden" name="csrf" value="private-form-token">
    <script>throw Error('private-script-token')</script>
    <div id="post_42"><div class="post-username"><a>fixture-author</a></div>
    <span class="post_date">2025-01-01</span><div class="pagnum"><a href="{URL}?pid=42">#1</a></div>
    <div class="post_body">{body}</div></div>{extra}'''


def file_at(tmp_path, content):
    file = tmp_path / "capture.html"
    file.write_text(content)
    return file


async def test_import_cli_then_restart_mcp_preserves_provenance_search_context_and_no_network(
    settings, tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    network = AsyncMock(side_effect=AssertionError("No native request during import or replay"))
    monkeypatch.setattr(ForumConnector, "fetch", network)
    extra = """<div id="post_43"><div class="pagnum"><a href="Thread-fixture?pid=43">#2</a></div>
    <div class="post_body">Clarifying reply <div class="hidden-content">Login to view</div></div></div>
    <a class="pagination_next" href="Thread-fixture?page=2">Next</a>"""
    file = file_at(tmp_path, document(extra=extra))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["forum-page-import", SID, URL, str(file)])
    assert result.exit_code == 0
    imported = json.loads(result.output)
    assert "private-form-token" not in result.output and "fixture-author" not in result.output
    assert len(imported["posts"]) == 2 and imported["hidden_posts"] == 1
    assert imported["next_page_urls"] == [URL + "?page=2"]
    assert imported["acquisition"]["document_sha256"] == hashlib.sha256(file.read_bytes()).hexdigest()
    anchor = imported["posts"][0]["evidence"]
    repeat = json.loads(runner.invoke(cli.app, ["forum-page-import", SID, URL, str(file)]).output)
    assert repeat["posts"][0]["evidence"]["revision_id"] == anchor["revision_id"]
    assert repeat["posts"][0]["evidence"]["change_type"] == "unchanged"
    # A separate server instance opens the same persistent store.
    async with Client(create_server(settings)) as client:
        hit = (await client.call_tool("search_evidence", {"query": "Acme", "kind": "forum"})).structured_content
        assert hit["ok"] and all(h["acquisition"]["method"] == "operator_html_import" for h in hit["results"])
        saved = (await client.call_tool("get_evidence", {"evidence_id": anchor["evidence_id"]})).structured_content[
            "evidence"
        ]
        assert saved["snapshot"]["acquisition"]["url_basis"] == "operator_supplied"
        assert "private-form-token" not in json.dumps(saved) and "private-script-token" not in json.dumps(saved)
        assert saved["snapshot"]["published_at"] is None  # No timezone invented for the old displayed date.
        context = (
            await client.call_tool("get_collected_thread", {"evidence_id": anchor["evidence_id"]})
        ).structured_content
        assert context["root_observed"] and context["total_stored_posts"] == 2
        assert context["results"][0]["acquisition"]["network_read"] is False
        earlier = (
            await client.call_tool(
                "get_collected_thread", {"evidence_id": anchor["evidence_id"], "as_of": "2025-01-02T00:00:00Z"}
            )
        ).structured_content
        assert earlier["error"]["code"] == "evidence_not_observed"
        refresh = (
            await client.call_tool("refresh_evidence", {"evidence_id": anchor["evidence_id"]})
        ).structured_content
        assert refresh["error"]["code"] == "operator_capture_reimport_required"
    async with Client(create_server(settings)) as client:
        replay = (
            await client.call_tool(
                "get_evidence", {"evidence_id": anchor["evidence_id"], "revision_id": anchor["revision_id"]}
            )
        ).structured_content
        assert replay["evidence"]["snapshot_hash"] == anchor["snapshot_hash"]
    store = EvidenceStore(settings.data_dir)
    summary = CorpusCoverage(store).get(kind="forum")["summary"]
    assert summary["operator_import_forum_items"] == 3 and summary["native_forum_items"] == 0
    assert summary["unreported_acquisition_forum_items"] == 0
    assert store.health(SID)["results"][0]["last_access_state"] == "never_attempted"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM cooldowns").fetchone()[0] == 0
    network.assert_not_awaited()


def test_import_preserves_exact_native_revision_and_does_not_duplicate_post_identity(settings, tmp_path):
    catalog = SourceCatalog(settings)
    store = EvidenceStore(settings.prepare_data_dir())
    service = CollectionService(settings, catalog, store, None, None)
    page = parse_forum(catalog.get(SID), URL, document("Earlier native body"), 60000, 100)
    page.acquisition = {"method": "native_fetch", "network_read": True}
    service.observe_forum(page)
    native = page.posts[0].evidence
    imported = import_forum_capture(settings, SID, URL, file_at(tmp_path, document()))
    item = imported["posts"][0]["evidence"]
    assert native["evidence_id"] == item["evidence_id"] and item["revision_id"] != native["revision_id"]
    old = store.get(native["evidence_id"], native["revision_id"])
    assert old["snapshot_hash"] == native["snapshot_hash"] and old["snapshot"]["text"] == "Earlier native body"
    assert old["snapshot"]["acquisition"]["method"] == "native_fetch"
    ctx = ThreadContext(store, 60000).get(item["evidence_id"])
    assert ctx["total_stored_posts"] == 1 and ctx["results"][0]["acquisition"]["method"] == "operator_html_import"
    # Inventory counts the selected latest method; older native revisions remain searchable.
    summary = CorpusCoverage(store).get(kind="forum")["summary"]
    assert summary["operator_import_forum_items"] == 2 and summary["native_forum_items"] == 0
    historical = store.search("Earlier native body", kind="forum")
    post = next(x for x in historical["results"] if x["evidence_id"] == native["evidence_id"])
    assert not post["is_latest_revision"] and post["acquisition"]["method"] == "native_fetch"


@pytest.mark.parametrize(
    "html,url,code",
    [
        ("<title>Verify Humanity</title>challenge", URL, "forum_capture_unreadable"),
        ("<title>Login</title>Sign in to continue", URL, "forum_capture_unreadable"),
        ("<html>No forum posts or titles</html>", URL, "forum_capture_unreadable"),
        (document().replace(URL + '"', 'https://other.example/Thread-fixture"'), URL, "forum_capture_scope_mismatch"),
        (
            document().replace('href="' + URL + '"', 'href="' + ORIGIN + '/Thread-unrelated"'),
            URL,
            "forum_capture_scope_mismatch",
        ),
        (document(), ORIGIN + "/showthread.php?tid=1&action=delete", "invalid_forum_url"),
        (document(), "https://127.0.0.1/Thread-fixture", "invalid_forum_url"),
    ],
)
def test_gates_wrong_source_and_actions_are_not_imported(settings, tmp_path, html, url, code):
    with pytest.raises(DataAccessError) as exc:
        import_forum_capture(settings, SID, url, file_at(tmp_path, html))
    assert exc.value.code == code
    assert not (settings.data_dir / "evidence.sqlite3").exists()


def test_file_bounds_encoding_and_invalid_cli_inputs_do_not_echo_content(settings, tmp_path, monkeypatch):
    file = file_at(tmp_path, "x" * (settings.max_response_bytes + 1))
    for raw in [file.read_bytes(), b"\xff\xfeprivate-secret", b""]:
        file.write_bytes(raw)
        with pytest.raises(DataAccessError) as exc:
            import_forum_capture(settings, SID, URL, file)
        assert exc.value.code == "invalid_forum_capture" and "private-secret" not in str(exc.value)
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    result = CliRunner().invoke(cli.app, ["forum-page-import", SID, URL, str(file)])
    assert result.exit_code == 1 and "private-secret" not in result.output


def test_partial_page_reports_omissions_and_can_be_reimported_at_higher_bound(settings, tmp_path):
    extra = '<div id="post_43"><div class="post_body">Second visible post</div></div>'
    file = file_at(tmp_path, document(extra=extra))
    partial = import_forum_capture(settings, SID, URL, file, limit=1)
    assert partial["omitted_posts"] == 1 and partial["truncated"] and len(partial["posts"]) == 1
    full = import_forum_capture(settings, SID, URL, file)
    assert len(full["posts"]) == 2 and full["omitted_posts"] == 0 and not full["truncated"]
    assert full["posts"][0]["evidence"]["revision_id"] == partial["posts"][0]["evidence"]["revision_id"]


def test_index_capture_preserves_listing_context_and_pagination(settings, tmp_path):
    html = """<title>Buyer Requests</title><table><tr class="inline_row">
    <td><span id="tid_17"><a href="Thread-fixture">Acme request</a></span></td></tr></table>
    <a class="pagination_next" href="Forum-Buyer-Requests?page=2">Next</a>"""
    result = import_forum_capture(settings, SID, ORIGIN + "/Forum-Buyer-Requests", file_at(tmp_path, html))
    assert result["page_type"] == "index" and result["thread_listings"] == 1 and result["posts"] == []
    snapshot = EvidenceStore(settings.data_dir).get(result["evidence"]["evidence_id"])["snapshot"]
    assert snapshot["thread_listing_scope"] == "recognized_thread_rows"
    assert snapshot["threads"][0]["title"] == "Acme request" and snapshot["threads"][0]["pinned"] is None
    assert snapshot["next_page_urls"] == [ORIGIN + "/Forum-Buyer-Requests?page=2"]


async def test_explicit_native_read_after_import_keeps_method_specific_history(settings, tmp_path):
    imported = import_forum_capture(settings, SID, URL, file_at(tmp_path, document()))
    ref = imported["posts"][0]["evidence"]
    catalog = SourceCatalog(settings)
    store = EvidenceStore(settings.data_dir)
    forums = ForumConnector(settings, catalog)
    forums.fetch = AsyncMock(return_value=parse_forum(catalog.get(SID), URL, document(), 60000, 100))
    service = CollectionService(settings, catalog, store, None, None, forums)
    native = await service.forum_page(SID, URL)
    assert native.acquisition["method"] == "native_fetch"
    latest = store.get(ref["evidence_id"])
    assert latest["snapshot"]["acquisition"]["network_read"]
    assert latest["content_hash"] == ref["content_hash"] and latest["revision_id"] != ref["revision_id"]
    old = store.get(ref["evidence_id"], ref["revision_id"])
    assert old["snapshot"]["acquisition"]["method"] == "operator_html_import"
    summary = CorpusCoverage(store).get(kind="forum")["summary"]
    assert summary["native_forum_items"] == 2 and summary["operator_import_forum_items"] == 0


@pytest.mark.parametrize("engine", ["xenforo", "q2a"])
def test_other_configured_parsers_preserve_capture_provenance_and_thread_identity(settings, tmp_path, engine):
    if engine == "xenforo":
        sid = "forum:hard-tm"
        url = "https://hard-tm.com/threads/42/"
        html = """<title>Captured request</title><link rel="canonical" href="/threads/request.42/">
        <article class="message--post" data-content="post-11" data-position="0" data-author="fixture">
        <div class="message-body"><div class="bbWrapper">Visible original</div></div></article>"""
    else:
        sid = "onion:a9bcde64e044"
        source = SourceCatalog().get(sid)
        url = urljoin(source["locator"], "/42/request")
        html = """<title>Captured question</title><link rel="canonical" href="/42/request">
        <div class="qa-q-view" id="q42"><div class="qa-q-view-content qa-post-content">
        <div itemprop="text">Visible question</div></div><a class="qa-user-link">fixture</a></div>"""
    imported = import_forum_capture(settings, sid, url, file_at(tmp_path, html))
    assert len(imported["posts"]) == 1
    saved = ThreadContext(EvidenceStore(settings.data_dir), 60000).get(imported["posts"][0]["evidence"]["evidence_id"])
    assert saved["root_observed"] and saved["results"][0]["acquisition"]["method"] == "operator_html_import"

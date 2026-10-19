from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from mcp import Client

from dark_mcp.coverage import CorpusCoverage
from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.forum_feeds import ForumFeeds, parse_feed
from dark_mcp.forums import ForumConnector, feed_url, source_url
from dark_mcp.references import ReferenceSearch
from dark_mcp.server import create_server
from dark_mcp.sources import SourceCatalog

SOURCE = "onion:91e4e785a3af"


def rss(*items):
    return (
        '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>Forum</title>'
        "<lastBuildDate>Mon, 19 Oct 2026 21:00:00 +0200</lastBuildDate>" + "".join(items) + "</channel></rss>"
    )


def item(source, tid=1, text="Acme.example source claim", date="Mon, 19 Oct 2026 20:00:00 +0200", extra=""):
    return f"""<item><title>Thread {tid}</title><link>{source["locator"]}showthread.php?tid={tid}</link>
    <guid>{source["locator"]}showthread.php?tid={tid}</guid><dc:creator>Unverified author</dc:creator>
    <pubDate>{date}</pubDate><description><![CDATA[{text}]]></description>{extra}</item>"""


def test_feed_dates_quotes_inline_names_and_metadata_are_separate():
    source = SourceCatalog().get(SOURCE)
    html = "A<span>cme</span>.example<br>Public body <blockquote>QuotedCorp.example claim</blockquote>"
    html += '<a href="https://company.example/path">short</a><script>instructions()</script>'
    result = parse_feed(source, source["feed_urls"][0], rss(item(source, text=html)), 60000, 20)
    record = result["results"][0]
    assert "Acme.example" in record["text"] and "QuotedCorp" not in record["text"]
    assert "QuotedCorp" in record["quotes"][0]["text"] and "instructions()" not in record["text"]
    assert record["published_at"] == "2026-10-19T18:00:00+00:00"
    assert record["published_at"] != result["feed_build_date_raw"]
    assert record["links"][0]["url"] == "https://company.example/path"
    assert record["reported_author"] == "Unverified author"
    assert record["content_scope"] == "source_feed_item" and result["history_complete"] is False


def test_timezone_free_pubdate_and_feed_build_date_do_not_supply_publication():
    source = SourceCatalog().get(SOURCE)
    result = parse_feed(source, source["feed_urls"][0], rss(item(source, date="Mon, 19 Oct 2026 20:00:00")), 1000, 20)
    assert result["results"][0]["published_at"] is None
    assert result["results"][0]["published_at_raw"] == "Mon, 19 Oct 2026 20:00:00"


def test_literal_html_doctype_in_cdata_is_content_not_an_xml_dtd():
    source = SourceCatalog().get(SOURCE)
    result = parse_feed(
        source,
        source["feed_urls"][0],
        rss(item(source, text="<!DOCTYPE html><p>Acme.example public claim</p>")),
        1000,
        20,
    )
    assert result["results"][0]["text"] == "Acme.example public claim"


def test_feed_author_markup_is_metadata_with_a_validated_source_local_profile():
    source = SourceCatalog().get(SOURCE)
    document = rss(item(source)).replace(
        "Unverified author",
        f'<![CDATA[<a href="{source["locator"]}member.php?action=profile&uid=123">Example Author</a>]]>',
    )
    row = parse_feed(source, source["feed_urls"][0], document, 1000, 20)["results"][0]
    assert row["reported_author"] == "Example Author" and row["reported_author_id"] == "123"
    assert row["reported_author_identity"]["basis"] == "source_feed_author_markup"
    assert row["reported_author_identity"]["identity_verified"] is False
    forged = document.replace(source["locator"] + "member.php", "https://evil.example/member.php")
    bad = parse_feed(source, source["feed_urls"][0], forged, 1000, 20)["results"][0]
    assert bad["reported_author_id"] is None and bad["reported_author_url"] is None


def test_leading_xml_whitespace_is_normalized_but_arbitrary_preludes_and_dtds_are_rejected():
    source = SourceCatalog().get(SOURCE)
    document = '<?xml version="1.0" encoding="UTF-8"?>' + rss(item(source))
    row = parse_feed(source, source["feed_urls"][0], "\r\n" + document, 1000, 20)["results"][0]
    assert row["published_at"] == "2026-10-19T18:00:00+00:00"
    for bad in ("PHP warning\n" + document, '\r\n<!DOCTYPE rss SYSTEM "http://127.0.0.1/secret">' + rss(item(source))):
        with pytest.raises(DataAccessError):
            parse_feed(source, source["feed_urls"][0], bad, 1000, 20)


@pytest.mark.parametrize(
    "document",
    [
        '<!DOCTYPE rss [<!ENTITY x "expansion">]><rss><channel/></rss>',
        "<html><title>Login or verify humanity</title></html>",
        "<rss>broken",
    ],
)
def test_gates_dtds_and_malformed_documents_are_not_evidence(document):
    source = SourceCatalog().get(SOURCE)
    with pytest.raises(DataAccessError):
        parse_feed(source, source["feed_urls"][0], document, 1000, 20)


def test_unsafe_links_duplicates_and_local_window_omissions_are_counted():
    source = SourceCatalog().get(SOURCE)
    unsafe = item(source, 2).replace(source["locator"], "https://evil.example/")
    result = parse_feed(
        source, source["feed_urls"][0], rss(item(source), item(source), unsafe, item(source, 3)), 1000, 1
    )
    assert result["invalid_items"] == 1 and result["duplicate_items"] == 1 and result["omitted_items"] == 1
    assert result["results"][0]["url"].startswith(source["locator"])


def test_feed_routes_are_exact_and_do_not_expand_normal_thread_permissions():
    source = SourceCatalog().get(SOURCE)
    url = source["feed_urls"][0]
    assert feed_url(source) == url
    with pytest.raises(DataAccessError):
        source_url(source, url)
    for wrong in (
        url + "?action=delete",
        url + "?limit=1000",
        url.replace("/syndication.php", "/member.php"),
        url.replace(source["allowed_hosts"][0], "elsewhere.onion"),
    ):
        with pytest.raises(DataAccessError):
            feed_url(source, wrong)
    with pytest.raises(DataAccessError):
        feed_url(SourceCatalog().get("forum:hard-tm"))


async def test_feed_replay_native_isolation_and_reference_search(settings):
    source = SourceCatalog().get(SOURCE)
    document = rss(item(source, text='Acme.example <a href="https://company.example/">short</a>'))
    forums = ForumConnector(settings, SourceCatalog())
    forums.fetch_document = AsyncMock(return_value=(source["feed_urls"][0], document, {"state": "public"}))
    store = EvidenceStore(settings.prepare_data_dir())
    reader = ForumFeeds(settings, forums, store)
    first = await reader.read(SOURCE)
    ev = first["results"][0]["evidence"]
    forums.fetch_document.return_value = (
        source["feed_urls"][0],
        document.replace("21:00:00", "22:00:00"),
        {"state": "public"},
    )
    second = await reader.read(SOURCE)
    assert second["results"][0]["evidence"]["change_type"] == "unchanged"
    assert second["results"][0]["evidence"]["revision_id"] == ev["revision_id"]
    assert first["acquisition"]["document_text_sha256"] != second["acquisition"]["document_text_sha256"]
    assert len(store.search("Acme.example", kind="forum_feed")["results"]) == 1
    assert store.search("Acme.example", kind="forum")["results"] == []
    assert ReferenceSearch(store).search(host="company.example", kind="forum_feed")["results"]
    assert CorpusCoverage(store).get(kind="forum_feed")["summary"]["indexed_forum_posts"] == 0


async def test_feed_wait_shares_source_cooldown_with_original_thread_reader(settings):
    forums = ForumConnector(settings, SourceCatalog())
    forums.fetch_document = AsyncMock(side_effect=DataAccessError("onion_rate_limited", "Wait", retry_after=120))
    store = EvidenceStore(settings.prepare_data_dir())
    reader = ForumFeeds(settings, forums, store)
    with pytest.raises(DataAccessError):
        await reader.read(SOURCE)
    with pytest.raises(DataAccessError) as error:
        await reader.read(SOURCE)
    assert error.value.code == "collection_rate_limited" and forums.fetch_document.call_count == 1
    with pytest.raises(DataAccessError):
        store.cooldown("onion:" + SOURCE)


@respx.mock
async def test_https_transport_xml_is_feed_only_with_pinned_host_and_robots(settings, monkeypatch):
    catalog = SourceCatalog()
    source = catalog.sources["forum:bf-st"]
    source["feed_urls"] = ["https://bf.st/syndication.php"]
    forums = ForumConnector(settings, catalog)
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(forums, "_pace", AsyncMock())
    respx.get("https://8.8.8.8/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /"))
    route = respx.get("https://8.8.8.8/syndication.php").mock(
        return_value=httpx.Response(200, text=rss(item(source)), headers={"Content-Type": "application/rss+xml"})
    )
    url, document, context = await forums.fetch_document(source["id"], source["feed_urls"][0], feed=True)
    assert "<rss" in document and route.calls[0].request.headers["host"] == "bf.st"
    with pytest.raises(DataAccessError):
        await forums.fetch(source["id"], source["feed_urls"][0])
    assert route.call_count == 1


async def test_mcp_feed_search_refresh_and_missing_window_item_are_not_deletion(settings, monkeypatch):
    source = SourceCatalog().get(SOURCE)
    document = rss(item(source))
    fake = AsyncMock(return_value=(source["feed_urls"][0], document, {"state": "public"}))
    monkeypatch.setattr(ForumConnector, "fetch_document", fake)
    async with Client(create_server(settings)) as client:
        result = (await client.call_tool("read_forum_feed", {"source_id": SOURCE})).structured_content
        assert result["ok"] and result["results"][0]["published_at"]
        eid = result["results"][0]["evidence"]["evidence_id"]
        fake.return_value = (source["feed_urls"][0], rss(item(source, 2)), {"state": "public"})
        refreshed = (await client.call_tool("refresh_evidence", {"evidence_id": eid})).structured_content
        assert refreshed["ok"] and refreshed["requested_item_returned"] is False
        old = (await client.call_tool("get_evidence", {"evidence_id": eid})).structured_content
        assert (
            old["evidence"]["availability"] == "observed"
            and old["evidence"]["snapshot"]["text"] == "Acme.example source claim"
        )

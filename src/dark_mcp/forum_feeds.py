"""Source-owned RSS windows: distinct from original thread bodies, histories and timestamps."""

import asyncio
import hashlib
import re
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from dark_mcp.authors import author_fields
from dark_mcp.errors import DataAccessError
from dark_mcp.forums import feed_url, reference_links, source_url, thread_identity
from dark_mcp.models import limit_value, now

COVERAGE = (
    "One source-owned RSS window, not complete history or original-thread context. Feed text may be edited, "
    "excerpted or gated by the publisher; quoted text is separate. Publication is the source item's reported "
    "pubDate, not our first observation or independently verified thread creation. Follow the original thread "
    "for replies and access context. Missing items in a later feed window do not establish deletion."
)


def rss_date(value):
    if not value:
        return None
    try:
        date = parsedate_to_datetime(value)
        return date.astimezone(UTC).isoformat() if date.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def reported_author(source: dict, item_url: str, raw: str | None) -> dict:
    markup = BeautifulSoup((raw or "")[:4000], "html.parser")
    name = markup.get_text(" ", strip=True)[:500] or None
    anchors = markup.select("a[href]")
    anchor = anchors[0] if len(anchors) == 1 else None
    if anchor:
        anchor.attrs.pop("data-user-id", None)  # Only explicit, validated profile links identify feed accounts.
    identity = author_fields(source, item_url, name, anchor)
    identity["author_identity"]["basis"] = "source_feed_author_markup"
    return {
        "reported_author": name,
        "reported_author_raw": raw[:2000] if raw else None,
        "reported_author_raw_truncated": bool(raw and len(raw) > 2000),
        "reported_author_id": identity["author_id"],
        "reported_author_url": identity["author_url"],
        "reported_author_identity": identity["author_identity"],
    }


def feed_body(document: str, base_url: str, max_chars: int):
    soup = BeautifulSoup(document, "html.parser")
    for node in soup.select("script,style,iframe,object,embed,form"):
        node.decompose()
    quotes = []
    quote_nodes = [node for node in soup.select("blockquote") if node.find_parent("blockquote") is None]
    for ordinal, node in enumerate(quote_nodes):
        if ordinal < 20:
            quoted = node.get_text(" ", strip=True)
            quotes.append(
                {
                    "text": quoted[:1000],
                    "truncated": len(quoted) > 1000,
                    "links": reference_links(node, base_url),
                    "verification": "source_feed_quote_unverified",
                }
            )
        node.decompose()
    links = reference_links(soup, base_url)
    for br in soup.select("br"):
        br.replace_with("\n")
    for block in soup.select("p,div,li"):
        block.insert_before("\n")
        block.insert_after("\n")
    text = soup.get_text("", strip=False).strip()
    return {
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
        "links": links,
        "quotes": quotes,
        "omitted_quotes": max(0, len(quote_nodes) - 20),
        "hidden_content": True
        if re.search(r'\[(?:hide|lock)\]|class=["\'][^"\']*(?:hideContent|hidden-content)', document, re.I)
        else None,
    }


def parse_feed(source: dict, url: str, document: str, max_chars: int, limit: int) -> dict:
    limit_value(limit, 50)

    class NoDTD(ET.TreeBuilder):
        def doctype(self, name, pubid, system):
            raise DataAccessError("unsafe_forum_feed", "DTD/entity declarations are unsupported in source feeds")

    try:
        # Some advertised feeds place whitespace/BOM before the XML declaration. Do not strip other preludes.
        root = ET.fromstring(document.lstrip("\ufeff \t\r\n"), parser=ET.XMLParser(target=NoDTD()))
    except (ET.ParseError, ValueError):
        raise DataAccessError(
            "forum_feed_unavailable", "Source returned malformed XML or a non-feed access page"
        ) from None
    channel = root.find("channel") if root.tag == "rss" else None
    if channel is None:
        raise DataAccessError(
            "forum_feed_unavailable", "Expected a source-owned RSS channel, not a gate or other document"
        )
    nodes = channel.findall("item")
    items, invalid, duplicates, seen = [], 0, 0, set()
    for node in nodes[:200]:
        link = node.findtext("link")
        try:
            if not link:
                raise ValueError
            link = source_url(source, urljoin(url, link.strip()))
            if thread_identity(link) is None:
                raise ValueError
        except (DataAccessError, ValueError):
            invalid += 1
            continue
        if link in seen:
            duplicates += 1
            continue
        seen.add(link)
        encoded = node.find("{http://purl.org/rss/1.0/modules/content/}encoded")
        field = "content:encoded" if encoded is not None else "description"
        body = encoded.text or "" if encoded is not None else node.findtext("description") or ""
        body_parts = feed_body(body, link, max_chars)
        raw_date = node.findtext("pubDate")
        date = rss_date(raw_date)
        author = node.findtext("{http://purl.org/dc/elements/1.1/}creator") or node.findtext("author")
        items.append(
            {
                "url": link,
                "title": (node.findtext("title") or "")[:1000],
                **body_parts,
                **reported_author(source, link, author),
                "reported_guid": (node.findtext("guid") or "")[:1000] or None,
                "published_at": date,
                "published_at_raw": raw_date[:500] if raw_date else None,
                "timestamp_basis": "source_rss_pubDate_explicit_offset" if date else "unresolved_source_feed_date",
                "content_scope": "source_feed_item",
                "body_field": field,
            }
        )
    return {
        "source_id": source["id"],
        "feed_url": url,
        "feed_title": (channel.findtext("title") or "")[:1000],
        "feed_build_date_raw": (channel.findtext("lastBuildDate") or "")[:500] or None,
        "results": items[:limit],
        "feed_items_reported": len(nodes),
        "examined_items": min(200, len(nodes)),
        "unexamined_items": max(0, len(nodes) - 200),
        "invalid_items": invalid,
        "duplicate_items": duplicates,
        "omitted_items": max(0, len(items) - limit),
        "history_complete": False,
        "coverage": COVERAGE,
    }


class ForumFeeds:
    def __init__(self, settings, forums, store):
        self.settings, self.forums, self.store = settings, forums, store

    async def read(self, source_id: str, url: str | None = None, limit: int = 20) -> dict:
        limit_value(limit, 50)
        source = self.forums.source(source_id)
        target = feed_url(source, url)
        self.store.register(source_id, source["kind"], source.get("locator"), source["name"], source)
        cooldown = ("onion:" if source.get("transport") == "tor" else "forum:") + source_id
        self.store.cooldown(cooldown)
        attempt = self.store.begin_attempt(source_id, {"url": target, "method": "source_rss", "limit": limit})
        try:
            final_url, document, access = await self.forums.fetch_document(source_id, target, feed=True)
            result = parse_feed(source, final_url, document, self.settings.max_text_chars, limit)
            result["collected_at"] = now()
            acquisition = {
                "method": "source_rss",
                "network_read": True,
                "transport": source.get("transport", "direct_https"),
                "source_identity_verified": False,
            }
            provenance = {
                **acquisition,
                "requested_url": final_url,
                "document_text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                "access_context": access,
            }
            result["acquisition"] = provenance
            for item in result["results"]:
                item["acquisition"] = acquisition
                snapshot = {**item, "access_state": "content_returned_unverified"}
                item["evidence"] = self.store.observe(
                    source_id,
                    "forum_feed",
                    source_id + ":" + item["url"],
                    item["url"],
                    snapshot,
                    result["collected_at"],
                    provenance=provenance,
                )
            self.store.finish_attempt(
                attempt,
                "content_returned_unverified",
                {
                    "url": final_url,
                    "feed_items": len(result["results"]),
                    "invalid_items": result["invalid_items"],
                    "unexamined_items": result["unexamined_items"],
                    "omitted_items": result["omitted_items"],
                },
            )
            return result
        except asyncio.CancelledError:
            self.store.finish_attempt(attempt, "cancelled", {"url": target, "content_read_complete": False})
            raise
        except DataAccessError as error:
            self.store.record_retry(cooldown, error)
            self.store.finish_attempt(attempt, error.code, error.as_dict())
            raise

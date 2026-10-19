"""Ingest an operator's saved forum HTML without browser control, scripts or network access."""

import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from dark_mcp.collection import CollectionService
from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.forums import parse_forum, source_url, thread_identity
from dark_mcp.models import classify_page, limit_value
from dark_mcp.sources import SourceCatalog
from dark_mcp.thread_context import forum_thread_key


def import_forum_capture(settings, source_id: str, url: str, file: Path, limit: int = 100) -> dict:
    """An import is an operator assertion, never proof of source identity, membership or capture time."""
    limit_value(limit)
    catalog = SourceCatalog(settings)
    source = catalog.get(source_id)
    if source["collection_method"] != "forum_page":
        raise DataAccessError("source_access_required", "Configure a supported forum parser before importing HTML")
    url = source_url(source, url)
    try:
        with file.open("rb") as stream:
            raw = stream.read(settings.max_response_bytes + 1)
        if not raw or len(raw) > settings.max_response_bytes:
            raise ValueError
        document = raw.decode("utf-8-sig")
    except (OSError, ValueError, UnicodeError):
        raise DataAccessError(
            "invalid_forum_capture", "Use a bounded UTF-8 HTML file saved from an accessible forum page"
        ) from None
    soup = BeautifulSoup(document, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if classify_page(title, soup.get_text(" ", strip=True)) == "challenge_suspected":
        raise DataAccessError("forum_capture_unreadable", "A challenge page does not contain collected discussion")
    # A canonical link can disprove the supplied scope, but cannot authenticate imported HTML.
    for canonical in soup.select('link[rel~="canonical"][href]'):
        try:
            target = source_url(source, urljoin(url, canonical["href"]))
        except DataAccessError:
            raise DataAccessError("forum_capture_scope_mismatch", "Canonical URL is outside this source") from None
        identity = thread_identity(url)
        if (identity is not None and forum_thread_key(thread_identity(target)) != forum_thread_key(identity)) or (
            identity is None and urlsplit(target).path.rstrip("/") != urlsplit(url).path.rstrip("/")
        ):
            raise DataAccessError("forum_capture_scope_mismatch", "Canonical URL does not match the supplied page")
    page = parse_forum(source, url, document, settings.max_text_chars, limit)
    if not page.posts and not page.threads:
        raise DataAccessError("forum_capture_unreadable", "No supported original posts or thread listings in this file")
    page.acquisition = {
        "method": "operator_html_import",
        "network_read": False,
        "source_identity_verified": False,
        "url_basis": "operator_supplied",
        "document_sha256": hashlib.sha256(raw).hexdigest(),
    }
    page.access_context = {"state": "operator_supplied_html", "authentication_verified": False}
    page.coverage = (
        "One operator-supplied HTML page. Source URL and provenance are unverified assertions. "
        "Local recording time is the import time, not browser capture time. No requests or scripts executed. "
        "Hidden content, omitted posts and unvisited pages remain gaps."
    )
    store = EvidenceStore(settings.prepare_data_dir())
    # No attempt/health/cooldown changes: a local import cannot prove current remote access.
    service = CollectionService(settings, catalog, store, None, None)
    service.observe_forum(page)
    return {
        "source_id": source_id,
        "url": page.url,
        "recorded_at": page.collected_at,
        "acquisition": page.acquisition,
        "page_type": page.page_type,
        "access_state": page.access_state,
        "evidence": page.evidence,
        "posts": [{"id": post.id, "evidence": post.evidence} for post in page.posts],
        "thread_listings": len(page.threads),
        "hidden_posts": sum(post.hidden_content for post in page.posts),
        "truncated": page.truncated,
        "omitted_posts": page.omitted_posts,
        "omitted_threads": page.omitted_threads,
        "next_page_urls": page.next_page_urls,
        "coverage": page.coverage,
    }

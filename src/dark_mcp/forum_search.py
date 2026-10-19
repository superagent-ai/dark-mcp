"""Bounded source-native full-post discovery; excerpts never stand in for original posts."""

import asyncio
import hashlib
import json
import re
import uuid
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from dark_mcp.authors import author_fields
from dark_mcp.errors import DataAccessError
from dark_mcp.forum_search_urls import initial_search_url, search_config, search_query, search_url
from dark_mcp.forums import source_url, thread_identity
from dark_mcp.models import classify_page, limit_value, now

COVERAGE = (
    "Source-native search using the source's tokenization, ranking and visibility. Search snippets may match "
    "titles, quoted text or content absent from the excerpt. They are not original post bodies or independent "
    "corroboration. Read each original URL and verify the matching post ID before deriving signals. Dates without "
    "an explicit timezone remain unresolved. Exhausting this result window does not prove complete forum "
    "coverage or absence of threats. Cached remote searches can expire or change between pages."
)
NO_RESULTS = (
    "Sorry, but no results were returned using the query information you provided. "
    "Please redefine your search terms and try again."
)


def matched_post_url(source, base_url, value):
    """Handle the observed MyBB SEO pid?highlight bug only in an explicit matched-post link."""
    parts = urlsplit(urljoin(base_url, value))
    query = parts.query
    malformed = re.fullmatch(r"pid=([1-9][0-9]{0,9})\?highlight=[^?&#]*", query)
    if malformed:
        query = "pid=" + malformed[1]
    fields = parse_qs(query, keep_blank_values=True)
    if set(fields) - {"pid", "tid", "highlight"} or any(len(v) != 1 for v in fields.values()):
        raise ValueError("Invalid matched post fields")
    pid = fields.get("pid", [""])[0]
    if not re.fullmatch(r"[1-9][0-9]{0,9}", pid) or parts.fragment != "pid" + pid:
        raise ValueError("No matching post identifier")
    if "tid" in fields and not re.fullmatch(r"[1-9][0-9]{0,9}", fields["tid"][0]):
        raise ValueError("Invalid thread identifier")
    clean = source_url(source, parts._replace(query="", fragment="").geturl())
    if thread_identity(clean) is None and urlsplit(clean).path != "/showthread.php":
        raise ValueError("Not a thread link")
    return pid, source_url(source, urljoin(clean, "/showthread.php?pid=" + pid))


def parse_results(source, url, document, context, max_chars):
    soup = BeautifulSoup(document, "html.parser")
    messages = [
        node.get_text(" ", strip=True)
        for node in soup.select("td.trow1")
        if not node.find_parent("tr", class_="inline_row")
    ]
    for message in messages:
        wait = re.search(
            r"you can only perform one search every [0-9]+ seconds.*?wait another ([0-9]+) seconds", message, re.I
        )
        if wait:
            raise DataAccessError(
                "forum_search_rate_limited", "Source requires a search wait", retry_after=max(1, int(wait[1]))
            )
        if "An invalid search was specified. Please go back and try again." in message:
            raise DataAccessError(
                "forum_search_expired", "Source search cache expired; start a new search when permitted"
            )
    nodes = soup.select("tr.inline_row")
    if len(nodes) > 200:
        raise DataAccessError(
            "forum_search_page_too_large", "Result page exceeds 200 rows; no rows were silently skipped"
        )
    results, invalid, duplicate, seen = [], 0, 0, set()
    for row in nodes:
        cells = row.find_all("td", recursive=False)
        if len(cells) != 8:
            invalid += 1
            continue
        candidates = []
        for link in cells[2].select("a[href]"):
            try:
                pid, original = matched_post_url(source, url, link["href"])
                candidates.append((pid, original))
            except (DataAccessError, ValueError):
                pass
        if len(candidates) != 1:
            invalid += 1
            continue
        pid, original = candidates[0]
        if pid in seen:
            duplicate += 1
            continue
        seen.add(pid)
        title_link = cells[2].select_one("span.smalltext > a[href]")
        excerpt = cells[2].find("table")
        if title_link is None or excerpt is None:
            invalid += 1
            continue
        for tag in excerpt.select("script,style,iframe,object,embed,form"):
            tag.decompose()
        text = excerpt.get_text("", strip=False).strip()
        author_link = cells[3].select_one("a[href]")
        author = cells[3].get_text(" ", strip=True)[:500] or None
        identity = author_fields(source, url, author, author_link)
        identity["author_identity"]["basis"] = "source_search_row_markup"
        results.append(
            {
                "url": original,
                "matched_post_id": pid,
                "title": title_link.get_text(" ", strip=True)[:1000],
                "text": text[:max_chars],
                "truncated": len(text) > max_chars,
                "source_excerpt_may_be_truncated": True,
                "reported_author": author,
                "reported_author_id": identity["author_id"],
                "reported_author_url": identity["author_url"],
                "reported_author_identity": identity["author_identity"],
                "reported_forum": cells[4].get_text(" ", strip=True)[:500],
                "published_at_raw": cells[7].get_text(" ", strip=True)[:500] or None,
                "published_at": None,
                "timestamp_basis": "unresolved_source_search_date",
                "query": context["query"],
                "content_scope": "source_search_excerpt",
            }
        )
    negative = not nodes and NO_RESULTS in messages
    if not results and not negative:
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        state = classify_page(title, soup.get_text(" ", strip=True))
        raise DataAccessError(
            state if state != "content_returned_unverified" else "forum_search_layout_unrecognized",
            "No recognized native post-search results; this is not a negative finding",
        )
    current = int(parse_qs(urlsplit(url).query).get("page", ["1"])[0])
    markers = soup.select(".pagination_current")
    if markers and any(node.get_text(strip=True) != str(current) for node in markers):
        raise DataAccessError("forum_search_page_mismatch", "Source returned a different result page")
    following, rejected = set(), set()
    for anchor in soup.select(".pagination a[href]"):
        if anchor["href"].strip().lower() in {"javascript:void(0)", "javascript:void(0);"}:
            continue  # MyBB page-jump UI toggle, not a pagination destination.
        candidate = urljoin(url, anchor["href"])
        try:
            candidate = search_url(source, candidate, dict(context))
            page = int(parse_qs(urlsplit(candidate).query).get("page", ["1"])[0])
            if page == current + 1:
                following.add(candidate)
        except DataAccessError:
            rejected.add(candidate)
    if len(following) > 1:
        raise DataAccessError("forum_search_pagination_conflict", "Source returned conflicting next-result URLs")
    if negative and (following or rejected):
        raise DataAccessError("forum_search_layout_unrecognized", "Conflicting negative result and pagination")
    return {
        "results": results,
        "next_url": next(iter(following), None),
        "invalid_rows": invalid,
        "duplicate_rows": duplicate,
        "source_page": current,
        "negative_result": negative,
        "unrecognized_pagination_links": len(rejected),
        "source_result_window_exhausted": not following and not rejected and not invalid,
    }


class ForumSearch:
    def __init__(self, settings, forums, store):
        self.settings, self.forums, self.store = settings, forums, store
        with store.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS forum_search_cursors ("
                "id TEXT PRIMARY KEY,source_id TEXT NOT NULL,query TEXT NOT NULL,"
                "state TEXT NOT NULL,created_at TEXT NOT NULL)"
            )

    def save_cursor(self, source_id, query, state):
        cursor = "fsc_" + uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO forum_search_cursors VALUES(?,?,?,?,?)",
                (cursor, source_id, query, json.dumps(state), now()),
            )
        return cursor

    def cursor(self, source_id, query, cursor):
        if not isinstance(cursor, str) or not re.fullmatch(r"fsc_[0-9a-f]{32}", cursor):
            raise ValueError("Use the opaque next_cursor from this source/query")
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM forum_search_cursors WHERE id=?", (cursor,)).fetchone()
        if not row or row["source_id"] != source_id or row["query"] != query:
            raise DataAccessError("forum_search_cursor_mismatch", "Cursor does not belong to this source/query")
        return json.loads(row["state"])

    def emit(self, source_id, query, state, limit, network_read):
        refs, rest = state["pending"][:limit], state["pending"][limit:]
        results = []
        for ref in refs:
            item = self.store.get(ref["evidence_id"], ref["revision_id"])
            results.append({**item["snapshot"], "collected_at": ref["collected_at"], "evidence": ref})
        next_cursor = (
            self.save_cursor(source_id, query, {**state, "pending": rest}) if rest or state["next_url"] else None
        )
        return {
            "source_id": source_id,
            "query": query,
            "results": results,
            "next_cursor": next_cursor,
            **state["page_info"],
            "source_result_window_exhausted": state["page_info"]["source_result_window_exhausted"] and not rest,
            "cached_results_remaining": len(rest),
            "acquisition": {
                **state.get("provenance", {}),
                "method": "source_native_forum_search",
                "network_read": network_read,
            },
            "collected_at": state["collected_at"],
            "history_complete": False,
            "coverage": COVERAGE,
        }

    async def search(self, source_id, query, limit=20, cursor=None):
        limit_value(limit, 50)
        query = search_query(query)
        source = self.forums.source(source_id)
        search_config(source)
        state = self.cursor(source_id, query, cursor) if cursor is not None else None
        if state and state["pending"]:
            return self.emit(source_id, query, state, limit, False)
        context = {"query": query, **({"sid": state["sid"]} if state else {})}
        target = search_url(source, state["next_url"], context) if state else initial_search_url(source, query)
        self.store.register(source_id, source["kind"], source["locator"], source["name"], source)
        scope = ("onion:" if source.get("transport") == "tor" else "forum:") + source_id
        self.store.cooldown(scope)
        search_scope = "forum-search:" + source_id
        if state is None:
            self.store.cooldown(search_scope)
        token = self.store.claim_connector("forum-search-read:" + source_id, self.settings.request_timeout + 15)
        attempt_context = {"method": "source_native_forum_search", "query": query, "continuation": state is not None}
        attempt = self.store.begin_attempt(source_id, attempt_context)
        try:
            if state is None:
                self.store.cooldown(search_scope)  # Recheck under the cross-process lease.
                self.store.cooldown(search_scope, 30)
            final_url, document, access = await self.forums.fetch_document(source_id, target, search=context)
            final_url = search_url(source, final_url, context)
            page = parse_results(source, final_url, document, context, self.settings.max_text_chars)
            fingerprint = hashlib.sha256(
                json.dumps([r["matched_post_id"] for r in page["results"]]).encode()
            ).hexdigest()
            if state and page["results"] and fingerprint == state["fingerprint"]:
                raise DataAccessError("forum_search_repeated_page", "Source repeated the previous result page")
            collected_at = now()
            acquisition = {
                "method": "source_native_forum_search",
                "network_read": True,
                "transport": source.get("transport", "direct_https"),
                "source_identity_verified": False,
            }
            provenance = {
                **acquisition,
                "query": query,
                "source_page": page["source_page"],
                "document_text_sha256": hashlib.sha256(document.encode()).hexdigest(),
                "access_context": access,
            }
            refs = []
            query_key = hashlib.sha256(query.encode()).hexdigest()
            for item in page["results"]:
                snapshot = {**item, "acquisition": acquisition, "access_state": "preview_only_unverified"}
                evidence = self.store.observe(
                    source_id,
                    "forum_search",
                    source_id + ":" + item["matched_post_id"] + ":" + query_key,
                    item["url"],
                    snapshot,
                    collected_at,
                    provenance=provenance,
                )
                refs.append(
                    {
                        "evidence_id": evidence["evidence_id"],
                        "revision_id": evidence["revision_id"],
                        "collected_at": collected_at,
                    }
                )
            new_state = {
                "pending": refs,
                "next_url": page["next_url"],
                "sid": context.get("sid"),
                "fingerprint": fingerprint,
                "collected_at": collected_at,
                "provenance": provenance,
                "page_info": {k: v for k, v in page.items() if k not in {"results", "next_url"}},
            }
            result = self.emit(source_id, query, new_state, limit, True)
            self.store.finish_attempt(
                attempt,
                "preview_only_unverified",
                {**attempt_context, "acquisition": provenance, "saved_results": len(refs), **new_state["page_info"]},
            )
            return result
        except asyncio.CancelledError:
            self.store.finish_attempt(attempt, "cancelled", {**attempt_context, "content_read_complete": False})
            raise
        except DataAccessError as error:
            self.store.record_retry(scope, error)
            if error.retry_after is not None:
                self.store.record_retry(search_scope, error)
            self.store.finish_attempt(attempt, error.code, {**attempt_context, **error.as_dict()})
            raise
        finally:
            self.store.release_connector("forum-search-read:" + source_id, token)

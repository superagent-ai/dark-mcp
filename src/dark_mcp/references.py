"""Search extracted references in immutable evidence, without following their URLs."""

import ipaddress
import json
from urllib.parse import urlsplit

import idna

from dark_mcp.models import limit_value


def canonical_host(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 253
        or any(ord(c) < 33 or ord(c) == 127 for c in value)
        or any(c in value for c in "/\\@?#%")
    ):
        raise ValueError("Use a hostname or IP address, without a URL, credentials or port")
    if value.startswith("[") and value.endswith("]"):
        return ipaddress.IPv6Address(value[1:-1]).compressed.lower()
    value = value.removesuffix(".")
    try:
        return ipaddress.ip_address(value).compressed.lower()
    except ValueError:
        try:
            return idna.encode(value, uts46=True, transitional=False, std3_rules=True).decode("ascii").lower()
        except idna.IDNAError:
            raise ValueError("Use a valid hostname or IP address") from None


def index_reference_revision(db, revision_id: int, kind: str, snapshot: dict):
    """Derived metadata only. Do not expand nested page copies, infer links from text or mutate snapshots."""
    links = snapshot.get("links")
    if kind not in {"forum", "onion"} or not isinstance(links, list):
        return
    seen = set()
    for ordinal, link in enumerate(links[:200]):
        if isinstance(link, str):
            url, label = link, ""
        elif isinstance(link, dict):
            url, label = link.get("url"), link.get("label", "")
        else:
            continue
        if (
            not isinstance(url, str)
            or not 1 <= len(url) <= 4000
            or not isinstance(label, str)
            or any(ord(c) < 33 or ord(c) == 127 for c in url)
            or "\\" in url
            or url in seen
        ):
            continue
        try:
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
                continue
            if parts.password is not None:
                continue
            _ = parts.port  # Validate a supplied port, without resolving or connecting to the reference.
            host = canonical_host(parts.hostname)
        except ValueError:
            continue
        seen.add(url)
        label = label[:500]
        db.execute(
            "INSERT OR IGNORE INTO evidence_references(revision_id,ordinal,url,label,host,search_text) "
            "VALUES(?,?,?,?,?,?)",
            (revision_id, ordinal, url, label, host, (url + "\n" + label).casefold()),
        )


class ReferenceSearch:
    def __init__(self, store):
        self.store = store

    def search(
        self,
        query: str | None = None,
        host: str | None = None,
        include_subdomains: bool = False,
        kind: str | None = None,
        source_id: str | None = None,
        include_history: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        limit_value(limit)
        if offset < 0 or kind not in {None, "forum", "onion"}:
            raise ValueError("Check evidence kind and pagination")
        if query is None and host is None:
            raise ValueError("Supply a reference query or host")
        if query is not None and (
            not query.strip() or len(query) > 500 or any(ord(c) < 32 or ord(c) == 127 for c in query)
        ):
            raise ValueError("Use a nonempty query up to 500 characters")
        term = query.strip().casefold() if query is not None else None
        host = canonical_host(host) if host is not None else None
        source_id = self.store._source_filter(source_id)
        try:
            ipaddress.ip_address(host or "")
            subdomains = False  # A DNS name ending in an IP literal is not that address.
        except ValueError:
            subdomains = include_subdomains
        conditions, args = [], []
        if term is not None:
            conditions.append("instr(l.search_text,?)>0")
            args.append(term)
            if len(term) >= 3:
                conditions.append("l.id IN (SELECT rowid FROM reference_search WHERE reference_search MATCH ?)")
                args.append('"' + term.replace('"', '""') + '"')
        if host is not None:
            conditions.append("(l.host=? OR (? AND l.host LIKE ?))")
            args.extend([host, subdomains, "%." + host])
        if source_id is not None:
            conditions.append("i.source_id=?")
            args.append(source_id)
        if kind is not None:
            conditions.append("i.kind=?")
            args.append(kind)
        if not include_history:
            conditions.append("r.id=i.latest_revision_id")
        conditions.append("r.access_state IN ('content_returned_unverified','search_hit_unverified')")
        sql = (
            "WITH matches AS (SELECT i.id evidence_id,i.source_id,i.kind,i.url current_item_url,"
            "i.availability current_availability,i.first_seen evidence_first_recorded_at,"
            "r.id revision_id,r.id=i.latest_revision_id is_latest_revision,r.title,r.published_at,"
            "r.collected_at,r.content_hash,r.snapshot_hash,r.truncated,"
            "coalesce(json_extract(r.snapshot,'$.hidden_content'),0) hidden_content,"
            "(SELECT max(o.observed_at) FROM observations o WHERE o.revision_id=r.id "
            "AND o.change_type IN ('new','edited','unchanged','restored')) order_recorded_at,"
            "json_extract(r.snapshot,'$.url') recorded_source_url,"
            "json_extract(r.snapshot,'$.acquisition') acquisition,l.url reference_url,l.label,l.host,"
            "row_number() OVER (PARTITION BY i.id,l.url ORDER BY r.id DESC) n "
            "FROM evidence_references l JOIN revisions r ON r.id=l.revision_id "
            "JOIN items i ON i.id=r.evidence_id WHERE "
            + " AND ".join(conditions)
            + ") SELECT * FROM matches WHERE n=1 "
            "ORDER BY order_recorded_at DESC,evidence_id,reference_url LIMIT ? OFFSET ?"
        )
        with self.store.connect() as db:
            db.execute("BEGIN")
            watermark = db.execute("SELECT coalesce(max(seq),0) FROM observations").fetchone()[0]
            rows = db.execute(sql, (*args, limit + 1, offset)).fetchall()
            ids = list({row["revision_id"] for row in rows[:limit]})
            times = {}
            if ids:
                marks = ",".join("?" for _ in ids)
                times = {
                    row["revision_id"]: dict(row)
                    for row in db.execute(
                        "SELECT revision_id,min(observed_at) matched_revision_first_recorded_at,"
                        "max(observed_at) matched_revision_last_recorded_at FROM observations "
                        f"WHERE revision_id IN ({marks}) AND change_type IN ('new','edited','unchanged','restored') "
                        "GROUP BY revision_id",
                        ids,
                    )
                }
        results = []
        for row in rows[:limit]:
            result = dict(row)
            result.pop("n")
            result.pop("order_recorded_at")
            recorded_url = result.pop("recorded_source_url")
            result["source_url"] = recorded_url or result["current_item_url"]
            result["source_url_basis"] = "selected_revision_snapshot" if recorded_url else "current_item_fallback"
            result["acquisition"] = json.loads(result["acquisition"]) if result["acquisition"] else {}
            result["is_latest_revision"] = bool(result["is_latest_revision"])
            result["truncated"] = bool(result["truncated"])
            result["hidden_content"] = bool(result["hidden_content"])
            fields = ["host"] if host else []
            if term is not None:
                fields.extend(
                    name
                    for name, value in [("url", result["reference_url"]), ("label", result["label"])]
                    if term in value.casefold()
                )
            result["matched_fields"] = fields or ["url_label_boundary"]
            result.update({k: v for k, v in times.get(row["revision_id"], {}).items() if k != "revision_id"})
            results.append(result)
        return {
            "results": results,
            "next_offset": offset + limit if len(rows) > limit else None,
            "observations_through_seq": watermark,
            "scope": {
                "query": query,
                "host": host,
                "include_subdomains": include_subdomains,
                "host_match_mode": "host_and_subdomains" if host and subdomains else "exact_host" if host else None,
                "kind": kind,
                "source_id": source_id,
                "include_history": include_history,
                "reference_fields": ["url", "label"],
                "per_revision_reference_limit": 200,
                "label_character_limit": 500,
            },
            "match_mode": "casefolded_literal_reference_text_and_exact_idna_host",
            "reference_followed": False,
            "coverage": (
                "Extracted top-level references on saved forum posts and onion pages only; no network requests. "
                "No nested forum-page copies, inferred text URLs, Telegram/media or attachment contents. "
                "Gated snapshots are excluded. "
                "References may be quoted, copied, stale or unrelated; they do not prove ownership or exposure. "
                "History returns the most recent matching revision per item/reference URL, explicitly labeled. "
                "Recording times concern the selected evidence revision, not earliest publication or compromise."
            ),
        }

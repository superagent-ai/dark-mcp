"""Source-local author metadata and retrieval from recorded forum posts."""

import json
import re
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from dark_mcp.models import limit_value, parse_date


def numeric_author_id(value) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value) and int(value) > 0:
        return str(int(value))
    return None


def author_fields(source: dict, url: str, author: str | None, anchor) -> dict:
    """Inspect only the selected post-header author node. Profile links are never fetched."""
    identity = {"basis": "source_post_header", "identity_verified": False}
    profile_url, profile_id, attribute_id = None, None, None
    if anchor is not None:
        attribute_id = numeric_author_id(anchor.get("data-user-id"))
        label = anchor.get_text(" ", strip=True)
        identity["handle_conflict"] = bool(author and label and author.casefold() != label.casefold())
        try:
            value = urljoin(url, anchor.get("href", ""))
            parts = urlsplit(value)
            engine, path = source["forum_engine"], parts.path
            query = parse_qsl(parts.query, keep_blank_values=True)
            allowed = False
            if engine == "xenforo":
                match = re.fullmatch(r"/members/(?:[^/]+\.)?([0-9]{1,10})/?", path)
                allowed = bool(match and not query)
                profile_id = numeric_author_id(match[1]) if allowed else None
            elif engine == "mybb":
                match = re.fullmatch(r"/user-([0-9]{1,10})\.html", path, re.I)
                if path == "/member.php" and sorted(k for k, _ in query) == ["action", "uid"]:
                    allowed = dict(query).get("action") == "profile"
                    profile_id = numeric_author_id(dict(query).get("uid")) if allowed else None
                    allowed = allowed and profile_id is not None
                else:
                    allowed = not query and bool(match or re.fullmatch(r"/User-[^/]+/?", path))
                    profile_id = numeric_author_id(match[1]) if match and allowed else None
            elif engine == "q2a":
                allowed = not query and bool(re.fullmatch(r"/user/[^/]+/?", path))
            if (
                not allowed
                or parts.scheme not in {"http", "https"}
                or parts.hostname not in source["allowed_hosts"]
                or parts.port not in {None, 80, 443}
                or parts.username is not None
                or parts.password is not None
                or len(value) > 4000
                or any(ord(c) < 33 or ord(c) == 127 for c in value)
                or "\\" in value
                or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|25|2e|2f|5c|7f)", path, re.I)
            ):
                profile_id = None
            else:
                profile_url = urlunsplit((*parts[:4], ""))
        except (TypeError, ValueError):
            profile_id = None
    identity.update(
        profile_id=profile_id,
        attribute_id=attribute_id,
        id_conflict=bool(profile_id and attribute_id and profile_id != attribute_id),
    )
    author_id = None if identity["id_conflict"] or identity.get("handle_conflict") else profile_id or attribute_id
    identity["id_basis"] = (
        "profile_url_and_attribute"
        if profile_id and attribute_id and author_id
        else ("profile_url" if profile_id and author_id else "header_attribute" if author_id else None)
    )
    return {"author_id": author_id, "author_url": profile_url, "author_identity": identity}


def index_author_revision(db, revision_id: int, source_id: str, kind: str, snapshot: dict):
    if kind != "forum" or not isinstance(snapshot.get("id"), str) or not snapshot["id"].isdigit():
        return
    author = snapshot.get("author")
    if author is not None and (not isinstance(author, str) or not 1 <= len(author) <= 500):
        author = None
    author_id = numeric_author_id(snapshot.get("author_id"))
    identity = snapshot.get("author_identity") or {}
    if isinstance(identity, dict) and (identity.get("id_conflict") or identity.get("handle_conflict")):
        author_id = None
    if not author and not author_id:
        return
    db.execute(
        "INSERT OR IGNORE INTO forum_author_members VALUES(?,?,?,?,?)",
        (revision_id, source_id, author_id, author, author.casefold() if author else None),
    )


class AuthorPosts:
    def __init__(self, store, max_text_chars: int):
        self.store, self.max_text_chars = store, max_text_chars

    def get(
        self,
        source_id: str,
        author_id: str | None = None,
        author: str | None = None,
        as_of: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        limit_value(limit)
        if offset < 0 or (author_id is None) == (author is None):
            raise ValueError("Supply one source-local author ID or exact handle and nonnegative offset")
        if author_id is not None:
            author_id = numeric_author_id(author_id)
            if author_id is None:
                raise ValueError("Use a positive numeric source-local author ID")
        if author is not None:
            if not author.strip() or len(author) > 500 or any(ord(c) < 32 or ord(c) == 127 for c in author):
                raise ValueError("Use a nonempty exact author handle up to 500 characters")
            author = author.strip()
        source_id = self.store._source_filter(source_id)
        if not source_id:
            raise ValueError("A source ID is required")
        cutoff = parse_date(as_of)
        cutoff = cutoff.isoformat() if cutoff else None
        field, value = ("author_id", author_id) if author_id is not None else ("handle_folded", author.casefold())
        match = f"a.{field}=?"
        # Select the state at the cutoff before matching its author, so renamed/removed
        # author metadata cannot revive an older revision by accident.
        cte = (
            "WITH ranked AS (SELECT o.*,row_number() OVER (PARTITION BY o.evidence_id ORDER BY o.seq DESC) n "
            "FROM observations o JOIN items i ON i.id=o.evidence_id WHERE i.source_id=? "
            "AND i.id IN (SELECT r.evidence_id FROM forum_author_members a JOIN revisions r ON r.id=a.revision_id "
            f"WHERE a.source_id=? AND {match}) AND (? IS NULL OR o.observed_at<=?)), "
            "members AS (SELECT o.*,a.author_id,a.handle,r.snapshot,r.content_hash,r.snapshot_hash,i.first_seen "
            "FROM ranked o JOIN forum_author_members a ON a.revision_id=o.revision_id "
            "JOIN revisions r ON r.id=o.revision_id JOIN items i ON i.id=o.evidence_id "
            f"WHERE o.n=1 AND a.source_id=? AND {match}) "
        )
        args = (source_id, source_id, value, cutoff, cutoff, source_id, value)
        with self.store.connect() as db:
            db.execute("BEGIN")
            watermark = db.execute("SELECT coalesce(max(seq),0) FROM observations").fetchone()[0]
            summary = db.execute(
                cte + "SELECT count(*) posts,count(DISTINCT author_id) native_ids,"
                "sum(author_id IS NULL) posts_without_native_id FROM members",
                args,
            ).fetchone()
            rows = db.execute(
                cte + "SELECT * FROM members ORDER BY observed_at DESC,seq DESC,evidence_id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
            times = {}
            if rows:
                ids = [row["revision_id"] for row in rows]
                marks = ",".join("?" for _ in ids)
                times = {
                    row["revision_id"]: dict(row)
                    for row in db.execute(
                        "SELECT revision_id,min(observed_at) matched_revision_first_recorded_at,"
                        "max(observed_at) matched_revision_last_recorded_at FROM observations "
                        f"WHERE revision_id IN ({marks}) AND (? IS NULL OR observed_at<=?) "
                        "AND change_type IN ('new','edited','unchanged','restored') GROUP BY revision_id",
                        (*ids, cutoff, cutoff),
                    )
                }
        results, remaining = [], self.max_text_chars
        for row in rows:
            snapshot = json.loads(row["snapshot"])
            text = snapshot.get("text", "")
            body = text[: min(16000, remaining)]
            remaining -= len(body)
            results.append(
                {
                    "evidence_id": row["evidence_id"],
                    "revision_id": row["revision_id"],
                    "author": row["handle"],
                    "author_id": row["author_id"],
                    "author_url": snapshot.get("author_url"),
                    "author_identity": snapshot.get("author_identity", {}),
                    "url": snapshot.get("url"),
                    "title": snapshot.get("title", "")[:500],
                    "text": body,
                    "thread_key": snapshot.get("thread_key"),
                    "post_id": snapshot["id"],
                    "is_thread_root": snapshot.get("is_thread_root"),
                    "published_at": snapshot.get("published_at"),
                    "published_at_raw": snapshot.get("published_at_raw"),
                    "timestamp_basis": snapshot.get("timestamp_basis"),
                    "evidence_first_recorded_at": row["first_seen"],
                    **{key: value for key, value in times.get(row["revision_id"], {}).items() if key != "revision_id"},
                    "recorded_at": row["observed_at"],
                    "collected_at": row["collected_at"],
                    "availability_at_observation": row["change_type"]
                    if row["change_type"] in {"unavailable", "deleted"}
                    else "observed",
                    "hidden_content": bool(snapshot.get("hidden_content")),
                    "truncated": bool(snapshot.get("truncated")),
                    "context_text_truncated": len(body) < len(text),
                    "acquisition": snapshot.get("acquisition", {}),
                    "content_hash": row["content_hash"],
                    "snapshot_hash": row["snapshot_hash"],
                }
            )
        return {
            "source_id": source_id,
            "author_id": author_id,
            "author": author,
            "match_basis": "source_local_numeric_id" if author_id else "casefolded_exact_handle_not_identity",
            "as_of": cutoff,
            "cutoff_basis": "local_observation_recording_time",
            "results": results,
            "total_stored_posts": summary["posts"],
            "distinct_native_author_ids": summary["native_ids"],
            "posts_without_native_author_id": summary["posts_without_native_id"] or 0,
            "next_offset": offset + limit if offset + limit < summary["posts"] else None,
            "observations_through_seq": watermark,
            "network_read": False,
            "coverage": "Saved structured forum posts in one source; latest recorded state per post within cutoff. "
            "Handles may be reused or renamed; numeric IDs are source-local unverified account metadata. "
            "No cross-source actor merging, profile requests, inferred legacy IDs or complete activity history. "
            "Source dates do not establish our detection time; retrieve exact revisions via get_evidence.",
        }

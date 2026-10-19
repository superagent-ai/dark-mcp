"""Source-local forum thread identity and retrieval from recorded evidence only."""

import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from dark_mcp.errors import DataAccessError
from dark_mcp.models import limit_value, parse_date


def forum_thread_key(value: str | None) -> str | None:
    """Normalize only explicit thread IDs or exact MyBB slugs, never titles or author handles."""
    if not isinstance(value, str) or len(value) > 4000:
        return None
    if re.fullmatch(r"tid:[0-9]{1,10}", value):
        return "mybb:tid:" + str(int(value[4:]))
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    path = parts.path
    xenforo = re.fullmatch(r"/threads/(?:[^/]+\.)?([0-9]{1,10})(?:/(?:page-[0-9]+|post-[0-9]+))?/?", path)
    if xenforo:
        return "xenforo:" + str(int(xenforo[1]))
    if path == "/showthread.php":
        tid = dict(parse_qsl(parts.query)).get("tid", "")
        if re.fullmatch(r"[0-9]{1,10}", tid):
            return "mybb:tid:" + str(int(tid))
    if re.fullmatch(r"/Thread-[^/]+/?", path):
        return "mybb:slug:" + unquote(path.rstrip("/"))
    return None


def index_forum_revision(db, revision_id: int, source_id: str, kind: str, snapshot: dict):
    """Derived index only; immutable source snapshots and hashes are never changed."""
    if kind != "forum" or not isinstance(snapshot.get("id"), str) or not snapshot["id"].isdigit():
        return
    key = forum_thread_key(snapshot.get("thread_key")) or forum_thread_key(snapshot.get("thread_url"))
    if key is None:
        return
    number = snapshot.get("post_number")
    if type(number) is not int or not 1 <= number <= 1_000_000_000:
        number = 1 if snapshot.get("is_thread_root") is True else None
    root = snapshot.get("is_thread_root")
    root = int(root) if type(root) is bool else None
    db.execute(
        "INSERT OR IGNORE INTO forum_thread_members VALUES(?,?,?,?,?,?)",
        (revision_id, source_id, key, snapshot["id"], number, root),
    )


class ThreadContext:
    def __init__(self, store, max_text_chars: int):
        self.store, self.max_text_chars = store, max_text_chars

    def get(self, evidence_id: str, as_of: str | None = None, offset: int = 0, limit: int = 20) -> dict:
        limit_value(limit)
        if offset < 0:
            raise ValueError("Use a nonnegative offset")
        cutoff = parse_date(as_of)
        cutoff = cutoff.isoformat() if cutoff else None
        seed = self.store.get(evidence_id)
        if seed["kind"] != "forum":
            raise DataAccessError(
                "thread_context_unavailable", "Only structured forum evidence has a local thread index"
            )
        with self.store.connect() as db:
            db.execute("BEGIN")  # Anchor, summary and page share one WAL read snapshot.
            # Use the revision actually recorded at the cutoff, not the item's present-day snapshot.
            anchor = db.execute(
                "SELECT r.snapshot,o.revision_id,o.observed_at FROM observations o "
                "JOIN revisions r ON r.id=o.revision_id WHERE o.evidence_id=? "
                "AND (? IS NULL OR o.observed_at<=?) ORDER BY o.seq DESC LIMIT 1",
                (evidence_id, cutoff, cutoff),
            ).fetchone()
            if anchor is None:
                raise DataAccessError("evidence_not_observed", "Anchor evidence had not been recorded by this cutoff")
            snapshot = json.loads(anchor["snapshot"])
            key = forum_thread_key(snapshot.get("thread_key")) or forum_thread_key(snapshot.get("thread_url"))
            if key is None:
                raise DataAccessError("thread_context_unavailable", "No explicit thread identity was preserved")
            # Rank observations before filtering membership: a post moved to another thread must not
            # reappear from an older revision. The cutoff uses our recording time, never source dates.
            cte = (
                "WITH ranked AS (SELECT o.*,row_number() OVER (PARTITION BY o.evidence_id ORDER BY o.seq DESC) n "
                "FROM observations o JOIN items i ON i.id=o.evidence_id WHERE i.source_id=? "
                "AND i.id IN (SELECT r.evidence_id FROM forum_thread_members f "
                "JOIN revisions r ON r.id=f.revision_id WHERE f.source_id=? AND f.thread_key=?) "
                "AND (? IS NULL OR o.observed_at<=?)), members AS ("
                "SELECT o.*,f.post_id,f.post_number,f.is_thread_root,r.snapshot,r.content_hash,r.snapshot_hash,"
                "r.published_at,r.collected_at AS revision_collected_at,i.first_seen "
                "FROM ranked o JOIN forum_thread_members f ON f.revision_id=o.revision_id "
                "JOIN revisions r ON r.id=o.revision_id JOIN items i ON i.id=o.evidence_id "
                "WHERE o.n=1 AND f.source_id=? AND f.thread_key=?) "
            )
            args = (seed["source_id"], seed["source_id"], key, cutoff, cutoff, seed["source_id"], key)
            summary = db.execute(
                cte + "SELECT count(*) posts,sum(is_thread_root=1) roots,min(first_seen) first_recorded_at,"
                "max(observed_at) last_recorded_at FROM members",
                args,
            ).fetchone()
            rows = db.execute(
                cte + "SELECT * FROM members ORDER BY CASE WHEN is_thread_root=1 THEN 0 ELSE 1 END,"
                "post_number IS NULL,post_number,published_at IS NULL,published_at,seq,evidence_id LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        posts, remaining = [], self.max_text_chars
        for row in rows:
            snap = json.loads(row["snapshot"])
            text = snap.get("text", "")
            author = snap.get("author")
            body = text[: min(16000, remaining)]
            remaining -= len(body)
            posts.append(
                {
                    "evidence_id": row["evidence_id"],
                    "revision_id": row["revision_id"],
                    "post_id": row["post_id"],
                    "post_number": row["post_number"],
                    "is_thread_root": bool(row["is_thread_root"]) if row["is_thread_root"] is not None else None,
                    "url": snap.get("url"),
                    "title": snap.get("title", "")[:500],
                    "author": author[:500] if isinstance(author, str) else None,
                    "author_display_truncated": isinstance(author, str) and len(author) > 500,
                    "text": body,
                    "hidden_content": bool(snap.get("hidden_content")),
                    "truncated": bool(snap.get("truncated")),
                    "context_text_truncated": len(body) < len(text),
                    "published_at": snap.get("published_at"),
                    "published_at_raw": snap.get("published_at_raw"),
                    "timestamp_basis": snap.get("timestamp_basis"),
                    "first_recorded_at": row["first_seen"],
                    "recorded_at": row["observed_at"],
                    "collected_at": row["collected_at"],
                    "revision_collected_at": row["revision_collected_at"],
                    "availability_at_observation": row["change_type"]
                    if row["change_type"] in {"unavailable", "deleted"}
                    else "observed",
                    "content_hash": row["content_hash"],
                    "snapshot_hash": row["snapshot_hash"],
                }
            )
        return {
            "source_id": seed["source_id"],
            "thread_key": key,
            "anchor": {"evidence_id": evidence_id, "revision_id": anchor["revision_id"]},
            "as_of": cutoff,
            "cutoff_basis": "local_observation_recording_time",
            "results": posts,
            "total_stored_posts": summary["posts"],
            "root_observed": bool(summary["roots"]),
            "first_recorded_at": summary["first_recorded_at"],
            "last_recorded_at": summary["last_recorded_at"],
            "next_offset": offset + limit if offset + limit < summary["posts"] else None,
            "order": "explicit_root_then_post_number_then_known_source_date_then_observation_sequence",
            "coverage": (
                "Saved structured forum posts only; no source request. Latest recorded state per post within cutoff. "
                "Missing roots, hidden content and uncollected replies remain gaps. "
                "Source dates do not establish our detection time. Retrieve exact bodies via get_evidence."
            ),
        }

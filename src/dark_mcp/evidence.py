"""Local, append-only evidence revisions, collection receipts and resumable jobs."""

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dark_mcp.errors import DataAccessError
from dark_mcp.models import limit_value, now, parse_date
from dark_mcp.thread_context import index_forum_revision


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identifier(kind: str, value: str) -> str:
    return kind + "_" + hashlib.sha256(value.encode()).hexdigest()[:32]


def date_value(value: str | None) -> str | None:
    date = parse_date(value)
    return date.isoformat() if date else None


def match_excerpt(text: str, folded_position: int) -> str:
    """Keep offsets in the original text even when casefold expands characters such as ß."""
    position = 0
    if folded_position >= 0:
        folded_length = 0
        for index, char in enumerate(text):
            folded_length += len(char.casefold())
            if folded_length > folded_position:
                position = index
                break
    start = max(0, position - 120)
    return text[start : start + 600]


class EvidenceStore:
    def __init__(self, directory: Path):
        self.path = directory / "evidence.sqlite3"
        with self.connect() as db:
            search_exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='revision_search'").fetchone()
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, locator TEXT, name TEXT,
                    canonical_id TEXT, metadata TEXT NOT NULL, identity TEXT
                );
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
                    kind TEXT NOT NULL, stable_key TEXT NOT NULL, url TEXT NOT NULL,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, latest_revision_id INTEGER,
                    availability TEXT NOT NULL DEFAULT 'observed'
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT NOT NULL REFERENCES items(id),
                    snapshot_hash TEXT NOT NULL, content_hash TEXT, title TEXT NOT NULL, text TEXT NOT NULL,
                    search_text TEXT NOT NULL, published_at TEXT, edited_at TEXT, collected_at TEXT NOT NULL,
                    access_state TEXT NOT NULL, truncated INTEGER NOT NULL, snapshot TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS revision_item ON revisions(evidence_id,id);
                CREATE INDEX IF NOT EXISTS revision_hash ON revisions(content_hash);
                CREATE VIRTUAL TABLE IF NOT EXISTS revision_search USING fts5(
                    search_text, content='revisions', content_rowid='id', tokenize='trigram'
                );
                CREATE TRIGGER IF NOT EXISTS revision_search_insert AFTER INSERT ON revisions BEGIN
                    INSERT INTO revision_search(rowid,search_text) VALUES(new.id,new.search_text);
                END;
                CREATE TABLE IF NOT EXISTS forum_thread_members (
                    revision_id INTEGER PRIMARY KEY REFERENCES revisions(id), source_id TEXT NOT NULL,
                    thread_key TEXT NOT NULL, post_id TEXT NOT NULL, post_number INTEGER, is_thread_root INTEGER
                );
                CREATE INDEX IF NOT EXISTS forum_thread_lookup ON forum_thread_members(source_id,thread_key);
                CREATE TABLE IF NOT EXISTS observations (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id TEXT NOT NULL REFERENCES items(id),
                    revision_id INTEGER NOT NULL REFERENCES revisions(id), observed_at TEXT NOT NULL,
                    collected_at TEXT NOT NULL, change_type TEXT NOT NULL, collection_id TEXT, detail TEXT
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
                    started_at TEXT NOT NULL, finished_at TEXT, state TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attempt_source ON attempts(source_id,started_at);
                CREATE INDEX IF NOT EXISTS observation_revision ON observations(revision_id,collected_at);
                CREATE INDEX IF NOT EXISTS observation_item ON observations(evidence_id,seq);
                CREATE TABLE IF NOT EXISTS cooldowns (kind TEXT PRIMARY KEY, retry_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS telegram_state (source_id TEXT PRIMARY KEY, pts INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), kind TEXT NOT NULL,
                    status TEXT NOT NULL, options TEXT NOT NULL, checkpoint TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error TEXT,
                    lease TEXT, lease_until TEXT
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, saved_at TEXT NOT NULL,
                    document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, from_id TEXT NOT NULL REFERENCES items(id),
                    to_id TEXT NOT NULL REFERENCES items(id), relation TEXT NOT NULL, note TEXT NOT NULL,
                    supporting_ids TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY, completed_at TEXT NOT NULL);
            """)
            if not search_exists:
                db.execute("INSERT INTO revision_search(revision_search) VALUES('rebuild')")
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM migrations WHERE name='forum-context-v1'").fetchone():
                cursor = db.execute(
                    "SELECT r.id,i.source_id,r.snapshot FROM revisions r JOIN items i ON i.id=r.evidence_id "
                    "WHERE i.kind='forum'"
                )
                while rows := cursor.fetchmany(500):
                    for row in rows:
                        index_forum_revision(db, row["id"], row["source_id"], "forum", json.loads(row["snapshot"]))
                db.execute("INSERT INTO migrations VALUES('forum-context-v1',?)", (now(),))
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, source_id: str, kind: str, locator: str | None, name: str, metadata: dict | None = None):
        with self.connect() as db:
            previous = db.execute("SELECT metadata FROM sources WHERE id=?", (source_id,)).fetchone()
            if previous and (old := json.loads(previous["metadata"])).get("configuration_origin") == "local_operator":
                keys = ("configuration_origin", "allowed_hosts", "transport", "forum_engine", "locator")
                if any(old.get(key) != (metadata or {}).get(key) for key in keys):
                    raise DataAccessError(
                        "source_identity_conflict", "Saved operator source identity changed; use a new slug"
                    )
            db.execute(
                "INSERT INTO sources(id,kind,locator,name,metadata) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET locator=excluded.locator,name=excluded.name,metadata=excluded.metadata",
                (source_id, kind, locator, name, encoded(metadata or {})),
            )

    def bind_identity(self, source_id: str, canonical_id: str, identity: dict):
        with self.connect() as db:
            db.execute(
                "UPDATE sources SET canonical_id=?,identity=? WHERE id=?", (canonical_id, encoded(identity), source_id)
            )

    def source(self, source_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise DataAccessError("source_not_found", "No stored source with this ID")
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        result["identity"] = json.loads(result["identity"]) if result["identity"] else None
        return result

    def observe(
        self,
        source_id: str,
        kind: str,
        stable_key: str,
        url: str,
        snapshot: dict,
        collected_at: str | None = None,
        collection_id: str | None = None,
        provenance: dict | None = None,
    ) -> dict:
        evidence_id = identifier("ev", kind + ":" + stable_key)
        timestamp, collected_at = now(), date_value(collected_at) or now()
        snapshot = {
            **snapshot,
            "published_at": date_value(snapshot.get("published_at")),
            "edited_at": date_value(snapshot.get("edited_at")),
        }
        serialized = encoded(snapshot)
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        text, title = snapshot.get("text", ""), snapshot.get("title", "")
        content_hash = hashlib.sha256(text.encode()).hexdigest() if text.strip() else None
        with self.connect() as db:
            # Serialize competing observations, including a resumed job in another server process.
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT * FROM items WHERE id=?", (evidence_id,)).fetchone()
            previous = (
                db.execute("SELECT * FROM revisions WHERE id=?", (item["latest_revision_id"],)).fetchone()
                if item
                else None
            )
            change = "new" if item is None else "edited" if previous["snapshot_hash"] != fingerprint else "unchanged"
            if item and item["availability"] != "observed" and change == "unchanged":
                change = "restored"
            if item is None:
                db.execute(
                    "INSERT INTO items(id,source_id,kind,stable_key,url,first_seen,last_seen) VALUES(?,?,?,?,?,?,?)",
                    (evidence_id, source_id, kind, stable_key, url, timestamp, timestamp),
                )
            if change in {"new", "edited"}:
                cursor = db.execute(
                    "INSERT INTO revisions(evidence_id,snapshot_hash,content_hash,title,text,search_text,published_at,"
                    "edited_at,collected_at,access_state,truncated,snapshot) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        evidence_id,
                        fingerprint,
                        content_hash,
                        title,
                        text,
                        (title + "\n" + text).casefold(),
                        snapshot.get("published_at"),
                        snapshot.get("edited_at"),
                        collected_at,
                        snapshot.get("access_state", "content_returned_unverified"),
                        bool(snapshot.get("truncated")),
                        serialized,
                    ),
                )
                revision_id = cursor.lastrowid
                index_forum_revision(db, revision_id, source_id, kind, snapshot)
            else:
                revision_id = previous["id"]
            db.execute(
                "UPDATE items SET source_id=?,url=?,last_seen=?,latest_revision_id=?,availability='observed' "
                "WHERE id=?",
                (source_id, url, timestamp, revision_id, evidence_id),
            )
            cursor = db.execute(
                "INSERT INTO observations(evidence_id,revision_id,observed_at,collected_at,"
                "change_type,collection_id,detail) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    revision_id,
                    timestamp,
                    collected_at,
                    change,
                    collection_id,
                    encoded(provenance) if provenance else None,
                ),
            )
        return {
            "evidence_id": evidence_id,
            "revision_id": revision_id,
            "change_type": change,
            "observation_seq": cursor.lastrowid,
            "content_hash": content_hash,
            "snapshot_hash": fingerprint,
        }

    def get(self, evidence_id: str, revision_id: int | None = None) -> dict:
        if revision_id is not None and revision_id <= 0:
            raise ValueError("Use a positive revision ID")
        with self.connect() as db:
            item = db.execute("SELECT * FROM items WHERE id=?", (evidence_id,)).fetchone()
            if not item:
                raise DataAccessError("evidence_not_found", "No evidence with this ID")
            revision = db.execute(
                "SELECT * FROM revisions WHERE id=? AND evidence_id=?",
                (revision_id or item["latest_revision_id"], evidence_id),
            ).fetchone()
            observation = db.execute(
                "SELECT detail FROM observations WHERE evidence_id=? AND revision_id=? "
                "AND detail IS NOT NULL AND change_type IN ('new','edited','unchanged','restored') "
                "ORDER BY seq DESC LIMIT 1",
                (evidence_id, revision_id or item["latest_revision_id"]),
            ).fetchone()
        if not revision:
            raise DataAccessError("revision_not_found", "Revision does not belong to this evidence")
        result = {
            **dict(item),
            "revision_id": revision["id"],
            "is_latest_revision": revision["id"] == item["latest_revision_id"],
            "content_hash": revision["content_hash"],
            "snapshot_hash": revision["snapshot_hash"],
            "collected_at": revision["collected_at"],
            "snapshot": json.loads(revision["snapshot"]),
        }
        result["provenance"] = json.loads(observation["detail"]) if observation else None
        return result

    def unavailable(self, evidence_id: str, reason: str, collection_id: str | None = None) -> dict:
        item = self.get(evidence_id)
        timestamp = now()
        with self.connect() as db:
            db.execute("UPDATE items SET availability='unavailable' WHERE id=?", (evidence_id,))
            cursor = db.execute(
                "INSERT INTO observations(evidence_id,revision_id,observed_at,collected_at,"
                "change_type,collection_id,detail) VALUES(?,?,?,?,?,?,?)",
                (evidence_id, item["revision_id"], timestamp, timestamp, "unavailable", collection_id, reason),
            )
        return {
            "evidence_id": evidence_id,
            "availability": "unavailable",
            "observation_seq": cursor.lastrowid,
            "deletion_confirmed": False,
            "reason": reason,
        }

    def confirm_deleted(self, evidence_id: str, detail: dict) -> dict:
        item = self.get(evidence_id)
        if item["availability"] == "deleted":
            return {"evidence_id": evidence_id, "change_type": "unchanged", "deletion_confirmed": True}
        with self.connect() as db:
            db.execute("UPDATE items SET availability='deleted' WHERE id=?", (evidence_id,))
            cursor = db.execute(
                "INSERT INTO observations(evidence_id,revision_id,observed_at,collected_at,"
                "change_type,detail) VALUES(?,?,?,?,?,?)",
                (evidence_id, item["revision_id"], now(), now(), "deleted", encoded(detail)),
            )
        return {
            "evidence_id": evidence_id,
            "observation_seq": cursor.lastrowid,
            "change_type": "deleted",
            "deletion_confirmed": True,
        }

    def telegram_pts(self, source_id: str, pts: int | None = None) -> int | None:
        with self.connect() as db:
            if pts is not None:
                db.execute(
                    "INSERT INTO telegram_state VALUES(?,?) ON CONFLICT(source_id) DO UPDATE "
                    "SET pts=max(telegram_state.pts,excluded.pts)",
                    (source_id, pts),
                )
            row = db.execute("SELECT pts FROM telegram_state WHERE source_id=?", (source_id,)).fetchone()
        return row["pts"] if row else None

    def _source_filter(self, source_id: str | None) -> str | None:
        if source_id is None:
            return None
        source = self.source(source_id)
        return source["canonical_id"] or source_id

    def search(
        self,
        query: str,
        kind: str | None = None,
        source_id: str | None = None,
        published_since: str | None = None,
        collected_since: str | None = None,
        include_history: bool = True,
        include_gates: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        limit_value(limit)
        if (
            not query.strip()
            or len(query) > 500
            or offset < 0
            or kind not in {None, "onion", "telegram", "forum", "provider"}
        ):
            raise ValueError("Check query, kind and pagination")
        source_id = self._source_filter(source_id)
        published, collected = date_value(published_since), date_value(collected_since)
        term = query.strip().casefold()
        indexed = len(term) >= 3
        fts_filter = "AND r.id IN (SELECT rowid FROM revision_search WHERE revision_search MATCH ?) " if indexed else ""
        fts_args = ('"' + term.replace('"', '""') + '"',) if indexed else ()
        with self.connect() as db:
            db.execute("BEGIN")  # Search hits and their local observation times share a read snapshot.
            rows = db.execute(
                "WITH matches AS (SELECT i.id AS evidence_id,i.source_id,i.kind,i.url,i.availability,"
                "r.id AS revision_id,r.title,r.text,r.published_at,r.collected_at,r.truncated,r.access_state,"
                "i.first_seen AS evidence_first_recorded_at,"
                "r.content_hash,r.id=i.latest_revision_id AS is_latest_revision,"
                "(SELECT max(o.collected_at) FROM observations o WHERE o.revision_id=r.id "
                "AND o.change_type IN ('new','edited','unchanged','restored')) AS last_collected_at,"
                "row_number() OVER (PARTITION BY i.id ORDER BY r.id DESC) AS n "
                "FROM items i JOIN revisions r ON r.evidence_id=i.id WHERE instr(r.search_text,?)>0 "
                + fts_filter
                + "AND (? IS NULL OR i.kind=?) AND (? IS NULL OR i.source_id=?) "
                "AND (? IS NULL OR r.published_at>=?) AND (? IS NULL OR EXISTS "
                "(SELECT 1 FROM observations o WHERE o.revision_id=r.id AND o.collected_at>=? "
                "AND o.change_type IN ('new','edited','unchanged','restored'))) "
                "AND (? OR r.id=i.latest_revision_id) AND (? OR r.access_state IN "
                "('content_returned_unverified','search_hit_unverified','preview_only_unverified'))) "
                "SELECT * FROM matches WHERE n=1 ORDER BY last_collected_at DESC,evidence_id LIMIT ? OFFSET ?",
                (
                    term,
                    *fts_args,
                    kind,
                    kind,
                    source_id,
                    source_id,
                    published,
                    published,
                    collected,
                    collected,
                    include_history,
                    include_gates,
                    limit + 1,
                    offset,
                ),
            ).fetchall()
            revision_ids = [row["revision_id"] for row in rows[:limit]]
            recorded = {}
            if revision_ids:
                placeholders = ",".join("?" for _ in revision_ids)
                recorded = {
                    row["revision_id"]: dict(row)
                    for row in db.execute(
                        "SELECT revision_id,min(observed_at) AS matched_revision_first_recorded_at,"
                        "max(observed_at) AS matched_revision_last_recorded_at FROM observations "
                        f"WHERE revision_id IN ({placeholders}) "
                        "AND change_type IN ('new','edited','unchanged','restored') GROUP BY revision_id",
                        revision_ids,
                    ).fetchall()
                }
        results = []
        for row in rows[:limit]:
            result = dict(row)
            text = result.pop("text")
            result.pop("n")
            position = text.casefold().find(term)
            fields = []
            if term in result["title"].casefold():
                fields.append("title")
            if position >= 0:
                fields.append("body")
            result["matched_fields"] = fields or ["title_body_boundary"]
            result["excerpt_basis"] = "body_match_context" if position >= 0 else "body_preview"
            times = recorded.get(result["revision_id"], {})
            result.update(
                {k: times.get(k) for k in ("matched_revision_first_recorded_at", "matched_revision_last_recorded_at")}
            )
            result["excerpt"] = match_excerpt(text, position)
            result["is_latest_revision"] = bool(result["is_latest_revision"])
            results.append(result)
        return {
            "results": results,
            "next_offset": offset + limit if len(rows) > limit else None,
            "match_mode": "casefolded_literal_substring",
            "includes_history": include_history,
            "corpus_scope": {
                "source_id": source_id,
                "evidence_kind": kind,
                "published_since": published,
                "collected_since": collected,
                "include_history": include_history,
                "include_gates": include_gates,
                "inventory_tool": "get_corpus_coverage",
                "inventory_is_search_denominator": False,
            },
            "coverage": "Stored evidence only; historical matches are explicitly marked, gates excluded by default",
        }

    def history(self, evidence_id: str, offset: int = 0, limit: int = 20) -> dict:
        self.get(evidence_id)
        limit_value(limit, 100)
        if offset < 0:
            raise ValueError("Check offset")
        with self.connect() as db:
            rows = db.execute(
                "SELECT id AS revision_id,content_hash,snapshot_hash,title,published_at,edited_at,"
                "collected_at,access_state,truncated FROM revisions WHERE evidence_id=? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (evidence_id, limit + 1, offset),
            ).fetchall()
        return {
            "results": [dict(row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def changes(
        self, after_seq: int = 0, source_id: str | None = None, limit: int = 100, include_unchanged: bool = False
    ) -> dict:
        limit_value(limit)
        if after_seq < 0:
            raise ValueError("Check after_seq")
        source_id = self._source_filter(source_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT o.*,i.source_id,i.kind,i.url FROM observations o JOIN items i ON i.id=o.evidence_id "
                "WHERE o.seq>? AND (? IS NULL OR i.source_id=?) "
                "AND (? OR o.change_type<>'unchanged') ORDER BY o.seq LIMIT ?",
                (after_seq, source_id, source_id, include_unchanged, limit + 1),
            ).fetchall()
        return {
            "results": [dict(row) for row in rows[:limit]],
            "next_after_seq": rows[min(len(rows), limit) - 1]["seq"] if rows else after_seq,
            "has_more": len(rows) > limit,
        }

    def duplicates(self, evidence_id: str, limit: int = 20) -> dict:
        item = self.get(evidence_id)
        limit_value(limit)
        with self.connect() as db:
            rows = db.execute(
                "SELECT i.id AS evidence_id,i.source_id,i.url,i.availability,r.id AS revision_id,r.collected_at "
                "FROM items i JOIN revisions r ON r.id=i.latest_revision_id "
                "WHERE i.id<>? AND r.content_hash=? AND r.access_state='content_returned_unverified' "
                "ORDER BY i.id LIMIT ?",
                (evidence_id, item["content_hash"], limit),
            ).fetchall()
        return {
            "results": [dict(row) for row in rows],
            "relationship": "identical_text_only",
            "coverage": "Identical text does not authenticate sources or establish independent corroboration",
        }

    def begin_attempt(self, source_id: str, detail: dict) -> str:
        attempt_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO attempts VALUES(?,?,?,NULL,'running',?)", (attempt_id, source_id, now(), encoded(detail))
            )
        return attempt_id

    def finish_attempt(self, attempt_id: str, state: str, detail: dict):
        with self.connect() as db:
            db.execute(
                "UPDATE attempts SET finished_at=?,state=?,detail=? WHERE id=?",
                (now(), state, encoded(detail), attempt_id),
            )

    def cooldown(self, kind: str, retry_after: int | None = None):
        if retry_after is not None:
            retry_at = (datetime.now(UTC) + timedelta(seconds=retry_after)).isoformat()
            with self.connect() as db:
                db.execute(
                    "INSERT INTO cooldowns VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET "
                    "retry_at=max(cooldowns.retry_at,excluded.retry_at)",
                    (kind, retry_at),
                )
            return
        with self.connect() as db:
            row = db.execute("SELECT retry_at FROM cooldowns WHERE kind=?", (kind,)).fetchone()
        if row and row["retry_at"] > now():
            seconds = max(1, int((parse_date(row["retry_at"]) - datetime.now(UTC)).total_seconds()) + 1)
            raise DataAccessError(
                "collection_rate_limited", "Stored connector cooldown is still active", retry_after=seconds
            )

    def health(self, source_id: str | None = None, offset: int = 0, limit: int = 20) -> dict:
        limit_value(limit)
        if offset < 0:
            raise ValueError("Check offset")
        if source_id:
            self.source(source_id)
        with self.connect() as db:
            sources = db.execute(
                "SELECT * FROM sources WHERE (? IS NULL OR id=?) ORDER BY id LIMIT ? OFFSET ?",
                (source_id, source_id, limit + 1, offset),
            ).fetchall()
            results = []
            for source in sources[:limit]:
                canonical = source["canonical_id"] or source["id"]
                scope = "source_id IN (SELECT id FROM sources WHERE id=? OR canonical_id=?)"
                scope_args = (source["id"], canonical)
                attempts = db.execute(
                    f"SELECT * FROM attempts WHERE {scope} ORDER BY started_at DESC LIMIT 20", scope_args
                ).fetchall()
                successful = db.execute(
                    f"SELECT max(finished_at) FROM attempts WHERE {scope} AND state IN "
                    "('content_returned_unverified','posts_returned','no_matching_posts')",
                    scope_args,
                ).fetchone()[0]
                content = db.execute(
                    f"SELECT max(finished_at) FROM attempts WHERE {scope} AND state IN "
                    "('content_returned_unverified','posts_returned')",
                    scope_args,
                ).fetchone()[0]
                last = attempts[0] if attempts else None
                results.append(
                    {
                        "source_id": source["id"],
                        "name": source["name"],
                        "kind": source["kind"],
                        "last_attempt_at": last["started_at"] if last else None,
                        "last_access_state": last["state"] if last else "never_attempted",
                        "last_successful_collection_at": successful,
                        "last_content_at": content,
                        "last_attempt": json.loads(last["detail"]) if last else None,
                        "observed_identity": json.loads(source["identity"]) if source["identity"] else None,
                        "canonical_source_id": source["canonical_id"],
                        "recent_attempts": [{**dict(a), "detail": json.loads(a["detail"])} for a in attempts],
                    }
                )
        return {
            "results": results,
            "next_offset": offset + limit if len(sources) > limit else None,
            "coverage": "Recorded local attempts and bounds; a successful empty search is not full-history coverage",
        }

    def create_job(self, source_id: str, kind: str, options: dict, checkpoint: dict) -> dict:
        job_id, timestamp = uuid.uuid4().hex, now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs VALUES(?,?,?,'ready',?,?,?,?,NULL,NULL,NULL)",
                (job_id, source_id, kind, encoded(options), encoded(checkpoint), timestamp, timestamp),
            )
        return self.job(job_id)

    def job(self, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise DataAccessError("collection_not_found", "No collection with this ID")
        result = dict(row)
        for key in ["options", "checkpoint", "last_error"]:
            result[key] = json.loads(result[key]) if result[key] else None
        result.pop("lease")
        return result

    def jobs(self, offset: int = 0, limit: int = 20) -> dict:
        limit_value(limit)
        if offset < 0:
            raise ValueError("Check offset")
        with self.connect() as db:
            rows = db.execute(
                "SELECT id FROM jobs ORDER BY created_at,id LIMIT ? OFFSET ?", (limit + 1, offset)
            ).fetchall()
        return {
            "results": [self.job(row["id"]) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def claim(self, job_id: str, seconds: float) -> tuple[dict, str]:
        self.job(job_id)
        token = uuid.uuid4().hex
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE jobs SET lease=?,lease_until=?,status='running' WHERE id=? "
                "AND status<>'complete' AND (lease IS NULL OR lease_until<?)",
                (token, (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(), job_id, now()),
            )
            if not cursor.rowcount:
                if self.job(job_id)["status"] == "complete":
                    raise DataAccessError("collection_complete", "Collection was completed by another caller")
                raise DataAccessError("collection_busy", "Another call holds this collection; retry after lease expiry")
        return self.job(job_id), token

    def checkpoint(self, job_id: str, token: str, checkpoint: dict, status: str, error: dict | None = None):
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE jobs SET checkpoint=?,status=?,last_error=?,updated_at=?,lease=NULL,"
                "lease_until=NULL WHERE id=? AND lease=?",
                (encoded(checkpoint), status, encoded(error) if error else None, now(), job_id, token),
            )
            if not cursor.rowcount:
                raise DataAccessError("collection_lease_lost", "Collection ownership changed; inspect its checkpoint")

    def save_profile(self, profile_id: str, document: dict) -> dict:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", profile_id) or len(encoded(document)) > 64000:
            raise ValueError("Use a short profile ID and a profile below 64 KB")
        if not document.get("name", "").strip() or not document.get("identifiers"):
            raise ValueError("Provide a company name and at least one identifier")
        with self.connect() as db:
            db.execute(
                "INSERT INTO profiles(id,saved_at,document) VALUES(?,?,?)", (profile_id, now(), encoded(document))
            )
        return self.profile(profile_id)

    def profile(self, profile_id: str, revision: int | None = None) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM profiles WHERE id=? AND (? IS NULL OR seq=?) ORDER BY seq DESC LIMIT 1",
                (profile_id, revision, revision),
            ).fetchone()
        if not row:
            raise DataAccessError("profile_not_found", "No company profile with this ID")
        return {
            "profile_id": profile_id,
            "revision": row["seq"],
            "saved_at": row["saved_at"],
            "document": json.loads(row["document"]),
            "verification": "caller_provided; not independently verified",
        }

    def relate(self, from_id: str, to_id: str, relation: str, supporting_ids: list[str], note: str = "") -> dict:
        if not relation.strip() or len(relation) > 80 or len(note) > 4000 or not 1 <= len(supporting_ids) <= 20:
            raise ValueError("Check relation, note and supporting evidence")
        for key in [from_id, to_id, *supporting_ids]:
            self.get(key)
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO relations(from_id,to_id,relation,note,supporting_ids,created_at) VALUES(?,?,?,?,?,?)",
                (from_id, to_id, relation, note, encoded(supporting_ids), now()),
            )
        return {
            "relation_id": cursor.lastrowid,
            "verification": "caller_asserted",
            "supporting_evidence_ids": supporting_ids,
        }

    def relations(self, evidence_id: str, limit: int = 20) -> dict:
        self.get(evidence_id)
        limit_value(limit)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM relations WHERE from_id=? OR to_id=? ORDER BY id LIMIT ?",
                (evidence_id, evidence_id, limit),
            ).fetchall()
        return {
            "results": [
                {**dict(row), "supporting_ids": json.loads(row["supporting_ids"]), "verification": "caller_asserted"}
                for row in rows
            ]
        }

    def counts(self) -> dict:
        with self.connect() as db:
            return {
                table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ["items", "revisions", "observations", "jobs"]
            }

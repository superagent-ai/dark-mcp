import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dark_mcp.models import OnionPage, limit_value, parse_date


class OnionIndex:
    """A local corpus with explicit coverage rather than network-wide search claims."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "onions.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, title TEXT, "
                "text TEXT, links TEXT, collected_at TEXT, truncated INTEGER)"
            )
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(url UNINDEXED, title, text)")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, page: OnionPage) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                (
                    page.url,
                    page.title,
                    page.text,
                    json.dumps(page.links),
                    page.collected_at,
                    page.truncated,
                ),
            )
            db.execute("DELETE FROM search WHERE url = ?", (page.url,))
            db.execute("INSERT INTO search VALUES (?, ?, ?)", (page.url, page.title, page.text))

    def search(self, query: str, limit: int = 10, since: str | None = None) -> dict:
        limit_value(limit)
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)
        if not tokens or len(query) > 500:
            raise ValueError("Use a nonempty search query of at most 500 characters")
        expression = " AND ".join(f'"{token}"' for token in tokens)
        date = parse_date(since)
        with self.connect() as db:
            rows = db.execute(
                "SELECT p.url, p.title, p.collected_at, "
                "snippet(search, 2, '', '', ' … ', 40) AS snippet "
                "FROM search JOIN pages p ON p.url = search.url "
                "WHERE search MATCH ? AND (? IS NULL OR p.collected_at >= ?) "
                "ORDER BY rank LIMIT ?",
                (expression, date.isoformat() if date else None, date.isoformat() if date else None, limit),
            ).fetchall()
            total = db.execute("SELECT count(*) FROM pages").fetchone()[0]
        return {
            "results": [dict(row) for row in rows],
            "coverage": {"scope": "locally_indexed_pages", "indexed_pages": total},
            "since_filter": "collection_time",
        }

    def count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT count(*) FROM pages").fetchone()[0]

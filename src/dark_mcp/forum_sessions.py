"""Operator-provisioned, exact-origin forum sessions stored separately from evidence."""

import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dark_mcp.errors import DataAccessError
from dark_mcp.models import parse_date
from dark_mcp.onion import onion_url

MAX_FILE_BYTES = 128_000
NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,200}")
VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]{0,8000}")


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port
    if port is not None and port != (443 if parts.scheme == "https" else 80):
        host += ":" + str(port)
    return urlunsplit((parts.scheme, host, "", "", ""))


def cookie_matches(cookie: dict, url: str, timestamp: float) -> bool:
    parts = urlsplit(url)
    path, scope = parts.path or "/", cookie["path"]
    return (
        (not cookie["secure"] or parts.scheme == "https")
        and (cookie["expires"] is None or cookie["expires"] > timestamp)
        and (path == scope or path.startswith(scope) and (scope.endswith("/") or path[len(scope) :].startswith("/")))
    )


def normalized_cookie(row: dict, host: str) -> dict:
    try:
        if not isinstance(row, dict) or str(row.get("domain", host)).lstrip(".").casefold() != host:
            raise ValueError
        name, value, path = row["name"], row["value"], row.get("path", "/")
        if (
            not isinstance(name, str)
            or not NAME.fullmatch(name)
            or not isinstance(value, str)
            or not VALUE.fullmatch(value)
            or not isinstance(path, str)
            or not path.startswith("/")
            or len(path) > 2000
            or any(ord(c) < 33 or ord(c) == 127 for c in path)
        ):
            raise ValueError
        expiry = row.get("expires", row.get("expirationDate"))
        if expiry is not None:
            expiry = float(expiry)
            if not math.isfinite(expiry):
                raise ValueError
            if expiry < 0:
                expiry = None
        if not isinstance(row.get("secure", False), bool):
            raise ValueError
        return {"name": name, "value": value, "path": path, "secure": row.get("secure", False), "expires": expiry}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DataAccessError(
            "invalid_forum_session", "Cookie export must contain valid cookies for the exact forum host"
        ) from exc


class ForumSessions:
    def __init__(self, settings, catalog):
        self.settings, self.catalog = settings, catalog
        self.path = settings.data_dir.expanduser() / "forum-sessions" / "sessions.sqlite3"

    def source(self, source_id):
        source = self.catalog.get(source_id)
        if source["collection_method"] != "forum_page":
            raise DataAccessError("source_access_required", "No configured forum collector for this source")
        return source

    def origin(self, source_id: str, value: str | None = None) -> str:
        source = self.source(source_id)
        value = value or origin_of(source["locator"])
        try:
            parts = urlsplit(value)
            if (
                parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or parts.username is not None
                or parts.password is not None
                or parts.hostname not in source["allowed_hosts"]
                or parts.port not in ({None, 80, 443} if source.get("transport") == "tor" else {None, 443})
                or any(ord(c) < 33 or ord(c) == 127 for c in value)
                or "\\" in value
            ):
                raise ValueError
            if source.get("transport") == "tor":
                onion_url(value)
            elif parts.scheme != "https":
                raise ValueError
            return origin_of(value)
        except (TypeError, ValueError) as exc:
            raise DataAccessError(
                "invalid_session_origin", "Choose an exact catalog forum origin without a path"
            ) from exc

    @contextmanager
    def connect(self, *, create=False):
        try:
            if create:
                self.settings.prepare_data_dir()
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.path.parent.chmod(0o700)
                try:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(descriptor)
                except FileExistsError:
                    pass
            if self.path.is_symlink():
                raise DataAccessError("forum_session_unavailable", "Forum session storage must not be a symbolic link")
            self.path.chmod(0o600)
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete=ON")
            try:
                with db:
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS sessions (source_id TEXT NOT NULL, origin TEXT NOT NULL, "
                        "session_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, "
                        "cookies TEXT NOT NULL, PRIMARY KEY(source_id,origin))"
                    )
                    yield db
            finally:
                db.close()
        except (OSError, sqlite3.Error) as exc:
            raise DataAccessError(
                "forum_session_unavailable", "Local forum session storage could not be accessed"
            ) from exc

    def _row(self, source_id: str, origin: str):
        if not self.path.exists():
            return None
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE source_id=? AND origin=?", (source_id, origin)).fetchone()
        return dict(row) if row else None

    def import_file(self, source_id: str, file: Path, origin: str | None = None, ttl_hours: int = 24) -> dict:
        origin = self.origin(source_id, origin)
        if not 1 <= ttl_hours <= 720:
            raise ValueError("Choose a local session lifetime of 1–720 hours")
        try:
            with file.open("rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError
            document = json.loads(content)
            rows = document.get("cookies") if isinstance(document, dict) else document
            if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
                raise ValueError
        except (OSError, ValueError, UnicodeError) as exc:
            raise DataAccessError(
                "invalid_forum_session", "Use a JSON cookie export for one forum host, up to 128 KB"
            ) from exc
        cookies = [normalized_cookie(row, urlsplit(origin).hostname) for row in rows]
        if len({(c["name"], c["path"]) for c in cookies}) != len(cookies):
            raise DataAccessError("invalid_forum_session", "Cookie export contains duplicate name/path entries")
        timestamp = datetime.now(UTC)
        cookies = [c for c in cookies if c["expires"] is None or c["expires"] > timestamp.timestamp()]
        if not cookies or not any(not c["secure"] or origin.startswith("https:") for c in cookies):
            raise DataAccessError("forum_session_expired", "No usable, unexpired cookies for this forum origin")
        with self.connect(create=True) as db:
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,origin) DO UPDATE SET "
                "session_id=excluded.session_id,created_at=excluded.created_at,expires_at=excluded.expires_at,"
                "cookies=excluded.cookies",
                (
                    source_id,
                    origin,
                    uuid.uuid4().hex,
                    timestamp.isoformat(),
                    (timestamp + timedelta(hours=ttl_hours)).isoformat(),
                    json.dumps(cookies),
                ),
            )
        return self.status(source_id, origin)

    def status(self, source_id: str, origin: str | None = None) -> dict:
        origin = self.origin(source_id, origin)
        row = self._row(source_id, origin)
        result = {"source_id": source_id, "origin": origin, "state": "public", "authentication_verified": False}
        if row:
            try:
                cookies = json.loads(row["cookies"])
                timestamp = datetime.now(UTC)
                active = [c for c in cookies if c["expires"] is None or c["expires"] > timestamp.timestamp()]
                expired = parse_date(row["expires_at"]) <= timestamp or not active
                result.update(
                    state="expired" if expired else "configured",
                    session_id=row["session_id"],
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    cookie_count=len(active),
                )
            except (ValueError, TypeError, KeyError) as exc:
                raise DataAccessError("forum_session_unavailable", "Stored forum session could not be read") from exc
        return result

    def headers(self, source_id: str, url: str) -> dict:
        origin = self.origin(source_id, origin_of(url))
        row = self._row(source_id, origin)
        if row is None or urlsplit(url).path == "/robots.txt":
            return {}
        status = self.status(source_id, origin)
        if status["state"] == "expired":
            raise DataAccessError(
                "forum_session_expired", "Forum session expired; renew it through the local operator CLI"
            )
        cookies = [c for c in json.loads(row["cookies"]) if cookie_matches(c, url, datetime.now(UTC).timestamp())]
        cookies.sort(key=lambda c: len(c["path"]), reverse=True)
        if sum(len(c["name"]) + len(c["value"]) + 3 for c in cookies) > 16000:
            raise DataAccessError("invalid_forum_session", "Applicable cookies exceed the request header bound")
        return {"Cookie": "; ".join(c["name"] + "=" + c["value"] for c in cookies)} if cookies else {}

    def context(self, source_id: str, url: str) -> dict:
        status = self.status(source_id, origin_of(url))
        return {**status, "credentials_attached": bool(self.headers(source_id, url))}

    def remove(self, source_id: str, origin: str | None = None) -> dict:
        origin = self.origin(source_id, origin)
        if self.path.exists():
            with self.connect() as db:
                db.execute("DELETE FROM sessions WHERE source_id=? AND origin=?", (source_id, origin))
        return self.status(source_id, origin)

    def remove_source_sessions(self, source_id: str) -> int:
        """Operator cleanup before removing a local source; never logs out the remote account."""
        if not self.path.exists():
            return 0
        with self.connect() as db:
            return db.execute("DELETE FROM sessions WHERE source_id=?", (source_id,)).rowcount

    def update(self, source_id: str, url: str, values: list[str]):
        """Accept exact-host cookie rotation from a read response; never extend the operator's local TTL."""
        origin = self.origin(source_id, origin_of(url))
        if not values or not self.path.exists():
            return
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE source_id=? AND origin=?", (source_id, origin)).fetchone()
            if row is None or parse_date(row["expires_at"]) <= datetime.now(UTC):
                return
            cookies = json.loads(row["cookies"])
            for value in values[:100]:
                jar = SimpleCookie()
                try:
                    jar.load(value)
                except (CookieError, ValueError):  # Malformed source headers are never echoed.
                    continue
                for morsel in jar.values():
                    expiry = None
                    delete = False
                    try:
                        if morsel["max-age"]:
                            seconds = int(morsel["max-age"])
                            delete = seconds <= 0
                            expiry = datetime.now(UTC).timestamp() + seconds
                        elif morsel["expires"]:
                            expiry = parsedate_to_datetime(morsel["expires"]).timestamp()
                            delete = expiry <= datetime.now(UTC).timestamp()
                        path = morsel["path"] or (urlsplit(url).path.rpartition("/")[0] or "/")
                        cookie = normalized_cookie(
                            {
                                "name": morsel.key,
                                "value": morsel.value,
                                "domain": morsel["domain"] or urlsplit(origin).hostname,
                                "path": path,
                                "secure": bool(morsel["secure"]),
                                "expires": expiry,
                            },
                            urlsplit(origin).hostname,
                        )
                    except (DataAccessError, ValueError, TypeError, OverflowError):
                        continue
                    cookies = [c for c in cookies if (c["name"], c["path"]) != (cookie["name"], cookie["path"])]
                    if not delete and len(cookies) < 100:
                        cookies.append(cookie)
            db.execute(
                "UPDATE sessions SET cookies=? WHERE source_id=? AND origin=?", (json.dumps(cookies), source_id, origin)
            )

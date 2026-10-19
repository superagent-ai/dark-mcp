"""Local operator-owned forum configuration. No agent-facing mutation or entitlement claims."""

import ipaddress
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from urllib.parse import urlsplit

from dark_mcp.errors import DataAccessError
from dark_mcp.models import now
from dark_mcp.onion import onion_url

PREFIX = "operator:forum:"
FIELDS = {"slug", "name", "locator", "forum_engine", "allowed_hosts", "collection_urls", "catalog_source_id"}


def validate_id(source_id: str):
    if not source_id.startswith(PREFIX) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", source_id[len(PREFIX) :]):
        raise DataAccessError("invalid_operator_source", "Only local operator source IDs can be removed")


def hosts_for(source: dict) -> set[str]:
    if source.get("kind") == "telegram":
        return set()
    return set(source.get("allowed_hosts") or [urlsplit(source.get("locator") or "").hostname]) - {None}


def normalize(document: dict) -> dict:
    from dark_mcp.forums import source_url

    try:
        if not isinstance(document, dict) or set(document) - FIELDS:
            raise ValueError
        slug, name = document["slug"], document["name"]
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", slug):
            raise ValueError
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200 or any(ord(c) < 32 for c in name):
            raise ValueError
        if document["forum_engine"] not in {"mybb", "xenforo"}:
            raise ValueError
        locator = document["locator"]
        host = urlsplit(locator).hostname
        hosts = document.get("allowed_hosts", [host])
        if not isinstance(hosts, list) or not 1 <= len(hosts) <= 10 or any(not isinstance(h, str) for h in hosts):
            raise ValueError
        hosts = list(dict.fromkeys(h.lower() for h in hosts))
        transport = "tor" if host and host.endswith(".onion") else "direct_https"
        for candidate in hosts:
            if transport == "tor":
                onion_url("http://" + candidate + "/")
            else:
                if not re.fullmatch(
                    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                    r"[a-z][a-z0-9-]{0,62}",
                    candidate,
                ) or candidate.endswith(".onion"):
                    raise ValueError
                try:
                    ipaddress.ip_address(candidate)
                except ValueError:
                    pass
                else:
                    raise ValueError
        source = {"allowed_hosts": hosts, "transport": transport, "forum_engine": document["forum_engine"]}
        locator = source_url(source, locator)
        urls = document.get("collection_urls", [locator])
        if not isinstance(urls, list) or not 1 <= len(urls) <= 20:
            raise ValueError
        urls = list(dict.fromkeys(source_url(source, url) for url in urls))
        reference = document.get("catalog_source_id")
        association = {}
        if reference is not None:
            packaged = json.loads(files("dark_mcp").joinpath("resources/source-catalog.json").read_text())
            candidate = next((s for s in packaged["sources"] if s["id"] == reference), None)
            if (
                not isinstance(reference, str)
                or candidate is None
                or candidate["kind"] not in {"onion", "forum"}
                or candidate["collection_method"] not in {"onion_page", "metadata_only"}
                or not set(hosts) <= hosts_for(candidate)
            ):
                raise ValueError
            association = {"catalog_source_id": reference}
        return {
            "slug": slug,
            "name": name.strip(),
            "locator": locator,
            **source,
            "collection_urls": urls,
            **association,
        }
    except (KeyError, TypeError, ValueError, AttributeError, DataAccessError):
        raise DataAccessError(
            "invalid_operator_source",
            "Use slug, name, locator, forum_engine (mybb/xenforo), and optional allowed_hosts/collection_urls. "
            "catalog_source_id can associate a catalog candidate's existing hosts, not replace a forum collector. "
            "Only scoped HTTPS or v3 Tor read routes are supported. Credentials are not source configuration.",
        ) from None


class OperatorSources:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data_dir.expanduser() / "operator-sources" / "registry.sqlite3"

    @contextmanager
    def connect(self, *, create=False):
        try:
            if create:
                self.settings.prepare_data_dir()
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.path.parent.chmod(0o700)
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(fd)
                except FileExistsError:
                    pass
            if self.path.is_symlink():
                raise ValueError
            self.path.chmod(0o600)
            db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    db.execute("PRAGMA secure_delete=ON")
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS configurations "
                        "(id TEXT PRIMARY KEY, document TEXT NOT NULL, imported_at TEXT NOT NULL, "
                        "active INTEGER NOT NULL)"
                    )
                    yield db
            finally:
                db.close()
        except (OSError, ValueError, sqlite3.Error):
            raise DataAccessError(
                "operator_source_storage_unavailable", "Local source configuration is unavailable"
            ) from None

    def import_file(self, file) -> dict:
        try:
            with file.open("rb") as handle:
                raw = handle.read(32_001)
            if len(raw) > 32_000:
                raise ValueError
            config = normalize(json.loads(raw))
        except (OSError, ValueError):
            raise DataAccessError(
                "invalid_operator_source", "Cannot read a bounded JSON source configuration"
            ) from None
        # Transport is derived, not an accepted input field.
        config.pop("transport")
        source_id = PREFIX + config["slug"]
        serialized = json.dumps(config, sort_keys=True)
        with self.connect(create=True) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT document,active FROM configurations WHERE id=?", (source_id,)).fetchone()
            if previous:
                if previous["document"] != serialized:
                    raise DataAccessError(
                        "operator_source_exists", "Source IDs retain their identity after removal; use a new slug"
                    )
            if not previous or not previous["active"]:
                packaged = json.loads(files("dark_mcp").joinpath("resources/source-catalog.json").read_text())
                occupied = set().union(
                    *(hosts_for(s) for s in packaged["sources"] if s["id"] != config.get("catalog_source_id"))
                )
                for row in db.execute("SELECT document FROM configurations WHERE active=1 AND id<>?", (source_id,)):
                    occupied.update(json.loads(row[0])["allowed_hosts"])
                if occupied.intersection(config["allowed_hosts"]):
                    raise DataAccessError(
                        "operator_source_conflict", "This host already has a configured source; use its ID"
                    )
                if db.execute("SELECT count(*) FROM configurations WHERE active=1").fetchone()[0] >= 100:
                    raise DataAccessError(
                        "operator_source_limit", "At most 100 local source configurations are supported"
                    )
            if previous:
                db.execute("UPDATE configurations SET active=1 WHERE id=?", (source_id,))
            else:
                db.execute("INSERT INTO configurations VALUES(?,?,?,1)", (source_id, serialized, now()))
        return {"source_id": source_id, "configured": True, "access_verified": False, "restart_required": True}

    def sources(self) -> list[dict]:
        if not self.path.exists() and not self.path.is_symlink():
            return []
        with self.connect() as db:
            rows = db.execute("SELECT * FROM configurations WHERE active=1 ORDER BY id LIMIT 101").fetchall()
        if len(rows) > 100:
            raise DataAccessError("operator_source_limit", "Local source configuration exceeds its supported bound")
        results = []
        for row in rows:
            try:
                config = normalize(json.loads(row["document"]))
            except ValueError:
                raise DataAccessError(
                    "invalid_operator_source", "Stored local source configuration is invalid"
                ) from None
            source_id = PREFIX + config.pop("slug")
            if row["id"] != source_id:
                raise DataAccessError("invalid_operator_source", "Stored local source identity is inconsistent")
            results.append(
                {
                    **config,
                    "id": source_id,
                    "kind": "forum",
                    "url": config["locator"],
                    "family": source_id,
                    "source_class": "forum",
                    "collection_method": "forum_page",
                    "configuration_origin": "local_operator",
                    "access_state": "operator_configured_unverified",
                    "checked_at": None,
                    "assessment": "Operator configuration, not verified identity, access or coverage.",
                    "research": {
                        "method": "local_operator_configuration",
                        "imported_at": row["imported_at"],
                        "access_verified": False,
                        "operator_identity_authenticated": False,
                        **(
                            {
                                "catalog_association": {
                                    "source_id": config["catalog_source_id"],
                                    "basis": "operator_assertion",
                                    "identity_verified": False,
                                    "independent_source": False,
                                }
                            }
                            if config.get("catalog_source_id")
                            else {}
                        ),
                    },
                }
            )
        return results

    def remove(self, source_id: str) -> bool:
        validate_id(source_id)
        if not self.path.exists() and not self.path.is_symlink():
            return False
        with self.connect() as db:
            return db.execute("UPDATE configurations SET active=0 WHERE id=? AND active=1", (source_id,)).rowcount > 0

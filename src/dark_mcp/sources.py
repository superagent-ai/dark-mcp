"""Packaged source discovery and bounded, agent-directed collection."""

import json
from collections import Counter
from importlib.resources import files
from urllib.parse import urlsplit

from dark_mcp.errors import DataAccessError
from dark_mcp.models import limit_value, now
from dark_mcp.onion import onion_url


class SourceCatalog:
    def __init__(self):
        self.document = json.loads(files("dark_mcp").joinpath("resources/source-catalog.json").read_text())
        self.sources = {source["id"]: source for source in self.document["sources"]}

    def get(self, source_id: str) -> dict:
        if source_id not in self.sources:
            raise DataAccessError("source_not_found", "Use a source_id returned by list_sources")
        return self.sources[source_id]

    def telegram_targets(self) -> set[str]:
        return {
            source["locator"].lower()
            for source in self.sources.values()
            if source["kind"] == "telegram" and source["locator"]
        }

    def summary(self) -> dict:
        return {
            "snapshot_date": self.document["snapshot_date"],
            "total": len(self.sources),
            "by_kind": dict(Counter(source["kind"] for source in self.sources.values())),
            "coverage": "Dated research candidates, including failures; listing is not confirmed access",
        }

    def list(
        self,
        kind: str | None = None,
        access_state: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        limit_value(limit, 100)
        if offset < 0 or kind not in {None, "onion", "telegram"}:
            raise ValueError("Check offset and source kind")
        if query is not None and (not query.strip() or len(query) > 500):
            raise ValueError("Use a nonempty query up to 500 characters")
        rows = [
            source
            for source in self.sources.values()
            if (kind is None or source["kind"] == kind)
            and (access_state is None or source["access_state"] == access_state)
            and (query is None or query.strip().casefold() in json.dumps(source, ensure_ascii=False).casefold())
        ]
        return {
            "results": [
                {key: value for key, value in row.items() if key != "research"} for row in rows[offset : offset + limit]
            ],
            "total": len(rows),
            "next_offset": offset + limit if offset + limit < len(rows) else None,
            **{key: value for key, value in self.summary().items() if key != "total"},
        }

    def detail(self, source_id: str) -> dict:
        source = self.get(source_id)
        return {
            "source": source,
            "snapshot_date": self.document["snapshot_date"],
            "research_references": self.document["references"][source["kind"]],
            "web_mirror_context": self.document["web_mirror_context"] if source["kind"] == "onion" else [],
            "coverage": "Historical checks; re-collect to establish current access. Mirrors are research context only.",
        }


def page_access_state(page) -> str:
    """Flag obvious gates without claiming that arbitrary page text is verified evidence."""
    title, text = page.title.casefold(), page.text.casefold()
    if not text.strip():
        return "empty_document"
    if any(
        phrase in title or phrase in text[:300]
        for phrase in ("verify humanity", "checking your browser", "just a moment...", "security check")
    ):
        return "challenge_suspected"
    if len(text) < 1500 and any(phrase in title or phrase in text for phrase in ("log in", "login", "sign in", "вход")):
        return "login_gate_suspected"
    return "content_returned_unverified"


async def collect_source(
    catalog: SourceCatalog,
    onion,
    telegram,
    source_id: str,
    url: str | None = None,
    query: str | None = None,
    limit: int = 20,
    before_id: int = 0,
    since: str | None = None,
) -> dict:
    source = catalog.get(source_id)
    limit_value(limit)
    context = {"source_id": source_id, "historical_access_state": source["access_state"]}
    try:
        if source["collection_method"] == "onion_page":
            if query is not None or before_id or since is not None:
                raise ValueError("Onion collection takes a URL; search returned text or the local index separately")
            target = onion_url(url or source["locator"])
            if urlsplit(target).hostname != urlsplit(source["locator"]).hostname:
                raise DataAccessError("source_url_mismatch", "Choose a URL on this source's onion host")
            page = await onion.fetch(target)
            return {
                "ok": True,
                **context,
                "access_state": page_access_state(page),
                "page": page.model_dump(),
                "coverage": "One page; allegations are unverified. Collection time is not publication time.",
            }
        if url is not None:
            raise ValueError("URL overrides are supported for onion sources only")
        if source["collection_method"] == "metadata_only":
            raise DataAccessError("source_access_required", "No durable locator is known; inspect get_source")
        result = await telegram.messages(
            channel=source["locator"],
            query=query,
            limit=limit,
            before_id=before_id,
            since=since,
        )
        return {
            "ok": True,
            **context,
            "access_state": "posts_returned" if result["results"] else "no_matching_posts",
            **result,
        }
    except DataAccessError as exc:
        return {**context, **exc.as_dict(), "attempted_at": now()}

"""Packaged source discovery and bounded, agent-directed collection."""

import json
from collections import Counter
from importlib.resources import files
from urllib.parse import urlsplit

from dark_mcp.errors import DataAccessError
from dark_mcp.models import classify_page, limit_value, now
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
            "by_collection_method": dict(Counter(source["collection_method"] for source in self.sources.values())),
            "access_snapshot_dates": self.document["access_snapshot_dates"],
            "vendor_research": {
                "researched_on": self.document["vendor_research"]["researched_on"],
                "providers_reviewed": len(self.document["vendor_research"]["providers"]),
                "forum_families_reviewed": self.document["vendor_research"]["forum_families_reviewed"],
                "current_provider_coverage_verified": False,
            },
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
        if offset < 0 or kind not in {None, "onion", "telegram", "forum"}:
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
        vendor = self.document["vendor_research"]
        reference_ids = {row["reference_id"] for row in source.get("vendor_evidence", [])}
        for event in source.get("status_context", []):
            reference_ids.update(event["reference_ids"])
        return {
            "source": source,
            "snapshot_date": self.document["snapshot_date"],
            "research_references": self.document["references"][source["kind"]],
            "web_mirror_context": self.document["web_mirror_context"] if source["kind"] == "onion" else [],
            "vendor_research_references": [row for row in vendor["references"] if row["id"] in reference_ids],
            "provider_research": vendor["providers"] if source.get("vendor_evidence") else [],
            "coverage": (
                "Historical checks and vendor research, not current access or API entitlement. "
                "Configured forum_page sources support bounded public reads. "
                "Leads with metadata_only have no collector; mirrors are research context only."
            ),
        }


def page_access_state(page) -> str:
    """Flag obvious gates without claiming that arbitrary page text is verified evidence."""
    return classify_page(page.title, page.text)


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
    forums=None,
) -> dict:
    source = catalog.get(source_id)
    limit_value(limit)
    context = {"source_id": source_id, "historical_access_state": source["access_state"]}
    try:
        if source["collection_method"] == "forum_page":
            if query is not None or before_id or since is not None:
                raise ValueError("Use list_forum_threads for a local title filter, or search_evidence after collection")
            if forums is None:
                raise DataAccessError("forum_not_configured", "Public forum collector is unavailable")
            page = await forums.forum_page(source_id, url, limit)
            return {
                "ok": True,
                **context,
                "access_state": page.access_state,
                "page": page.model_dump(),
                "coverage": page.coverage,
            }
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
            raise ValueError("URL overrides are supported for onion and configured forum sources only")
        if source["collection_method"] == "metadata_only":
            raise DataAccessError(
                "source_access_required", "No supported collection locator is verified; inspect get_source research"
            )
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

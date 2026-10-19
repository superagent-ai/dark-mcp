"""Inventory of locally retained evidence; never a claim of source-wide coverage."""

from dark_mcp.models import limit_value

COUNTS = (
    "items",
    "stored_revisions",
    "indexed_forum_posts",
    "forum_page_items",
    "unindexed_forum_items",
    "native_forum_items",
    "operator_import_forum_items",
    "unreported_acquisition_forum_items",
    "indexed_forum_threads",
    "threads_without_explicit_root",
    "hidden_forum_post_items",
    "truncated_items",
    "default_search_excluded_items",
    "unavailable_items",
    "items_without_published_at",
)
DATES = ("first_recorded_at", "last_positive_recorded_at", "oldest_published_at", "newest_published_at")


class CorpusCoverage:
    def __init__(self, store):
        self.store = store

    def get(self, source_id: str | None = None, kind: str | None = None, offset: int = 0, limit: int = 20) -> dict:
        limit_value(limit)
        if offset < 0 or kind not in {
            None,
            "onion",
            "telegram",
            "telegram_archive",
            "telegram_web",
            "forum",
            "forum_feed",
            "forum_search",
            "provider",
        }:
            raise ValueError("Check evidence kind and pagination")
        source_id = self.store._source_filter(source_id)
        # One row per item, even after repeated collection, edits, reverts, or unavailable observations.
        # Thread membership comes from the selected latest revision, never from a post's old location.
        cte = """
            WITH eligible AS (
                SELECT i.*,r.snapshot,r.published_at,r.truncated,r.access_state,
                    f.thread_key,f.post_id,f.is_thread_root
                FROM items i JOIN revisions r ON r.id=i.latest_revision_id
                LEFT JOIN forum_thread_members f ON f.revision_id=r.id
                WHERE (? IS NULL OR i.source_id=?) AND (? IS NULL OR i.kind=?)
            ), revision_counts AS (
                SELECT r.evidence_id,count(*) n FROM revisions r
                JOIN eligible e ON e.id=r.evidence_id GROUP BY r.evidence_id
            ), threads AS (
                SELECT source_id,thread_key,max(is_thread_root=1) has_root
                FROM eligible WHERE thread_key IS NOT NULL GROUP BY source_id,thread_key
            ), thread_counts AS (
                SELECT source_id,count(*) n,sum(coalesce(has_root,0)=0) missing_root
                FROM threads GROUP BY source_id
            ), inventory AS (
                SELECT e.source_id,count(*) items,sum(v.n) stored_revisions,
                    sum(e.post_id IS NOT NULL) indexed_forum_posts,
                    sum(e.kind='forum' AND json_extract(e.snapshot,'$.page_type') IS NOT NULL) forum_page_items,
                    sum(e.kind='forum' AND e.post_id IS NULL AND json_extract(e.snapshot,'$.page_type') IS NULL)
                        unindexed_forum_items,
                    sum(e.kind='forum' AND json_extract(e.snapshot,'$.acquisition.method')='native_fetch')
                        native_forum_items,
                    sum(e.kind='forum' AND json_extract(e.snapshot,'$.acquisition.method')='operator_html_import')
                        operator_import_forum_items,
                    sum(e.kind='forum' AND coalesce(json_extract(e.snapshot,'$.acquisition.method'),'')
                        NOT IN ('native_fetch','operator_html_import')) unreported_acquisition_forum_items,
                    coalesce(t.n,0) indexed_forum_threads,coalesce(t.missing_root,0) threads_without_explicit_root,
                    sum(e.post_id IS NOT NULL AND coalesce(json_extract(e.snapshot,'$.hidden_content'),0)=1)
                        hidden_forum_post_items,
                    sum(e.truncated) truncated_items,
                    sum(e.access_state NOT IN ('content_returned_unverified','search_hit_unverified',
                        'preview_only_unverified')) default_search_excluded_items,
                    sum(e.availability<>'observed') unavailable_items,
                    sum(e.published_at IS NULL) items_without_published_at,
                    min(e.first_seen) first_recorded_at,max(e.last_seen) last_positive_recorded_at,
                    min(e.published_at) oldest_published_at,max(e.published_at) newest_published_at
                FROM eligible e JOIN revision_counts v ON v.evidence_id=e.id
                LEFT JOIN thread_counts t ON t.source_id=e.source_id GROUP BY e.source_id
            )
        """
        args = (source_id, source_id, kind, kind)
        with self.store.connect() as db:
            db.execute("BEGIN")
            observation_seq = db.execute("SELECT coalesce(max(seq),0) FROM observations").fetchone()[0]
            sums = ",".join(f"coalesce(sum({key}),0) {key}" for key in COUNTS)
            summary = dict(
                db.execute(
                    cte + "SELECT count(*) sources_with_items," + sums + ",min(first_recorded_at) first_recorded_at,"
                    "max(last_positive_recorded_at) last_positive_recorded_at,"
                    "min(oldest_published_at) oldest_published_at,"
                    "max(newest_published_at) newest_published_at FROM inventory",
                    args,
                ).fetchone()
            )
            rows = db.execute(
                cte + "SELECT s.id source_id,s.name,s.kind catalog_kind,c.* FROM sources s "
                "LEFT JOIN inventory c ON c.source_id=s.id WHERE "
                "(? IS NOT NULL AND s.id=?) OR (? IS NULL AND c.source_id IS NOT NULL) "
                "ORDER BY s.id LIMIT ? OFFSET ?",
                (*args, source_id, source_id, source_id, limit + 1, offset),
            ).fetchall()
            results = []
            for row in rows[:limit]:
                result = {key: row[key] for key in ("source_id", "name", "catalog_kind")}
                result.update({key: row[key] or 0 for key in COUNTS})
                result.update({key: row[key] for key in DATES})
                results.append(result)
        return {
            "summary": summary,
            "observations_through_seq": observation_seq,
            "results": results,
            "next_offset": offset + limit if len(rows) > limit else None,
            "scope": {"source_id": source_id, "evidence_kind": kind, "revision_basis": "latest_per_item"},
            "coverage": (
                "Local inventory, not a search-match count or source-wide coverage. Counts include gated, "
                "truncated and unavailable items; pages and individual posts can contain overlapping text. "
                "Acquisition counts describe latest forum snapshots; local imports do not prove remote access. "
                "Older unreported acquisition methods are not retroactively classified as native reads. "
                "Thread counts require explicit saved identities; missing roots and unknown dates remain gaps. "
                "Source publication ranges do not establish continuous collection or pre-compromise lead time. "
                "telegram_archive items are historical third-party imports, not native Telegram observations. "
                "telegram_web items are public HTML previews with unverified handle-based identities and media gaps. "
                "forum_feed items are source feed copies, not original-thread depth or complete history. "
                "forum_search items are query-specific source excerpts, not original forum posts. "
                "Use list_collections/get_collection for job bounds and get_source_health for access attempts. "
                "Search history, publication/collection filters and gate exclusions can narrow or change its scope."
            ),
        }

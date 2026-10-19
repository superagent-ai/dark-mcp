# Agent investigation workflow

The MCP collects and preserves evidence. The agent develops hypotheses, resolves conflicting claims and assesses relevance and urgency. A runner schedules calls and notifications. No attack score or background monitor is built into this server.

## Start with scope and provenance

1. Call `get_status`, then paginate `list_sources`. Historical catalog checks are not current access guarantees.
2. Save company identifiers and context using `save_company_profile`, citing references. Profiles are versioned caller-provided information; they are never sent to sources automatically. Retrieve previous versions through `get_company_profile(profile_id, revision)`.
3. Inspect `get_source`, `get_source_health` and `get_corpus_coverage`. Health reports actual local requests and their limits, not a global coverage percentage. An empty search is recorded separately from a successful content read.

## Inspect listing age before selecting originals

For recognized forum rows, compare the source creation date (`published_at`) with `last_activity_at` before describing an offer as new. A years-old pinned advertisement may have activity today. Raw dates without a source timezone stay unresolved. Creator handles, dates and pin labels remain unverified source metadata. Check `thread_listing_scope`: recognized rows exclude unrelated sidebars; fallback links carry less context. Inspect row-cap omissions before interpreting a negative title filter.

An agent may set `exclude_pinned=true` on `list_forum_threads` or a section collection job. It excludes only known pins, retains unknown status, and leaves the complete index evidence saved. This controls collection selection; it is not a threat score or a claim that pinned offers are irrelevant. Jobs preserve the option across restarts and record exclusions as deferred URLs. Read selected originals and replies before drawing a company-specific conclusion.

## Collect history and resume after a restart

Create a collection:

```json
{
  "source_id": "telegram:breachdetect",
  "mode": "history",
  "query": "superagent",
  "since": "2026-10-01T00:00:00Z"
}
```

Pass this to `start_collection`. Save the returned collection ID. Call `collect_next(collection_id, max_items=50, max_pages=1)` until the observed pagination is exhausted or your investigation bound is reached. The exact options and checkpoint persist across process restarts. `get_collection` and `list_collections` recover them.

Omit `query` to collect a bounded unfiltered history. The remote search engine determines Telegram matching; local `search_evidence` uses literal matching instead. An exhausted filtered search does not establish complete channel coverage.

Onion history jobs accept an optional same-host `url`, such as a known thread or forum category. They follow only explicit next-page links, including `rel=next` and recognizable next-page anchors. They preserve each fetched page, with its visible replies, within configured response/text limits. Generic onion history does not automatically discover every thread, parse every forum engine or visit arbitrary navigation links. For the configured structured forum collectors, `mode="section"` explicitly discovers and ingests bounded original threads; [see the section guide](forum-collection.md#ingest-a-bounded-forum-section). Dates are extracted only from explicit article publication metadata; an arbitrary reply's date is not substituted for a thread's publication time. Missing dates remain unknown.

An onion challenge, login gate or empty page is retained as evidence of a collection gap. It does not exhaust the job: the failing URL remains in its queue. Repeated next-page URLs are not revisited. `complete` means the observed pagination is exhausted, not that the entire source has been read. Inspect `truncated` before claiming full-page coverage.

Job calls are bounded to 1–5 pages and 1–100 messages per page. A persisted lease prevents simultaneous advancement of the same job by two callers. If a process crashes, the lease expires; replay is safe because evidence identity and unchanged snapshots are deduplicated. Rate limits return immediately, preserve the checkpoint, and set a connector-wide Telegram cooldown that survives restarts. The runner should honor `retry_after_seconds` rather than issuing more requests.

## Poll new posts and observe edits/deletions

Create a separate Telegram collection with `mode="updates"`. Its first `collect_next` establishes a high-water mark from the latest accessible post; it does not silently backfill the source. Later calls read new IDs oldest-first so a bounded page cannot skip the middle of a new-post backlog. An `idle` job remains pollable. These jobs do not detect edits to older IDs by themselves.

For edits and deletions, call `sync_telegram_changes(channel)`. The first call establishes a Telegram `pts` cursor. Later calls store new/edited posts and apply explicit `UpdateDeleteChannelMessages` events only to matching, already-observed channel/message identities. Follow `has_more` with another bounded call. The account must be permitted to obtain channel differences; some sources will reject that API. A server-reported difference gap is recorded, never interpreted as a list of deleted posts. A gap may omit older events.

`refresh_evidence` also detects a changed page or directly rechecks a known broadcast post. A missing post on this lookup is marked **unavailable**, with `deletion_confirmed=false`: deletion and changed access cannot always be distinguished. Only explicit channel deletion events mark **deleted**. Previous snapshots remain available in either case. Restored identical content produces a restoration observation. A changed username cannot cause a missing message in a different channel to delete the old evidence.

`read_telegram_thread(channel, message_id, ...)` collects the root and one page of accessible linked discussion replies without joining a group. A history job can also take `thread_id` to resume those replies. Telegram does not support server-side text search in these replies, so combining `query` with `thread_id` is rejected. Replies retain their actual discussion peer IDs; they are not incorrectly cited as broadcast posts. Re-collect threads to observe reply edits; the channel update cursor covers the broadcast source, not inaccessible linked groups.

## Search and inspect exact evidence

Before treating a company text search as negative, also check extracted references with `search_references(host="company.example", include_subdomains=true, kind="forum")` or a literal URL/label query. Shortened visible labels can omit the actual destination. Hits identify an exact source revision; quoted/copied links, historical matches and acquisition provenance remain explicit. A host match does not verify ownership or exposure, and references are never fetched by this tool. Its index includes explicitly captured Telegram URL entities, but excludes media and inferred text URLs. See the [reference search guide](reference-search.md).

All MCP content-read tools persist their returned onion/Telegram snapshots, including legacy read tools. Telegram identity uses channel ID plus message ID; aliases do not create separate copies of the same post. Onion identity uses the validated final URL. Source claims are never authenticated by these identities alone.

`search_evidence(query, kind, source_id, published_since, collected_since, include_history, include_gates, offset, limit, include_quotes=false)` searches collected onion, Telegram and forum evidence. Historical provider snapshots, if present from an earlier checkout, remain replayable. It uses casefolded literal substring matching, with a trigram index for queries of three or more characters. Punctuation is preserved: `superagent.sh` is not treated as a Boolean query or as unrelated words.

Set `include_quotes=true` to retrieve explicitly captured forum/feed/public-Telegram quoted context too. `matched_quotes` preserves its reference/ordinal and labels it as unverified quoted context; the enclosing post's date and author do not authenticate the quoted original. This does not fetch references. See [quote search](quote-search.md) for exact matching, history and coverage limits.

Search hits include `matched_fields`: `title`, `body`, or `title_body_boundary` when the literal spans the separator between them. A reply can match only because its thread title repeats the company name. `excerpt_basis="body_preview"` labels that preview; it is not a matching excerpt from the reply body. `body_match_context` identifies context around a literal body match, preserving original Unicode text even when casefold expands characters. Matching fields are text provenance, not threat classifications or independent claims.

`evidence_first_recorded_at` is when this store first recorded the item. `matched_revision_first_recorded_at` and `matched_revision_last_recorded_at` concern positive local observations of the selected revision. A post first recorded earlier can acquire company text in a later revision. An old source publication or import date cannot backdate that local observation. These fields are not the earliest-ever mention of the query: metadata changes, extraction improvements and reverted text can create new revisions. Inspect hashes, earlier revisions and the original context before asserting new targeting or lead time.

Historical matches are included by default and explicitly marked `is_latest_revision=false`. Gates are excluded by default. Historical provider snapshots retain their explicit access-state and scope labels; they have no active collector. `published_since` filters only known publication dates; undated pages do not meet that filter. `collected_since` filters positive collection observations, including an unchanged snapshot re-observed recently. `collected_at` is the revision's original collection time and `last_collected_at` is its latest positive observation. `first_seen`/`last_seen` record this evidence store's observations, not the original publication date or global first sighting.

Use `get_evidence(evidence_id, revision_id)` to retrieve an exact revision. A content hash groups identical text; a snapshot hash covers the stored revision. `get_evidence_history` lists previous revisions without returning every full body in one response. Hashes support reproducibility; they do not prove the source's statements are true. Text/media truncation and publication/edit times are preserved where available. There is no image OCR or attachment download.

After a forum hit, use `get_collected_thread(evidence_id, as_of=null, offset=0, limit=20)` to reconstruct the posts already saved from that thread. It reads the local corpus without contacting the source. It accepts a saved post or thread-page evidence ID, returns exact revision references and hashes, and paginates 1–100 posts per call. Each body is bounded to 16,000 characters and the response has the configured total text bound; `context_text_truncated` reports a shortened body. Retrieve full saved bodies through `get_evidence`. A missing root, hidden content or unavailable post remains explicit; an exhausted local cursor is not complete source coverage.

`as_of` accepts a UTC-offset timestamp and selects the latest observation per post that this store had **recorded** by that time. Source publication dates and a provider's claimed collection time do not qualify a later local observation. A cutoff before the anchor was recorded returns `evidence_not_observed`. Later edits, replies, URLs and availability changes are excluded. `first_recorded_at`, `recorded_at`, the selected observation's `collected_at`, and its revision's original `revision_collected_at` remain distinct. Without a cutoff, each post uses its latest locally recorded state; posts may have been collected at different times.

Question2Answer context also returns question/answer/comment type, direct parent post IDs and labeled source events. Selected answers are not presented as independent corroboration or inferred edits.

Thread membership is source-local and derived only from preserved thread IDs or exact MyBB slugs. Numeric XenForo IDs group title-slug and catalog-mirror variants within the same source; equal titles or handles do not merge unrelated threads or sources. MyBB slugs and numeric thread IDs are not merged without an explicit shared identity. Ordering prefers explicit roots, then source post numbers, known source timestamps and observation order. Unknown positions or dates remain unknown. Older snapshots gain a derived membership index without changing their hashes or manufacturing source post numbers.

This cutoff is useful for testing what the investigator could have known before a disclosure. It does not prove that every relevant source had been collected, or that the system warned before compromise.

Use `get_exact_duplicates` for identical text across different items. It does not determine semantic claim equivalence or independence. `add_evidence_relation` stores an explicit agent assertion with 1–20 supporting evidence IDs; `get_evidence_relations` returns these as `caller_asserted`. The agent must explain and revisit such relationships.

## Track change and report coverage

`get_corpus_coverage(source_id=null, kind=null, offset=0, limit=20)` reports the locally saved inventory without making network requests. The summary covers the entire selected inventory regardless of row pagination. Each response uses one database read snapshot and returns a store-wide `observations_through_seq` watermark; collection between calls can change the inventory while you paginate. By default it lists sources with saved items; requesting an exact registered source also returns an explicit zero-item row. `kind` filters evidence kind, so `kind="forum"` includes structured Tor forum posts even when their catalog kind is onion.

Counts use each item's latest revision. `stored_revisions` separately includes its older snapshots. `indexed_forum_posts` and `indexed_forum_threads` require an explicit saved thread identity; legacy items without one remain in `unindexed_forum_items`. Aggregate pages and individual posts overlap in text and must not be added together as independent evidence. `threads_without_explicit_root` means no saved post is explicitly marked as the root; it does not prove that the source has no root. Hidden post counts omit aggregate-page copies. Truncated items, currently unavailable/deleted items, unknown publication dates and items excluded by the default search access-state filter are reported separately and can overlap.

`first_recorded_at` and `last_positive_recorded_at` bound local positive recording times. Oldest/newest publication dates describe only known dates in the selected latest snapshots. Neither range proves continuous collection. The inventory is **not a search denominator**: historical matches, publication/collection filters and gate exclusions can change search scope. Search responses return those filters in `corpus_scope` and point to this tool. Consult `list_collections`/`get_collection` for exact visited/queued/deferred URLs and configured section bounds, and source health for failed requests. Coverage does not establish threat absence or detection before compromise.

`list_evidence_changes(after_seq)` returns a persistent observation sequence for new, edited, deleted, restored and unavailable items. Save `next_after_seq` in the runner. Unchanged observations remain stored but are omitted by default. Snapshot changes can include metadata or parser extraction improvements, so an `edited` observation is not itself proof that an attacker changed a claim.

Reports should identify original evidence and exact revisions, original publication time when known, collection bounds, source-health gaps, duplicates, alternative explanations and uncertainty. No matches within the collected corpus does not establish absence of targeting. A public CVE, an unverified access advertisement and a corroborated company-specific threat should remain distinct findings.

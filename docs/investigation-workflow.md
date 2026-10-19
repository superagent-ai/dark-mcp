# Agent investigation workflow

The MCP collects and preserves evidence. The agent develops hypotheses, resolves conflicting claims and assesses relevance and urgency. A runner schedules calls and notifications. No attack score or background monitor is built into this server.

## Start with scope and provenance

1. Call `get_status`, then paginate `list_sources`. Historical catalog checks are not current access guarantees.
2. Save company identifiers and context using `save_company_profile`, citing references. Profiles are versioned caller-provided information; they are never sent to sources automatically. Retrieve previous versions through `get_company_profile(profile_id, revision)`.
3. Inspect `get_source` and `get_source_health`. Health reports actual local requests and their limits, not a global coverage percentage. An empty search is recorded separately from a successful content read.

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

Onion history jobs accept an optional same-host `url`, such as a known thread or forum category. They follow only explicit next-page links, including `rel=next` and recognizable next-page anchors. They preserve each fetched page, with its visible replies, within configured response/text limits. They do not automatically discover every thread, parse every forum engine or visit arbitrary navigation links. Dates are extracted only from explicit article publication metadata; an arbitrary reply's date is not substituted for a thread's publication time. Missing dates remain unknown.

An onion challenge, login gate or empty page is retained as evidence of a collection gap. It does not exhaust the job: the failing URL remains in its queue. Repeated next-page URLs are not revisited. `complete` means the observed pagination is exhausted, not that the entire source has been read. Inspect `truncated` before claiming full-page coverage.

Job calls are bounded to 1–5 pages and 1–100 messages per page. A persisted lease prevents simultaneous advancement of the same job by two callers. If a process crashes, the lease expires; replay is safe because evidence identity and unchanged snapshots are deduplicated. Rate limits return immediately, preserve the checkpoint, and set a connector-wide Telegram cooldown that survives restarts. The runner should honor `retry_after_seconds` rather than issuing more requests.

## Poll new posts and observe edits/deletions

Create a separate Telegram collection with `mode="updates"`. Its first `collect_next` establishes a high-water mark from the latest accessible post; it does not silently backfill the source. Later calls read new IDs oldest-first so a bounded page cannot skip the middle of a new-post backlog. An `idle` job remains pollable. These jobs do not detect edits to older IDs by themselves.

For edits and deletions, call `sync_telegram_changes(channel)`. The first call establishes a Telegram `pts` cursor. Later calls store new/edited posts and apply explicit `UpdateDeleteChannelMessages` events only to matching, already-observed channel/message identities. Follow `has_more` with another bounded call. The account must be permitted to obtain channel differences; some sources will reject that API. A server-reported difference gap is recorded, never interpreted as a list of deleted posts. A gap may omit older events.

`refresh_evidence` also detects a changed page or directly rechecks a known broadcast post. A missing post on this lookup is marked **unavailable**, with `deletion_confirmed=false`: deletion and changed access cannot always be distinguished. Only explicit channel deletion events mark **deleted**. Previous snapshots remain available in either case. Restored identical content produces a restoration observation. A changed username cannot cause a missing message in a different channel to delete the old evidence.

`read_telegram_thread(channel, message_id, ...)` collects the root and one page of accessible linked discussion replies without joining a group. A history job can also take `thread_id` to resume those replies. Telegram does not support server-side text search in these replies, so combining `query` with `thread_id` is rejected. Replies retain their actual discussion peer IDs; they are not incorrectly cited as broadcast posts. Re-collect threads to observe reply edits; the channel update cursor covers the broadcast source, not inaccessible linked groups.

## Search and inspect exact evidence

All MCP content-read tools persist their returned onion/Telegram snapshots, including legacy read tools. Telegram identity uses channel ID plus message ID; aliases do not create separate copies of the same post. Onion identity uses the validated final URL. Source claims are never authenticated by these identities alone.

`search_evidence(query, kind, source_id, published_since, collected_since, include_history, include_gates, offset, limit)` searches onion, Telegram, forum and provider evidence. It uses casefolded literal substring matching, with a trigram index for queries of three or more characters. Punctuation is preserved: `superagent.sh` is not treated as a Boolean query or as unrelated words.

Historical matches are included by default and explicitly marked `is_latest_revision=false`. Gates are excluded by default. Provider search hits and previews remain searchable with explicit access-state labels; retrieve the snapshot to inspect `record_scope`. `published_since` filters only known publication dates; undated pages do not meet that filter. `collected_since` filters positive collection observations, including an unchanged snapshot re-observed recently. `collected_at` is the revision's original collection time and `last_collected_at` is its latest positive observation. `first_seen`/`last_seen` record this evidence store's observations, not the original publication date or global first sighting.

Use `get_evidence(evidence_id, revision_id)` to retrieve an exact revision. A content hash groups identical text; a snapshot hash covers the stored revision. `get_evidence_history` lists previous revisions without returning every full body in one response. Hashes support reproducibility; they do not prove the source's statements are true. Text/media truncation and publication/edit times are preserved where available. There is no image OCR or attachment download.

Use `get_exact_duplicates` for identical text across different items. It does not determine semantic claim equivalence or independence. `add_evidence_relation` stores an explicit agent assertion with 1–20 supporting evidence IDs; `get_evidence_relations` returns these as `caller_asserted`. The agent must explain and revisit such relationships.

## Track change and report coverage

`list_evidence_changes(after_seq)` returns a persistent observation sequence for new, edited, deleted, restored and unavailable items. Save `next_after_seq` in the runner. Unchanged observations remain stored but are omitted by default. Snapshot changes can include metadata, so an `edited` observation is not itself proof that an attacker changed a claim.

Reports should identify original evidence and exact revisions, original publication time when known, collection bounds, source-health gaps, duplicates, alternative explanations and uncertainty. No matches within the collected corpus does not establish absence of targeting. A public CVE, an unverified access advertisement and a corroborated company-specific threat should remain distinct findings.

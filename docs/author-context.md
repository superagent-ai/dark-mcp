# Source-local forum author context

Version 0.17 adds `get_collected_author_posts`. An investigator can retrieve other posts already collected for a seller or discussion participant, inspect context across threads and replay the exact evidence. This is local evidence retrieval. It does not fetch profiles, resolve identities, infer aliases or merge people across forums.

```text
get_collected_author_posts
{"source_id":"forum:niflheim","author_id":"123","limit":20}

get_collected_author_posts
{"source_id":"forum:bf-st","author":"example-handle","as_of":"2026-10-19T15:00:00Z","limit":20}
```

Supply exactly one `author_id` or `author`. IDs are positive numeric account IDs scoped to one source record. Leading zeroes normalize. A handle query matches exact casefolded text, including Unicode, and is explicitly labeled `casefolded_exact_handle_not_identity`. The same handle can describe several accounts or a renamed/reused alias. Results report distinct captured numeric IDs and posts lacking IDs; they do not consolidate these into an actor. Account IDs themselves are unverified source metadata and do not authenticate operators or human ownership.

## Original metadata

New structured forum reads preserve `author_id`, `author_url` and `author_identity` when explicitly available in the selected post header. MyBB numeric profile URLs, XenForo numeric member URLs and header `data-user-id` attributes can supply IDs. MyBB `/User-<handle>` and Question2Answer `/user/<name>` links remain profile references without invented numeric IDs. Recognized same-source profile links are metadata only; action routes, other-source destinations and unsafe/credential-bearing URLs are excluded. These links do not expand the collector's allowed read routes.

Conflicting IDs or a conflicting header handle remain unresolved, with original candidates and conflict flags retained. Profiles embedded inside a message body do not supply its author's ID. No profile requests are made. `get_evidence` and `get_collected_thread` retain these fields along with the rest of the saved post.

Legacy snapshots retain their original author strings. The derived author index backfills those existing fields in bounded batches without changing bodies, hashes, revisions or observation dates. It does not infer numeric IDs from a later collection, copied text or previously saved profile URLs. Newly preserved metadata can create a new snapshot revision with an unchanged body hash; the store's `edited` change does not by itself prove an author edit.

## Time, scope and pagination

`as_of` accepts an explicit timezone-aware timestamp. It selects the last recorded state of each post at that time before applying the author match. Later account IDs, renames, replies and author corrections do not appear in an earlier view. A removed or changed attribution does not revive an older matching revision in the current view. Explicit unavailable/deleted observations retain their state labels and previously captured evidence.

Each result includes evidence/revision IDs, hashes, author metadata, thread/post identifiers, source dates, selected-revision recording times, the evidence item's first-recorded time, acquisition method and hidden/truncated flags. `evidence_first_recorded_at` concerns the post, not the first time its present account metadata was known. Inspect `matched_revision_first_recorded_at` and the cutoff for that distinction. Source dates do not establish detection before disclosure or compromise.

Results order by latest local observation, then sequence/evidence ID. `limit` is 1–100, with offset pagination, a total matching saved-post count and a store observation watermark. Collection between calls can change pages. Post bodies are bounded to 16,000 characters each and the configured aggregate text cap; `context_text_truncated` reports response omissions. Full saved bodies remain available through `get_evidence`. No source-wide activity completeness, reputation score or monitoring runner is supplied by this lookup.

## Validation

A real stdio MCP check retrieved six existing discussions for one exact forum handle, verified an exact revision, passed a negative control and left corpus counts/hashes unchanged. One fresh native Niflheim thread read preserved two account IDs with matching header attributes and member links. Local ID lookup and exact replay passed across a server restart. An observation cutoff before that collection returned no retroactively inferred account-ID matches. No profile or Telegram requests were made. These checks establish collection/retrieval, not a new exposure, verified actor or early-warning lead time.

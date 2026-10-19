# Native forum post search

`search_forum_posts(source_id, query, limit=20, cursor=null)` uses a configured forum's own full-post search. It discovers mentions inside posts that title-only `list_forum_threads(query=...)` cannot find. `search_evidence` remains a literal search of this installation's saved corpus.

Inspect `get_source` for `native_search`. Only Spear's source-advertised MyBB GET form has been verified and enabled in the packaged catalog (`onion:91e4e785a3af`). Other collectors do not gain native search automatically. No accounts, memberships or access permissions are added.

```json
{"source_id":"onion:91e4e785a3af","query":"company.example","limit":20}
```

The query is 3–200 characters. The source chooses tokenization, matching, ranking, visibility and any server-side result cap. Full-post search can match thread titles, quoted content or text absent from the excerpt. A match is a lead, not a verified company exposure.

## Continue and retrieve originals

Pass `next_cursor` back with the identical source and query. The cursor is an opaque local handle; cached rows retain exact evidence revision bindings across server restarts. A call returns at most 50 rows and reads at most one remote result page. If `cached_results_remaining` is positive, continuation serves saved rows without a network request. A larger limit does not automatically read additional remote pages.

Each result supplies `url` and `matched_post_id`. Call `read_forum_thread` with that URL and verify that the returned page includes the matching post. Source search excerpts can be truncated, omit quoted context or hidden material, and do not establish what the original page currently contains. Ordinary original-thread pagination still applies. The collector never follows body links, attachments or proof files.

`source_result_window_exhausted` describes the inspected native result window after all cached rows have been returned. It is false when there are unresolved rows or pagination links. `history_complete` is always false: exhausting a source search does not establish complete coverage or absence of threats. Unknown templates, gates, explicit expired searches and repeated result pages are errors, not negative results. Only the observed native no-results message is recognized as a negative search.

Remote searches can expire or drift between pages. Cursors do not lock a remote snapshot. A failed continuation never starts a fresh query automatically; repeat the query explicitly when permitted. Reusing an older cursor can replay its rows or reread its specified remote page, not silently advance a mutable checkpoint.

## Evidence and timing

Excerpts are `kind=forum_search`, keyed by source, matched post ID and exact query. Different queries can produce different excerpts for the same post; these remain separate records. They never overwrite original `forum` posts or join the original-post author/thread indexes. Literal search and corpus coverage support this kind. `refresh_evidence` instructs callers to repeat the saved query or fetch the original; it never routes a search excerpt through Telegram.

Reported author labels/profile identifiers remain unverified source metadata. Raw timezone-free source dates remain unresolved: `published_at` is null. Collection/recording time does not prove pre-disclosure discovery. An excerpt revision change is not proof that its underlying original post was edited. Document checksums, page number and access context stay in observation provenance; changing search-cache identifiers and view counts do not create false excerpt edits. Original post text and identity require their own observation.

## Transport and waits

Only configured same-origin `/search.php` GET requests for full-post search and descending result pages are permitted. Redirects and pagination stay bound to the first returned search ID. Search routes are separate from normal thread/action permissions. Existing Tor or DNS-pinned HTTPS transports, robots, sessions, pacing and response bounds apply. Search cannot submit messages, solve challenges or authenticate an account.

Fresh queries have a persisted minimum 30-second interval per source and a cross-process search-read lease. Source HTML flood waits and HTTP rate limits persist to both search and original-source cooldowns. Cached local replay remains available during a network wait. No account switching, wait resets, automatic retries, scheduler or monitoring loop is included.

## Validation

Live stdio MCP validation on 19 October 2026 searched Spear for `OpenAI`, saved 14 result excerpts, returned two and replayed the remaining 12 after a process restart without a network request. A matched original post was fetched separately. A source-advertised buyer request for AI/cloud API keys demonstrates original collection; its claims and actor identity remain unverified. No company-specific compromise, impending attack or pre-disclosure detection was established.

A larger `account` query returned 20 excerpts on page one and 20 different excerpts on page two after a process restart. The second call made a remote read and retained a continuation cursor. The source advertised additional pages; they were not traversed. Live pagination required MyBB’s default `uid=0` parameter; other user filters remain unsupported.

A separate live `Superagent` query completed with the source's explicit no-results message and zero returned excerpts. This describes one guest query on one forum; it does not establish that the company faces no threats. Negative searches retain their query, document checksum and access context in saved collection attempts.

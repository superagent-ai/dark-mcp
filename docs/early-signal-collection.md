# Collect useful early-stage evidence

Version 0.5 adds native section ingestion and operator-owned sessions. The calling agent assesses relevance and uncertainty; scheduling, alerts and response remain outside the MCP.

## Direct original threads over Tor

Spear (`onion:91e4e785a3af`) now supports the same structured forum tools as the HTTPS sources. Use `get_source` to inspect its actual transport, known section URLs and dated access checks. It remains an onion-kind catalog record; filter on `collection_method="forum_page"` in returned records rather than assuming every forum collector has kind `forum`.

```text
list_forum_threads
{"source_id":"onion:91e4e785a3af","url":"http://spear4h2potiyk43oilufhuq2jqbioyaizcs7xttlyw75s5ykwxpxlid.onion/Forum-Access-Market","limit":100}

read_forum_thread
{"source_id":"onion:91e4e785a3af","url":"<thread URL returned above>","limit":20}
```

The catalog also supplies buyer, seller and database-discussion section URLs. Index filters apply only to titles on that page. Follow explicit pagination for more results. History index jobs do not enter threads. Use `mode="section"` with explicit thread/index/reply-page bounds to ingest discovered originals; see the [collection guide](forum-collection.md#ingest-a-bounded-forum-section).

Each collected post retains its visible body, author, permalink, root/reply marker when explicitly available, dates, quoted references, hidden-content status, body links and persistent evidence ID. Actual outbound URLs are retained even when the displayed link is shortened. References are metadata, not verified source identities, and are not automatically followed. A public forum repost of an access offer does not establish access to the marketplace it credits.

For uncertain dates, `published_at` remains null. `published_at_raw` preserves the displayed date/time; `published_at_hint` may preserve an absolute date in a tooltip. `timestamp_basis="unresolved_source_text"` explicitly reports the lack of a usable offset or source Unix epoch. Never turn a displayed September date collected in October into proof that our system detected an incident in September.

Use `start_collection` / `collect_next` to resume an index or thread across restarts. Post-limit omissions retain the current page cursor. Use `get_evidence`, `refresh_evidence`, `get_source_health` and `list_evidence_changes` as for other collectors. Tor reads honor robots, pacing, text/response bounds and redirect validation. Optional exact-origin sessions can be provisioned through the operator CLI. Registration, password login, challenge solving and downloads are not implemented; live authenticated access remains unverified.

### Live validation on 19 October 2026

A real stdio MCP client retrieved two relevant indexes and three original threads: an anonymous SaaS access advertisement, a request for LLM/cloud keys, and a webshell access advertisement with a public reply. Four post records retained authors, root/reply identity and source dates. A paginated access-market job continued after a server restart; a stored post was replayed and directly refreshed. Raw receipts remain private outside version control.

These are useful *types of evidence*: alleged access sales and advertised credential demand. Neither access validity, a named victim match, upcoming exploitation nor pre-compromise detection was established. Some posts explicitly credit another marketplace; its gated listing was not collected. Telegram was not contacted during its existing account cooldown.

## Build and operate our own source access

The product direction is a collection capability we operate ourselves. Competitor research identifies source gaps and validation criteria; it does not introduce competitor intelligence APIs as runtime dependencies. The optional onion search API remains a discovery aid, while original posts and our collected corpus supply evidence.

A bounded live section-ingestion check also collected 22 HARD-TM post records from two original threads and resumed after restart. Four posts retained hidden-content flags. A matching bounded Spear run collected two original roots and one reply across a restart. Earlier Tor attempts timed out; a longer request deadline succeeded. This is collection proof, not a validated early warning.

The next access milestones are:

1. **Validate source identity and readable depth.** Prioritize access-sale and targeting sections with original bodies, not homepages, mirrored headlines or advertisements pointing to inaccessible origins. Store verified locators, source incarnation, current access state and section-level coverage.
2. **Support operator-owned access where required.** For sources requiring membership, establish an eligible account and read permissions first. The forum connectors can now use an exact-origin server-side session provisioned by the operator, with explicit expiry and local removal. Credentials never belong in agent arguments. An implementation cannot create entitlement to a gated source; authentication and current access must be tested separately. No forum accounts are currently available, so authenticated live collection remains an access milestone. See [operator session setup](forum-collection.md#operator-owned-sessions).
3. **Collect the full context we can access.** Preserve roots, replies, author IDs/handles, original references, dates and hidden-content limits. Keep an access-market repost distinct from the listing it references. Expand parsers only against inspected source structures.
4. **Search the corpus we build.** Collect selected sections and chosen original threads with bounded, resumable calls. The MCP provides cursors and evidence storage; a later runner determines frequency and scheduling. Local searches must report the collected bounds rather than imply source-wide or global coverage.
5. **Verify actionable evidence and freshness.** Each new collector must retrieve a substantive original post, preserve its metadata and exact evidence revision, resume a multi-page read after restart, refresh the same item and report gates/expiry/rate limits explicitly. Keep publication, local first-seen and collection times separate. Detection before disclosure requires a recorded observation before that disclosure; detection before compromise needs a separately established compromise timeline.

Existing source research remains in `docs/vendor-source-research.md`. Research-only forum records do not become working collectors until access and parsing are implemented and verified. Monitoring, notification delivery and defender response remain later work.

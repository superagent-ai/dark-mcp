# Collect useful early-stage evidence

The MCP includes native section ingestion, operator-owned sessions and saved-thread reconstruction with local observation cutoffs. Exilio404 adds original Question2Answer questions, answers and comments over Tor. Version 0.13 adds local ingestion of operator-supplied browser HTML with distinct acquisition provenance. The calling agent assesses relevance and uncertainty; scheduling, alerts and response remain outside the MCP.

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

Use `get_collected_thread` to inspect saved roots/replies without re-fetching. Its optional `as_of` cutoff excludes content recorded later, even when a source displays an earlier publication date. This prevents later-collected evidence from becoming a claimed earlier warning.

Use `start_collection` / `collect_next` to resume an index or thread across restarts. Post-limit omissions retain the current page cursor. Use `get_evidence`, `refresh_evidence`, `get_source_health` and `list_evidence_changes` as for other collectors. Tor reads honor robots, pacing, text/response bounds and redirect validation. Optional exact-origin sessions can be provisioned through the operator CLI. Registration, password login, challenge solving and downloads are not implemented; live authenticated access remains unverified.

### Live validation on 19 October 2026

A real stdio MCP client retrieved two relevant indexes and three original threads: an anonymous SaaS access advertisement, a request for LLM/cloud keys, and a webshell access advertisement with a public reply. Four post records retained authors, root/reply identity and source dates. A paginated access-market job continued after a server restart; a stored post was replayed and directly refreshed. Raw receipts remain private outside version control.

These are useful *types of evidence*: alleged access sales and advertised credential demand. Neither access validity, a named victim match, upcoming exploitation nor pre-compromise detection was established. Some posts explicitly credit another marketplace; its gated listing was not collected. Telegram was not contacted during its existing account cooldown.

## Regional original discussions over Tor

Exilio404 (`onion:a9bcde64e044`) now supports structured collection in configured hacking/leak categories, using the same forum and section-ingestion tools. A real stdio MCP check collected nine posts from two original discussions: two questions, four answers and three comments. Authors, explicit timestamps and comment parents were preserved; restart/resume, exact replay and direct refresh passed. See the [Exilio404 guide](forum-collection.md#exilio404-questions-answers-and-comments).

One request mentions two training providers and explicitly says it is for studying. Its wording supplies context for an access-sharing inquiry; attack intent, account validity and early-warning lead time remain unverified. Broad marketplace categories contained substantial unrelated activity and are excluded from this structured collector. This is secondary regional coverage alongside the higher-priority access-market sources.

## Priority access-market recheck

A fresh MCP check found the cataloged DarkForums route (`onion:e51377a2216a`) readable after the original timeout. Structured access-market/buyer collection now preserves original bodies, author handles, explicit post numbers and raw dates. A bounded live run collected 13 posts across two discussions, resumed a thread after restart and verified saved context/replay/refresh. The sampled sale thread's closing reply states that the data was sold; that is an unverified source statement, not a currently open offer or proven transaction. The sold statement does not establish remediation or remove possible continuing exposure. Collection today cannot establish earlier detection from an older displayed date.

The [dated priority snapshot](../sources/priority-forum-access-2026-10-19.json) also records current Exploit/XSS challenges and the failed ReHub connection. Those requests did not establish original-thread access. See the [DarkForums guide](forum-collection.md#darkforums-access-market-context) for exact tool calls and coverage limits.

## Bounded AI-service investigation

A subsequent live MCP run traversed 12 access-market, buyer and web-vulnerability index pages on DarkForums and Spear. It inspected 248 listing rows (including repeated pinned entries), selected 12 title candidates, and collected four chosen discussions containing ten post records. This was a bounded investigation; the saved index jobs still have unvisited pagination. It establishes neither full forum coverage nor absence of other mentions.

The review found generic API-key acquisition demand, account/session-sale advertisements and an endpoint exposure claim. A pair of similarly worded requests across the two forums was linked as a caller-asserted possible repost with both originals as support. Shared wording and a contact label do not establish common ownership or independent corroboration. No keys, credentials, proof downloads or third-party endpoints were tested.

Within the saved structured-forum corpus, current literal searches returned zero `superagent.sh`/`Superagent` or `Legora` records. The 23 `Replit` hits included 19 title-only matches; the 26 `Lovable` hits included 25 title-only matches. These are matching records, including aggregate pages and replies, not counts of distinct threats. Account/session-sale and abuse-tooling claims remained unverified, with gated payloads and unclear victim scope. None established an impending attack against the test companies or pre-disclosure detection. Raw receipts and bodies remain private.

The investigation exposed two collection-quality issues corrected in 0.8.1: public HTML spoiler content had been omitted, and search hits did not identify repeated-title matches. Refreshing the affected original restored its service list while preserving the earlier evidence revision. Search now reports title/body provenance and the selected revision's local recording times. This supports honest investigations without adding threat scores, scheduling or alerts to the MCP.

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

## Local inventory and additional candidate checks (0.9)

The MCP now exposes `get_corpus_coverage` so investigators can inspect saved item counts, explicit thread identities, missing roots, hidden posts and other collection limits before interpreting searches. It is an inventory of the corpus we have built, not a percentage of the underground or a count of independent claims. Search responses retain their exact filters and identify this inventory tool.

A follow-up public access check tested the historically reported DamageLib onion root through `fetch_onion`; it failed or timed out. Candidate BHF roots were checked with the native bounded HTTPS transport: `bhf.pro` failed/timed out, `bhf.gg` returned HTTP 530, and `bhf.im` returned a domain-sale page. The dated candidate receipts are retained in the [source research](../sources/vendor-source-research-2026-10-19.json) and available through `get_source`. These checks establish no original-thread access or operator continuity. Both records remain metadata-only with no verified collection locator. Six configured forum collectors remain; these failures are not new feeds.

The first inventory check found 219 indexed post items across 44 explicit forum threads, with 33 threads lacking an explicit saved root marker. Inspecting returned HTML showed that header navigation/share links could precede the numbered permalink. The parser now reads all eligible header counters, excludes body/quote counters and leaves conflicting counters unresolved. Live MCP reads of one original thread each on Cracked, Niflheim and HARD-TM returned 22 posts in total with explicit numbers and three explicit roots. Local reconstruction recognized the roots and older evidence revisions remained replayable. Thirty other saved threads still lack explicit root markers in their latest snapshots; the fix does not rewrite old evidence or establish full-source coverage. This validates extraction quality, not an early-warning finding.

## Additional native source: LeakForum (0.10)

`forum:leakforum` adds original buyer-request and service discussions over bounded direct HTTPS. A real stdio MCP run discovered a Google GCP/AI Studio account request, restarted the server, collected its three posts and reconstructed the saved thread. Two other original discussions contributed twelve posts. A separate account-resale discussion contributed twenty posts over two pages, with a restart between pages and further pagination left unvisited. All 35 unique post items retained author handles, explicit source timestamps and post numbers; all four roots were identified. Exact replay, direct refresh, positive local search and a negative control passed. This validates source access and evidence retrieval; it does not establish an impending attack or independently validate any advertised access.

There are now seven configured forum collectors, including four HTTPS sources and three Tor sources. The registry has 173 candidate records, not 173 working feeds. New native candidate checks on Cracking.org, a PwnForums HTTPS address, HydraForums and Duty Free established no thread access. Cracking.org was denied by the transport's robots handling; the other candidate connections failed or timed out. Those dated failures remain metadata, without active collectors. No forum accounts were created, no actors were contacted and no Telegram requests were made.

## Listing context and selection (0.11)

Original-source collection now distinguishes recognized MyBB/XenForo thread rows from unrelated thread links, preserves source creator/date/activity/pin context, and supports optional known-pin exclusion in listing and resumable section calls. Unknown pin status remains eligible; the default preserves all listings. Row-cap omissions are explicit in page evidence, source attempts and section checkpoints. Old evidence revisions are not rewritten.

Live stdio MCP checks on HARD-TM, LeakForum and DarkForums verified this against three original indexes. HARD-TM returned 28 actual thread rows rather than the earlier 35 generic thread links; three were explicitly pinned and 25 remained eligible. LeakForum retained all 20 normal buyer-request rows despite a misleading sticky CSS class. DarkForums returned 20 actual rows rather than 21 generic links. Earlier revisions remained replayable. Two filtered section jobs selected the existing Lovable advertisement and GCP account request, resumed across a server restart and collected four post records with explicit roots/numbers. Further index/thread pages were deferred at the chosen bounds.

The HARD-TM Lovable advertisement was created on 18 August and showed activity on 19 October. That metadata is context for a previously collected resale claim, not a new attack finding. MyBB index dates lacked offsets and remain raw. These checks improve collection relevance and chronology; they do not establish a pre-compromise warning or broader source access.

## Larger public access-market trial

On 19 October 2026, a real stdio MCP run collected access-market and buyer sections from DarkForums and Spear. Four bounded jobs retrieved 42 pages: eight indexes containing 160 listing rows and 34 thread pages covering 32 distinct discussions and 108 distinct post items. This added 91 post items and 27 threads to the saved corpus; 17 post items were revisited. All four jobs completed at the chosen bounds without collection errors. Each selected at most eight threads, two indexes and two pages per thread, excluding known pinned listings. Further section coverage and one thread's reply pagination remain unvisited. No hidden/truncated post flags or index row-cap omissions occurred in this sample; those checks cannot establish access to content outside it.

Literal title/body searches restricted to these 108 post items found no matches for `superagent.sh`, `Superagent`, `Legora`, `Replit`, `Lovable` or `Firecrawl`. One body matched `OpenCode` in the already collected generic API-key demand post. None established a company-specific impending attack, verified exposure or detection before compromise/disclosure. These are scoped search results, not absence claims for the companies or forums. Other saved sources are outside this trial's search denominator.

Three sampled access advertisements explicitly credited the same upstream marketplace and linked to its homepage. A direct native root request failed with `tor_unavailable`; no upstream listing was collected. Two `shared_upstream_reference` evidence relationships record that published reference as a caller assertion. This does not merge victims, authenticate actors or establish independent corroboration. An anonymous country/sector description cannot identify a test company. A later reply in a previously collected sale thread says the data was sold; that statement remains unverified and does not establish remediation or a currently open offer.

All sampled source publication dates lacked usable offsets/epochs and remain raw. The completed collection window was 14:39:59–14:42:23 UTC; an older displayed date cannot move our observation back in time. Raw receipts, evidence references and the detailed report remain private under `data/`. No Telegram calls, actor contacts, proof downloads or advertised credential/endpoint tests were made. The next access milestone remains substantive original listings and operator-owned gated reads, rather than more relay counts or monitoring tooling.

## Browser-read source ingestion (0.13)

`forum-page-import` can now ingest an operator's bounded UTF-8 HTML from a configured readable forum page without making requests or running scripts. Parsed evidence retains original post identities and exact revisions alongside `operator_html_import` acquisition metadata and an input-document hash. Native forum reads now explicitly identify their acquisition method. Search, local thread reconstruction and corpus inventory preserve the distinction; older revisions without this metadata remain unreported. Import time is the local observation time. Imports do not establish source reachability, clear cooldowns or verify the supplied URL, membership or origin. Imported latest revisions require another local capture for refresh. See the [capture workflow](forum-captures.md).

A real browser/import check preserved 20 LeakForum buyer-request listings and three original GCP/AI Studio request posts. This reobserved previously known content and established neither a new feed nor a verified early warning. A normal-browser Exploit check still reached a challenge/gateway page. Source access remains a separate milestone; the importer does not replace membership, support every forum engine or authenticate a captured page.

## Operator-provided source access (0.12)

Operators can now configure a private MyBB/XenForo source locally, attach an existing exact-origin session and use the standard MCP tools without publishing that collection configuration. Source import is not an access test. It rejects credentials and unsafe routes, separates operator provenance from vendor research, preserves source identity across removal/re-import and keeps generic/structured Tor cooldown identity shared. Removal clears local sessions and disables the source on restart while preserving evidence. See the [operator source guide](operator-sources.md).

Additional native root checks found no new content access: ReHub's HTTP onion route was denied by robots handling; the DumpForums HTTPS candidate returned an empty document; and a separately published BreachForums onion candidate failed/timed out. These are recorded separately in the [dated priority checks](../sources/priority-forum-access-2026-10-19.json), without activating collectors or authenticating successor identities. An isolated live operator configuration for a DarkForums HTTPS candidate loaded correctly, reached a browser challenge and retained its collection cursor. Removal took effect after restart. It established no original content access or independent source coverage. Authenticated account collection is still verified only with deterministic fixtures; operator-provided membership remains necessary for a live gated-source test.

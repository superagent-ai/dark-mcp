# Collect original forum threads

Version 0.12 uses the same two forum tools for four HTTPS sources and three Tor sources: Spear, Exilio404 and DarkForums. They were tested through a real stdio MCP client on 19 October 2026; these are dated, bounded access checks.

| Source ID | Tested access | Material limitation |
| --- | --- | --- |
| `forum:hard-tm` | Original Lovable advertisement: one author, publication timestamp, post permalink and over 3,000 characters of visible terms | Purchase/contact links require registration; no account provenance or compromise established |
| `forum:niflheim` | AI advertisement with two posts; FAQ with 20 posts on each of two pages, authors and timestamps | Other thread bodies can require login; readable replies do not reveal hidden content or corroborate a claim |
| `onion:91e4e785a3af` (Spear, Tor) | Access-market and buyer indexes; three threads with four visible posts, authors, root/reply markers and body references | Source dates lack explicit timezone/epoch in the tested posts; original marketplace destinations can be gated; claims unverified |
| `onion:a9bcde64e044` (Exilio404, Tor) | Two original discussions: two questions, four answers and three comments, with authors and explicit timestamps | Portuguese-language regional coverage; sample access-sharing request says it is for studying; threat intent and lead time unverified |
| `onion:e51377a2216a` (DarkForums, Tor) | Access-market and buyer indexes; 13 posts across two discussions, authors and explicit post numbers | Raw dates lack timezone/epoch; sampled sale thread ends with a claimed sold update; source identities and transactions unverified |
| `forum:leakforum` | Buyer and service discussions: 35 post items across four threads, authors, source epochs, explicit numbers and replies | Two advertisement pages sampled; more remain. Identity, access-sale claims and early-warning value unverified |
| `forum:cracked` | Staff notice and a listing with visible replies on two pages, authors and timestamps | Listing payload hidden behind registration; many replies contain little substantive text |

The [access snapshot](../sources/forum-access-2026-10-19.json) records hosts, example URLs, identity caveats and verification bounds. Niflheim and separately cataloged BHF are not treated as the same source. The currently observed Cracked-branded service is not authenticated as a continuation of any historical operator.

## Discover and read

Call the tools with these JSON arguments:

```text
list_forum_threads
{"source_id":"forum:hard-tm","query":"Lovable","limit":20}

read_forum_thread
{"source_id":"forum:hard-tm","url":"https://hard-tm.com/threads/185218/","limit":20}

search_evidence
{"query":"Lovable","kind":"forum","source_id":"forum:hard-tm","include_history":false}
```

`list_forum_threads` inspects one accessible index page, extracting up to 100 unique listings before applying the local title filter. Recognized MyBB subject rows and XenForo thread rows exclude unrelated sidebar/navigation links. The response labels `thread_listing_scope="recognized_thread_rows"`; unfamiliar layouts fall back to `unscoped_links`, with unknown pin status. Question2Answer currently leaves the listing scope unreported. `omitted_threads` counts listings beyond the 100-row cap; `omitted_matches` separately counts filtered matches beyond the requested result limit. Follow `next_page_urls`, returned `sections`, or another `collection_urls` section from `get_source`. An empty filtered index does not establish source-wide absence. Automated search routes are not supported.

Recognized rows return `listing_basis`, a source-local `source_thread_id` when explicit, creator `author`, `published_at`/`published_at_raw`, `last_activity_at`/`last_activity_at_raw`, and `pinned` (`true`, `false` or unknown/null). Numeric source IDs are metadata, not an assertion that different URLs/sources are independent or automatically merged. Last activity can be a reply or bump on an old thread; it is not a new publication or a compromise timestamp. Only explicit offset-bearing source datetimes are normalized; offset-free MyBB dates remain raw. Handles and times remain source claims.

Use `exclude_pinned=true` on `list_forum_threads` to skip only explicit pins after applying the title filter. The default includes all listings, and unknown status stays eligible. `excluded_pinned_matches` and `unknown_pin_status_matches` describe matches before the result limit. The full parsed index remains in saved page evidence. MyBB pin status uses the nearest explicit Important/Sticky/Normal Threads section heading, not misleading theme CSS classes. Unrecognized headings remain unknown. XenForo uses explicit sticky/normal thread-group containers; other layouts remain unknown. Pinned offers may still be relevant, so exclusion is an investigator choice.

`get_collected_thread` reconstructs saved posts and replies without fetching the source again, optionally as of a local recording-time cutoff; see the [investigation workflow](investigation-workflow.md#search-and-inspect-exact-evidence).

`read_forum_thread` reads one page of posts, labeled with its source ID. Each post includes its permalink, visible text, author handle, normalized publication time where available, the raw displayed date, quoted-post references, `hidden_content`, `truncated` and a persistent evidence reference. MyBB publication epochs are parsed from post-specific UI metadata without executing scripts. Source times and author identities remain unverified. A page's first visible reply is not presented as the thread's creation time. `post_number` preserves an explicit source counter or position when available. `is_thread_root` uses that source counter or position; when absent it remains null. A page publication time is only taken from an explicitly identified root. Header counters are inspected after navigation/share links; quoted/body counters and conflicting header numbers do not establish a position. `timestamp_basis`, `published_at_raw` and `published_at_hint` distinguish explicit timestamps from unresolved displayed dates. No timezone is inferred from collection time.

`hidden_content=true` means some content or links require access the collector does not have. A readable post may still be partial. Images, attachment contents, linked documents and link-only destinations are not collected. Returned HTML is parsed as text; scripts are never executed. MyBB spoiler text already present in returned HTML is included when its inspected `.spoiler_title`/`.spoiler_button` and `.spoiler_content` markup identifies a presentation toggle. No scripts or source-provided validation commands are executed. Explicit login/reply gates remain partial, and separately fetched content is not unlocked.

Up to 50 body `links` preserve the actual URL and displayed label as untrusted references; a reference is never fetched automatically. References from quoted text can also be present and do not establish authorship.

Niflheim may return a login placeholder as a post body while exposing replies publicly. Those posts are marked `hidden_content=true`; the existence of visible replies is not evidence that the underlying payload was read. When tracing reposts, compare the source's publication time with the relay time. A matching advertisement published after a relay supports a related advertising cluster, not an exact original-post attribution.

## Exilio404 questions, answers and comments

```text
list_forum_threads
{"source_id":"onion:a9bcde64e044","url":"http://exiliow4ctlzrvaglkgwqnpxdlvrxmdgvuy2hkbzqoziebfim6q5hwid.onion/hacking","query":"Codex","limit":100}
```

Use a returned question URL with `read_forum_thread` or a `history` job. Section mode also discovers these question roots and resumes after restart. The configured categories are `/hacking`, `/vazamento-de-dados` and their `/questions/` feeds. Numeric `show` and `start` parameters support post rechecks and explicit forward pagination. Login, profile, arbitrary category, search, posting, plugin and attachment routes are excluded. The source's robots URL currently redirects to its homepage; the existing Tor reader follows allowed same-host redirects and parses the returned document for robots directives.

Question2Answer posts expose `post_type` (`question`, `answer`, `comment`) and `reply_to_post_id`. Only an explicit question root receives post number 1; answers can be ranked by votes/selection rather than chronology, so their numbers remain unknown. Comments present in returned HTML retain their own authors and dates; separately loaded comments are outside coverage. A parent's dates and body do not absorb nested comments.

`source_events` preserves labeled changes such as answer selection. Selection is not classified as an edit. Only an explicitly labeled edit fills `edited_at`; a generic `dateModified` attribute alone is insufficient. Company names in ordinary study requests or software discussions do not establish hostile targeting.

Live MCP validation on 19 October 2026 collected two root questions, four answers and three nested comments. All nine had explicit timestamps and authors. Section discovery continued after a process restart; exact evidence replay, local thread reconstruction and direct refresh succeeded. These checks establish access and parsing. They do not establish source authenticity, unauthorized access or detection before disclosure/compromise. Private receipts and raw bodies remain outside Git.

## DarkForums access-market context

```text
list_forum_threads
{"source_id":"onion:e51377a2216a","url":"http://darkfoxaqhfpxkrbt7vxns2z2u2k72sgmqbzeorupaiottw3ecm2wgyd.onion/Forum-Buyers-Place","query":"Insiders","limit":100}
```

Read selected original thread URLs with `read_forum_thread`, or use history/section collection. This classic MyBB theme places author handles in its user-profile header and post numbers/permalinks in the post header. The collector preserves those fields; replies on a later page do not become a new root. Source dates such as offset-free numeric dates or “hours ago” remain raw text, with `published_at=null` when no usable source offset/epoch exists.

A real stdio MCP run checked two indexes and collected 13 posts across two discussions: an access-sale claim and an insider-recruitment request. One thread's pagination resumed after a process restart; local thread context, exact revision replay and direct refresh succeeded. All 13 posts retained authors and explicit post numbers. The sampled access-sale thread ends with the original poster stating the data was sold. That preserved context prevents treating its opening advertisement as a newly available offer; it does not verify the claimed sale or compromise. A sold statement does not establish remediation or remove possible continuing exposure. Linked proof images remain uncollected references.

The [priority access check](../sources/priority-forum-access-2026-10-19.json) separates this newly readable route from Exploit/XSS challenge pages and a failed ReHub connection. Original 18 October checks remain preserved in the catalog's research history. Current site branding and a successful read do not authenticate the operators or historical incarnation.

## LeakForum buyer requests and services

```text
list_forum_threads
{"source_id":"forum:leakforum","url":"https://leakforum.io/Forum-Buyer-Requests","query":"Google GCP","limit":100}

start_collection
{"source_id":"forum:leakforum","url":"https://leakforum.io/Forum-Buyer-Requests","mode":"section","query":"Google GCP","max_threads":1,"max_index_pages":1,"max_thread_pages":2}
```

Use returned thread URLs with `read_forum_thread`, or call `collect_next` to advance the bounded section job. The configured discovery sections are buyer requests and the two distinct service sections. The public MyBB theme places numbered permalinks in `.pagnum`; those source counters identify roots and reply positions. Explicit post-specific quote epochs supply timestamps without executing source code. Dates remain source claims.

Live stdio MCP validation on 19 October 2026 collected 35 unique post items across four original discussions: a Google GCP/AI Studio account request, a claimed breaching service, a log-search service advertisement, and two pages of an account-resale advertisement. All sampled posts retained authors, timestamps and numbers. Section discovery and reply pagination resumed across server restarts; exact evidence replay, local reconstruction, direct refresh, corpus search and a negative control passed. The two-page history job still has unvisited pagination and remains `ready`; it has no background runner.

The requested cloud accounts and advertised services provide investigative leads. They do not verify stolen access, identify a targeted company or establish detection before compromise/disclosure. No keys, advertised services, attachments or third-party endpoints were tested. Current public access is not a guarantee of other sections or authenticated content. `leakforum.io` is the only configured host. Its homepage advertises `leakforum.st` as a mirror, but that host remains unvalidated and outside this collector. This source is distinct from LeakBase and is not authenticated as a BreachForums successor.

## bf.st public buyer and access discussions (0.14)

`forum:bf-st` is a separately observed HTTPS source using BreachForums branding. Native reads established public original posts on the exact host `bf.st`; they do not authenticate historical operators or successor status. The metadata-only `forum:breachforums` record and its older candidate checks remain separate. The homepage-advertised onion reference is not a verified mirror or an allowed collector host.

```text
list_forum_threads
{"source_id":"forum:bf-st","url":"https://bf.st/Forum-Buyers-Place","query":"Claude","limit":100}

start_collection
{"source_id":"forum:bf-st","url":"https://bf.st/Forum-Buyers-Place","mode":"section","query":"Claude Pro","max_threads":1,"max_index_pages":1,"max_thread_pages":2}
```

Read a returned original URL, or advance the job with `collect_next`. The two configured sections are `Forum-Buyers-Place` and `Forum-Access-Market`. Source read-route restrictions, public-DNS pinning, TLS verification, robots, pacing and body/pagination bounds remain in force. No registration, source search/action routes or attachments are enabled.

This MyBB theme puts author handles in `.post__author .post__user-profile` and numbered permalinks inside nested `.post_head .float_right` elements. Native reads now retain those fields. Counters inside post bodies/quotes cannot identify a root; conflicting header numbers remain unknown. Publication text excludes a nested edit notice, whose separate `edited_at_raw` is retained. Offset-free month-name dates and relative labels stay unresolved; a source tooltip does not supply a missing timezone.

The first three original-page checks collected 14 post items, with all author handles, explicit numbers and three roots. An account-cookie request, a recruitment request and an anonymous access advertisement are source claims, not verified stolen access or an impending provider attack. The access advertisement was created months before its recent activity; pagination and later replies must be inspected before calling it new or open. A recruitment post closely resembles previously captured DarkForums advertising, which does not establish common ownership or independent corroboration. Raw bodies, contact labels and proof references remain private; references are never automatically fetched.

The packaged source then passed a separate real stdio MCP acceptance run: a bounded buyer section and a two-page access discussion resumed across server processes, collecting 17 distinct post items across the three selected threads. All 17 retained authors and explicit numbers; three roots were recognized. Exact revision replay, direct refresh, positive local search and a negative control passed. A caller-asserted possible-repost relation links the similar recruitment text without authenticating actors. The buyer job read one index and one selected thread; other listings and index pagination remain deferred. No source dates had usable offsets/epochs, so no pre-compromise/disclosure lead time is established.

## Resume multiple pages

```text
start_collection
{"source_id":"forum:niflheim","url":"https://niflheim.world/threads/frequently-asked-question.110/","mode":"history"}

collect_next
{"collection_id":"<returned id>","max_items":20,"max_pages":1}

get_collection
{"collection_id":"<returned id>"}
```

History jobs follow explicit next-page links within the same thread or index. History index jobs do not enter linked threads. Use section mode below to discover and ingest selected original threads. Each call reads at most five pages; post limits are 1–100 per page, with 16,000 characters per post and the configured total text bound. A post limit that omits visible posts produces `forum_page_incomplete` and retains the page cursor. Retry with a higher `max_items` or text bound. Hidden sections and truncated posts remain explicit even when a job exhausts its observed page cursor.

Evidence, source attempts and checkpoints survive restarts. `get_evidence` retrieves a saved revision with hashes and provenance; `refresh_evidence` rechecks the post permalink. A missing post is unavailable evidence, not proof of deletion. `search_evidence` searches only collected text with literal substring matching. Include relevant aliases/domains and inspect matches in context.

## Ingest a bounded forum section

For a small sample of the original context and a later page, use [root/last-page collection](last-page-collection.md). Version 0.18 adds optional `thread_page_order="root_then_last"`; the default remains `oldest_first`.

```text
start_collection
{"source_id":"forum:hard-tm","url":"https://hard-tm.com/forums/sell_all/","mode":"section","max_threads":10,"max_index_pages":2,"max_thread_pages":3}

collect_next
{"collection_id":"<returned id>","max_items":100,"max_pages":5}
```

Section mode reads indexes, canonicalizes discovered links to thread starters, collects their bodies and follows explicit reply pagination. `query`, if supplied, is a local title filter; it does not search bodies or the whole source. Optional `exclude_pinned=true` is supported only in section mode and skips known pins while retaining unknown status. The default is false, including when resuming older jobs without this option. `since` and `thread_id` are not supported in this mode. Repeat `collect_next` to advance; queue, selected threads and page counts survive process restarts. There is no background collection.

Bounds are 1–500 threads, 1–50 index pages and 1–20 pages per thread, plus an overall ceiling of `max_index_pages + max_threads * max_thread_pages` attempted pages. Inspect checkpoint `section`: `threads_selected`, `thread_pages`, `failed_pages`, `hidden_posts_seen`, `deferred_count` and `deferred_urls` (a sample capped at 100 URLs, with reasons). Known pins skipped by choice use reason `pinned_thread_excluded`. `index_rows_omitted` accumulates listings omitted by the per-page 100-row cap; those unknown URLs remain a coverage gap. A finished job means its configured bounds were exhausted, including recorded gaps. It does not establish full source coverage.

Individual gated/missing threads are recorded as gaps and collection continues. Root/index failures, transient connection/server errors, expired sessions and rate limits retain the current cursor for retry. Rate limits persist across restarts. A page whose visible posts exceed `max_items` retains its cursor rather than silently skipping them. Hidden content remains unavailable even when other posts on the page are readable.

A real stdio MCP check on 19 October 2026 discovered two HARD-TM threads from one index and saved 22 post records across three pages, with a server restart between discovery and thread ingestion. Four posts had hidden content; 35 URLs were deferred at the chosen bounds. Exact evidence replay succeeded. This validates ingestion, not the truth or relevance of the posts, lead time, or authenticated access. A second public run on Spear collected two root posts and one reply from two discovered access-market threads, also across a restart. Earlier Tor attempts timed out; the successful run used a 90-second request deadline. It recorded 19 deferred URLs and no failed pages. Raw receipts stay private.

## Operator-owned sessions

Custom MyBB/XenForo addresses can be provisioned through the local operator registry before importing their sessions; see the [operator source guide](operator-sources.md). This configuration path does not grant forum membership or bypass a challenge.


An operator with an existing account may import a JSON cookie export containing only one exact catalog host. Supported shapes are a list or `{"cookies":[...]}`; each row needs `name` and `value`, with optional `domain`, `path`, `secure` and Unix `expires`/`expirationDate`. Exports are limited to 128 KB and 100 cookies. The CLI rejects wrong-host and malformed entries without echoing their values.

```sh
uv run dark-mcp forum-session-import forum:hard-tm /private/path/forum-cookies.json --ttl-hours 24
uv run dark-mcp forum-session-status forum:hard-tm
uv run dark-mcp forum-session-remove forum:hard-tm
```

The default origin is the source locator. Supply `--origin https://hard-tm.su` for that catalog mirror; it needs its own session. Cookies are never forwarded from `.com` to `.su`, even if DNS resolves both to the same IP. Path, Secure and cookie expiry are enforced. A local TTL of 1–720 hours applies independently; response cookie rotation never extends it. Expired configured sessions fail before network access. Removing a local session returns reads to public access and does not revoke the remote login.

Sessions live separately in `<data_dir>/forum-sessions/sessions.sqlite3`, unencrypted, with a 0700 directory and 0600 database. Restrict access to the data directory, protect the original export and remove it when no longer needed. Session import/removal are operator CLI actions, never MCP tools. The read-only `get_forum_access_status` returns metadata without cookie names or values. `access_context` on evidence records the origin, local session ID/state and whether credentials were attached. `authentication_verified=false` intentionally persists: configured cookies or an HTTP 200 alone do not establish successful login, entitlement or additional readable depth.

Deterministic tests exercise authenticated reads over HTTPS and Tor, expiry, cookie rotation, host isolation and sanitized evidence. **Live authenticated forum collection remains unverified: no accounts are available yet.** Register/sign in separately and validate a permitted original thread before claiming gated coverage. The MCP performs no registration, password login, challenge solving, purchases or posting.

## Transport and coverage

Cracked, Niflheim, HARD-TM and LeakForum use direct HTTPS. Spear, Exilio404 and DarkForums use the configured Tor SOCKS proxy and the existing bounded onion transport; none is resolved through the direct HTTPS DNS path. Every Tor forum redirect and robots URL is restricted to that catalog host and the supported read routes. No forum credentials are needed for visible content. Only configured catalog hosts and read routes are eligible. Requests validate redirects, reject private or mixed DNS answers, connect to checked public IPs while retaining hostname TLS verification, honor robots.txt, and apply per-host pacing and response limits. Requests do not use environment proxies. Imported operator sessions are optional and exact-origin scoped; robots requests never carry account cookies. Registration, password login, challenge bypass, posting, account purchases and attachment retrieval are unsupported.

Cracked's configured host is `cracked.st`; Niflheim uses `niflheim.world` and `niflheim.top`; HARD-TM uses `hard-tm.com` and `hard-tm.su`; LeakForum uses `leakforum.io`. Observed redirects or matching branding establish routing context, not operator authenticity. Other forum catalog entries remain metadata-only until separately implemented and verified.

The MCP supplies original evidence and access diagnostics. The investigating agent decides whether a claim concerns the company, whether it is original or reposted, and whether it merits action. A subscription resale advertisement alone is not evidence that the platform was breached.

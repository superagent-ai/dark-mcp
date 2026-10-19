# Collect original forum threads

Version 0.5 uses the same two forum tools for three HTTPS sources and Spear over Tor. They were tested through a real stdio MCP client on 19 October 2026; these are dated, bounded access checks.

| Source ID | Tested access | Material limitation |
| --- | --- | --- |
| `forum:hard-tm` | Original Lovable advertisement: one author, publication timestamp, post permalink and over 3,000 characters of visible terms | Purchase/contact links require registration; no account provenance or compromise established |
| `forum:niflheim` | AI advertisement with two posts; FAQ with 20 posts on each of two pages, authors and timestamps | Other thread bodies can require login; readable replies do not reveal hidden content or corroborate a claim |
| `onion:91e4e785a3af` (Spear, Tor) | Access-market and buyer indexes; three threads with four visible posts, authors, root/reply markers and body references | Source dates lack explicit timezone/epoch in the tested posts; original marketplace destinations can be gated; claims unverified |
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

`list_forum_threads` inspects one accessible index page, extracting up to 100 thread links before applying the local title filter. Follow its `next_page_urls`, returned `sections`, or inspect another `collection_urls` section from `get_source`. An empty filtered index does not mean the forum has no matching posts. Automated search routes are not supported.

`read_forum_thread` reads one page of posts, labeled with its source ID. Each post includes its permalink, visible text, author handle, normalized publication time where available, the raw displayed date, quoted-post references, `hidden_content`, `truncated` and a persistent evidence reference. MyBB publication epochs are parsed from post-specific UI metadata without executing scripts. Source times and author identities remain unverified. A page's first visible reply is not presented as the thread's creation time. `is_thread_root` uses an explicit source post counter or position; when absent it remains null. A page publication time is only taken from an explicitly identified root. `timestamp_basis`, `published_at_raw` and `published_at_hint` distinguish explicit timestamps from unresolved displayed dates. No timezone is inferred from collection time.

`hidden_content=true` means some content or links require access the collector does not have. A readable post may still be partial. Images, attachment contents, linked documents and link-only destinations are not collected. Returned HTML is parsed as text; scripts are never executed. Up to 50 body `links` preserve the actual URL and displayed label as untrusted references; a reference is never fetched automatically. References from quoted text can also be present and do not establish authorship.

Niflheim may return a login placeholder as a post body while exposing replies publicly. Those posts are marked `hidden_content=true`; the existence of visible replies is not evidence that the underlying payload was read. When tracing reposts, compare the source's publication time with the relay time. A matching advertisement published after a relay supports a related advertising cluster, not an exact original-post attribution.

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

```text
start_collection
{"source_id":"forum:hard-tm","url":"https://hard-tm.com/forums/sell_all/","mode":"section","max_threads":10,"max_index_pages":2,"max_thread_pages":3}

collect_next
{"collection_id":"<returned id>","max_items":100,"max_pages":5}
```

Section mode reads indexes, canonicalizes discovered links to thread starters, collects their bodies and follows explicit reply pagination. `query`, if supplied, is a local title filter; it does not search bodies or the whole source. `since` and `thread_id` are not supported in this mode. Repeat `collect_next` to advance; queue, selected threads and page counts survive process restarts. There is no background collection.

Bounds are 1–500 threads, 1–50 index pages and 1–20 pages per thread, plus an overall ceiling of `max_index_pages + max_threads * max_thread_pages` attempted pages. Inspect checkpoint `section`: `threads_selected`, `thread_pages`, `failed_pages`, `hidden_posts_seen`, `deferred_count` and `deferred_urls` (a sample capped at 100 URLs, with reasons). A finished job means its configured bounds were exhausted, including recorded gaps. It does not establish full source coverage.

Individual gated/missing threads are recorded as gaps and collection continues. Root/index failures, transient connection/server errors, expired sessions and rate limits retain the current cursor for retry. Rate limits persist across restarts. A page whose visible posts exceed `max_items` retains its cursor rather than silently skipping them. Hidden content remains unavailable even when other posts on the page are readable.

A real stdio MCP check on 19 October 2026 discovered two HARD-TM threads from one index and saved 22 post records across three pages, with a server restart between discovery and thread ingestion. Four posts had hidden content; 35 URLs were deferred at the chosen bounds. Exact evidence replay succeeded. This validates ingestion, not the truth or relevance of the posts, lead time, or authenticated access. A second public run on Spear collected two root posts and one reply from two discovered access-market threads, also across a restart. Earlier Tor attempts timed out; the successful run used a 90-second request deadline. It recorded 19 deferred URLs and no failed pages. Raw receipts stay private.

## Operator-owned sessions

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

Cracked, Niflheim and HARD-TM use direct HTTPS. Spear uses the configured Tor SOCKS proxy and the existing bounded onion transport; it is never resolved through the direct HTTPS DNS path. Every Tor forum redirect and robots URL is restricted to that catalog host and the supported read routes. No forum credentials are needed for visible content. Only configured catalog hosts and read routes are eligible. Requests validate redirects, reject private or mixed DNS answers, connect to checked public IPs while retaining hostname TLS verification, honor robots.txt, and apply per-host pacing and response limits. Requests do not use environment proxies. Imported operator sessions are optional and exact-origin scoped; robots requests never carry account cookies. Registration, password login, challenge bypass, posting, account purchases and attachment retrieval are unsupported.

Cracked's configured host is `cracked.st`; Niflheim uses `niflheim.world` and `niflheim.top`; HARD-TM uses `hard-tm.com` and `hard-tm.su`. Observed redirects or matching branding establish routing context, not operator authenticity. Other forum catalog entries remain metadata-only until separately implemented and verified.

The MCP supplies original evidence and access diagnostics. The investigating agent decides whether a claim concerns the company, whether it is original or reposted, and whether it merits action. A subscription resale advertisement alone is not evidence that the platform was breached.

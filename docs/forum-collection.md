# Collect original public forum threads

Version 0.3 adds two tools for three catalog sources. They were tested through a real stdio MCP client on 19 October 2026; these are dated, bounded access checks.

| Source ID | Tested access | Material limitation |
| --- | --- | --- |
| `forum:hard-tm` | Original Lovable advertisement: one author, publication timestamp, post permalink and over 3,000 characters of visible terms | Purchase/contact links require registration; no account provenance or compromise established |
| `forum:niflheim` | AI advertisement with two posts; FAQ with 20 posts on each of two pages, authors and timestamps | Original Replit cookie-related thread remains unresolved; FAQ pagination validates access, not a company threat |
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

`list_forum_threads` inspects one public index page, extracting up to 100 thread links before applying the local title filter. Follow its `next_page_urls` or inspect another `collection_urls` section from `get_source`. An empty filtered index does not mean the forum has no matching posts. Automated search routes are not supported.

`read_forum_thread` reads one page of posts, labeled with its source ID. Each post includes its permalink, visible text, author handle, normalized publication time where available, the raw displayed date, quoted-post references, `hidden_content`, `truncated` and a persistent evidence reference. MyBB publication epochs are parsed from post-specific UI metadata without executing scripts. Source times and author identities remain unverified. A page's first visible reply is not presented as the thread's creation time.

`hidden_content=true` means some content or links require access the collector does not have. A readable post may still be partial. Images, attachment contents, linked documents and link-only destinations are not collected. Returned HTML is parsed as text; scripts are never executed.

## Resume multiple pages

```text
start_collection
{"source_id":"forum:niflheim","url":"https://niflheim.world/threads/frequently-asked-question.110/","mode":"history"}

collect_next
{"collection_id":"<returned id>","max_items":20,"max_pages":1}

get_collection
{"collection_id":"<returned id>"}
```

Jobs follow explicit next-page links within the same thread or index. Index jobs do not automatically enter every linked thread: the agent chooses relevant thread URLs and starts separate jobs. Each call reads at most five pages; post limits are 1–100 per page, with 16,000 characters per post and the configured total text bound. A post limit that omits visible posts produces `forum_page_incomplete` and retains the page cursor. Retry with a higher `max_items` or text bound. Hidden sections and truncated posts remain explicit even when a job exhausts its observed page cursor.

Evidence, source attempts and checkpoints survive restarts. `get_evidence` retrieves a saved revision with hashes and provenance; `refresh_evidence` rechecks the post permalink. A missing post is unavailable evidence, not proof of deletion. `search_evidence` searches only collected text with literal substring matching. Include relevant aliases/domains and inspect matches in context.

## Transport and coverage

Public forum reads use direct HTTPS, independently of Tor and Telegram. No forum credentials are needed for visible content. Only configured catalog hosts and read routes are eligible. Requests validate redirects, reject private or mixed DNS answers, connect to checked public IPs while retaining hostname TLS verification, honor robots.txt, and apply per-host pacing and response limits. Requests do not import cookies or use environment proxies. Authentication, registration, challenge bypass, posting, account purchases and attachment retrieval are unsupported.

Cracked's configured host is `cracked.st`; Niflheim uses `niflheim.world` and `niflheim.top`; HARD-TM uses `hard-tm.com` and `hard-tm.su`. Observed redirects or matching branding establish routing context, not operator authenticity. Other forum catalog entries remain metadata-only until separately implemented and verified.

The MCP supplies original evidence and access diagnostics. The investigating agent decides whether a claim concerns the company, whether it is original or reposted, and whether it merits action. A subscription resale advertisement alone is not evidence that the platform was breached.

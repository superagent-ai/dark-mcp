# Public Telegram preview collection

Version 0.21 adds `read_telegram_public_preview`. It retrieves visible text and metadata from Telegram's public HTML pages and post embeds without an account. This explicit path is separate from native MTProto reads and historical dataset imports. The official [post widget documentation](https://core.telegram.org/widgets/post) describes embedding public posts.

## Configuration and MCP use

```dotenv
DARK_MCP_TELEGRAM_WEB_ENABLED=true
DARK_MCP_TELEGRAM_WEB_CHANNELS=["permitted_public_channel"]
```

`DARK_MCP_TELEGRAM_WEB_CHANNELS` permits only public HTML collection. It does not extend the native account allowlist. `get_status` reports these configured handles without claiming successful access. Public handles in the existing `DARK_MCP_TELEGRAM_CHANNELS` list remain permitted for web reads too.

The existing `DARK_MCP_TELEGRAM_CATALOG_SOURCES=true` opt-in also permits public usernames already in the packaged catalog. Imported directory leads remain disabled by default. Separately setting `DARK_MCP_TELEGRAM_WEB_DIRECTORY_SOURCES=true` permits public handles from those local directories through this web collector only; it does not change native account permissions or turn reported status into an access claim. Native credentials, native connector enablement and global search enablement are not required. Numeric/private locators, invite links and arbitrary URLs are unsupported.

```text
read_telegram_public_preview(channel="permitted_public_channel", limit=8)
read_telegram_public_preview(channel="permitted_public_channel", before_id=RETURNED_NEXT_BEFORE_ID, limit=8)
read_telegram_public_preview(channel="permitted_public_channel", message_id=KNOWN_POST_ID)
search_evidence(query="company.example", kind="telegram_web")
search_evidence(query="company.example", kind="telegram_web", include_quotes=true)
search_references(host="company.example", kind="telegram_web")
get_corpus_coverage(kind="telegram_web")
```

Each call reads one page, with at most 100 retained posts and 200 examined widgets. Text is limited to 16,000 characters, with truncation explicit. `message_id` selects a public post embed and cannot be combined with `before_id`. `refresh_evidence` uses this direct public embed for saved web evidence; it never switches to an account API or interprets absence as deletion. Missing embeds, redirects and unavailable previews remain explicit errors.

Follow `next_before_id`. When the requested limit omits parsed posts, the cursor starts before the oldest retained post rather than skipping to the page's older link. Newer/out-of-range posts are excluded on history reads; ignored pagination returns an error rather than an endless repeated page. A missing older link does not establish complete history. No jobs, scheduler or future-post subscription are created.

## Evidence and gaps

Posts use kind `telegram_web` and source IDs such as `telegram-web:permitted_public_channel`. These handle-based identities never overwrite native numeric-channel evidence or archived records. Handles can be reassigned; public HTML does not authenticate a channel's numeric ID, operator or actor label.

Only an explicit `js-message_text` body supplies post text. Quoted reply previews stay in `quotes`, and reported forward headers in `forward`. A company mentioned only in a quoted preview is not inserted into the new post's body. Inline formatting preserves contiguous company names. Body anchors retain URL destinations and labels for reference search; linked destinations are never fetched.

A matching widget `data-post`, footer permalink and explicit-offset footer timestamp establish reported publication fields. Conflicting or timezone-free dates remain unresolved. Local recording times stay separate. Post changes create immutable revisions; refreshed page-view counters and document hashes remain observation provenance rather than synthetic post edits. `get_evidence` exposes the latest corresponding observation's request URL and document checksum in `provenance`, with its own `provenance_recorded_at` and `provenance_collected_at`, separate from the revision's first collection time.

Responses distinguish `visible_text_posts`, `unavailable_posts`, `posts_without_visible_text`, omitted/rejected widgets and unvisited pagination. Unsupported placeholders do not become body claims or replace stored content. Media-only records preserve available metadata without pretending images/files were read. Media, OCR, attachments, discussion history and private content are outside this collector.

## Request handling

Requests use only fixed `https://t.me` preview/embed routes and `robots.txt`. The collector executes no scripts, sends no account session, clears cookies between requests, downloads no media and follows no redirects. Robots rules are respected: 404 means absent rules; 401/403 mean denial. Robots results are cached for five minutes. Crawl delay/request-rate rules and a minimum one-second request interval apply.

A shared SQLite lease serializes public web reads across processes using the same data directory. HTTP 429 deadlines are saved before lease release; repeated blocked reads do not extend them. Web request limits and the native account cooldown are separate. Native failures never silently trigger a web fallback or reset an account deadline. Response types, size and duration are bounded.

## Live validation, 19 October 2026

Seven permitted handles were checked over MCP with native client construction forbidden. Six returned preview posts: ThreatMarket, BQTosint, GhostSecS, breaches, leak_db2 and vxunderground. BorwitaFreeLogs redirected and remains a failed read. Operator/actor attribution is unverified; readable previews do not establish independent sources or useful threats.

The bounded run retained 48 public post records from six source IDs, including 47 visible text bodies. Its newest reported posts were published on 19 October. One unsupported post was returned as unavailable and not stored as body evidence; one media-only record has no visible body. An older-page cursor, positive body search, exact text/hash replay, direct post refresh and restart all passed. No native client was constructed or called, and its saved cooldown deadline was unchanged. This proves current public-text retrieval for the samples, not source-wide coverage or pre-compromise warning lead time.

Follow-up checks used the explicit directory web opt-in. Three additional threat-actor directory leads were readable but stale (2023–2025). Of sixteen selected credential-exposure directory leads, six returned public text and ten redirected; none returned a post within 30 days. Across all 26 tested handles, 15 were readable and six had a reported post within 30 days, including two with posts on 19 October. The combined retained inventory is 76 public post records, 74 with visible text, from 15 handle-based source IDs. Two unsupported posts were excluded from body evidence. These findings demonstrate why an upstream ONLINE label is not a freshness or intelligence-quality claim.

`list_open_telegram_sources` reports `public_web_permission.permitted_by_configuration` separately from stored import metadata. It checks the current local opt-ins/allowlist without requesting the source. Permission to try a read is not successful access; native account permissions remain unchanged.

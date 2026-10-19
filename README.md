# dark-mcp

Standalone MCP server for proactive company exposure research on onion pages, Telegram channels and original forum threads.

Search existing indexes for company names, domains, and aliases; inspect matching onion pages through Tor; retrieve relevant Telegram posts with source links and timestamps. Investigate early warning signals such as company targeting, exposed information, alleged data sales, and threat activity before an incident is known. The same tools can be called repeatedly by a monitoring agent or scheduled workflow.

Probing here means investigating sources and exposure signals. Findings should retain their evidence and uncertainty: a source's claim does not establish that an intrusion occurred.

## Data sources

| Source | Access | Required setup |
| --- | --- | --- |
| Onion discovery | [Onion Search Engine API](https://onionsearchengine.com/api_documentation.php), over HTTPS | Provider API key |
| Onion page content | Direct HTTP(S) through your Tor SOCKS proxy | Running Tor client |
| Telegram channel posts | Telegram client API via Telethon | Your application credentials, local account login, permitted channels |
| Original forum threads | Cracked, Niflheim, HARD-TM, LeakForum, Spear, Exilio404 and DarkForums; public access by default | HTTPS for the first four; Tor for Spear, Exilio404 and DarkForums; optional operator-owned session |
| Local onion search | SQLite FTS index populated by fetches and bounded crawls | No external search key |

**The MCP collects original source content into its own local corpus.** The optional Onion Search Engine API helps discover URLs; it is not the evidence corpus. Local search only covers content this installation has collected, and discovery-provider coverage and freshness are not independently verified.

### Telegram source research

The [18 October 2026 source catalog](sources/telegram-research-2026-10-18.json) records live checks of 108 public handles, including access failures, stale feeds, evidence links and source assessments. Only 18 had any visible post within 30 days; recent activity can still be a promotion or migration notice.

[telegram-starter.json](sources/telegram-starter.json) contains seven selected public handles for a local `DARK_MCP_TELEGRAM_CHANNELS` allowlist. Four cover underground claims or forum listings; three provide researcher/community context. The catalog separates these roles, conditional guest previews and marketing-only watchlists. It does not authenticate operators or alleged breaches. Review source IDs, current content and applicable access permissions before using a dated snapshot.

### Onion source research

The [18 October 2026 onion catalog](sources/onion-research-2026-10-18.json) records tests of 30 addresses across 27 source families. Twelve responded, but only six exposed public indexes; the others returned login gates, challenges, an empty document or a catalog shell. Follow-up checks confirmed deeper public reads on two forums. Timeouts are collection failures at the time of testing, not proof of permanent downtime.

[onion-starter.json](sources/onion-starter.json) contains five public seed pages across PwnForums, Spear and Exilio404. Fetch selected URLs with `fetch_onion` to populate the local index. PwnForums was limited to its public homepage; tested deeper sections returned HTTP 403. [onion-disclosure-context.json](sources/onion-disclosure-context.json) separately lists a readable ransomware victim-claim page. These snapshots establish limited access, not complete forum coverage, authenticated identities or evidence that a particular incident could have been detected early.

### Comparable API and forum research

The [19 October vendor research](docs/vendor-source-research.md) compares eight providers and 23 forum families, including Flare, DarkOwl, KELA, SOCRadar, Intel 471, Searchlight Cyber, Intelligence X and Bitsight/Cybersixgill. The [evidence matrix](sources/vendor-source-research-2026-10-19.json) distinguishes explicit monitoring claims, dated collection examples and research mentions. It also records disruption reports and uncertain identities.

Use `list_sources(kind="forum")` to inspect 18 forum records. Four now have public original-thread collectors: **Cracked, Niflheim, HARD-TM and LeakForum**. The remaining 14, including DamageLib, BHF, Duty Free, Lolzteam and HydraForums, are metadata-only leads. Vendor research informs our own source-access priorities; competitor intelligence APIs are not runtime data sources. Neither a vendor mention nor a historical collection chart proves present-day API access. `get_status` separates collection methods from source counts.

### Original forum access

The [19 October access checks](sources/forum-access-2026-10-19.json) document actual MCP reads on the four HTTPS forums. `list_forum_threads` discovers links on a public section page; `read_forum_thread` returns visible post bodies, author handles, publication timestamps, post permalinks, hidden-content flags and explicit pagination. Posts persist individually as evidence and can be searched locally with `search_evidence(kind="forum")`. Structured Spear collection over Tor includes: access-market and buyer indexes, original bodies, authors, root/reply markers, raw source dates, referenced URLs and resumable pagination. Version 0.12 includes bounded section-to-thread ingestion, operator-provisioned forum sessions and local thread reconstruction with observation-time cutoffs. See the [early-signal collection guide](docs/early-signal-collection.md) for verified access bounds and priorities for our own collection.

Exilio404 now has a Question2Answer collector for its configured hacking and leak categories. Questions, answers and returned HTML comments retain separate identities, parent links, authors and explicit dates. A live MCP check collected nine posts from two discussions and resumed section ingestion after restart. The samples included an access-sharing inquiry explicitly framed as studying; attack intent and early-warning lead time were not established. General marketplace categories are outside this collector’s configured sections.

DarkForums became readable in the [19 October priority recheck](sources/priority-forum-access-2026-10-19.json), after a timeout in the original catalog. It now supports structured access-market and buyer threads over Tor. A live MCP check collected 13 posts across two discussions, including a later reply stating the sampled access-sale offer was sold. Author handles, explicit post numbers, raw dates and original replies are preserved; the claim and transaction remain unverified. Exploit and the tested XSS route still returned challenges, and ReHub's connection failed.

LeakForum now exposes buyer requests and service discussions through the same tools. A real MCP check collected 35 original post items across four threads, with authors, explicit source timestamps and numbers. Section discovery and two pages of a thread resumed across restarts; unvisited pagination remains explicit. A Google GCP/AI Studio account request is observable demand, not verified account theft or an impending provider breach. See the [LeakForum guide](docs/forum-collection.md#leakforum-buyer-requests-and-services).

Recognized MyBB and XenForo index rows now preserve creator handles, source creation dates, last-activity dates and explicit pin status. Optional `exclude_pinned=true` keeps unknown status eligible. The parser excludes unrelated sidebar links when it recognizes the thread rows, reports fallback scope and row-cap omissions, and preserves complete index evidence before filtering. A last-activity date does not turn an old advertisement into a newly created claim.

A current HARD-TM Lovable advertisement matching earlier Telegram repost headlines was collected with its author, timestamp and visible terms. Matching titles and handles do not prove it is the exact post behind an older relay. Its outbound links require registration. Niflheim exposed readable posts and multiple pages of replies; other post bodies can require login. Cracked exposed visible discussion with registration-only content marked as hidden. These are bounded access checks, not verified breach claims or complete site coverage. See the [forum collection guide](docs/forum-collection.md) for concrete calls and limitations.

### Local operator source configuration

Use `source-import` to configure an operator-provided MyBB/XenForo source locally, then restart the server and use its `operator:forum:<slug>` ID with the normal collection tools. Addresses and account sessions do not need to be published in this repository. Configuration is bounded, has separate provenance and does not verify identity or access. The packaged catalog remains unchanged; `get_status` separates its records from local configurations. See the [operator source guide](docs/operator-sources.md) for JSON input, session provisioning, removal and identity retention.

### Browser-read forum content

Version 0.13 adds local `forum-page-import` for UTF-8 HTML saved from an operator's accessible forum page. It uses the configured parser to preserve posts, replies, dates, references, hidden-content flags and pagination in the same evidence store. Search, saved thread context and corpus inventory distinguish these unverified operator submissions from native network reads. Imports do not run scripts, make source requests, verify membership or establish an earlier capture time. They do not solve challenges or provide access to unsupported/gated sources. See the [capture guide](docs/forum-captures.md).

### Full source registry for agents

The server bundles **173 research records**: 30 onion addresses across 27 source families, 108 public Telegram handles, 17 invite-derived leads and 18 forum records (four HTTPS collectors and 14 metadata-only leads; the onion records also include structured Spear, Exilio404 and DarkForums collectors). Temporary previews are included in those invite records. These are candidates, including stale, gated, unavailable and low-relevance sources; the count is not a count of working or independent feeds. Eight ordinary-web mirror checks are included as research context in `get_source`; they do not have a collector.

`list_sources` exposes the complete registry with stable source IDs, filtering and pagination. `get_source` returns assessments, discovery references, evidence links, historical access checks and known forum section URLs. The catalog is packaged with the server, so an installed wheel or Docker image does not depend on this checkout's `sources/` directory.

`collect_source` reads one onion/forum page or a bounded page of Telegram posts. Spear, Exilio404 and DarkForums are onion-kind sources with the `forum_page` collection method; they use the same forum tools through Tor. For onion and configured forum sources, provide a known section or discovered thread URL to explore deeper. For Telegram, use `query`, `since` and `next_before_id` to search and paginate. Metadata-only invite and forum records return `source_access_required`; numeric IDs can only be read if the account can resolve and access them. Collection does not join channels, renew invite previews or solve login/browser challenges. Gates and hidden forum content are flagged; other returned text remains unverified.

To permit Telegram reads across all locatable catalog records, enable the following alongside your authenticated Telegram configuration:

```dotenv
DARK_MCP_TELEGRAM_CATALOG_SOURCES=true
```

This adds known catalog locators to the explicit `DARK_MCP_TELEGRAM_CHANNELS` allowlist; arbitrary new channels still require configuration. It does not make inaccessible sources readable. This switch defaults to `false` for fresh installations.

An investigating agent should:

1. Call `get_status`, then page through `list_sources`. Use source assessments to prioritize relevant primary sources and keep mirrors/reposts in the same evidence cluster.
2. Inspect `get_source` before collection. Historical access states and research priorities are leads, not current availability or authenticity guarantees.
3. Use `collect_source` with company names, domains and aliases for Telegram; inspect onion pages and follow relevant same-host section/thread links. `search_onion_cache` searches only pages actually collected. `search_onion` can discover additional candidates outside the registry.
4. Follow returned pagination until the chosen investigation bounds are met. Stop on rate limits and honor `retry_after_seconds`; record inaccessible sources as coverage gaps.
5. Derive signals from original evidence: company targeting, alleged access/data offers, credential exposure claims and supplier exposure. Cite the original URL and post ID, observed entity ID, publication time if available, collection time, matching identifiers and uncertainty. Do not equate an advertisement or allegation with a verified compromise.
6. Separate duplicate/reposted claims, original posts and later replies. Report sources checked, collection bounds, access failures and unresolved attribution alongside any findings. No matches means no matches within that collected coverage.

The MCP supplies discovery, persistent evidence and resumable collection; the calling agent derives signals and controls further collection. There is no built-in schedule or alert service. Telegram, onion and forum content reads persist locally as evidence with revision history. See the [investigation workflow](docs/investigation-workflow.md).

Maintainers can update the dated research snapshots and run `uv run python scripts/build_source_catalog.py` to regenerate the distributable registry. Tests check that the packaged registry matches the research files.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/), or Docker. Tor is required for onion content reads; public forum reads work over direct HTTPS.

```sh
git clone https://github.com/superagent-ai/dark-mcp.git
cd dark-mcp
uv sync --locked
cp .env.example .env
```

Set `DARK_MCP_ONION_SEARCH_API_KEY` in `.env` to enable discovery. Get your own key through the provider's [account flow](https://onionsearchengine.com/api_plans.php). This project does not create accounts or subscribe to paid plans for you.

Start Tor for direct page retrieval:

```sh
docker compose up -d --build tor
docker compose logs -f tor
```

Wait for `Bootstrapped 100%`. The container health check only checks the SOCKS listener; it does not establish bootstrap completion. An existing local Tor process on port 9050 also works. If that port is in use, use your existing Tor process or change the Compose host port and `DARK_MCP_TOR_PROXY` together.

```sh
uv run dark-mcp serve
```

The default transport is stdio. Do not type into this process: connect an MCP client as shown below.

## Connect an MCP client

Use the absolute path to your checkout; the working directory is where `.env` is read.

```json
{
  "mcpServers": {
    "dark-mcp": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/dark-mcp", "run", "dark-mcp", "serve"],
      "env": {"PYTHONPATH": "/absolute/path/dark-mcp/src"}
    }
  }
}
```

The explicit source path also supports editable checkouts where the Python installation does not load the generated `.pth` file. Installed wheels do not need it.

For a client supporting local Streamable HTTP:

```sh
uv run dark-mcp serve --transport streamable-http --port 8000
```

Connect to `http://127.0.0.1:8000/mcp`. HTTP binds to loopback only and has no application authentication. Keep it local; remote, multi-user deployment requires an authenticated gateway and isolated credentials/data per tenant.

## Tools

| Tool | Purpose |
| --- | --- |
| `get_status()` | Configuration and local index count; does not test connectivity |
| `start_collection(source_id, mode="history", ..., max_threads=100, max_index_pages=3, max_thread_pages=3, exclude_pinned=false)` | Create a resumable history job, Telegram new-post cursor, or bounded forum `section` ingestion; no background process |
| `collect_next(collection_id, max_items=20, max_pages=1)` | Read up to five pages; retain transient/source-level failures for retry; section jobs record individual inaccessible threads as gaps |
| `get_collection(collection_id)` / `list_collections(offset=0, limit=20)` | Inspect or resume jobs across restarts |
| `search_evidence(query, ...)` | Literal search of saved revisions; label title/body matches and local revision recording times; filter source, kind and dates |
| `get_evidence(evidence_id, revision_id=null)` / `get_evidence_history(evidence_id, ...)` | Retrieve exact snapshots, provenance, hashes and revision history |
| `get_collected_thread(evidence_id, as_of=null, offset=0, limit=20)` | Reconstruct saved forum posts/replies without network access; optional cutoff uses local recording time |
| `list_evidence_changes(after_seq=0, ...)` | Cursor over new, edited, deleted, restored and unavailable observations |
| `get_exact_duplicates(evidence_id, limit=20)` | Other latest snapshots with identical text; does not imply independent corroboration |
| `refresh_evidence(evidence_id)` | Directly recheck a stored post/page; retain older evidence |
| `get_corpus_coverage(source_id=null, kind=null, offset=0, limit=20)` | Local item/revision inventory, explicit thread depth and gaps; no network calls |
| `get_source_health(source_id=null, ...)` | Recorded attempts, bounds, identities, last successful read/content and errors |
| `save_company_profile(profile_id, name, identifiers, context=null, references=null)` / `get_company_profile(profile_id, revision=null)` | Version caller-provided company context; not independently verified |
| `add_evidence_relation(from_id, to_id, relation, supporting_ids, note="")` / `get_evidence_relations(evidence_id)` | Save explicit, evidence-backed agent assertions; not automatic factual attribution |
| `list_sources(kind=null, access_state=null, query=null, offset=0, limit=20)` | Full candidate inventory; paginate with `next_offset` |
| `get_source(source_id)` | Provenance, evidence links, historical checks and known section URLs |
| `collect_source(source_id, url=null, query=null, limit=20, before_id=0, since=null)` | Read one onion/forum page or page of Telegram posts; report current result and historical access state |
| `get_forum_access_status(source_id, origin=null)` | Sanitized local session metadata; does not test live authentication |
| `list_forum_threads(source_id, url=null, query=null, limit=20, exclude_pinned=false)` | Discover thread rows with source creation/activity dates and pin status; optional local title and known-pin filters |
| `read_forum_thread(source_id, url, limit=20)` | Read visible original posts/replies with authors, timestamps, permalinks, hidden-content flags and next-page links |
| `search_onion(query, page=1)` | Discover onion mentions using the search provider; returns `next_page` |
| `fetch_onion(url)` | Fetch a v3 onion page over Tor, extract text/links, update local index |
| `crawl_onion(seeds, max_pages=5)` | Collect at most 10 pages from seed onions and same-host links |
| `search_onion_cache(query, limit=10, since=null)` | Full text search of collected pages; `since` refers to collection time |
| `search_telegram_channels(query, limit=10)` | Discover channels; requires global search to be enabled |
| `read_telegram_channel(channel, limit=20, before_id=0)` | Read an allowed broadcast channel |
| `read_telegram_thread(channel, message_id, limit=20, before_id=0)` | Read the root and one page of accessible discussion replies, using the actual peer IDs for citations |
| `sync_telegram_changes(channel, limit=100)` | Establish/resume a Telegram update cursor for edits and explicit deletion events; mark server-reported gaps |
| `search_telegram_messages(channel, query, limit=20, before_id=0, since=null)` | Search an allowed channel; `since` refers to publication time |
| `search_telegram_posts(query, limit=20, cursor=null)` | Public post search using free quota only; requires global search to be enabled |

Dates must include a timezone, for example `2026-10-18T00:00:00Z`. Telegram message limits are 1–100; channel discovery is 1–50. Use the returned `next_before_id`, `next_cursor`, or `next_page` for pagination. Search API page size is provider controlled.

Tools return structured `ok`, `results`/`page`, coverage information, and errors with stable codes. Rate limit errors include a retry delay when Telegram provides one. Missing configuration and source failures are explicit errors, never empty results disguised as successful searches.

## Example proactive company investigation

For a fictional company called Acme with domain `acme.example`:

1. Call `search_onion` separately for `"Acme"`, `acme.example`, and relevant brand aliases. These are search strings; matching behavior is controlled by the provider.
2. Inspect relevant results with `fetch_onion`. Record the exact excerpt, URL, and collection time. The search API does not guarantee publication dates; an old indexed page is not necessarily a new disclosure.
3. Search selected Telegram channels for the same identifiers using `search_telegram_messages`, with the start of the research window in `since`. Where separately permitted, use public post search to discover additional sources.
4. Assess whether the text actually refers to the company. A mention or an alleged leak is not a confirmed compromise. Cite the evidence and retain uncertainty.
5. For repeated monitoring, the calling workflow should persist seen source URLs, Telegram channel/message IDs, and content hashes; compare each run and notify on new or changed evidence. The server supplies data access, not a built-in scheduler or alert delivery service.

Collection searches text only. It does not OCR images, download attachments, bypass authentication/CAPTCHAs, join channels, post messages, or verify the authenticity of leaked data.

## Telegram setup

Telegram's [API terms](https://core.telegram.org/api/terms) and [content licensing terms](https://telegram.org/tos/content-licensing) restrict using platform data for AI, including deployment. Public accessibility and an authenticated session do not grant those rights. Enable this connector only where an applicable permission arrangement or consent exception covers your intended use. The configuration switches are operational controls, not a grant of permission.

1. Register your own Telegram application at [my.telegram.org](https://my.telegram.org/apps).
2. Set `DARK_MCP_TELEGRAM_API_ID` and `DARK_MCP_TELEGRAM_API_HASH` locally in `.env`.
3. Run the login command yourself in a terminal:

```sh
uv run dark-mcp telegram-login
```

4. Set the permitted channels and enable the connector:

```dotenv
DARK_MCP_TELEGRAM_ENABLED=true
DARK_MCP_TELEGRAM_CHANNELS=["permitted_channel","-1001234567890"]
```

Global channel discovery and public post search are disabled separately. Where your permission arrangement covers these searches, set `DARK_MCP_TELEGRAM_GLOBAL_SEARCH=true`. Public post search may require Premium and available free quota. The server never authorizes spending Telegram Stars.

Private channels require the account to already have access. Read/search tools accept broadcast channels only. Credentials and the account session stay server-side; login is never exposed as an MCP tool. Treat the session file as an account credential. Revoke a session through Telegram's Devices settings if compromised.

## Optional forum sessions

Public reads need no account. If you already have membership and read permissions, provision a session locally using a JSON cookie export containing only that exact forum host. No passwords, cookie values or cookie files are accepted through MCP tools.

```sh
uv run dark-mcp forum-session-import forum:hard-tm /private/path/forum-cookies.json --ttl-hours 24
uv run dark-mcp forum-session-status forum:hard-tm
uv run dark-mcp forum-session-remove forum:hard-tm
```

See the [session setup and validation guide](docs/forum-collection.md#operator-owned-sessions) for the export format, mirror origins and limits. Importing a session reports `configured`, with `authentication_verified=false`; a successful original-thread read is required to establish its actual access. No live authenticated forum reads have been verified yet. The operator obtains accounts and signs in separately; the MCP does not register accounts or solve challenges.

## Docker-only usage

```sh
docker compose build
docker compose up -d tor
# Interactive account login, if using Telegram:
docker compose run --rm mcp telegram-login
# MCP stdio, suitable for an MCP client's command/args:
docker compose run --rm --no-deps -T mcp serve
```

The MCP client must run that command in this checkout. Persistent container data and sessions live in the `mcp-data` volume; local `uv` runs use `DARK_MCP_DATA_DIR` instead. Do not run multiple processes against the same Telegram session. Tor's SOCKS port is published only to the host loopback address.

## Local onion corpus

For sources you already know, populate the optional local corpus without a discovery key:

```sh
uv run dark-mcp crawl 'http://YOUR_VALID_V3_ADDRESS.onion/' --max-pages 5
uv run dark-mcp search 'company name'
```

Pages are fetched through Tor, with v3 address checksums checked, redirects validated, robots.txt respected, per-host pacing, request deadlines, and response limits. Crawling follows same-host links; cross-host redirects are still restricted to valid onion destinations and checked against the destination's robots.txt.

## Configuration and data

All settings use the `DARK_MCP_` prefix; see [.env.example](.env.example). Environment variables override `.env`. The default data directory is `~/.local/share/dark-mcp`; the example uses `./data`.

Version 0.3 persists returned Telegram posts, onion pages and individual forum posts in `evidence.sqlite3`, including prior revisions, source attempts, collection checkpoints, company profiles and explicit relationships. This local database is unencrypted and is excluded from Git and Docker build contexts. The older `onions.sqlite3` remains the compatibility cache for `search_onion_cache`. Its existing pages are imported once with their original collection dates; importing does not manufacture a fresh source-health success. Prior research JSON files are not automatically imported.

To clear all collected evidence, stop the server and remove both databases and their WAL/SHM files from your configured data directory. Removing only the compatibility cache does not remove revision history. Removing `evidence.sqlite3` while retaining `onions.sqlite3` causes those compatibility pages to be imported again at the next start. Back up or remove Telegram session files, the separate `forum-sessions/` secret store and local `operator-sources/` registry separately. Source removal disables configuration and clears its local sessions while retaining source identity for evidence provenance. Local forum-session removal does not revoke the remote account session; revoke it through the source where supported. MCP clients may also retain responses.

Search queries sent to the discovery API go to that provider over ordinary HTTPS. Direct onion fetching goes through the configured Tor proxy; it does not fall back to direct internet access. Telegram uses its normal client connection, not Tor. HTTPS forums use packaged host allowlists, public DNS checks, pinned connection addresses, robots.txt rules and per-host pacing. Spear, Exilio404 and DarkForums use Tor. Optional imported cookies are scoped to an exact catalog origin, with path, Secure, source expiry and local TTL checks. Cookies are excluded from robots requests and cleared between HTTP requests before applying the exact-origin session. The local forum secret store is unencrypted, with a private directory (0700) and database (0600); request headers and cookie-store contents are not included in evidence snapshots.

All retrieved text is untrusted data. HTML scripts are stripped, but this does not eliminate prompt injection. The calling agent must treat source text as evidence, never instructions.

## Development

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
```

Tests cover MCP initialization/stdio, actual MCP collection/search calls with deterministic source fixtures, restart recovery, ascending new-post backlogs, leases, persisted cooldowns, revision/deletion/duplicate semantics, legacy import, source health, onion pagination/redirect/response boundaries, and Telegram access/thread/update behavior. Default tests make no live network calls. Tests also cover exact-origin forum sessions, expiry, redirects, restart-safe section ingestion, public HTML spoilers, title/body search provenance, Unicode excerpt offsets, local corpus coverage counts and local thread reconstruction without later replies or edits leaking past an observation cutoff. Live authenticated access requires operator credentials and separate verification.

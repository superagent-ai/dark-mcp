# Broad and deep research with dark-mcp

The agent conducts the investigation; dark-mcp supplies bounded collection and retrieval tools backed by AIL.
Search covers retained, indexed content. It is not a global onion search engine, forum membership service or
Telegram directory. Installing the runtime does not provide a historical corpus or authenticated source access.

## 1. Define the investigation and budget

Record the question, relevant time window and confirmed entity identifiers: company name, domains, product
names, former names and operator-supplied assets. Keep ambiguous names separate until verified. Distinguish
interest in a cloud provider's customer accounts from compromise of the provider itself.

For an initial pass, a reasonable adjustable budget is 30 queries, 10 new page captures at depth 0 and 30 minutes
of observation. Reserve part for pivots and original-thread retrieval. State the budget and any changes in the
report. Respect the configured collection concurrency and source restrictions; do not create recurring tasks.
If collection outlasts the observation budget, report unresolved tasks and reconcile them before resubmission.

## 2. Establish what this installation can see

Call `ail_status` with `{}`. Check authentication, index health, document counts, workers and Lacus connectivity.
Use actual index names returned by the backend. `onion` and `web` contain collected page bodies; `title` can
duplicate those sources as derived documents. Native forum/chat indexes only represent content actually ingested.
Do not equate total index documents with distinct pages, posts, sources or independently corroborating accounts.

Call `ail_chat_instances` with `{"page":1}` to inventory native chat ingestion if relevant. With returned IDs, use
`ail_chat_channels` and `ail_chat_messages`, paging as needed. An empty instance list means no native chat
coverage here, not that Telegram has no relevant messages. A captured `t.me/s/...` page belongs to web coverage.

Record a baseline of queries and current retained objects before adding pages. This distinguishes pre-existing
corpus hits from content collected during this investigation.

## 3. Build and expand a query plan

Start with separate name, domain and alias queries. Search page bodies first, for example:

```json
{"query":"example.com","scopes":["onion","web"],"page":1,"sort":"recent"}
```

This is an `ail_search` argument object. Use available scopes only; omitting scopes searches available indexes.
Query syntax and matching are native AIL/Meilisearch behavior; do not assume Boolean operators, quotes or domain
punctuation guarantee exact matching. Inspect originals and page through results within the query budget.

Expand along the research question, using individual terms and combinations as exploratory queries:

| Research direction | Terms and context to investigate |
| --- | --- |
| Procurement and preparation | Requests for accounts, access, API keys, billing history, insiders or targeted services |
| Exposure claims | Access offers, credential claims, source-code claims, data-sale listings and claimed affected domains |
| Impersonation | Brand/domain references in phishing, fake applications or malicious installer claims |
| Product and supplier context | Vulnerability discussions, dependent services and tools mentioned alongside the entity |
| Historical context | Prior claims, repost chains and changes in the same original discussion |

Generic intent queries can reveal relevant original threads even when a brand query finds nothing. They also
generate unrelated material: preserve the distinction between a useful research lead and a target-specific warning.
Pivot on substantiated entities, original thread links and public indicators; stop when the budget is reached or
new pivots no longer add relevant evidence. Explain the stopping condition.

## 4. Discover original sources and collect selectively

Use approved external discovery tools, operator-provided source URLs, retained listings and links inside relevant
posts to locate original threads or message permalinks. dark-mcp has no automatic source catalog or global channel
discovery tool. If separate discovery tools are unavailable, state that limitation and use known accessible seeds.

Move from relevant forum listings to original threads, then relevant reply pages. Prefer original posts over
aggregator summaries. Check pagination and visible dates; a single listing or preview is a sample, not complete
forum/channel coverage. Confirm source permissions before collection. Record login walls, hidden content and
challenges as access outcomes. Continue other accessible sources rather than treating these pages as datasets.

Queue an explicit page with `ail_crawl_url`:

```json
{"url":"https://example.com/public-thread","depth":0,"javascript":false}
```

Replace the example URL with an authorized source. Depth 0 requests that page, with ordinary browser resources;
depth 1–2 expands linked collection and should consume an explicit budget. Enable JavaScript for normal rendering
when needed, not to solve access challenges. Avoid blind crawls of whole forums and duplicate submissions.

## 5. Verify asynchronous captures before interpreting them

1. Record the returned task UUID and the hostname's existing retained IDs.
2. Call `ail_crawl_status` with `{"task_id":"RETURNED-UUID"}` while work is queued/running. Queue wait, capture
   retries and processing can exceed a short observation timer. Missing tasks, including 404 after removal,
   establish neither success nor failure.
3. Call `ail_domain_captures` with `{"domain":"example.com"}`. It returns the latest 20 root capture outcomes
   for the hostname, not a task-specific receipt. Save relevant IDs before later captures displace them.
4. Call `ail_get_object` with `{"global_id":"RETURNED-GLOBAL-ID"}`. Match the actual retained URL, collection
   time and readable content to the intended page. Evaluate ordinary redirects, including thread fragment changes.
   Do not assign the first new object on a shared hostname to a particular task.
5. Classify the body: original thread, listing, preview, landing page, login/challenge, hidden-content claim or
   error. Readable HTML can coexist with missing media, screenshot/storage failures or incomplete replies.

Our expanded validation initially encountered a late same-host capture outside a polling window; reconciling
actual native URLs prevented miscounting a listing as another requested page. Do not restart workers or enqueue
copies solely to make an observation script finish. Preserve unresolved work as a report limitation.

After retention, allow bounded time for indexing and inspect `ail_status`. Test a distinctive phrase actually
visible in a fresh original page, retrieve its result and compare its object ID/content. A random negative query
is a useful additional control, but does not prove recall. If a page is retained but unsearchable, report an indexing
gap; zero search hits cannot clear the source. `ail_import_text` does not repair this: ordinary operator submissions
are stored but not included in current upstream full-text indexes, and are never independent observations.

## 6. Qualify every relevant hit against original content

Retrieve original objects for candidate hits, not just snippets. Validate whole entity names and domain boundaries
in visible post text and meaningful context. Exclude navigation, CSS, link parameters, usernames, placeholder
examples and unrelated similarly named entities. Keyword typo/prefix tolerance produced `Exa` hits on words such
as “exact” and `Replit` hits around reply-related text in live tests; all were rejected after inspection.

Interpret obfuscation only when clear, such as an unambiguous domain written with `(.)`. Leave undefined acronyms
undefined. A request for “Kraken(.)com VM” did not establish what “VM” meant or prove any access.

Separate these outcomes:

- **Procurement intent:** someone asks to obtain accounts or access; it does not prove possession or theft.
- **Exposure/access claim:** a source asserts it has data/access; assess specificity and corroboration separately.
- **Capability advertisement:** seller marketing; advertised capabilities and target implications are unverified.
- **Public security discussion:** relevant context, possibly already widely known or technically uncorroborated.
- **Repost:** investigate its original origin and lag; repeated messages are not independent confirmations.
- **Gated or irrelevant content:** record the access limit or rejection reason; do not count it as verified exposure.

For each candidate, assess target identity/role, specificity, freshness, directness and independent corroboration.
Use qualitative confidence with reasons; do not invent calibrated probabilities. A channel name alone does not
authenticate an actor, and a post proves the existence of a claim rather than its truth.

Use primary public disclosures or independent original sources to corroborate when available. Do not test exposed
credentials, contact actors, purchase access, join restricted groups, download stolen datasets/payloads or probe
company infrastructure as part of this passive workflow. Redact any encountered secrets and unnecessary personal
data; retain detailed evidence privately under operator controls.

## 7. Preserve chronology and provenance

For each useful source, record:

| Field | Meaning |
| --- | --- |
| Original URL/permalink and AIL global ID | How to revisit the retained original and its claimed origin |
| Collection timestamp | When this installation collected the page, in UTC; add local time if useful |
| Source publication timestamp | What the source shows, with timezone/uncertainty |
| Claimed event date | An assertion in the post, distinct from publication and observation |
| Independent public disclosure date | Verified external reference, if established |
| Query, scope, page and capture bounds | What was searched/collected and what was omitted |
| Source excerpt/paraphrase and interpretation | Separate observation from inference and confidence |

Preserve any native hashes as identifiers of the retained representation; do not claim an immutable revision or
exact HTTP-byte snapshot without verification. Save a private tool journal and IDs; do not commit raw receipts,
source HTML, credentials or sessions. Follow-ups can change live pages, while domain history is bounded.

An old post collected today is a retrospective observation. To claim early detection, establish that the actual
local observation preceded the relevant independently verified disclosure; detection before compromise further
requires reliable compromise timing. Source dates alone establish neither. In one preview sample, 13 reposts
lagged the channel's own claimed event dates by 37–67 days (median 48); those dates were not independent incident
ground truth. Measure freshness rather than assuming underground content is early.

## 8. Deliver a useful report

Lead with what was established, which leads deserve follow-up and what remains unknown. Include:

1. Scope, time window, budgets, actual queries/captures and backend/source capabilities.
2. Findings with original URL, global ID, relevant timestamps, observed context, interpretation, confidence and
   a practical defensive follow-up. Keep access claims separate from verified compromise.
3. Rejected matches and why, especially ambiguous names and fuzzy search artifacts.
4. Coverage by distinct original sources and content type; separate title documents, forwarded/reposted messages,
   listings, gated pages and landing pages from readable originals.
5. Collection/indexing failures, unfinished tasks, unavailable accounts, missing native chat/media history and
   source-specific gaps. State the stopping condition and the next access or collection step that would matter.

Phrase negative results as “no substantiated matching signal in the collected sources within these bounds.”
Do not conclude “no threat,” imply full dark-web coverage or assert vendor parity from a small validation corpus.
The fixed scripts under `scripts/` demonstrate dated collection paths; they do not replace this research process.

See [initial validation](validation-2026-10-20.md) and
[expanded validation](expanded-validation-2026-10-20.md) for measured results and limitations.

# Collect useful early-stage evidence

Version 0.4 improves source collection. The calling agent assesses relevance and uncertainty; scheduling, alerts and response remain outside the MCP.

## Direct original threads over Tor

Spear (`onion:91e4e785a3af`) now supports the same structured forum tools as the HTTPS sources. Use `get_source` to inspect its actual transport, known section URLs and dated access checks. It remains an onion-kind catalog record; filter on `collection_method="forum_page"` in returned records rather than assuming every forum collector has kind `forum`.

```text
list_forum_threads
{"source_id":"onion:91e4e785a3af","url":"http://spear4h2potiyk43oilufhuq2jqbioyaizcs7xttlyw75s5ykwxpxlid.onion/Forum-Access-Market","limit":100}

read_forum_thread
{"source_id":"onion:91e4e785a3af","url":"<thread URL returned above>","limit":20}
```

The catalog also supplies buyer, seller and database-discussion section URLs. Index filters apply only to titles on that page. Follow explicit pagination for more results. Index jobs do not automatically enter every thread; the investigator selects relevant links.

Each collected post retains its visible body, author, permalink, root/reply marker when explicitly available, dates, quoted references, hidden-content status, body links and persistent evidence ID. Actual outbound URLs are retained even when the displayed link is shortened. References are metadata, not verified source identities, and are not automatically followed. A public forum repost of an access offer does not establish access to the marketplace it credits.

For uncertain dates, `published_at` remains null. `published_at_raw` preserves the displayed date/time; `published_at_hint` may preserve an absolute date in a tooltip. `timestamp_basis="unresolved_source_text"` explicitly reports the lack of a usable offset or source Unix epoch. Never turn a displayed September date collected in October into proof that our system detected an incident in September.

Use `start_collection` / `collect_next` to resume an index or thread across restarts. Post-limit omissions retain the current page cursor. Use `get_evidence`, `refresh_evidence`, `get_source_health` and `list_evidence_changes` as for other collectors. Tor reads honor robots, pacing, text/response bounds and redirect validation. Credentials, registration, challenge solving and downloads are not implemented.

### Live validation on 19 October 2026

A real stdio MCP client retrieved two relevant indexes and three original threads: an anonymous SaaS access advertisement, a request for LLM/cloud keys, and a webshell access advertisement with a public reply. Four post records retained authors, root/reply identity and source dates. A paginated access-market job continued after a server restart; a stored post was replayed and directly refreshed. Raw receipts remain private outside version control.

These are useful *types of evidence*: alleged access sales and advertised credential demand. Neither access validity, a named victim match, upcoming exploitation nor pre-compromise detection was established. Some posts explicitly credit another marketplace; its gated listing was not collected. Telegram was not contacted during its existing account cooldown.

## Optional licensed source: Flare

The adapter implements Flare's documented global search and event retrieval. It requires your own entitled API key; no subscription, trial or account is created. The implementation has mocked HTTP contract tests. No licensed live collection is claimed without a configured key and a successful check.

```dotenv
DARK_MCP_FLARE_API_KEY=your-key-in-a-local-secret-store
# Optional; otherwise the API key's default tenant is used.
DARK_MCP_FLARE_TENANT_ID=1234
```

The API key needs the relevant search/read permissions. Flare documents `Read Tenant` for global search and `Read All` for event retrieval; account quotas and source entitlements still apply. The key is exchanged for an in-memory token, with the tenant selected at token generation. HTTP requests use the fixed Flare API origin and never follow redirects. Errors do not echo provider response bodies or credentials.

```text
search_flare_events
{"query":"\"superagent.sh\"","types":["forum_post","forum_topic","chat_message","listing"],"since":"2026-10-01T00:00:00Z","limit":20}

read_flare_event
{"uid":"<metadata.uid from a returned hit>"}
```

Global search consumes provider quota. It returns one page and a `next_cursor`. Resume with the same query, types, since, limit and tenant, passing the cursor unchanged. Even an empty page may have a continuation. The date filter applies to the provider's `estimated_created_at`, not to independently verified source publication or our local first-seen time.

Search hits contain snippets and have separate evidence identities from event bodies. `read_flare_event` returns supported original text (`content`/`description`) or a clearly marked provider preview. It retains author, original URL, source name, parent event UID and thread ID where supplied. A thread-level record does not imply that all replies were collected. Read the referenced parent UID separately if needed; linked URLs and payloads are never fetched automatically.

Persisted provider evidence is searchable with `search_evidence(kind="provider")`, replayable with `get_evidence`, and recheckable with `refresh_evidence`. Refresh requires the original configured tenant. A provider 404 is an access/availability error, not confirmed deletion. Rate-limit cooldowns survive restarts. Inspect provider `source_id` from `get_status` with `get_source_health` for actual attempt outcomes; status itself performs no entitlement probe.

Keep four clocks separate:

| Field | Meaning |
| --- | --- |
| `published_at` / `published_at_raw` | Explicit source post date supplied by the provider; unknown offset stays unresolved |
| `provider_first_seen_at` | Provider `first_crawled_at`, when supplied; not our observation |
| `provider_estimated_created_at` | Provider estimate; not silently substituted for publication |
| `collected_at` and stored local `first_seen` | When this installation retrieved/observed the evidence |

Provider metadata and a hash of the returned payload are retained alongside the selected text fields. This is a bounded extraction, not a raw archive of every provider field. `record_scope`, `truncated` and `access_state` explain the limits. Provider copies and direct-source reads are not automatically independent corroboration. No source completeness, commercial corpus freshness or detection lead time is inferred from a successful API response.

Official API references, checked 19 October 2026:

- [Authentication](https://api.docs.flare.io/concepts/authentication)
- [Global search guide](https://api.docs.flare.io/guides/global-search)
- [Search API](https://api.docs.flare.io/api-reference/v4/endpoints/global-search)
- [Retrieve event](https://api.docs.flare.io/api-reference/v2/endpoints/activities/get-fireworkv2activities-)
- [Forum post data model](https://api.docs.flare.io/event-types/forum-post)
- [Paging](https://api.docs.flare.io/concepts/paging)

# Architecture

```text
Agent → MCP stdio → authenticated AIL JSON API
                              ↓
                      AIL Crawler → Lacus → public page / Tor
                              ↓
                  native AIL objects, queues and files
                              ↓
                      AIL Indexer → Meilisearch
                              ↓
                    AIL search_engine → MCP results
```

No parallel evidence database, custom search index or custom source scraper is maintained by the MCP.
The runtime starts upstream AIL libraries and workers at the pinned revision. Private configuration and object
files live under ignored `data/`; databases live in named Compose volumes. The optional MCP container connects to
an external AIL URL; the local setup normally runs MCP through uv.

## Compatibility extension

AIL's REST API supports authenticated object reads, crawler requests and feeder submissions. Its native full-text
search library is called by the web UI. `runtime/ail_api_app.py` imports the genuine Flask app and registers JSON
routes using AIL's `token_required` decorator:

- `POST /api/v1/dark-mcp/search`: calls `lib.search_engine.api_search`; preserves pagination, adds global IDs.
- `GET /api/v1/dark-mcp/status`: native search health/statistics and Lacus configuration status.
- `GET /api/v1/dark-mcp/crawl?uuid=…`: temporary native task/capture; no cookies/headers returned.
- `GET /api/v1/dark-mcp/domain?domain=…`: latest 20 retained native domain outcomes and root objects.
- `GET /api/v1/dark-mcp/object?gid=…`: native object API plus item URL/crawler context.

For an existing deployment, put this extension on AIL's Python import path and run `ail_api_app:app` instead of the
stock Flask entrypoint. Preserve AIL's environment and configuration. The bundled deployment does this through
`runtime/start_ail.py`. These routes are an extension, not stock upstream REST endpoints.

AIL auth is enforced on every compatibility route. The adapter sends its token only to the configured backend,
rejects redirects, bounds response size and sanitizes transport/backend errors. AIL is a trusted service. Source
access, DNS resolution, redirects and network isolation also require controls at AIL/Lacus and deployment boundaries.
Adapter public-IP validation alone is not a complete network isolation boundary.

## Evidence limits

AIL retains native object IDs, source links and collection context. The wrapper does not promise immutable revisions,
exact-byte snapshots or complete task-to-object receipts where upstream does not provide them. Domain history is
scoped by hostname, not proof that a particular task succeeded. Inspect the saved URL, body and collection time.
The domain tool lists root objects; child pages remain available through AIL search/object retrieval.

AIL supplies the collection/indexing foundation. It does not grant gated forum access or make a fresh corpus equal
to a commercial vendor's historical coverage. Installing the framework alone establishes no benchmark parity.

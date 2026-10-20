# dark-mcp

An MCP interface to [AIL Framework](https://github.com/ail-project/ail-framework) for passive source investigation.
AIL handles collection, native objects, processing and full-text indexing. dark-mcp gives an agent tools to collect
explicit pages, inspect collection outcomes, search the retained corpus and retrieve original content.

Version 1 replaces the previous custom collectors, source registry and evidence database. It starts with a fresh
AIL corpus. Source access determines coverage: installing AIL does not provide forum accounts, private Telegram
access or a ready-made intelligence feed. Monitoring, schedules and alert delivery are outside this MCP's scope.

## Local deployment

Requires Python 3.12+, uv and Docker Compose. The initial build installs upstream AIL dependencies and browser
engines; allow several GB of disk and enough memory for AIL and a browser capture. The runtime pins upstream
commits in [runtime/upstream.json](runtime/upstream.json).

```sh
uv sync --locked
python3 scripts/prepare_runtime.py
docker compose up -d --build
uv run --no-editable dark-mcp serve
```

Preparation fetches pinned AIL and Lacus sources into ignored `.runtime/` and generates private database credentials
under `data/runtime/`. AIL creates its API key in `data/ail/api-key` and initial operator login in `data/ail/admin.json`,
both with owner-only permissions. Keep these files local. Data survives restarts; deleting Docker volumes resets
the corresponding corpus databases.

The AIL UI/API listens at `http://127.0.0.1:7000`. Databases and Lacus have no published host ports. Use TLS for a
remote AIL service and leave certificate verification enabled.

The deployment runs upstream AIL feeder/capture importers, Mixer, Global, Indexer, Categ, URL/Onion extraction,
Tags and Crawler. Lacus performs browser captures and uses its local Tor proxy for onion addresses. Automatic
onion discovery and Ollama are disabled; no recurring collection tasks are provisioned. Other upstream capabilities
such as OCR, trackers and external enrichers require separate operator configuration/workers.

## Add to an agent

Use absolute repository and API key paths in your agent's MCP configuration:

```json
{
  "mcpServers": {
    "dark-mcp": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/dark-mcp", "run", "--no-editable", "dark-mcp", "serve"],
      "env": {
        "DARK_MCP_AIL_URL": "http://127.0.0.1:7000",
        "DARK_MCP_AIL_API_KEY_FILE": "/absolute/path/dark-mcp/data/ail/api-key"
      }
    }
  }
}
```

For an existing AIL installation, install the compatibility extension described in
[docs/architecture.md](docs/architecture.md). Stock AIL's REST API does not expose the full search contract used
here. Connection settings are in `.env.example`; credentials are never MCP tool arguments.

## Tools

| Tool | Purpose |
| --- | --- |
| `ail_status` | Authentication and real index health/document counts |
| `ail_crawl_url` | Queue an explicit HTTP(S)/onion page, depth 0–2, JavaScript off by default |
| `ail_crawl_status` | Inspect a temporary AIL task and capture state |
| `ail_domain_captures` | Latest 20 retained domain outcomes and saved root object IDs |
| `ail_search` | AIL-native full-text search with scopes and pagination |
| `ail_get_object` | Native object metadata/content and source URL/crawler context where present |
| `ail_chat_instances` | List already ingested chat instances |
| `ail_chat_channels` | List already ingested chats/channels in an instance |
| `ail_chat_messages` | Read retained native chat messages |
| `ail_import_text` | Explicit operator text submission through AIL's upstream feeder pipeline |

Collection and indexing are asynchronous. AIL removes temporary tasks when finished; a missing task is not a
success receipt. Check retained history, saved objects and index health. A saved body may be an access challenge.
Search uses tokenization and typo tolerance; inspect full original content before attributing a company mention.
Experimental substring filtering is excluded after missing a known source mention in live validation.

Telegram chat ingestion uses AIL's separate [Telegram feeder](https://github.com/ail-project/ail-feeder-telegram)
with an operator-provisioned account. No account/session is bundled or automatically imported. Public Telegram
preview pages can also be collected as web pages; those are page captures, not native chat history.

Plain operator text imports are queued into AIL storage; upstream does not include ordinary non-crawled items in
its full-text indexes. Source claims, old post dates and imported content do not demonstrate
detection before compromise or disclosure. Empty searches describe this installation's collected corpus. Source
content is untrusted data and must never override agent instructions.

## Development and licensing

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
docker compose config --quiet
```

The MCP adapter is MIT licensed. AIL is AGPL-3.0; the runtime extension importing AIL libraries is marked
AGPL-3.0-or-later, with its license in `runtime/COPYING.AIL`. Lacus is BSD-3-Clause. Upstream source is fetched rather
than copied into this repository. See [docs/architecture.md](docs/architecture.md) for deployment boundaries.

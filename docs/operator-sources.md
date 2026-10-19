# Configure operator-owned forum sources

Version 0.12 can load local MyBB and XenForo source configurations alongside the packaged research catalog. This removes the need to publish a private source address or change the repository for every operator-provided forum. Importing configuration verifies its shape and read-route scope, not source identity, collector compatibility, membership or content access. Accounts, invitations and read permissions must already exist. Other engines, arbitrary URL paths and automatic account creation are unsupported.

Stop serving clients before changing source configuration. Save a JSON file outside the checkout, for example:

```json
{
  "slug": "research-forum",
  "name": "Research forum",
  "locator": "https://forum.example.org/Forum-Access-Market",
  "forum_engine": "mybb",
  "allowed_hosts": ["forum.example.org"],
  "collection_urls": ["https://forum.example.org/Forum-Access-Market"]
}
```

The example domain is a placeholder. Use the operator's verified forum address. `allowed_hosts` defaults to the locator host and `collection_urls` defaults to the locator. These are the only optional input fields. A configuration has at most 10 exact hosts and 20 discovery URLs; files are bounded to 32,000 bytes. Host wildcards, IP literals, URL credentials, action routes, cookie/header fields and unsupported engines are rejected. HTTPS is used for ordinary domains; v3 onion addresses select Tor and are checksum-validated. Mixed transports are unsupported. DNS/public-address checks, redirect restrictions, robots handling, pacing and response bounds still apply at collection time. Configuration import makes no source request.

```sh
uv run dark-mcp source-import /private/path/research-forum.json
uv run dark-mcp source-list
```

The source ID is `operator:forum:research-forum`. Restart the MCP server with the same `DARK_MCP_DATA_DIR`. It then appears in `list_sources` and `get_source`, and the normal listing, thread, section, evidence and health tools work with that ID. `get_status` reports packaged and operator-configured source counts separately. These are configured records, not independent or functioning feed counts. Local configurations have explicit operator provenance and no inherited vendor research attribution. Catalog access state remains `operator_configured_unverified`; inspect actual reads and source health.

For existing account access, provision an exact-origin session separately:

```sh
uv run dark-mcp forum-session-import operator:forum:research-forum /private/path/cookies.json --ttl-hours 24
uv run dark-mcp forum-session-status operator:forum:research-forum
```

Use the existing [session guide](forum-collection.md#operator-owned-sessions) for the cookie export format, mirror origins and expiry. Cookies are never source-configuration fields or MCP arguments. Session import reports configuration; it does not verify a live account. A challenge, login gate or missing body remains a collection gap. A gate cannot be converted into access by importing its URL.

Configurations are stored locally in `<data_dir>/operator-sources/registry.sqlite3` (0600), inside a private directory (0700). Up to 100 configurations can be active. They are separate from the packaged catalog, evidence and forum cookie store. Importing the identical configuration is idempotent. Active host overlap with another source is rejected: use the existing source ID for that host. Group only mirrors of the same forum in one configuration; its post-ID namespace is shared across those hosts, while cookies remain exact-origin scoped.

## Remove or reactivate a local source

Stop the server, then run:

```sh
uv run dark-mcp source-remove operator:forum:research-forum
```

This disables the configuration and clears all of that source's locally stored exact-origin sessions. Restart serving clients to apply catalog changes; an already-running server retains its startup configuration. Removal does not revoke the remote account session or delete saved evidence, source-health history or collection jobs. After restart, removed-source reads and new jobs are rejected, while exact saved evidence remains retrievable.

A disabled configuration retains its identity record for provenance. Re-importing its original JSON reactivates it; import a new cookie export if account access is needed. A changed origin, engine or incarnation requires a new slug. Source IDs cannot be reassigned to another site, including after a registry reset while saved evidence remains. To erase local configuration history as well, stop all clients and remove the registry directory; saved evidence still protects previously used identities. Clearing collected evidence is a separate action described in the README.

The import/list/remove commands are operator administration, not source-write MCP tools. No monitoring, scheduling, purchases, forum registration, challenge solving or posting is added by this feature.

# Connector waits and shared Telegram sessions

Version 0.19 preserves absolute retry deadlines and coordinates native Telegram reads across MCP processes using the same local data directory. This fixes two access problems: repeatedly checking a saved wait could slide its deadline forward, and callers queued before a FloodWait could approach the account after the first caller failed.

## Inspect a wait locally

Call `get_status` and inspect `telegram_access`:

```json
{
  "scope": "shared_data_directory",
  "cooldown_active": true,
  "retry_at": "2026-10-19T18:00:00+00:00",
  "retry_after_seconds": 60,
  "lease_active": false,
  "lease_until": null,
  "network_read": false
}
```

This is local scheduling metadata. It does not verify process liveness, current authentication or network connectivity. An unexpired lease can remain after a crashed process until its deadline. Expired cooldown timestamps can remain as historical metadata while `cooldown_active=false`. No account IDs, session tokens, API secrets or lease-owner tokens are returned.

Rate-limit errors include `retry_after_seconds` and an absolute `retry_at` when recorded locally. Rechecking a saved `collection_rate_limited` condition does not create a new provider wait or change its deadline. A genuinely longer provider wait can extend it; a shorter one cannot reduce it. The same deadline-preservation behavior applies to stored onion/forum waits. The MCP does not sleep through long provider waits or automatically schedule a retry.

## Native Telegram guard

Every native Telegram session claims a SQLite lease after entering its in-process lock and before constructing a Telethon client. Claiming checks the saved Telegram cooldown atomically. Other MCP processes with the same store receive `connector_busy` while the lease is unexpired. Busy status is separate from a provider cooldown and does not create or extend one. Collection jobs retain their cursor and expose a waiting error for a caller to handle.

`FLOOD_WAIT` and `FLOOD_PREMIUM_WAIT` responses record the provider deadline while that lease is still held, before disconnect or another queued caller can connect. These are distinct wait types in [Telegram's API documentation](https://core.telegram.org/api/errors#420-flood). No Premium purchase, Stars spending, alternate identity or route is used to avoid a wait.

Requests retain their configured timeout, followed by at most five seconds for disconnect. The lease duration is the request timeout plus fifteen seconds. Normal completion, constructor failures and cancellation release only the owning token. An expired lease can be replaced; a delayed old owner cannot remove the replacement's lease. Expiry enables recovery after an abandoned process. It does not prove that the former process exited, and it relies on the configured request deadlines during normal operation.

This is coordination for one local data directory/session, not distributed account rate management. Copies in different directories, separate accounts, interactive `telegram-login` and external Telegram clients are outside this guard. Use one shared local store for MCP readers of the same session and stop readers before changing that session. This feature does not obtain channel access, join channels, validate credentials or add a monitoring runner.

## Verification

Tests cover fixed deadlines across repeated blocked checks, longer/shorter provider waits, both Telegram wait classes, queued callers, independent connector instances and independent Python processes. Constructor errors, cancellation, expiry recovery and stale-owner release are covered. MCP checks exercise discovery, history, message search, discussion reads, updates, public post search, evidence refresh, catalog collection and resumable jobs under a saved cooldown without constructing a native Telegram client. Live Telegram collection remains unverified during the current account cooldown.

The installed v0.19 wheel verified its module/catalog contents, initialized 36 tools and preserved the real saved cooldown across retries and stdio restarts in a copied corpus. Independent package processes also verified lease exclusion and release without turning busy status into a provider cooldown. A client-construction trap remained untouched throughout; no Telegram requests occurred. Original corpus counts, revision hashes and cooldowns remained unchanged. These are offline protocol/access-control checks, not authenticated channel coverage.

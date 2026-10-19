# Search captured URL references

`search_references` searches URL/label metadata captured from forum posts, onion pages and, from version 0.16, Telegram URL entities. A visible link may say “details” or show a shortened address while its actual destination contains the company domain. `search_evidence` searches titles and bodies, so that full destination can be absent from its matches. The reference tool returns the original evidence revision without fetching the reference.

```text
search_references
{"host":"superagent.sh","include_subdomains":true,"kind":"forum","limit":100}

search_references
{"query":"company-name","kind":"forum","include_history":false}
```

These are example queries, not claims of observed Superagent exposure. Read each hit's exact source snapshot with `get_evidence(evidence_id, revision_id)` and its already collected discussion with `get_collected_thread`. A matching link may be quoted, copied, promotional or unrelated. It does not establish domain ownership, a verified victim, stolen access or an impending attack.

## Matching and bounds

Supply `query`, `host`, or both. When both are present, both conditions must match. `query` uses case-folded literal substring matching over the saved URL and label; it does not search the post body, decode arbitrary encodings or interpret source-native query syntax. `matched_fields` identifies URL, label and/or host matches.

`host` accepts a hostname or IP address, without a URL, port, credentials or wildcard. Hosts normalize using non-transitional IDNA and lowercase ASCII, with one trailing root dot removed. Unicode/punycode equivalents match without merging distinct domains such as `straße.de` and `strasse.de`. Matching is exact by default. `include_subdomains=true` additionally accepts proper dot-boundary subdomains, not names such as `company.example.evil.invalid`. IP literals remain exact; `scope.host_match_mode` reports the effective behavior. Host normalization makes no DNS request and does not prove reachability or identity.

Optional filters are `kind` (`forum`, `onion` or `telegram`) and `source_id`. Results order by the selected revision's latest positive local recording, including unchanged refreshes, then use offset pagination with 1–100 rows per call; `next_offset` continues a query. The response includes a store-wide observation watermark from the same read snapshot. New collection between calls can change pagination; the watermark is not a continuous coverage guarantee.

Only top-level structured `links` metadata is indexed: up to 200 unique valid HTTP(S) references per revision, with labels bounded to 500 characters. Native forum extraction currently returns at most 50 references per post; onion extraction has its own page bounds. Telegram captures URL and text-URL entities from the first 200 entities within the retained message text, including hidden destinations and UTF-16 offsets. `link_capture` reports bounds and omitted URL entities; `reference_metadata` reports the selected link's extraction basis. Nested aggregate-page copies, inferred text URLs, images and attachment contents are outside this index. Invalid/credential-bearing URLs are skipped. Gated snapshots are excluded from results. Referenced private or unavailable addresses are still unverified metadata, never fetch instructions.

Telegram hits also return captured forward headers separately from the observed channel and publication time. See [Telegram attribution](telegram-attribution.md). Legacy snapshots without captured entities have no reference coverage; migration does not infer links or forward headers from their text. Absence from reference search is not a company-wide negative finding.

## Revisions and provenance

The default `include_history=false` searches each item's latest revision. A reference removed by a later capture no longer matches that default. With history enabled, the tool selects the most recent matching revision per evidence item/reference URL and marks whether it is the current revision. Repeated snapshots and duplicate URL entries do not become independent claims. `current_availability` describes the evidence item's current state, not necessarily its state at the historical observation.

Each hit includes source/evidence IDs, revision ID, snapshot and body hashes, hidden/truncated source flags, source publication time when known, the selected revision's local recording times and acquisition provenance. A visible reference can occur in an otherwise partial post. `source_url` comes from that revision's snapshot when available; a legacy current-item fallback is explicitly labeled. The evidence item's first-recorded time is separate from the selected revision's times. Neither establishes the first-ever mention or proves that a link was observed before compromise or disclosure. There is no observation-cutoff filter on this tool; inspect exact revisions and timestamps when assessing chronology.

Native reads and operator HTML imports retain their acquisition labels. An empty acquisition object on older snapshots means the method was not recorded. Link references may occur inside quoted text and do not establish an author's endorsement or ownership.

## Existing stores and live validation

On the next server start, a derived reference/FTS index backfills existing immutable snapshots in bounded batches. New revisions update the same index transactionally. Backfill does not change evidence bodies, hashes, revisions, observation dates, jobs or source attempts. A large store can take longer to initialize. The index accelerates local retrieval; it does not add a remote source, background crawler or monitoring runner.

A real stdio MCP check indexed 4,332 reference rows across the local revision history. For one previously observed upstream-marketplace hostname, it recovered seven current post items where the full hostname was absent from title/body text. All returned references matched exact saved snapshots, replayed after a server restart and passed a negative control. Revision hashes and item/revision/observation/job/attempt counts were unchanged. These are retrieval results, not seven independent offers or new original marketplace access. No reference URLs were fetched and no Telegram calls were made. Raw receipts remain private.

Separately, a normal browser could perform a LeakForum guest search, but the source's robots rules denied native automated search routes. bf.st's search route returned a browser challenge. Those access checks are recorded in `get_source`; no native site-wide forum search was enabled. Existing section collection and original-thread reads remain bounded by their normal rules.

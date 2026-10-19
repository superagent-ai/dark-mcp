# Source-advertised forum feeds

Version 0.22 adds `read_forum_feed` for three inspected source-owned RSS endpoints. It collects a bounded publisher window with text, reported authors, links and explicit-offset publication dates. Feed evidence stays separate from original thread bodies; it does not provide gated-source access or complete history. No competitor API, paid entitlement or scheduler is involved.

## Configured sources

| Source ID | Verified endpoint scope | Transport |
| --- | --- | --- |
| `onion:91e4e785a3af` | Spear's advertised site-wide RSS window | Tor |
| `onion:e51377a2216a` | DarkForums' advertised access-market RSS window (`fid=82`) | Tor |
| `forum:bf-st` | bf.st's advertised site-wide RSS window; operator/lineage unverified | HTTPS |

Use `get_source` to inspect `feed_urls`, advertisement provenance and the dated check. Only exact configured endpoints are permitted; agents cannot add parameters, use arbitrary feeds or submit action URLs. Normal HTML thread permissions do not expand to feed endpoints. The [source snapshot](../sources/forum-feeds-2026-10-19.json) is compiled into the installed catalog; its 174 candidate count is unchanged because these are additional views of existing sources.

```text
read_forum_feed(source_id="onion:91e4e785a3af", limit=20)
read_forum_feed(source_id="onion:e51377a2216a", limit=20)
read_forum_feed(source_id="forum:bf-st", limit=20)
search_evidence(query="company.example", kind="forum_feed")
search_references(host="company.example", kind="forum_feed")
get_corpus_coverage(kind="forum_feed")
```

The limit is 1–50 retained items. The parser examines at most 200 items and reports invalid, duplicate, unexamined and locally omitted items. The response's `history_complete` is always false: the inspected feeds return 15 entries, with no history cursor. Asking for more retained items cannot manufacture older records. A later runner decides when to refresh; no continuous collection is started.

## Evidence and chronology

Each item retains its original same-source thread link, title, captured body field, reported GUID, author metadata, links and source publication text. `content:encoded` is used when present; otherwise the body comes from `description`. These are publisher-supplied feed copies and may be excerpts or contain hidden-content notices. Quotes are separated from body text, scripts/forms are discarded and links/attachments are never fetched. Truncation and omitted quotes are explicit.

`published_at` normalizes only an item `pubDate` with an explicit offset. Missing or timezone-free values remain unresolved. Feed build time never supplies item publication time. A publisher's reported date is not independently verified thread creation, compromise time or our observation time. Feed collection does not assign a previously unknown timezone/date to an older HTML revision or establish earlier detection.

Author markup is parsed as source metadata, preserving the raw value. Only explicit, validated same-source profile links can supply a reported source-local ID; these IDs do not authenticate people or merge feed records into the saved original-post author index. A live original-thread read matched the inspected feed item's reported author after normalizing its HTML profile label.

Items persist as `forum_feed` evidence keyed by source and thread URL. They never replace `forum` post records. Immutable revisions, exact replay and local recording times use the existing evidence store. Document checksums and access context stay in observation provenance so changing feed-build timestamps do not create false item edits. `get_evidence` exposes the provenance's own collection/recording times separately from the revision's first collection time.

`refresh_evidence` re-reads the current feed window and returns `requested_item_returned`. Falling out of that window does not mark a record unavailable or deleted. Follow the item URL through `read_forum_thread` for original roots, replies, hidden content and contextual dates. Feed/root agreement does not corroborate a claim independently: both are publisher-controlled views.

## Transport and parsing

Feeds reuse the forum transports, DNS/IP pinning for HTTPS, Tor URL checks, robots rules, pacing, response-size/time bounds and operator-session handling. Feed and original-page reads share the same saved source cooldown; a feed call cannot bypass an existing forum wait. Feed XML content types are permitted only on this explicit path. Normal thread reads retain their existing content-type and route restrictions.

Only leading XML whitespace/BOM is normalized. Arbitrary preludes and malformed XML fail. XML DTDs/entities are rejected by the parser before expansion or external loading; a literal HTML doctype inside CDATA remains text. No scripts, profiles, attachments, proof files or referenced URLs are requested.

## Live validation, 19 October 2026

Real stdio MCP reads collected 15 items per source, 45 total, all with body text, reported author labels and explicit-offset dates. Spear and bf.st returned items published that day; the sampled DarkForums access window ranged from 29 September to 17 October. One item was followed to an original thread with an explicit root and matching author label. Positive body search, exact text/hash replay, feed-window refresh and restart passed. Feed checks forbade native Telegram client construction.

A separate native Telegram read ran only after its saved deadline had expired: five posts returned without a retry. That sample contained neither forward headers nor hidden text-URL entities, so those particular live metadata cases remain unexercised. Earlier forum-linked Telegram checks produced one stale public preview and six redirects; contact references were not treated as active channels or joined/messaged.

These checks establish bounded current data retrieval and source chronology. They do not establish comprehensive coverage, operator identity, access-offer validity, company-specific impending attacks or pre-disclosure lead time. Monitoring, scheduling and alerts remain deferred.

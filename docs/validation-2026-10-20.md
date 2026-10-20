# AIL integration validation — 20 October 2026

The replacement MCP was tested against a genuine AIL/Lacus/Tor/Meilisearch deployment on ARM64 Docker.
The old custom backend and corpus were removed from the active checkout and archived locally before replacement.

## Verified

- MCP stdio, 10 discoverable tools, authenticated AIL API and unauthenticated-request rejection (401).
- Fresh collection of one original Spear forum thread and three Telegram public preview pages (ThreatMarket,
  breaches, vxunderground), with 59 rendered message text blocks across the previews, including possible forwards.
- Four original URLs and complete returned AIL item bodies remained retrievable after AIL restarted.
- AIL indexes contained four page bodies plus four derived titles; eight documents are not eight sources.
- Native keyword search for `opencode` recovered one literal mention in the original API-key buying thread.
- `superagent.sh` and `superagent` returned zero results in this bounded corpus.
- An explicitly marked operator import passed storage and original-object retrieval. Ordinary non-crawled items
  are not part of upstream full-text indexing and were not counted as source findings.
- 21 adapter tests passed; lint/format/package and Compose checks passed; AIL, Lacus and MCP images built.
  AIL's installed dependency consistency check passed.

## Interpretation and limits

The OpenCode post solicits keys across many providers. It establishes market demand, not a verified imminent
attack, victim identity or compromise. The original URL was selected from a known lead, so this is a collection
and retrieval acceptance test, not prospective discovery. No exact `opencode.ai` domain mention was present.

Two important failure cases were caught. Experimental substring filtering missed the known mention and is now
excluded from the MCP and compatibility API. Keyword search for `opencode.ai` retrieved the same page despite no
exact domain match, so the investigator must check full source content and company identity.

Telegram browser captures produced storage timeouts/retries but retained readable HTML. Browser state and preview
completeness are not guaranteed; the item metadata does not retain every browser warning. There were no native
Telegram chat instances or authenticated forum accounts. Monitoring and alerting were not added. No detection
before compromise or disclosure was demonstrated. The result is not a commercial coverage benchmark.

Private source bodies, object IDs, hashes and protocol receipts live under ignored
`data/research/ail-validation-2026-10-20/`. The local HTML report links these receipts. None are committed.

# Expanded end-to-end validation — 2026-10-20

This extends [initial validation](validation-2026-10-20.md) using a real MCP stdio client through AIL, Lacus/Tor,
native retained objects, full-text indexing and original-object retrieval. It was bounded passive research, with
no runtime implementation changes, authenticated forum accounts, native Telegram session or OCR worker added.
Private reports and full receipts remain under ignored `data/research/ail-expanded-e2e-2026-10-20/`.

## Collection and search results

| Measurement | Result |
| --- | --- |
| New explicit URL attempts / retained HTML bodies | 9 / 9 |
| Readable original thread pages | 3: two Spear threads and one Niflheim thread |
| Readable forum listings | 2: Spear marketplace and buyers pages |
| Readable public Telegram previews | 2: BaseLeak and a GhostSec-named channel |
| Gated claim-only thread | 1: Cracked; underlying content hidden behind registration/login |
| Telegram landing page | 1: BreachDetect preview redirected to a landing page; zero message coverage |
| Total retained page bodies including earlier validation | 13 |
| Native index documents | 26: 5 onion bodies, 8 web bodies, 13 derived titles |
| Final keyword queries | 31; all API calls succeeded |

Nine retained bodies do not mean nine useful original threads. Index document counts do not represent independent
sources. Native chat ingestion and authenticated forum history remained unavailable.

## Useful original content and interpretation

- **AWS account procurement:** an original Spear buyer post requested active EC2 accounts with established usage,
  billing history and compute capacity. This is potential abuse-preparation context. It establishes neither stolen
  accounts nor a compromise of AWS itself, and contains no verified exposed asset.
- **Kraken reference:** an original buyer post referenced “Kraken(.)com VM.” The acronym was undefined; the brief
  request provided no proof of access, account ownership or imminent attack. It is an ambiguous research lead.
- **APEX AI advertisement:** an original Niflheim thread advertised an uncensored AI/terminal service. These are
  unverified seller capability claims, with no demonstrated target-specific attack.
- **Cracked:** a page advertised a large credential-log collection but hid the main content. The claimed records
  were not collected or verified. A title is not dataset access.

Original source URLs and local object IDs are preserved in private receipts. No advertised credential testing,
actor contact, purchases or dataset/payload downloads were performed. Browser capture can load ordinary page
resources; this was not a complete audit of all source assets.

## Brand checks and false positives

| Entity | Name-query raw hits | Domain-query raw hits | Substantiated entity matches |
| --- | ---: | ---: | ---: |
| Firecrawl / firecrawl.dev | 0 | 0 | 0 |
| Exa / exa.ai | 2 | 0 | 0 |
| Replit / replit.com | 7 | 0 | 0 |
| Lovable / lovable.dev | 0 | 0 | 0 |
| Legora / legora.com | 0 | 0 | 0 |
| Cursor / cursor.com | 0 | 0 | 0 |

All nine Exa/Replit raw hits were rejected after inspecting original content. Tokenization and typo/prefix
tolerance matched unrelated words and reply-related text. There were no whole-entity matches for these six
companies in this corpus. This is a bounded negative result, not evidence that those companies face no threats.

Known real phrases recovered the original AWS, Kraken and APEX material; a random negative query returned zero.
These controls establish basic retrieval in the sample, not comprehensive search recall. No synthetic company
findings were imported. Earlier public security discussions were retrospective controls, not new prospective
pre-disclosure discoveries.

## Freshness and source quality

The BaseLeak preview contained 13 posts with both publication timestamps and dates asserted for the claimed
events. Calculated repost lag was 37–67 days, median 48. These are comparisons against the channel's own claims,
not independently verified incident/disclosure dates. This sample offered historical context rather than timely
early warning. Rendered text blocks can include forwards and duplicates, so they are not unique-message counts.

The GhostSec-named preview was primarily promotional. Its name did not verify the channel operator's identity.
BreachDetect yielded no readable message history. Neither should be counted as authenticated actor coverage.

## Operational lessons

- Short polling windows can miss late successful captures. Queueing, retries and browser work can outlast an
  observer deadline; all nine retained objects were reconciled after the temporary tasks disappeared.
- A late same-host capture initially confused the local research harness's page attribution. Actual retained
  URLs and native objects were reconciled before final counts. Hostname history alone is not a task receipt.
- Niflheim retained the expected thread after a normal post-link redirect. BreachDetect's redirect instead
  produced a landing page. Verify actual URL and content, not just a task acknowledgment.
- Readable HTML coexisted with browser storage/screenshot/favicon warnings. This does not establish complete
  collection of media, replies or underlying gated content.

The tested value is collecting accessible original source text and checking it to reject misleading search hits
and qualify intent. No verified company-specific imminent attack, detection before compromise/disclosure or
commercial-source coverage parity was demonstrated. The [research playbook](research-playbook.md) incorporates
these lessons into an agent-driven discovery, collection and verification workflow.

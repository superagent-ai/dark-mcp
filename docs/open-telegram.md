# Open Telegram research imports

Version 0.20 adds local imports of third-party Telegram research data. No Telegram account is needed to search imported messages. This supplies historical context and source discovery; it does not establish current access, continuous coverage, or advance warning of an attack.

## Supported inputs and attribution

| Input | Support | Provenance and terms |
| --- | --- | --- |
| [DarkGram](https://github.com/SayakSR/DarkGram) `data/posts.zip` | Read CSV posts directly inside the ZIP, optionally selecting an exact upstream category. No ZIP extraction, media retrieval, or evaluation of serialized Python fields. | Sayak Saha Roy et al., *DarkGram*, USENIX Security 2025. The [archived release](https://zenodo.org/records/14736880) declares Apache-2.0. Released data may be processed or obfuscated; imported claims are unverified. |
| [deepdarkCTI](https://github.com/fastfire/deepdarkCTI) `telegram_threat_actors.md` and `telegram_infostealer.md` | Import public handles and invite links as directory leads, preserving reported status, labels, and source revision. | fastfire and deepdarkCTI contributors; upstream GPL-3.0. Imported lists stay in local data storage; this package does not bundle those lists or relicense their contents. |
| [TGDataset](https://github.com/SystemsLab-Sapienza/TGDataset) bounded JSON shards | Import text messages and reported forwarding metadata. The fetch command downloads only the small published preview. | Massimo La Morgia, Alessandro Mei, Alberto Maria Mongardini, *TGDataset*, KDD 2025. The [dataset record](https://zenodo.org/records/7640712) declares CC-BY-4.0. Attribution is retained in every imported message. |

The artifact download pins a repository commit and SHA-256 for each file. These checks establish which artifact was downloaded; they do not authenticate claims made inside it. Imported data and manifests remain in the configured local data directory and are excluded from the public repository and wheel.

## Fetch and import locally

Run these commands from an installed checkout (`uv run dark-mcp ...` after installation, or `PYTHONPATH=src uv run python -m dark_mcp ...`). The fetch command returns local paths, repository versions and checksums:

```sh
dark-mcp telegram-open-fetch darkgram
dark-mcp telegram-open-fetch deepdarkcti
dark-mcp telegram-open-fetch tgdataset
```

Fetches only contact the pinned GitHub research artifact URLs. They never follow URLs found inside the data. A checksum mismatch fails before the downloaded file is installed. Downloads are not imports. Cached artifacts are checked again before reuse. Files use private permissions.

Using paths and versions returned by the download manifest:

```sh
dark-mcp telegram-archive-import /absolute/path/posts.zip darkgram COMMIT \
  --category blackhat_resources --limit 100000
dark-mcp telegram-archive-import /absolute/path/posts.zip darkgram COMMIT \
  --category 'credential_compromise (obfuscated)' --limit 100000
dark-mcp telegram-directory-import /absolute/path/telegram_threat_actors.md COMMIT \
  --collection telegram_threat_actors
dark-mcp telegram-directory-import /absolute/path/telegram_infostealer.md COMMIT \
  --collection telegram_infostealer
dark-mcp telegram-archive-import /absolute/path/tgdataset_preview.json tgdataset COMMIT
```

`--limit` bounds scanned message rows, including filtered/empty/invalid rows. Follow `next_offset` by repeating the same artifact, version and category with `--offset`. Category selection uses the exact upstream directory name. Default batch size is 1,000; maximum is 100,000 scanned rows. Inputs are limited to 100 MB, ZIPs to 150 MB declared expansion, and archives to 200,000 message rows. Larger TGDataset files need to be reduced to bounded JSON shards outside this importer; its full 70.7 GB compressed corpus is not downloaded automatically.

Counts report imported, unchanged, empty, filtered and invalid rows. Interrupted or malformed imports can retain earlier rows; repeating identical inputs reuses the same evidence identities and revisions. Imports do not silently infer remote deletions. These operator commands are intentionally absent from MCP: agents cannot read arbitrary local paths or supply credentials through import tools.

## Use from an agent

1. `list_open_telegram_sources(dataset="deepdarkcti", query="access")` searches imported directory metadata. Paginate with `next_offset`. `kind="telegram_directory"` selects leads; `kind="telegram_archive"` selects sources with imported messages. Their inventory is separate from `list_sources`, which describes configured collection candidates.
2. `search_evidence(query="company.example", kind="telegram_archive")` searches imported message bodies using the existing literal matching and source/date filters.
3. `get_evidence(evidence_id, revision_id)` retrieves the exact imported record, including artifact SHA-256, dataset/version, citation/license, member/record, publication fields and local import times.
4. `get_corpus_coverage(kind="telegram_archive")` shows imported inventory and date gaps. `get_exact_duplicates` can expose identical copies; this never proves independent corroboration.

Directory listings also attach `public_web_observation` from saved attempts and post snapshots for the same public handle. Use `web_text_since` to filter on saved text posts’ reported publication dates before pagination. These local facts never verify current access, change permissions, authenticate operators or make upstream ONLINE labels true. See the [public preview guide](public-telegram.md#local-access-observations-and-source-selection).

Directory imports never enable channel collection, join channels, validate invitations, or change the Telegram allowlist. `VALID` is an upstream status label, not a successful live read. Repeated locators within one artifact share a lead record while retaining all reported labels and statuses; conflicting statuses are explicit. Historical artifacts remain separate, so record counts can exceed unique locators. Old public handles can be reassigned; generated post links are unverified candidates. Numeric IDs without a public handle retain a dataset-record locator rather than an invented private Telegram link. Archive IDs cannot overwrite native Telegram evidence. `refresh_evidence` rejects archived records; reimport locally or explicitly investigate an allowed original channel separately.

The inspected DarkGram release mixes CSV schemas. Legacy files without a channel column use the member filename as an explicitly reported, unverified handle basis. Integral decimal post IDs such as `1730.0` normalize to `1730` with the original value retained. Fractional or missing IDs remain excluded and counted; serialized peer dictionaries are never evaluated.

Publication time, reported forwarding time, and local recording time stay separate. In particular, the inspected DarkGram CSV `date` values omit a timezone: `published_at_raw` retains them, `published_at` remains null, and `timestamp_basis` explains the gap. Epoch seconds and explicit UTC offsets are normalized. A publication-date filter excludes records with unresolved publication times. First local recording is always the import time, never the older dataset date. Archive messages have no native Telegram URL-entity metadata; reference search does not infer hidden links from their text.

## Remaining access gaps

TeleHunt's authors describe research-purpose dataset sharing; no account, request, or commercial-use entitlement has been obtained here. TGStat is a third-party index with separate API access, not an open bulk dataset. Neither is connected by this change. These imports do not remove the need for fresh original-source collection or change Telegram cooldowns. Schedulers and alerts remain outside this implementation.

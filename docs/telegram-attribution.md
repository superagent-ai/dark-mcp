# Telegram source attribution

Version 0.16 preserves URL entities and forward headers on channel history, direct message refreshes, discussion replies, updates and global post search. Collection stores these fields in immutable evidence revisions. No new Telegram access, membership or source authentication is provided by this change.

Field interpretation follows Telegram's [message entity documentation](https://core.telegram.org/api/entities) and [forward-header schema](https://core.telegram.org/constructor/messageFwdHeader).

## Captured references

`links` contains valid HTTP(S) destinations explicitly returned as `MessageEntityUrl` or `MessageEntityTextUrl`, including a hidden destination behind text such as “Details.” Each entry records its basis, label and original offset/length in UTF-16 code units. Emoji can occupy two units; these offsets are not Python character indices. Labels are limited to 500 characters and destination URLs to 4,000 characters.

Collection examines the first 200 entities, including formatting entities. A URL entity must fit fully inside the retained 16,000-character message text and end on complete Unicode characters. Invalid ranges, unsupported URL schemes, credential-bearing URLs and out-of-scope spans are omitted. `link_capture` reports examined/total entities, the entity cap, omitted URL-entity count within the examined range and number captured. Omitted entities beyond that range are unexamined. Plain text without an explicit URL entity is not scanned for links. Media, web previews and attachments are not read.

Use `search_references(host="company.example", kind="telegram")` to search this metadata locally, then `get_evidence` for the exact revision. Querying links never follows them, resolves hosts or expands shortened destinations. The evidence still belongs to the observed channel; outbound links do not prove company ownership or exposure.

## Forwarded messages

`forward` is null when the response supplies no forward header. Otherwise it retains the header's original peer type/ID, display name when available, original publication date, channel post ID, signature, imported-history flag and separate Saved Messages context. The returned message's `channel`, `url` and `published_at` still describe the observed copy.

`origin_url` is a numeric `t.me/c/<channel-id>/<post-id>` candidate only when the header identifies a channel and positive channel-post ID. Imported foreign history does not generate this candidate. Numeric channel links can require membership and do not prove that the source is public, still reachable or independent. `origin_verified=false` and `origin_fetched=false` are explicit. Hidden/private sender names do not become channel identities, and Saved Messages context does not replace the original sender.

No additional peer resolution, joins, history reads or outbound requests are made to obtain this metadata. Origin candidates are not automatically registered as sources. Agents can inspect them as leads within the configured access policy; header metadata alone does not verify an actor, establish a complete forwarding chain or prove first publication.

## Evidence and validation bounds

Existing snapshots remain unchanged. Their missing entity/header fields mean unrecorded metadata, not “no links” or “original author.” The derived reference-index migration indexes only fields already saved. A subsequent collection with newly preserved metadata can create a new revision with the same body hash. The store's `edited` change means a changed snapshot; inspect the source's `edited_at` and revision differences before treating this as an author edit. Original-header dates never become our first-recorded date or proof of pre-disclosure detection.

Deterministic protocol tests cover emoji offsets, hidden links, forward/privacy/imported-history semantics, bounds, invalid destinations, link-only revision changes, history search, exact replay and restart. No raw Telegram headers were retained in the older corpus, so those omitted fields cannot be recovered offline. A later live check is described below; earlier samples contained no forward headers or hidden URL entities.

The isolated installed wheel passed hidden-destination retrieval, a body-search negative control, forward attribution and exact evidence replay across two stdio server processes using a synthetic acceptance fixture explicitly labeled as such. A repeated migration on a copy of the real corpus preserved all 757 revision hashes/snapshots and the existing source/item/observation/job/attempt counts. Its 25 Telegram revisions contained no captured entity metadata; none was invented by the migration. These checks made no source requests and do not establish live Telegram coverage.

## Live source-lineage check, 19 October 2026

One bounded native read of five DieNet posts returned three real forward headers and 54 ordinary URL-entity destinations. The account's saved wait had expired; no retry or persistent allowlist change was made. The read used an explicitly scoped process permission for that public source. Hidden text-URL entities were absent, so that particular live case remains unexercised.

The public copy at `dienet3/843` explicitly referenced `alnaaze757/109`. A separate public-embed read returned that original with an identical body hash. Its reported publication time matched the native header's origin date; the copied record retained its own later publication time. The later copied attack claim at `dienet3/845` reported an edit at 02:50:15 UTC on 11 September. The public HTML acquisition exposed no edit marker in this sample, while native metadata did. Publication time alone cannot establish which content was visible before an edit.

The original announcement's reported publication preceded the later copied claim by 3h22m58s. Both were first recorded here on 19 October. This is a historical regional planning signal and source-reported sequence, not verified attack timing, company-specific targeting, our detection lead time or independent corroboration. The attack-claim origin was not fetched. Native `origin_fetched=false` continues to describe that acquisition; a later caller-asserted relationship can separately reference a followed public original without rewriting the snapshot.

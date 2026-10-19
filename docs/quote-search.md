# Search captured quoted context

`search_evidence(include_quotes=true)` also searches the explicit `quotes[].text` fields saved with structured forum posts, forum-feed items and public Telegram HTML posts. This helps retrieve replies and excerpts whose company reference appears only in quoted context. It does not acquire a new source, validate a claim or expand source permissions.

```text
search_evidence(query="company.example", include_quotes=true, include_history=false)
search_evidence(query="company.example", kind="telegram_web", include_quotes=true)
get_evidence(evidence_id=RETURNED_ID, revision_id=RETURNED_REVISION)
```

Quote search is opt-in. The default remains a literal search of the saved title/body. Some forum body captures already contain quoted text; a body match and a quote match are not independent evidence. Each result is returned once, even if several quotes and its body match.

`matched_fields` can include `quotes`. `matched_quotes` identifies each matching quote's original zero-based ordinal, a case-preserving excerpt, saved reference metadata and `attribution="quoted_context_unverified"`. The ordinary top-level `excerpt` continues to describe the enclosing body, with `excerpt_basis` distinguishing a body match from a preview. Retrieve the exact revision to inspect all captured context and acquisition provenance.

Quotes are searched separately: a query cannot match across the boundary between two quotes or between a body and a quote. Casefolded literal matching, short queries and punctuation follow the existing search behavior. URLs or labels in quote metadata do not become quote-text matches. Reference metadata is returned as captured and never fetched or authenticated by search.

Source, kind, access-state, publication/collection date, history and pagination filters still apply to the **enclosing evidence**. Publication time does not become the quoted original's publication time. Historical quote hits name the matching revision and its local recording times; they do not imply the quote is still visible in the latest revision. `include_history=false` uses only current snapshots. Repeated copies and quoted previews do not establish independent corroboration or earlier detection.

The derived index backfills captured quote arrays without altering snapshots, hashes, observations or first-recorded times. Missing legacy quotes stay missing. Native Telegram forward headers, archive metadata, nested aggregate-page copies and inferred text are not indexed as quotes. Captured quotes can be partial: the forum, feed and public-preview collectors have different extraction bounds, and older snapshots may lack truncation flags. An exhausted result page is not complete source history.

## Validation on 19 October 2026

The existing corpus contained public Telegram replies with `Discord` and `estrosa-dot-it` only in their quoted previews. Each bounded query returned one saved body hit by default and two results with quote search enabled; the additional result was labeled solely as quoted context. These are related context records, not additional independent threats. Migration preserved all 48,529 saved revisions, 48,739 observations and 48,339 items byte-for-byte at the row level. The sample does not demonstrate pre-disclosure warning lead time.

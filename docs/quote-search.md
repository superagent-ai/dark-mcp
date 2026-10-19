# Search captured quoted context

`search_evidence(include_quotes=true)` also searches the explicit `quotes[].text` fields saved with structured forum posts, forum-feed items and public Telegram HTML posts. This helps retrieve replies and excerpts whose company reference appears only in quoted context. It does not acquire a new source, validate a claim or expand source permissions.

```text
search_evidence(query="company.example", include_quotes=true, include_history=false)
search_evidence(query="company.example", kind="telegram_web", include_quotes=true)
get_evidence(evidence_id=RETURNED_ID, revision_id=RETURNED_REVISION)
```

Quote search is opt-in. The default remains a literal search of the saved title/body. New MyBB/XenForo captures remove HTML blockquotes from the body and label it `post_body_without_blockquotes_v1`. Older or unreported body scopes can still mix quotes into body text; a body match and a quote match are not independent evidence. Each result is returned once, even if several quotes and its body match.

`matched_fields` can include `quotes`. `matched_quotes` identifies each matching quote's original zero-based ordinal, a case-preserving excerpt, saved reference metadata and `attribution="quoted_context_unverified"`. The ordinary top-level `excerpt` continues to describe the enclosing body, with `excerpt_basis` distinguishing a body match from a preview. Retrieve the exact revision to inspect all captured context and acquisition provenance.

Quotes are searched separately: a query cannot match across the boundary between two quotes or between a body and a quote. Casefolded literal matching, short queries and punctuation follow the existing search behavior. URLs or labels in quote metadata do not become quote-text matches. Reference metadata is returned as captured and never fetched or authenticated by search.

Source, kind, access-state, publication/collection date, history and pagination filters still apply to the **enclosing evidence**. Publication time does not become the quoted original's publication time. Historical quote hits name the matching revision and its local recording times; they do not imply the quote is still visible in the latest revision. `include_history=false` uses only current snapshots. Repeated copies and quoted previews do not establish independent corroboration or earlier detection.

The derived index backfills captured quote arrays without altering snapshots, hashes, observations or first-recorded times. Missing legacy quotes stay missing. Native Telegram forward headers, archive metadata, nested aggregate-page copies and inferred text are not indexed as quotes. Captured quotes can be partial: the forum, feed and public-preview collectors have different extraction bounds, and older snapshots may lack truncation flags. An exhausted result page is not complete source history.

## Validation on 19 October 2026

The existing corpus contained public Telegram replies with `Discord` and `estrosa-dot-it` only in their quoted previews. Each bounded query returned one saved body hit by default and two results with quote search enabled; the additional result was labeled solely as quoted context. These are related context records, not additional independent threats. Migration preserved all 48,529 saved revisions, 48,739 observations and 48,339 items byte-for-byte at the row level. The sample does not demonstrate pre-disclosure warning lead time.

## Forum quote separation and older revisions

From version 0.28, structured MyBB/XenForo reads retain the first three outermost HTML blockquotes separately, up to 500 text characters each. Nested quotes stay inside their outer quote. Each retained quote reports `truncated`, source-local post ID when explicit, unverified links and hidden-content flags. All recognized blockquotes are excluded from the post body, including those beyond the capture bound; `omitted_quotes` reports that gap. Quoted links stay inside `quotes[].links` and are not indexed as top-level body references. No reference is fetched.

The enclosing post's `hidden_content` and body links describe its remaining body. A hidden notice appearing only in a quote stays on that quote. These are structural content boundaries, not authentication of speakers or proof that unquoted text is an independent claim. Other collectors and old snapshots with `body_scope=unreported` do not acquire these guarantees.

A fresh read creates a new immutable revision when its parsed snapshot changes. A transition from an older body scope carries `processing_change.kind=body_scope_changed` with `source_edit_verified=false`; `get_evidence` retains that revision's first processing note after unchanged reads and restarts. The changed snapshot does not establish an upstream edit, and simultaneous source changes are not ruled out. No edit timestamp is invented. Old revisions, hashes and recording times remain replayable. Existing legacy bodies are not rewritten or heuristically stripped of quote text.

For current-body analysis use `include_history=false` and inspect `matched_fields`, `body_scope` and `is_latest_revision`. The MCP's history-inclusive default can still return an older mixed-body revision, explicitly marked as historical. A title match on multiple replies can come entirely from a shared thread title; it does not supply multiple body mentions. Source-native search excerpts also retain their separate, source-defined scope.

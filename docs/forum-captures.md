# Bring browser-read pages into the evidence corpus

Some sources can be read in an operator's browser but remain unavailable to a direct HTTP client. Version 0.13 adds `forum-page-import` so HTML obtained through existing permitted browser access can enter the local evidence corpus. It is a local operator command, not a new agent-facing write tool. The command does not open a browser, navigate a source, import cookies, register accounts, execute scripts, solve challenges or fetch referenced content.

The source must already have a configured MyBB, XenForo or Question2Answer collector. Local operator sources support MyBB/XenForo; see [source configuration](operator-sources.md). This feature does not add an Exploit parser or establish access to Exploit, XSS, DamageLib or a referenced marketplace. A browser or membership alone does not guarantee readable original bodies.

## Import a readable original

Save the original page's HTML as UTF-8 outside version control. For dynamically rendered pages, export the rendered DOM after the posts are visible; browser Save Page may preserve an earlier response instead. The importer reads one file and never follows external resources or `<base>` URLs. Keep complete saved pages private: browser exports can contain account-specific markup even though only parsed evidence is stored.

```sh
uv run dark-mcp forum-page-import forum:leakforum \
  'https://leakforum.io/Thread-<original-returned-by-discovery>' \
  /private/path/thread.html --limit 100
```

Use the actual original thread URL; the URL above is a placeholder. The exact host, transport and read route must match the configured source. Login/action/download routes and credentials in URLs are rejected. A conflicting canonical URL is rejected when present; absence or agreement cannot authenticate supplied HTML. Challenge pages, login placeholders without supported content, empty files and unsupported layouts fail without recording evidence. Files are bounded by `DARK_MCP_MAX_RESPONSE_BYTES` (default 2 MB); limits are 1–100 posts, 16,000 characters per post and the configured total text bound. Parsed section indexes are also supported.

The result contains evidence references and coverage metadata, without dumping post bodies or local file contents. It reports hidden/truncated posts, omitted posts/listings and explicit next-page URLs. Import each accessible subsequent page separately. Reimport at a higher bound if visible posts were omitted. A repeated identical file reuses unchanged post revisions; source post identities are shared with native reads, so they do not become duplicate independent claims. Changes to acquisition metadata can create a new snapshot revision even when the body hash is unchanged. Previous revisions remain available.

## Evidence provenance and chronology

Imported page and post snapshots include:

```json
{
  "acquisition": {
    "method": "operator_html_import",
    "network_read": false,
    "source_identity_verified": false,
    "url_basis": "operator_supplied",
    "document_sha256": "<SHA-256 of the exact input file>"
  }
}
```

The source URL and content are operator assertions. A document hash supports reproducibility of the supplied bytes; it does not prove their origin or truth. Local observation/collection timestamps are generated at import. The command accepts no backdated capture timestamp. An old displayed source date cannot become an earlier observation. Source dates still follow the normal parser rules: explicit usable timestamps are retained, offset-free dates remain raw, and claimed identities/access remain unverified. Form tokens, cookie jars and scripts are not stored as raw HTML; keep the original private file separately if exact byte replay is required.

`search_evidence` returns the selected revision's `acquisition`; `get_collected_thread` returns it per post. `get_evidence` preserves full parsed snapshots and provenance. An empty acquisition object on older evidence means the method was not recorded, not that an import or native fetch was verified.

`get_corpus_coverage` separates latest forum items into `native_forum_items`, `operator_import_forum_items` and `unreported_acquisition_forum_items`. These include page and post items, with overlapping text; they are not independent-source or threat counts. Local imports make no source attempts, clear no cooldowns and do not update network access health. Imported material therefore cannot falsely establish current remote reachability. `refresh_evidence` on an imported latest revision returns `operator_capture_reimport_required`; reimport a new capture locally. An explicit native read is a separate operation and retains its own acquisition metadata.

## Live validation on 19 October 2026

A normal browser reached LeakForum's buyer-request index and an original Google GCP/AI Studio account-request thread. The import preserved 20 listing rows and three post items, with their existing source identities, authors, timestamps, root/reply numbers and evidence references. Raw HTML and receipts remain private. This is a repeat observation of a known request, not a new exposure or proof of stolen access. Exploit's normal-browser check still returned a challenge/gateway page; no original Exploit content or authenticated forum access was established. No actors were contacted, accounts created, credentials tested or Telegram calls made.

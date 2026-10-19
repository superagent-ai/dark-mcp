# Sample the root and declared final thread page

Version 0.18 adds optional `thread_page_order="root_then_last"` to forum section jobs. On a long thread, a small oldest-first page budget can stop before recent replies. This policy collects the starter page and the final page explicitly advertised by its pagination. Under the source's usual chronological ordering, that can reach later discussion, updated terms or closing replies. Page position alone does not establish freshness, attack intent or a new offer.

```text
start_collection
{"source_id":"forum:hard-tm","mode":"section","url":"https://hard-tm.com/forums/sell_all/","max_threads":5,"max_index_pages":1,"max_thread_pages":2,"thread_page_order":"root_then_last"}

collect_next
{"collection_id":"<returned ID>","max_items":100,"max_pages":1}
```

Repeat `collect_next` within the job's bounds. The queue and policy persist across server restarts. This is an on-demand collection primitive; no schedule, alert or recurring runner is added.

## Explicit pagination

Thread-page responses now expose `last_page_url`, `last_page_number`, `last_page_basis` and `last_page_conflict`. The parser uses a recognized MyBB page-count label or XenForo page-jump maximum matched to an actual same-thread read link, or an explicit last-page link inside the pagination. A declared count without a matching link records the number and its basis but supplies no URL. A currently observed page matching the declared maximum can identify itself as the final page. No URL is manufactured from a page count, post count or timestamp.

Other threads, other hosts, actions, attachment routes, ambiguous query parameters and pagination embedded inside posts/quotes are excluded. Conflicting totals remain unresolved. Existing robots, session, transport, response and post bounds apply to both root and final reads. The policy currently supports configured MyBB and XenForo collectors; Question2Answer is unsupported. It requires a bound of at least two thread pages. Larger bounds remain upper limits; this policy samples at most two pages per selected thread.

The default `oldest_first` behavior follows explicit forward pagination. Older persisted jobs without the new option retain that behavior. Title queries, known-pin exclusion and index selection bounds work as before. This option is valid only in section mode.

## Coverage and failures

Inspect `checkpoint.section.thread_samples`. Each selected thread records the requested root, whether an explicit root post was observed, the source's advertised final URL/number/basis, final-page completion and a sampling state. For a jump from page one to eight, `uncollected_middle_pages` records pages two through seven. `deferred_urls` also records the exposed next link as `middle_pages_not_sampled`; it is a bounded URL sample, not an enumeration of every skipped page.

If the root exposes a forward link but no usable final link, the job records `last_page_not_explicit` and leaves the later discussion uncollected. No observed forward pagination is reported separately from an explicit final-page declaration. A gate, failure, changed thread identity, redirected page number or conflicting source totals remains a sampling gap. An incomplete post-bound read retains its cursor and error so the caller can adjust bounds without silently skipping visible posts.

The thread may grow between requests. The job preserves its original advertised boundary and records any later pagination or changed final count on the sampled page. It does not chase new pages indefinitely. A completed job means its selected sample/bounds finished, including reported gaps. It does not mean the whole thread or source was collected. Saved thread reconstruction may include older captures outside this job; their observation times and acquisition remain separate.

## Live verification

A real stdio MCP job read one Niflheim index, selected an eight-page discussion and collected its root. A separate server process resumed the persisted page-eight request and preserved 14 posts, while the job left pages two through seven unvisited. Root recognition, job completion, exact evidence replay and saved-thread context passed. This proves bounded original-page collection and restart behavior. It does not establish a new exposure or pre-disclosure signal. Telegram was not contacted.

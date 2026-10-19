# Comparable APIs and source coverage

Research date: **19 October 2026**. Eight providers were reviewed through their public documentation and research. The [machine-readable evidence matrix](../sources/vendor-source-research-2026-10-19.json) contains 23 forum families, citations, evidence types, historical collection windows, identity caveats and validation tasks.

The most useful finding is that source depth and source identity matter more than a large candidate count. Some providers show named, dated collection examples; others publish recommendations or broad coverage categories. None of those establishes our access, complete thread coverage or a current customer entitlement. No paid API, customer source directory or new forum was accessed for this research.

## What comparable providers disclose

| Provider | Public evidence | Meaning for dark-mcp |
| --- | --- | --- |
| Flare | [Named collection examples](https://flare.io/learn/resources/blog/top-cybercrime-forums), [explicit monitoring claim](https://flare.io/learn/resources/blog/exploit-forum), [Global Search API](https://api.docs.flare.io/guides/global-search) | Strongest reviewed public evidence of named historical forum collection. Request current source status and sample threads before choosing a commercial adapter. |
| DarkOwl | [Search API](https://www.darkowl.com/products/search-api/), [forum recommendations](https://www.darkowl.com/blog-content/after-ramp-what-comes-next-for-the-ransomware-underground/), [network and domain filters](https://support.darkowl.com/knowledge-base/filters-dates-and-advanced-search-options) | Relevant searchable corpus; recommendations alone do not demonstrate a given forum's current API availability. |
| KELA | [Forum research](https://kelacyber.com/blog/top-deep-web-and-dark-web-forums/), [DamageLib observations](https://www.kelacyber.com/blog/xss-forum-after-takedown-damagelib-emerges/), [partner data lake API](https://www.kelacyber.com/partners/partner-program/) | Useful named-source and migration research; confirm source-level data access and export terms. |
| SOCRadar | [Surface-web forum research](https://socradar.io/blog/top-5-underground-hacker-forums-that-are-accessible-via-your-web-browsers-such-as-google-chrome-firefox-and-internet-explorer/), [CTI Dark Web News API](https://socradar.io/solutions/api.html) | Forum-post API is described publicly; purchased tier and original-thread completeness remain untested. |
| Intel 471 | [XSS/DamageLib reporting](https://www.intel471.com/blog/after-disruption-xss-cybercrime-forum-faces-loss-of-trust), [TITAN API integration](https://intel471.com/titan) | Analyst observations support source relevance; they do not establish a raw-post API contract. |
| Searchlight Cyber | [Named forum research](https://www.slcyber.io/blog/2023-in-review-hacking-forums-and-dark-web-marketplaces), [DarkIQ API brochure](https://slcyber.io/wp-content/uploads/2023/07/Dark-web-monitoring-for-the-finance-industry-Searchlight-Cyber.pdf) | Historical interface and source evidence. Confirm current offerings and coverage rather than relying on the 2023 brochure. |
| Intelligence X | [API bucket documentation](https://blog.intelx.io/2022/05/05/list-of-buckets/) | Tor/I2P/public-web categories are useful discovery context; this 2022 document provides no verified current named-forum list. |
| Bitsight / Cybersixgill | [Current adversary-intelligence product](https://www.bitsight.com/products/adversary-intelligence), [Cybersixgill transition](https://www.bitsight.com/cybersixgill) | Broad forum and API claims; no named current collection inventory established in the reviewed material. |

Flare also documents an [authenticated data MCP](https://api.docs.flare.io/sdk/api-mcp). Its separate documentation MCP only answers API documentation questions. Neither is connected to dark-mcp by this update. The [Collection Overview](https://docs.flare.io/collection-overview) explains where customers can inspect source status, intended crawl frequency and limited section coverage. That source-level export is a better validation artifact than a vendor-wide source count.

## Forum comparison

“Existing” below means the registry has a candidate onion locator from the 18 October access audit. It does not mean full collection works. “New lead” means a metadata-only record; there is no verified collection locator or new scraper. Priorities are our judgment for company exposure research.

| Forum family | Vendor evidence reviewed | Registry action and priority |
| --- | --- | --- |
| Exploit | Flare, DarkOwl, KELA, SOCRadar, Intel 471, Searchlight | Existing; high. Prioritize company access listings and relevant exploit discussions. |
| XSS | Same six providers | Existing; high. Preserve incarnation and migration uncertainty. |
| DarkForums | Flare, DarkOwl, KELA, SOCRadar | Existing; high. Follow original company claims and sellers. |
| ReHub | DarkOwl | Existing; high. Validate successor and migration leads. |
| Dread | Flare | Existing; medium. Broad community and migration context. |
| Exilio404 | KELA | Existing; medium. Portuguese-language coverage. |
| RAMP | Flare, KELA, Searchlight; newer disruption reports | Existing; historical. Do not present its old locator as a confirmed active forum. |
| DamageLib | Flare collection example; KELA and Intel 471 observations | New lead; high. Track technical discussion separately from XSS. |
| BHF / Best Hack Forum | Flare collection example; KELA and SOCRadar | New lead; high. Credential and tooling discussion coverage. |
| BreachForums / claimed successors | DarkOwl, Searchlight | New lead; high. Resolve each incarnation separately. |
| Duty Free | Flare collection example | New lead; medium. Relevant targeting announcements and recruitment. |
| Lolzteam / Zelenka | Flare collection example | New lead; medium. Prioritize company-related accounts over general marketplace noise. |
| Hackforums | Flare collection example | New lead; medium. Relevant tooling and company mentions. |
| HydraForums | Flare collection example; DarkOwl | New lead; medium. Resolve identity before collecting. |
| XForums | DarkOwl recommendation | New lead; medium. Validate smaller migration destinations. |
| DarkNetArmy Forums | Flare collection example | New lead; medium. Published onion locator is invalid; do not use it. |
| Cracking.org | Flare collection example | New lead; medium. Vendor naming is inconsistent; do not merge with Cracked. |
| Cracked | SOCRadar; related mention in Flare | New lead; medium. Validate any post-seizure identity. |
| Niflheim | KELA | New lead; low. Single reviewed vendor lead; verify identity and relevance. |
| Altenen | Flare collection example; KELA | New lead; low for this SaaS scope. Financial-fraud context. |
| WWH-Club | Flare collection example | New lead; low for this SaaS scope. Financial-fraud context. |
| LeakBase | Flare collection example; KELA and DarkOwl; official disruption report | New lead; historical. Archive correlation and successor research. |
| Nulled | Contextual references in Flare and SOCRadar | New lead; historical. Related mention does not prove vendor collection. |

The source JSON classifies each vendor statement separately. In particular, Flare's named event charts cover periods in **2024–2025**. They are stronger evidence than a generic blog mention, but are not proof of live collection in October 2026.

## Freshness and identity problems found

- **RAMP:** [ZeroFox's January 2026 report](https://www.zerofox.com/intelligence/flash-report-fbi-seizes-dark-web-forum-ramp/) and [DarkOwl's March follow-up](https://www.darkowl.com/blog-content/after-ramp-what-comes-next-for-the-ransomware-underground/) describe the January 28 seizure. Retain the historical access failure and the separately dated disruption context; neither establishes whether a later same-name service is authentic.
- **LeakBase:** the [Belgian Federal Prosecutor's March 5 release](https://www.om-mp.be/sites/default/files/media/documents/Persbericht-05-03-2026.pdf) describes the March 3–4 dismantling operation. Older recommendations must not silently become active-feed claims.
- **DamageLib:** separate it from XSS even where archives were copied. The [KELA investigation](https://www.kelacyber.com/blog/xss-forum-after-takedown-damagelib-emerges/) explicitly retains uncertainty about matching actors. Copied posts are not independent corroboration.
- **BHF and BFD:** these are different names; the existing BFD Forum must not stand in for missing BHF coverage.
- **Cracking.org, Cracked and Nulled:** the [Flare article](https://flare.io/learn/resources/blog/top-cybercrime-forums) mixes names in its prose. Separate records retain the ambiguity instead of asserting shared identities.
- **DarkNetArmy:** that same article prints `darknetarmy.onion`, which is not a valid Tor v3 hostname. It is rejected as a collector target. A plausible forum name or vendor mention cannot substitute for a valid address.
- **HydraForums:** do not conflate it with the historical Hydra marketplace. Likewise, a generic “breaches” onion name does not authenticate a BreachForums incarnation.

## Changes available to an agent

At the vendor-research stage, the registry had **171 records**: 30 onion addresses, 125 Telegram candidates and 16 metadata-only forum leads. Existing onion IDs and original access timestamps remain intact. Nine onion records across seven families gain vendor provenance and, where applicable, disruption context. The 29 metadata-only records include the prior 13 invite leads without locators.

Use `list_sources(kind="forum")` for the new leads, `list_sources(query="Flare")` to discover vendor-linked entries, and `get_source(source_id)` for claim types, dated observations, citations, priority and validation tasks. `get_status` reports collection-method counts separately from record counts. `collect_source` and `start_collection` reject the new leads with `source_access_required`, so a caller cannot mistake them for functioning collectors.

That research snapshot expanded source discovery only. Subsequent native collection work brought the registry to 172 records and implemented three HTTPS forum collectors plus structured Spear, Exilio404 and DarkForums reads over Tor; see the [current collection guide](early-signal-collection.md). The research candidates above are not all functioning collectors. No paid intelligence integration is currently enabled.

## Next collection work

1. **Validate the highest-priority gaps:** DamageLib, BHF and a specific BreachForums incarnation. Resolve addresses from source-owned announcements and corroborating research; record the incarnation, public sections and access state. The 19 October recheck enabled structured DarkForums reads. Exploit and the checked XSS route remained challenged; ReHub failed to connect. Validate deeper permitted access on those routes and record each new check separately.
2. **Support relevant surface-web forums:** add a separately scoped collector for validated public forum hosts, with bounded pagination and the existing evidence/revision model. Keep actual post access distinct from a homepage or login response. A matching Tor mirror may supply the same content, so group mirrors before counting coverage.
3. **Develop operator-owned access:** the current product direction is fully in-house. Establish permitted forum accounts and import exact-origin sessions locally. Prioritize original access-market, buyer and technical discussions. Session support exists, but authenticated live reads remain unverified until an operator supplies access. Competitor API documentation remains research context.
4. **Use identical acceptance queries:** company domains and aliases, a known historical forum post, a recent thread and a negative control. Compare original post text, timestamps, replies, source identity and freshness. Include anonymized access offers where relevant, with any company association marked as an analyst hypothesis. Publication time and local first observation must remain separate; use observation cutoffs to avoid hindsight.
5. **Bring accepted originals into dark-mcp evidence:** retain source post IDs, original URLs, publication dates, local recording times and exact revisions. Match mirrors and copied archives without treating copies as independent corroboration. Report unavailable sources, hidden content and bounded coverage alongside findings.

A commercial provider may supply access that our public collectors lack. Adding its forum names to a registry cannot reproduce its corpus, subscriptions or historical archives.

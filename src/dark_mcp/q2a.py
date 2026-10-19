"""Question2Answer HTML collection: questions, answers and comments; no form submission or scripts."""

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from dark_mcp.errors import DataAccessError
from dark_mcp.forums import reference_links, source_url, thread_identity, timestamp
from dark_mcp.models import ForumPage, ForumPost, classify_page

POSTS = ".qa-q-view, .qa-a-list-item, .qa-c-list-item"
CLASSES = {"qa-q-view", "qa-a-list-item", "qa-c-list-item"}


def owner(tag):
    return tag.find_parent(lambda node: bool(CLASSES.intersection(node.get("class", []))))


def owned(node, selector):
    return next((tag for tag in node.select(selector) if owner(tag) is node), None)


def parse_q2a(source, url, document, max_chars, limit):
    soup = BeautifulSoup(document, "html.parser")
    title = soup.title.get_text(" ", strip=True)[:500] if soup.title else ""
    key = thread_identity(url)
    threads, sections, next_pages, posts = [], [], [], []
    current_start = int(dict(parse_qsl(urlsplit(url).query)).get("start", "0"))
    for a in soup.select(".qa-q-item-title a[href], .qa-category-link[href], .qa-suggest-next a[href]"):
        try:
            target = source_url(source, urljoin(url, a["href"]))
        except DataAccessError:
            continue
        row = {"url": target, "title": a.get_text(" ", strip=True)[:500]}
        if thread_identity(target):
            if len(threads) < 100 and thread_identity(target) not in {thread_identity(t["url"]) for t in threads}:
                threads.append(row)
        elif target != url and len(sections) < 100 and target not in {s["url"] for s in sections}:
            sections.append(row)
    # Only explicit forward pagination; switching from activity to the questions feed is not pagination.
    for a in soup.select("a.qa-page-next[href], a[rel~=next][href]"):
        try:
            target = source_url(source, urljoin(url, a["href"]))
        except DataAccessError:
            continue
        start = int(dict(parse_qsl(urlsplit(target).query)).get("start", "0"))
        same = thread_identity(target) == key if key else urlsplit(target).path == urlsplit(url).path
        if same and start > current_start:
            next_pages.append(target)
    remaining, omitted = max_chars, 0
    for node in soup.select(POSTS) if key else []:
        ident = re.fullmatch(r"([qac])([1-9][0-9]{0,9})", node.get("id", ""))
        if ident is None:
            continue
        kind, pid = ident[1], ident[2]
        question_id = key.removeprefix("q2a:")
        ancestor = node.find_parent(class_="qa-q-view")
        if (kind == "q" and pid != question_id) or (ancestor and ancestor.get("id") != "q" + question_id):
            continue
        body = owned(node, ".qa-post-content, .qa-c-item-content")
        if body is None:
            continue
        if len(posts) >= limit or remaining <= 0:
            omitted += 1
            continue
        published_node = owned(node, "time[itemprop=dateCreated]")
        published, raw = timestamp(published_node)
        author = owned(node, ".qa-user-link")
        parent = owner(node)
        parent_id = re.fullmatch(r"[qac]([1-9][0-9]{0,9})", parent.get("id", "")) if parent else None
        parent_id = parent_id[1] if parent_id else key.removeprefix("q2a:") if kind != "q" else None
        events, edited, raw_edit = [], None, None
        for time in node.select("time[itemprop=dateModified]"):
            if owner(time) is not node:
                continue
            when = time.find_parent("span", class_=re.compile(r"qa-[qac].*-when$"))
            label = when.find_previous_sibling("span") if when else None
            label = label.get_text(" ", strip=True)[:100] if label else "source_modification"
            value, display = timestamp(time)
            events.append({"label": label, "at": value, "raw": display})
            if re.fullmatch(r"editad[ao]|edited", label, re.I):
                edited, raw_edit = value, display
            if len(events) >= 5:
                break
        hidden = bool(body.select(".hidden-content, .hideContent, [data-hide]")) or bool(
            re.search(
                r"(?:conte[uú]do (?:oculto|restrito)|"
                r"(?:entre|fa[çc]a login|cadastre-se).{0,80}para (?:ver|visualizar))",
                body.get_text(" ", strip=True),
                re.I,
            )
        )
        quotes = [{"post_id": None, "text": q.get_text(" ", strip=True)[:500]} for q in body.select("blockquote")[:3]]
        for tag in body.select(
            "script,style,noscript,iframe,svg,[hidden],[style*='display:none'],[style*='display: none']"
        ):
            tag.decompose()
        links = reference_links(body, url)
        text = body.get_text("\n", strip=True)
        cap = min(16000, remaining)
        parts = urlsplit(url)
        # Numeric show links identify a returned post; fragments are unnecessary for direct rechecks.
        post_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode({"show": pid}), ""))
        post_url = source_url(source, post_url)
        posts.append(
            ForumPost(
                id=pid,
                url=post_url,
                author=author.get_text(" ", strip=True) if author else None,
                text=text[:cap],
                published_at=published,
                published_at_raw=raw,
                timestamp_basis="source_datetime" if published else "unresolved_source_text" if raw else None,
                is_thread_root=kind == "q",
                post_number=1 if kind == "q" else None,
                post_type={"q": "question", "a": "answer", "c": "comment"}[kind],
                reply_to_post_id=parent_id,
                source_events=events,
                edited_at=edited,
                edited_at_raw=raw_edit,
                hidden_content=hidden,
                quotes=quotes,
                links=links,
                truncated=len(text) > cap,
            )
        )
        remaining -= len(text[:cap])
    access = classify_page(title, soup.get_text(" ", strip=True))
    if posts:
        access = "content_returned_unverified"
    elif key and access == "content_returned_unverified":
        access = "thread_body_unavailable"
    elif not threads and access == "content_returned_unverified":
        access = "unsupported_forum_structure"
    full_text = "\n\n".join(p.text for p in posts) if posts else soup.get_text(" ", strip=True)
    text = full_text[:max_chars]
    return ForumPage(
        source_id=source["id"],
        url=url,
        title=title,
        text=text[:max_chars],
        page_type="thread" if key else "index",
        thread_key=key,
        posts=posts,
        threads=threads if not key else [],
        sections=sections if not key else [],
        next_page_urls=next_pages[:1],
        access_state=access,
        omitted_posts=omitted,
        truncated=bool(omitted or any(p.truncated for p in posts) or len(full_text) > max_chars),
        published_at=next((p.published_at for p in posts if p.is_thread_root), None),
        transport=source.get("transport", "tor"),
        coverage=(
            "Returned public HTML only. Questions, answers and comments present in the document; "
            "comments loaded separately, hidden material and unvisited pages are not collected. "
            "Answer selection is a source event, not evidence of an edit or corroboration."
        ),
    )

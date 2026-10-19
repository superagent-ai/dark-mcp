"""Bounded public forum reads on catalog hosts, with DNS-pinned HTTPS and structured posts."""

import asyncio
import ipaddress
import re
import socket
import time
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from dark_mcp.errors import DataAccessError
from dark_mcp.forum_sessions import ForumSessions, origin_of
from dark_mcp.models import ForumPage, ForumPost, classify_page, limit_value, parse_date
from dark_mcp.onion import onion_url

USER_AGENT = "dark-mcp/0.15 (+https://github.com/superagent-ai/dark-mcp)"


def forum_url(
    value: str,
    hosts: list[str],
    *,
    robots: bool = False,
    transport: str = "direct_https",
    engine: str | None = None,
    section_paths: list[str] | None = None,
) -> str:
    try:
        if transport == "tor":
            value = onion_url(value)
        parts = urlsplit(value)
        host = parts.hostname or ""
        path = parts.path or "/"
        query = parse_qsl(parts.query, keep_blank_values=True)
        if engine == "q2a":
            question = bool(re.fullmatch(r"/[1-9][0-9]{0,9}(?:/[^/]+)?/?", path))
            read_route = path in (section_paths or []) or question
            query_keys = {"show", "start"} if question else {"start"}
        else:
            read_route = bool(
                re.fullmatch(r"/(?:threads|forums)/[^/]+(?:/(?:page-[0-9]+|post-[0-9]+|unread|latest))?/?", path)
                or re.fullmatch(r"/(?:Thread-|Forum-)[^/]+/?", path)
                or path in {"/showthread.php", "/forumdisplay.php"}
            )
            query_keys = {"page", "tid", "fid", "pid"}
        if (
            (parts.scheme not in {"http", "https"} if transport == "tor" else parts.scheme != "https")
            or len(value) > 4000
            or host not in hosts
            or parts.port not in ({None, 80, 443} if transport == "tor" else {None, 443})
            or parts.username is not None
            or parts.password is not None
            or any(ord(c) < 33 or ord(c) == 127 for c in value)
            or "\\" in value
            or any(segment in {".", ".."} for segment in path.split("/"))
            or len({key for key, _ in query}) != len(query)
            or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|25|2e|2f|5c|7f)", path, re.I)
            or any(k not in query_keys or not re.fullmatch(r"[0-9]{1,10}", v) for k, v in query)
            or re.search(r"/(?:reply|edit|delete|report|react|like|watch|unwatch|create-thread)(?:/|$)", path, re.I)
            or not (path == "/" or (robots and path == "/robots.txt") or read_route)
        ):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise DataAccessError(
            "invalid_forum_url",
            "Use a public forum page on this source's catalog hosts and transport; actions are unsupported",
        ) from exc
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, urlencode(query), ""))


def source_url(source: dict, value: str, *, robots: bool = False) -> str:
    return forum_url(
        value,
        source["allowed_hosts"],
        robots=robots,
        transport=source.get("transport", "direct_https"),
        engine=source.get("forum_engine"),
        section_paths=source.get("section_paths"),
    )


def reference_links(body, base_url: str) -> list[dict]:
    """Preserve body references as untrusted metadata; never follow or download them."""
    links = []
    for anchor in body.select("a[href]"):
        try:
            target = urljoin(base_url, anchor["href"])
            parts = urlsplit(target)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or len(target) > 4000
                or any(ord(c) < 33 or ord(c) == 127 for c in target)
                or "\\" in target
            ):
                continue
            if parts.hostname.endswith(".onion"):
                target = onion_url(target)
            if target not in {link["url"] for link in links}:
                links.append({"url": target, "label": anchor.get_text(" ", strip=True)[:500]})
        except (ValueError, DataAccessError):
            continue
        if len(links) >= 50:
            break
    return links


def expand_public_spoilers(body):
    """Preserve MyBB spoiler text already returned in HTML; do not unlock or fetch gated content."""
    for wrapper in body.select(".spoiler"):
        title = wrapper.find(class_="spoiler_title", recursive=False)
        content = wrapper.find(class_="spoiler_content", recursive=False)
        if title is None or content is None or title.select_one(".spoiler_button") is None:
            continue
        gate_classes = {"hidden-content", "hideContent", "bbCodeBlock--hide"}
        if (
            gate_classes.intersection(content.get("class", []))
            or content.has_attr("data-hide")
            or content.find_parent(class_=list(gate_classes))
            or content.find_parent(attrs={"data-hide": True})
        ):
            continue
        content.unwrap()  # Drop only the collapsed presentation wrapper, retaining the returned text.
        title.decompose()


async def public_addresses(host: str) -> list[str]:
    records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(row[4][0] for row in records))
    if not addresses or any(
        not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in addresses
    ):
        raise DataAccessError(
            "forum_address_denied", "Forum host must resolve exclusively to public internet addresses"
        )
    return sorted(addresses, key=lambda ip: ipaddress.ip_address(ip).version)[:3]


def thread_identity(url: str) -> str | None:
    parts = urlsplit(url)
    question = re.fullmatch(r"/([1-9][0-9]{0,9})(?:/[^/]+)?/?", parts.path)
    if question:
        return "q2a:" + question[1]
    match = re.match(r"(/threads/[^/]+)(?:/|$)", parts.path)
    if match:
        return match[1]
    if parts.path.startswith("/Thread-"):
        return parts.path
    query = dict(parse_qsl(parts.query))
    if parts.path == "/showthread.php" and query.get("tid"):
        return "tid:" + query["tid"]
    return None


def page_number(url: str) -> int:
    match = re.search(r"/page-([0-9]+)", urlsplit(url).path)
    return int(match[1]) if match else int(dict(parse_qsl(urlsplit(url).query)).get("page", "1"))


def timestamp(node) -> tuple[str | None, str | None]:
    if not node:
        return None, None
    raw = node.get("datetime") or node.get("title") or node.get_text(" ", strip=True)
    try:
        value = parse_date(node.get("datetime")) if node.get("datetime") else None
        return value.isoformat() if value else None, raw[:200]
    except ValueError:
        return None, raw[:200]


def mybb_pin_status(row) -> bool | None:
    # Some themes assign forumdisplay_sticky to every row. Only an explicit
    # nearest section heading establishes status; unfamiliar headings stay unknown.
    for sibling in row.previous_siblings:
        if getattr(sibling, "name", None) != "tr":
            continue
        heading = sibling.find("td", class_="trow_sep", recursive=False)
        if heading is not None:
            label = " ".join(heading.get_text(" ", strip=True).casefold().split())
            return (
                True
                if label in {"important threads", "sticky threads"}
                else False
                if label == "normal threads"
                else None
            )
    return None


def thread_listings(source: dict, url: str, soup) -> tuple[list[dict], str, int]:
    """Prefer explicit thread-title rows; a general navigation link carries no listing metadata."""
    candidates = []
    if source["forum_engine"] == "mybb":
        for subject in soup.select('[id^="tid_"]'):
            match = re.fullmatch(r"tid_([1-9][0-9]{0,9})", subject.get("id", ""))
            row = subject.find_parent("tr", class_="inline_row")
            if not match or row is None:
                continue
            pinned = mybb_pin_status(row)
            author = row.select_one(".author a")
            published, raw_published = timestamp(
                row.select_one(".thread-date time") or row.select_one(".thread-date span[title]")
            )
            activity, raw_activity = timestamp(
                row.select_one(".last-poster_data time, .last-poster_data span[title]")
                or row.select_one(".lastpost time, .lastpost span[title]")
            )
            for anchor in subject.select("a[href]"):
                candidates.append(
                    (
                        anchor,
                        {
                            "listing_basis": "mybb_subject_row",
                            "source_thread_id": match[1],
                            "author": author.get_text(" ", strip=True)[:500] if author else None,
                            "published_at": published,
                            "published_at_raw": raw_published,
                            "last_activity_at": activity,
                            "last_activity_at_raw": raw_activity,
                            "pinned": pinned,
                        },
                    )
                )
    elif source["forum_engine"] == "xenforo":
        for row in soup.select(".structItem--thread"):
            group = row.find_parent(class_="structItemContainer-group")
            classes = group.get("class", []) if group else []
            pinned = (
                True
                if "structItemContainer-group--sticky" in classes
                else (False if "js-threadList" in classes else None)
            )
            author = row.select_one(".structItem-cell--main .structItem-minor .username")
            published, raw_published = timestamp(row.select_one(".structItem-startDate time"))
            activity, raw_activity = timestamp(row.select_one("time.structItem-latestDate"))
            explicit_id = next(
                (
                    m[1]
                    for cls in row.get("class", [])
                    if (m := re.fullmatch(r"js-threadListItem-([1-9][0-9]{0,9})", cls))
                ),
                None,
            )
            for anchor in row.select(".structItem-title a[href]"):
                candidates.append(
                    (
                        anchor,
                        {
                            "listing_basis": "xenforo_thread_row",
                            "source_thread_id": explicit_id,
                            "author": (row.get("data-author") or (author.get_text(" ", strip=True) if author else ""))[
                                :500
                            ]
                            or None,
                            "published_at": published,
                            "published_at_raw": raw_published,
                            "last_activity_at": activity,
                            "last_activity_at_raw": raw_activity,
                            "pinned": pinned,
                        },
                    )
                )
    scope = "recognized_thread_rows" if candidates else "unscoped_links"
    if not candidates:
        candidates = [(a, {"listing_basis": "unscoped_link", "pinned": None}) for a in soup.select("a[href]")]
    results, seen = [], set()
    for anchor, metadata in candidates:
        try:
            target = source_url(source, urljoin(url, anchor["href"]))
        except DataAccessError:
            continue
        key, label = thread_identity(target), anchor.get_text(" ", strip=True)
        if (
            not key
            or not label
            or label.startswith("#")
            or label.isdigit()
            or label.casefold()
            in {
                "last post",
                "next",
                "previous",
            }
        ):
            continue
        stable = metadata.get("source_thread_id") or key
        if stable in seen:
            continue
        seen.add(stable)
        results.append({"title": label[:500], "url": target, **metadata})
    return results[:100], scope, max(0, len(results) - 100)


def parse_forum(source: dict, url: str, document: str, max_chars: int, limit: int) -> ForumPage:
    if source["forum_engine"] == "q2a":
        from dark_mcp.q2a import parse_q2a

        return parse_q2a(source, url, document, max_chars, limit)
    soup = BeautifulSoup(document, "html.parser")
    title = soup.title.get_text(" ", strip=True)[:500] if soup.title else ""
    identity = thread_identity(url)
    full_text = soup.get_text(" ", strip=True)
    access = classify_page(title, full_text)
    is_thread = identity is not None
    next_pages, sections = [], []
    for a in soup.select("a[rel~=next], a.pageNav-jump--next, a.pagination_next"):
        try:
            target = source_url(source, urljoin(url, a.get("href", "")))
        except DataAccessError:
            continue
        # Ignore unrelated breadcrumb pagination and only advance the current thread or section.
        same = (
            thread_identity(target) == identity
            if is_thread
            else urlsplit(target).path.split("/page-")[0].rstrip("/")
            == urlsplit(url).path.split("/page-")[0].rstrip("/")
        )
        if not is_thread and urlsplit(target).path == "/forumdisplay.php":
            paginator = a.find_parent(class_="pagination")
            current = paginator.select_one(".pagination_current[href]") if paginator else None
            current_fid = dict(parse_qsl(urlsplit(current["href"]).query)).get("fid") if current else None
            same = bool(current_fid and current_fid == dict(parse_qsl(urlsplit(target).query)).get("fid"))
        if same and page_number(target) > page_number(url) and target not in next_pages:
            next_pages.append(target)
    for a in soup.select("a[href]"):
        try:
            target = source_url(source, urljoin(url, a["href"]))
        except DataAccessError:
            continue
        key = thread_identity(target)
        label = a.get_text(" ", strip=True)
        if not key and label and target != url and urlsplit(target).path != "/showthread.php" and len(sections) < 100:
            if target not in {row["url"] for row in sections}:
                sections.append({"title": label[:500], "url": target})
    threads, listing_scope, omitted_threads = (
        thread_listings(source, url, soup) if not is_thread else ([], "not_applicable", 0)
    )
    if source["forum_engine"] == "xenforo":
        nodes = soup.select("article.message--post")
    else:
        nodes = [node for node in soup.select('[id^="post_"]') if re.fullmatch(r"post_[0-9]+", node.get("id", ""))]
    if not is_thread:
        nodes = []
    posts, remaining, omitted = [], max_chars, 0
    for node in nodes:
        if len(posts) >= limit or remaining <= 0:
            omitted += 1
            continue
        body = node.select_one(".message-body .bbWrapper, .post_body")
        if body is None:
            continue
        pid = node.get("data-content", "").removeprefix("post-") or node.get("id", "").removeprefix("post_")
        if not pid.isdigit():
            continue
        author_node = node.select_one(
            ".message-name .username, .post-username a, a[data-class=profile_url], "
            ".post_author .post_user-profile a, .post__author .post__user-profile a[href]"
        )
        author = node.get("data-author") or (author_node.get_text(" ", strip=True) if author_node else None)
        date_node = node.select_one(".message-attribution-main time") or node.select_one(".post_date")
        if date_node and source["forum_engine"] == "mybb" and date_node.select_one(".post_edit"):
            # Some themes nest the edit notice inside the publication date. Keep the
            # original displayed date and its tooltip separate from that later event.
            date_node = BeautifulSoup(str(date_node), "html.parser").find()
            for edit in date_node.select(".post_edit"):
                edit.decompose()
        published, raw_date = timestamp(date_node)
        basis = "source_datetime" if published else "unresolved_source_text" if raw_date else None
        hint_node = date_node.select_one("[title]") if date_node else None
        hint = hint_node.get("title", "")[:200] if hint_node else None
        edited, raw_edit = timestamp(node.select_one(".message-lastEdit time, .post_edit time"))
        if source["forum_engine"] == "mybb" and not raw_edit:
            notice = node.select_one(".post_head .post_edit, .post_date .post_edit")
            if notice:
                raw_edit = notice.get_text(" ", strip=True)[:200] or None
        # MyBB exposes a post-specific Unix timestamp in its quote UI metadata. Parse data; never execute scripts.
        if not published and source["forum_engine"] == "mybb":
            epoch = re.search(r"quick_quote\(" + pid + r",\s*'[^']*',\s*([0-9]{10})\)", str(node))
            if epoch:
                published = datetime.fromtimestamp(int(epoch[1]), UTC).isoformat()
                basis = "source_unix_timestamp"
        hidden = bool(body.select(".hidden-content, .bbCodeBlock--hide, .hideContent, [data-hide]"))
        hidden = hidden or bool(
            re.search(
                r"(?:must (?:log ?in|register|reply).{0,80}to (?:view|see|reveal)|"
                r"must be registered (?:for|to) (?:see|view) links|content is hidden|"
                r"to (?:view|see) (?:the |this )?content.{0,80}(?:sign in|log ?in|register))",
                body.get_text(" ", strip=True),
                re.I,
            )
        )
        expand_public_spoilers(body)
        quotes = []
        for quote in body.select("blockquote")[:3]:
            value = quote.get("data-source", "")
            quotes.append(
                {
                    "post_id": value.removeprefix("post:") if value.startswith("post:") else None,
                    "text": quote.get_text(" ", strip=True)[:500],
                }
            )
        for tag in body.select(
            "script, style, noscript, iframe, svg, .signature, [hidden], "
            "[style*='display: none'], [style*='display:none']"
        ):
            tag.decompose()
        links = reference_links(body, url)
        text = body.get_text("\n", strip=True)
        cap = min(16000, remaining)
        permalink = node.select_one(
            ".message-attribution-main a[href], .posturl a[href], .postbit-number a[href], "
            ".post_head > .float_right > strong > a[href], .post_head .float_right a[href], .pagnum a[href]"
        )
        counters = node.select(
            ".postbit-number a, .posturl a, .message-attribution-opposite a, "
            ".post_head > .float_right > strong > a[href], .post_head .float_right a[href], .pagnum a[href]"
        )
        position = node.get("data-position")
        number = int(position) + 1 if position and re.fullmatch(r"[0-9]{1,9}", position) else None
        root = number == 1 if number is not None else None
        # Themes often put scroll/share links before the numbered permalink. Inspect every
        # header candidate; body/quoted links must not become source post-position metadata.
        numbers = set()
        numbered_links = []
        for counter in counters:
            if counter.find_parent(class_=["post_body", "bbWrapper"]):
                continue
            match = re.fullmatch(r"#([1-9][0-9]{0,8})", counter.get_text(strip=True))
            if match:
                numbers.add(int(match[1]))
                if counter.has_attr("href"):
                    numbered_links.append(counter)
        if len(numbers) == 1:
            number = numbers.pop()
            root = number == 1
            permalink = next(iter(numbered_links), permalink)
        elif numbers:
            number, root = None, None  # Conflicting source counters do not establish a position.
        fallback = urlunsplit((urlsplit(url).scheme, urlsplit(url).netloc, identity + f"/post-{pid}", "", ""))
        if source["forum_engine"] == "mybb":
            params = {k: v for k, v in parse_qsl(urlsplit(url).query) if k == "tid"}
            params["pid"] = pid
            fallback = urlunsplit((*urlsplit(url)[:3], urlencode(params), ""))
        try:
            post_url = source_url(source, urljoin(url, permalink["href"]) if permalink else fallback)
        except DataAccessError:
            post_url = fallback
        posts.append(
            ForumPost(
                id=pid,
                url=post_url,
                author=author,
                text=text[:cap],
                published_at=published,
                published_at_raw=raw_date,
                published_at_hint=hint,
                timestamp_basis=basis,
                is_thread_root=root,
                post_number=number,
                links=links,
                edited_at=edited,
                edited_at_raw=raw_edit,
                hidden_content=hidden,
                quotes=quotes,
                truncated=len(text) > cap,
            )
        )
        remaining -= len(text[:cap])
    if posts:
        access = "content_returned_unverified"
    elif is_thread and access == "content_returned_unverified":
        access = "thread_body_unavailable"
    elif not threads and access == "content_returned_unverified":
        access = "unsupported_forum_structure"
    text = "\n\n".join(p.text for p in posts) if posts else full_text[:max_chars]
    return ForumPage(
        source_id=source["id"],
        url=url,
        title=title,
        text=text[:max_chars],
        page_type="thread" if is_thread else "index",
        thread_key=identity,
        posts=posts,
        threads=threads if not is_thread else [],
        thread_listing_scope=listing_scope,
        omitted_threads=omitted_threads,
        sections=sections if not is_thread else [],
        next_page_urls=next_pages[:1],
        access_state=access,
        truncated=bool(omitted or omitted_threads or any(p.truncated for p in posts) or len(text) > max_chars),
        omitted_posts=omitted,
        published_at=next((p.published_at for p in posts if p.is_thread_root is True), None),
        transport=source.get("transport", "direct_https"),
        coverage=(
            "One accessible page. Hidden content and unvisited pages are not collected. "
            "Author names and source claims are unverified."
        ),
    )


class ForumConnector:
    def __init__(self, settings, catalog, onion=None):
        self.settings, self.catalog, self.onion = settings, catalog, onion
        self.semaphore, self.pace_lock = asyncio.Semaphore(2), asyncio.Lock()
        self.last_request, self.robots_cache = {}, {}
        self.sessions = ForumSessions(settings, catalog)

    def source(self, source_id: str) -> dict:
        source = self.catalog.get(source_id)
        if source["collection_method"] != "forum_page":
            raise DataAccessError("source_access_required", "This source has no supported forum collector")
        return source

    def target(self, source: dict, value: str, *, robots=False) -> str:
        try:
            return source_url(source, value, robots=robots)
        except DataAccessError as original:
            parts = urlsplit(value)
            if (
                not robots
                and parts.hostname in source["allowed_hosts"]
                and re.match(r"/(?:login|sign-?in|member\.php)(?:/|$)", parts.path, re.I)
            ):
                state = self.sessions.status(source["id"], origin_of(value))
                if state["state"] != "public":
                    raise DataAccessError(
                        "forum_auth_required", "Source requires sign-in; renew the local operator session"
                    ) from original
            raise

    async def _pace(self, host: str, delay: float = 4):
        async with self.pace_lock:
            remaining = delay - (time.monotonic() - self.last_request.get(host, 0))
            if remaining > 0:
                await asyncio.sleep(remaining)
            self.last_request[host] = time.monotonic()

    async def _request(self, client, source, url, delay=4):
        url = self.target(source, url, robots=True)
        host = urlsplit(url).hostname
        addresses = await public_addresses(host)
        await self._pace(host, delay)
        for number, address in enumerate(addresses):
            # Resolve once, reject all private answers, and connect to that IP. Host/SNI preserve TLS validation.
            authority = f"[{address}]" if ":" in address else address
            parts = urlsplit(url)
            pinned = urlunsplit(("https", authority, parts.path, parts.query, ""))
            try:
                client.cookies.clear()
                async with client.stream(
                    "GET",
                    pinned,
                    headers={"Host": host, **self.sessions.headers(source["id"], url)},
                    extensions={"sni_hostname": host},
                ) as response:
                    if urlsplit(url).path != "/robots.txt":
                        self.sessions.update(source["id"], url, response.headers.get_list("set-cookie"))
                    if response.is_redirect:
                        target = response.headers.get("location")
                        if not target:
                            raise DataAccessError("invalid_redirect", "Forum returned no redirect target")
                        return self.target(source, urljoin(url, target)), None
                    if response.status_code == 429:
                        wait = response.headers.get("retry-after", "60")
                        raise DataAccessError(
                            "forum_rate_limited",
                            "Forum requires a wait",
                            retry_after=int(wait) if wait.isdigit() else 60,
                        )
                    if response.status_code >= 400:
                        raise DataAccessError(
                            f"http_{response.status_code}", f"Forum returned HTTP {response.status_code}"
                        )
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise DataAccessError("unsupported_encoding", "Forum must return uncompressed content")
                    kind = response.headers.get("content-type", "text/html").split(";", 1)[0].lower()
                    if kind not in {"text/html", "text/plain", "application/xhtml+xml"}:
                        raise DataAccessError("unsupported_content", "Only public HTML/text pages are supported")
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        data.extend(chunk)
                        if len(data) > self.settings.max_response_bytes:
                            raise DataAccessError("response_too_large", "Forum page exceeded the response bound")
                    try:
                        return url, data.decode(response.encoding or "utf-8", errors="replace")
                    except LookupError as exc:
                        raise DataAccessError(
                            "unsupported_encoding", "Forum declared an unknown text encoding"
                        ) from exc
            except httpx.RequestError:
                if number == len(addresses) - 1:
                    raise
        raise DataAccessError("forum_unavailable", "No usable public address")

    async def _robots(self, client, source, url):
        host = urlsplit(url).hostname
        cached = self.robots_cache.get(host)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        parser = RobotFileParser()
        target = f"https://{host}/robots.txt"
        try:
            for _ in range(6):
                target, text = await self._request(client, source, target)
                if urlsplit(target).path != "/robots.txt":
                    raise DataAccessError("robots_unavailable", "Forum robots redirected away from robots.txt")
                if text is not None:
                    parser.parse(text.splitlines())
                    break
            else:
                raise DataAccessError("too_many_redirects", "Forum robots exceeded five redirects")
        except DataAccessError as exc:
            if exc.code == "http_404":
                parser.parse([])
            elif exc.code in {"http_401", "http_403"}:
                parser.parse(["User-agent: *", "Disallow: /"])
            else:
                raise
        self.robots_cache[host] = (time.monotonic() + 300, parser)
        return parser

    async def fetch(self, source_id: str, url: str | None = None, limit: int = 20) -> ForumPage:
        limit_value(limit)
        source = self.source(source_id)
        target = self.target(source, url or source["locator"])
        self.sessions.context(source_id, target)  # Expired configured sessions fail before network access.
        if source.get("transport") == "tor":
            if self.onion is None:
                raise DataAccessError("tor_unavailable", "Tor forum transport is unavailable")
            access_context = {}

            def headers_for(value):
                access_context.clear()
                access_context.update(self.sessions.context(source_id, value))
                return self.sessions.headers(source_id, value)

            target, document, _ = await self.onion.read_document(
                target,
                validate=lambda value: self.target(source, value, robots=urlsplit(value).path == "/robots.txt"),
                headers_for=headers_for,
                on_response=lambda value, cookies: self.sessions.update(source_id, value, cookies),
            )
            page = parse_forum(source, target, document, self.settings.max_text_chars, limit)
            page.access_context = access_context
            return page
        async with self.semaphore:
            try:
                async with asyncio.timeout(self.settings.request_timeout):
                    async with httpx.AsyncClient(
                        trust_env=False,
                        follow_redirects=False,
                        timeout=self.settings.request_timeout,
                        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                        limits=httpx.Limits(max_keepalive_connections=0),
                    ) as client:
                        for _ in range(6):
                            rules = await self._robots(client, source, target)
                            if not rules.can_fetch(USER_AGENT, target):
                                raise DataAccessError("robots_denied", "Source robots.txt disallows this path")
                            delay = max(4, rules.crawl_delay(USER_AGENT) or rules.crawl_delay("*") or 4)
                            rate = rules.request_rate(USER_AGENT) or rules.request_rate("*")
                            if rate and rate.requests > 0:
                                delay = max(delay, rate.seconds / rate.requests)
                            access_context = self.sessions.context(source_id, target)
                            target, document = await self._request(client, source, target, delay)
                            if document is not None:
                                page = parse_forum(source, target, document, self.settings.max_text_chars, limit)
                                page.access_context = access_context
                                return page
                        raise DataAccessError("too_many_redirects", "Forum exceeded five redirects")
            except (httpx.RequestError, OSError, TimeoutError) as exc:
                raise DataAccessError("forum_unavailable", "Public forum connection failed or timed out") from exc

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
from dark_mcp.models import ForumPage, ForumPost, classify_page, limit_value, parse_date

USER_AGENT = "dark-mcp/0.3 (+https://github.com/superagent-ai/dark-mcp)"


def forum_url(value: str, hosts: list[str], *, robots: bool = False) -> str:
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        path = parts.path or "/"
        query = parse_qsl(parts.query, keep_blank_values=True)
        if (
            parts.scheme != "https"
            or len(value) > 4000
            or host not in hosts
            or parts.port not in {None, 443}
            or parts.username is not None
            or parts.password is not None
            or any(ord(c) < 33 or ord(c) == 127 for c in value)
            or "\\" in value
            or any(segment in {".", ".."} for segment in path.split("/"))
            or len({key for key, _ in query}) != len(query)
            or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|25|2e|2f|5c|7f)", path, re.I)
            or any(k not in {"page", "tid", "fid", "pid"} or not re.fullmatch(r"[0-9]{1,10}", v) for k, v in query)
            or re.search(r"/(?:reply|edit|delete|report|react|like|watch|unwatch|create-thread)(?:/|$)", path, re.I)
            or not (
                path == "/"
                or (robots and path == "/robots.txt")
                or re.fullmatch(r"/(?:threads|forums)/[^/]+(?:/(?:page-[0-9]+|post-[0-9]+|unread|latest))?/?", path)
                or re.fullmatch(r"/(?:Thread-|Forum-)[^/]+/?", path)
                or path in {"/showthread.php", "/forumdisplay.php"}
            )
        ):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise DataAccessError(
            "invalid_forum_url", "Use a public HTTPS forum page on this source's catalog hosts; actions are unsupported"
        ) from exc
    return urlunsplit(("https", host, path, urlencode(query), ""))


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


def parse_forum(source: dict, url: str, document: str, max_chars: int, limit: int) -> ForumPage:
    soup = BeautifulSoup(document, "html.parser")
    title = soup.title.get_text(" ", strip=True)[:500] if soup.title else ""
    hosts = source["allowed_hosts"]
    identity = thread_identity(url)
    full_text = soup.get_text(" ", strip=True)
    access = classify_page(title, full_text)
    is_thread = identity is not None
    next_pages, threads = [], []
    for a in soup.select("a[rel~=next], a.pageNav-jump--next, a.pagination_next"):
        try:
            target = forum_url(urljoin(url, a.get("href", "")), hosts)
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
            target = forum_url(urljoin(url, a["href"]), hosts)
        except DataAccessError:
            continue
        key = thread_identity(target)
        label = a.get_text(" ", strip=True)
        if (
            key
            and label
            and not label.startswith("#")
            and not label.isdigit()
            and label.casefold() not in {"last post", "next", "previous"}
        ):
            if key not in {thread_identity(row["url"]) for row in threads}:
                threads.append({"title": label[:500], "url": target})
        if len(threads) >= 100:
            break
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
        author_node = node.select_one(".message-name .username, .post-username a, a[data-class=profile_url]")
        author = node.get("data-author") or (author_node.get_text(" ", strip=True) if author_node else None)
        date_node = (
            node.select_one(".message-attribution-main time")
            or node.select_one(".post_date span[title]")
            or node.select_one(".post_date")
        )
        published, raw_date = timestamp(date_node)
        edited, raw_edit = timestamp(node.select_one(".message-lastEdit time, .post_edit time"))
        # MyBB exposes a post-specific Unix timestamp in its quote UI metadata. Parse data; never execute scripts.
        if not published and source["forum_engine"] == "mybb":
            epoch = re.search(r"quick_quote\(" + pid + r",\s*'[^']*',\s*([0-9]{10})\)", str(node))
            if epoch:
                published = datetime.fromtimestamp(int(epoch[1]), UTC).isoformat()
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
        text = body.get_text("\n", strip=True)
        cap = min(16000, remaining)
        permalink = node.select_one(".message-attribution-main a[href], .posturl a[href]")
        fallback = urlunsplit(("https", urlsplit(url).hostname, identity + f"/post-{pid}", "", ""))
        if source["forum_engine"] == "mybb":
            params = {k: v for k, v in parse_qsl(urlsplit(url).query) if k == "tid"}
            params["pid"] = pid
            fallback = urlunsplit((*urlsplit(url)[:3], urlencode(params), ""))
        try:
            post_url = forum_url(urljoin(url, permalink["href"]) if permalink else fallback, hosts)
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
        next_page_urls=next_pages[:1],
        access_state=access,
        truncated=bool(omitted or any(p.truncated for p in posts) or len(text) > max_chars),
        omitted_posts=omitted,
        published_at=None,  # The first visible reply is not a thread creation date.
        coverage=(
            "One public page. Hidden content and unvisited pages are not collected. "
            "Author names and source claims are unverified."
        ),
    )


class ForumConnector:
    def __init__(self, settings, catalog):
        self.settings, self.catalog = settings, catalog
        self.semaphore, self.pace_lock = asyncio.Semaphore(2), asyncio.Lock()
        self.last_request, self.robots_cache = {}, {}

    def source(self, source_id: str) -> dict:
        source = self.catalog.get(source_id)
        if source["collection_method"] != "forum_page":
            raise DataAccessError("source_access_required", "This source has no supported public forum collector")
        return source

    async def _pace(self, host: str, delay: float = 4):
        async with self.pace_lock:
            remaining = delay - (time.monotonic() - self.last_request.get(host, 0))
            if remaining > 0:
                await asyncio.sleep(remaining)
            self.last_request[host] = time.monotonic()

    async def _request(self, client, source, url, delay=4):
        url = forum_url(url, source["allowed_hosts"], robots=True)
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
                    "GET", pinned, headers={"Host": host}, extensions={"sni_hostname": host}
                ) as response:
                    if response.is_redirect:
                        target = response.headers.get("location")
                        if not target:
                            raise DataAccessError("invalid_redirect", "Forum returned no redirect target")
                        return forum_url(urljoin(url, target), source["allowed_hosts"], robots=True), None
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
        target = forum_url(url or source["locator"], source["allowed_hosts"])
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
                            target, document = await self._request(client, source, target, delay)
                            if document is not None:
                                return parse_forum(source, target, document, self.settings.max_text_chars, limit)
                        raise DataAccessError("too_many_redirects", "Forum exceeded five redirects")
            except (httpx.RequestError, OSError, TimeoutError) as exc:
                raise DataAccessError("forum_unavailable", "Public forum connection failed or timed out") from exc

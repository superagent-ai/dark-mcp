import asyncio
import base64
import hashlib
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from dark_mcp.config import Settings
from dark_mcp.errors import DataAccessError
from dark_mcp.index import OnionIndex
from dark_mcp.models import OnionPage, classify_page, limit_value, parse_date

USER_AGENT = "dark-mcp/0.4 (+https://github.com/superagent-ai/dark-mcp)"


def onion_url(value: str) -> str:
    """Validate a bare v3 onion host, including its version and checksum."""
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        name, suffix = host.rsplit(".", 1)
        if (
            parts.scheme not in {"http", "https"}
            or suffix != "onion"
            or len(name) != 56
            or parts.username is not None
            or parts.password is not None
            or parts.port not in {None, 80, 443}
            or any(ord(char) < 33 for char in value)
            or "\\" in value
        ):
            raise ValueError
        raw = base64.b32decode(name.upper())
        checksum = hashlib.sha3_256(b".onion checksum" + raw[:32] + raw[-1:]).digest()[:2]
        if raw[-1] != 3 or checksum != raw[32:34]:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise DataAccessError(
            "invalid_onion_url", "Use an HTTP(S) v3 onion URL on port 80 or 443, without credentials"
        ) from exc
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


class OnionConnector:
    def __init__(self, settings: Settings, index: OnionIndex):
        self.settings = settings
        self.index = index
        self.semaphore = asyncio.Semaphore(2)
        self.pace_lock = asyncio.Lock()
        self.last_request: dict[str, float] = {}

    def client(self) -> httpx.AsyncClient:
        if urlsplit(self.settings.tor_proxy).scheme not in {"socks5", "socks5h"}:
            raise DataAccessError("invalid_proxy", "Tor proxy must use socks5:// or socks5h://")
        return httpx.AsyncClient(
            proxy=self.settings.tor_proxy,
            trust_env=False,
            follow_redirects=False,
            timeout=self.settings.request_timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        )

    async def _pace(self, url: str, rules: RobotFileParser) -> None:
        host = urlsplit(url).netloc
        interval = max(1, rules.crawl_delay(USER_AGENT) or rules.crawl_delay("*") or 1)
        rate = rules.request_rate(USER_AGENT) or rules.request_rate("*")
        if rate and rate.requests > 0:
            interval = max(interval, rate.seconds / rate.requests)
        async with self.pace_lock:
            delay = interval - (time.monotonic() - self.last_request.get(host, 0))
            if delay > 0:
                await asyncio.sleep(delay)
            self.last_request[host] = time.monotonic()

    async def _get(
        self, client: httpx.AsyncClient, url: str, *, respect_robots: bool = False, validate=None
    ) -> tuple[str, str, str]:
        url = onion_url(url)
        for _ in range(6):
            if validate:
                url = validate(url)
            if respect_robots:
                rules = await self._robots(client, url, validate=validate)
                if not rules.can_fetch(USER_AGENT, url):
                    raise DataAccessError("robots_denied", "Source robots.txt disallows this path")
                await self._pace(url, rules)
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise DataAccessError("invalid_redirect", "Source returned no redirect target")
                    url = onion_url(urljoin(url, location))
                    continue
                if response.status_code >= 400:
                    raise DataAccessError(
                        f"http_{response.status_code}",
                        f"Onion source returned HTTP {response.status_code}",
                    )
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise DataAccessError("unsupported_encoding", "Source must return uncompressed content")
                kind = response.headers.get("content-type", "text/html").split(";", 1)[0].lower()
                if kind not in {"text/html", "text/plain", "application/xhtml+xml"}:
                    raise DataAccessError("unsupported_content", "Only HTML and plain text are supported")
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > self.settings.max_response_bytes:
                        raise DataAccessError("response_too_large", "Source exceeded the response size limit")
                encoding = response.encoding or "utf-8"
                try:
                    text = data.decode(encoding, errors="replace")
                except LookupError:
                    text = data.decode("utf-8", errors="replace")
                return url, text, kind
        raise DataAccessError("too_many_redirects", "Source exceeded five redirects")

    async def _robots(self, client: httpx.AsyncClient, url: str, validate=None) -> RobotFileParser:
        root = urlsplit(url)
        robots_url = urlunsplit((root.scheme, root.netloc, "/robots.txt", "", ""))
        parser = RobotFileParser()
        try:
            _, text, _ = await self._get(client, robots_url, validate=validate)
        except DataAccessError as exc:
            if exc.code == "http_404":
                parser.parse([])
                return parser
            if exc.code in {"http_401", "http_403"}:
                parser.parse(["User-agent: *", "Disallow: /"])
                return parser
            raise
        parser.parse(text.splitlines())
        return parser

    async def read_document(self, url: str, validate=None) -> tuple[str, str, str]:
        """Read bounded HTML/text through Tor, optionally restricting every redirect and robots URL."""
        url = onion_url(url)
        async with self.semaphore:
            try:
                async with asyncio.timeout(self.settings.request_timeout):
                    async with self.client() as client:
                        return await self._get(client, url, respect_robots=True, validate=validate)
            except (httpx.RequestError, TimeoutError) as exc:
                raise DataAccessError(
                    "tor_unavailable", "Tor request failed or timed out; check the proxy and source availability"
                ) from exc

    async def fetch(self, url: str) -> OnionPage:
        final_url, html, kind = await self.read_document(url)
        soup = BeautifulSoup(html, "html.parser")
        links, next_pages = [], []
        published_at = None
        if kind != "text/plain":
            # Only explicitly typed article metadata; an arbitrary reply's <time> is not page publication time.
            published = soup.find("meta", attrs={"property": "article:published_time"})
            if published and published.get("content"):
                try:
                    published_at = parse_date(published["content"]).isoformat()
                except ValueError:
                    pass
            for anchor in soup.find_all(["a", "link"], href=True):
                label = anchor.get_text(" ", strip=True).casefold()
                if "next" in anchor.get("rel", []) or re.fullmatch(
                    r"next(?: page)?[ »›→]*|pr[oó]xim[oa](?: p[aá]gina)?[ »›→]*|следующая[ »›→]*|下一页[ »›→]*", label
                ):
                    try:
                        target = onion_url(urljoin(final_url, anchor["href"]))
                        if urlsplit(target).hostname == urlsplit(final_url).hostname and target not in next_pages:
                            next_pages.append(target)
                    except DataAccessError:
                        continue
                    if len(next_pages) >= 10:
                        break
            for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
                tag.decompose()
            for anchor in soup.find_all("a", href=True):
                try:
                    link = onion_url(urljoin(final_url, anchor["href"]))
                    if link not in links:
                        links.append(link)
                except DataAccessError:
                    continue
                if len(links) >= 200:
                    break
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            text = soup.get_text(" ", strip=True)
        else:
            title, text = "", html
        page = OnionPage(
            url=final_url,
            title=title[:500],
            text=text[: self.settings.max_text_chars],
            links=links,
            truncated=len(text) > self.settings.max_text_chars,
            next_page_urls=next_pages,
            published_at=published_at,
            access_state=classify_page(title, text),
        )
        self.index.put(page)
        return page

    async def crawl(self, seeds: list[str], max_pages: int = 5) -> dict:
        limit_value(max_pages, 10)
        if not 1 <= len(seeds) <= 10:
            raise ValueError("Provide between one and ten seed URLs")
        queue = list(dict.fromkeys(onion_url(seed) for seed in seeds))
        origins = {urlsplit(url).netloc for url in queue}
        visited: set[str] = set()
        indexed, errors = [], []
        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                page = await self.fetch(url)
                indexed.append(page.url)
                queue.extend(link for link in page.links if urlsplit(link).netloc in origins and link not in visited)
                queue = list(dict.fromkeys(queue))[:100]
            except DataAccessError as exc:
                errors.append({"url": url, **exc.as_dict()["error"]})
        return {
            "indexed": indexed,
            "errors": errors,
            "attempted": len(visited),
            "coverage": "Provided seeds and their same-host links only",
        }

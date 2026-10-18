"""Discovery through an existing onion search index, independently of Tor fetching."""

import asyncio
import json

import httpx

from dark_mcp.config import Settings
from dark_mcp.errors import DataAccessError
from dark_mcp.models import now
from dark_mcp.onion import onion_url
from dark_mcp.telegram import query_value

ENDPOINT = "https://onionsearchengine.com/api.php"


class OnionSearch:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, page: int = 1) -> dict:
        query = query_value(query)
        if not 1 <= page <= 1000:
            raise ValueError("page must be between 1 and 1000")
        if not self.settings.onion_search_api_key:
            raise DataAccessError(
                "search_not_configured",
                "Set DARK_MCP_ONION_SEARCH_API_KEY for Onion Search Engine, or use search_onion_cache",
            )
        try:
            async with asyncio.timeout(self.settings.request_timeout):
                async with httpx.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                    timeout=self.settings.request_timeout,
                ) as client:
                    async with client.stream(
                        "GET",
                        ENDPOINT,
                        params={"q": query, "page": page},
                        headers={
                            "X-API-Key": self.settings.onion_search_api_key.get_secret_value(),
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                        },
                    ) as response:
                        if response.status_code in {401, 403}:
                            raise DataAccessError("search_auth_failed", "Search provider rejected the API key or plan")
                        if response.status_code == 429:
                            raise DataAccessError("search_rate_limited", "Search provider quota or rate limit reached")
                        if response.status_code != 200:
                            raise DataAccessError(
                                "search_unavailable", "Search provider returned an unsuccessful response"
                            )
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise DataAccessError("unsupported_encoding", "Search response must be uncompressed")
                        data = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            data.extend(chunk)
                            if len(data) > self.settings.max_response_bytes:
                                raise DataAccessError("response_too_large", "Search response exceeded the size limit")
                        payload = json.loads(data)
        except (httpx.RequestError, TimeoutError) as exc:
            raise DataAccessError("search_unavailable", "Search provider connection failed or timed out") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise DataAccessError("invalid_search_response", "Search provider returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise DataAccessError("invalid_search_response", "Search provider did not report success")
        rows, pagination = payload.get("results"), payload.get("pagination", {})
        if not isinstance(rows, list) or not isinstance(pagination, dict):
            raise DataAccessError("invalid_search_response", "Search provider returned an unexpected response shape")
        results, omitted = [], 0
        for row in rows[:100]:
            if not isinstance(row, dict) or not isinstance(row.get("url"), str):
                omitted += 1
                continue
            try:
                url = row["url"]
                url = onion_url(url if "://" in url else f"http://{url}")
            except DataAccessError:
                omitted += 1
                continue
            results.append(
                {
                    "url": url,
                    "title": str(row.get("title", ""))[:500],
                    "snippet": str(row.get("context", ""))[:4000],
                }
            )
        total_pages = pagination.get("total_pages")
        return {
            "provider": "onionsearchengine.com",
            "query": query,
            "results": results,
            "page": page,
            "next_page": page + 1 if isinstance(total_pages, int) and page < total_pages else None,
            "total_results": pagination.get("total_results"),
            "omitted_invalid_results": omitted + max(0, len(rows) - 100),
            "collected_at": now(),
            "coverage": "Provider index only; freshness and completeness are not independently verified",
            "published_at": None,
        }

import httpx
import pytest
import respx
from pydantic import SecretStr

from dark_mcp.errors import DataAccessError
from dark_mcp.search import ENDPOINT, OnionSearch


async def test_missing_key_is_explicit(settings):
    with pytest.raises(DataAccessError) as exc:
        await OnionSearch(settings).search("Acme")
    assert exc.value.code == "search_not_configured"


@respx.mock
async def test_provider_contract_and_pagination(settings, onion):
    settings.onion_search_api_key = SecretStr("test-key")
    route = respx.get(ENDPOINT, params={"q": "acme.com", "page": 2}).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "pagination": {"total_results": 30, "total_pages": 3},
                "results": [
                    {"url": onion, "title": "Claim", "context": "Acme data"},
                    {"url": "http://localhost/", "title": "Unsafe"},
                ],
            },
        )
    )
    result = await OnionSearch(settings).search("acme.com", page=2)
    assert route.calls[0].request.headers["X-API-Key"] == "test-key"
    assert result["results"] == [{"url": onion, "title": "Claim", "snippet": "Acme data"}]
    assert result["next_page"] == 3
    assert result["omitted_invalid_results"] == 1
    assert result["published_at"] is None


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {}, "search_auth_failed"),
        (429, {}, "search_rate_limited"),
        (200, [], "invalid_search_response"),
        (200, {"status": "success", "results": None}, "invalid_search_response"),
        (302, {}, "search_unavailable"),
    ],
)
@respx.mock
async def test_errors_are_sanitized(settings, status, body, code):
    settings.onion_search_api_key = SecretStr("never-show-this")
    respx.get(ENDPOINT).mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(DataAccessError) as exc:
        await OnionSearch(settings).search("Acme")
    assert exc.value.code == code
    assert "never-show-this" not in str(exc.value)

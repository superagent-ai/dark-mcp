from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from dark_mcp.errors import DataAccessError
from dark_mcp.evidence import EvidenceStore
from dark_mcp.forums import ForumConnector
from dark_mcp.index import OnionIndex
from dark_mcp.onion import OnionConnector
from dark_mcp.robots import RobotsRules
from dark_mcp.sources import SourceCatalog
from dark_mcp.telegram_web import TelegramWeb


def rules(text):
    result = RobotsRules()
    result.parse(text.splitlines())
    return result


def test_blank_lines_comments_and_bom_do_not_discard_a_group():
    policy = rules("\ufeffUser-agent: *\n\n# explanation\nDisallow: /search/\n\nDisallow: /members/\nAllow: /")
    assert not policy.can_fetch("dark-mcp/0.27", "https://example.test/search/")
    assert not policy.can_fetch("dark-mcp", "https://example.test/members/test")
    assert policy.can_fetch("dark-mcp", "https://example.test/threads/example.1/")


def test_longest_match_allow_tie_wildcards_and_query_end_anchors():
    policy = rules(
        "User-agent: *\nDisallow: /\nAllow: /public/\nDisallow: /public/private/\n"
        "Allow: /public/private/help\nDisallow: /public/private/help\nDisallow: /*?download=*\n"
        "Disallow: /public/search$"
    )
    assert policy.can_fetch("dark-mcp", "https://example.test/public/list")
    assert not policy.can_fetch("dark-mcp", "https://example.test/public/private/file")
    assert policy.can_fetch("dark-mcp", "https://example.test/public/private/help")
    assert not policy.can_fetch("dark-mcp", "https://example.test/public/item?download=1")
    assert not policy.can_fetch("dark-mcp", "https://example.test/public/search")
    assert policy.can_fetch("dark-mcp", "https://example.test/public/searching")
    assert policy.can_fetch("dark-mcp", "https://example.test/robots.txt")


def test_matching_agent_groups_merge_without_inheriting_wildcard_group():
    policy = rules(
        "User-agent: *\nDisallow: /\nUser-agent: dark-mcp\nDisallow: /search/\n"
        "User-agent: DARK-MCP\nDisallow: /private/\nUser-agent: unrelated\nDisallow: /public/"
    )
    assert not policy.can_fetch("dark-mcp/0.27", "https://example.test/search/")
    assert not policy.can_fetch("dark-mcp/0.27", "https://example.test/private/")
    assert policy.can_fetch("dark-mcp/0.27", "https://example.test/public/")
    assert not policy.can_fetch("other-bot", "https://example.test/public/")


def test_encoded_paths_empty_rules_fractional_delays_and_request_rates():
    policy = rules(
        "User-agent: *\nDisallow:\nDisallow: /caf%C3%A9\nDisallow: /~private\n"
        "Disallow: /a%2Fb\nCrawl-delay: 0.5\nRequest-rate: 5/10"
    )
    assert not policy.can_fetch("dark-mcp", "https://example.test/café")
    assert not policy.can_fetch("dark-mcp", "https://example.test/%7Eprivate")
    assert not policy.can_fetch("dark-mcp", "https://example.test/a%2Fb")
    assert policy.can_fetch("dark-mcp", "https://example.test/a/b")
    assert policy.can_fetch("dark-mcp", "https://example.test/other")
    assert policy.crawl_delay("dark-mcp") == 0.5
    rate = policy.request_rate("dark-mcp")
    assert rate.requests == 5 and rate.seconds == 10
    assert rules("").can_fetch("dark-mcp", "https://example.test/")


@respx.mock
async def test_https_realistic_blank_line_policy_stops_before_content_request(settings, monkeypatch):
    catalog = SourceCatalog()
    forums = ForumConnector(settings, catalog)
    monkeypatch.setattr("dark_mcp.forums.public_addresses", AsyncMock(return_value=["8.8.8.8"]))
    monkeypatch.setattr(forums, "_pace", AsyncMock())
    respx.get("https://8.8.8.8/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\n\nDisallow: /threads/\n\nAllow: /")
    )
    target = respx.get("https://8.8.8.8/threads/example.1/").mock(
        return_value=httpx.Response(200, text="should not read")
    )
    with pytest.raises(DataAccessError) as error:
        await forums.fetch_document("forum:niflheim", "https://niflheim.world/threads/example.1/")
    assert error.value.code == "robots_denied" and target.call_count == 0


async def test_tor_wildcard_policy_stops_before_feed_or_search_request(settings, monkeypatch, onion):
    requested = []

    async def handler(request):
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\n\nDisallow: /syndication.php*\nDisallow: /*action=search*")
        return httpx.Response(200, text="should not read")

    connector = OnionConnector(settings, OnionIndex(settings.prepare_data_dir()))
    monkeypatch.setattr(connector, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    for path in ["syndication.php?fid=82", "index.php?action=search&query=Acme"]:
        with pytest.raises(DataAccessError) as error:
            await connector.read_document(onion + path)
        assert error.value.code == "robots_denied"
    assert requested == ["/robots.txt", "/robots.txt"]


@respx.mock
async def test_public_telegram_web_shares_blank_line_and_wildcard_guard(settings, monkeypatch):
    settings.telegram_web_enabled = True
    settings.telegram_web_channels = ["example_channel"]
    reader = TelegramWeb(settings, SourceCatalog(settings), EvidenceStore(settings.prepare_data_dir()))
    monkeypatch.setattr(reader, "_pace", AsyncMock())
    respx.get("https://t.me/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\n\nDisallow: /s/*"))
    content = respx.get("https://t.me/s/example_channel").mock(return_value=httpx.Response(200, text="should not read"))
    with pytest.raises(DataAccessError) as error:
        await reader.read("example_channel")
    assert error.value.code == "robots_denied" and content.call_count == 0

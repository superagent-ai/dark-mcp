"""Shared robots matching; transports retain responsibility for fetching, limits and pacing."""

from urllib.parse import urlsplit

from protego import Protego


class RobotsRules:
    """Keep the collectors' API while using modern group, wildcard and specificity rules."""

    def __init__(self):
        self.rules = Protego.parse("")

    def parse(self, lines):
        self.rules = Protego.parse("\n".join(lines).lstrip("\ufeff"))

    def can_fetch(self, user_agent, url):
        parts = urlsplit(url)
        if parts.path == "/robots.txt" and not parts.query:
            return True
        return self.rules.can_fetch(url, user_agent)

    def crawl_delay(self, user_agent):
        return self.rules.crawl_delay(user_agent)

    def request_rate(self, user_agent):
        return self.rules.request_rate(user_agent)

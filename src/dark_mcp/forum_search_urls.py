"""Exact configured MyBB search requests; separate from ordinary thread and action routes."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dark_mcp.errors import DataAccessError


def search_query(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Use a search query of 3–200 characters")
    value = value.strip()
    if not 3 <= len(value) <= 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Use a search query of 3–200 characters without control characters")
    return value


def search_config(source: dict) -> dict:
    config = source.get("native_search", {})
    if source.get("forum_engine") != "mybb" or config.get("engine") != "mybb_get":
        raise DataAccessError("forum_search_not_configured", "No verified native search route is configured")
    return config


def search_url(source: dict, value: str, context: dict) -> str:
    from dark_mcp.forums import source_url

    config = search_config(source)
    try:
        parts, endpoint = urlsplit(value), urlsplit(config["endpoint"])
        source_url(source, urlunsplit((parts.scheme, parts.netloc, "/", "", "")))
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        query = dict(pairs)
        if (
            parts.path != "/search.php"
            or (parts.scheme, parts.netloc.lower(), parts.path)
            != (endpoint.scheme, endpoint.netloc.lower(), endpoint.path)
            or parts.fragment
            or len(value) > 4000
            or "\\" in value
            or any(ord(c) < 33 or ord(c) == 127 for c in value)
            or len(query) != len(pairs)
        ):
            raise ValueError
        if query.get("action") == "do_search":
            expected = {
                "action": "do_search",
                "keywords": search_query(context["query"]),
                "postthread": "1",
                "showresults": "posts",
                "sortby": "lastpost",
                "sortordr": "desc",
            }
            if query != expected or context.get("sid"):
                raise ValueError
        elif query.get("action") == "results":
            sid = query.get("sid", "")
            if (
                not re.fullmatch(r"[0-9a-f]{32}", sid)
                or (context.get("sid") and context["sid"] != sid)
                or set(query) - {"action", "sid", "sortby", "order", "page", "uid"}
                or query.get("uid", "0") != "0"
                or query.get("sortby", "lastpost") not in {"lastpost", "dateline"}
                or query.get("order", "desc") != "desc"
                or not re.fullmatch(r"[1-9][0-9]{0,5}", query.get("page", "1"))
            ):
                raise ValueError
            context.setdefault("sid", sid)
        else:
            raise ValueError
    except (KeyError, ValueError, TypeError) as exc:
        raise DataAccessError(
            "invalid_forum_search_url", "Use only this search's configured native read routes"
        ) from exc
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(pairs), ""))


def initial_search_url(source: dict, query: str) -> str:
    config = search_config(source)
    target = (
        config["endpoint"]
        + "?"
        + urlencode(
            {
                "action": "do_search",
                "keywords": search_query(query),
                "postthread": "1",
                "showresults": "posts",
                "sortby": "lastpost",
                "sortordr": "desc",
            }
        )
    )
    return search_url(source, target, {"query": query})

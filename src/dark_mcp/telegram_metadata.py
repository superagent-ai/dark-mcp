"""Bounded Telegram attribution metadata; never resolve peers or follow references."""

from telethon import types

from dark_mcp.references import reference_host


def message_links(text: str, entities) -> tuple[list[dict], dict]:
    """Offsets refer to UTF-16 units of the retained text, not Python character indices."""
    entities = entities or []
    encoded = text.encode("utf-16-le")
    links, omitted = [], 0
    for entity in entities[:200]:
        if not isinstance(entity, (types.MessageEntityUrl, types.MessageEntityTextUrl)):
            continue
        start, length = entity.offset, entity.length
        if start < 0 or length <= 0 or (start + length) * 2 > len(encoded):
            omitted += 1
            continue
        try:
            label = encoded[start * 2 : (start + length) * 2].decode("utf-16-le")
        except UnicodeDecodeError:
            omitted += 1
            continue
        hidden = isinstance(entity, types.MessageEntityTextUrl)
        url = entity.url if hidden else label
        if reference_host(url) is None:
            omitted += 1
            continue
        links.append(
            {
                "url": url,
                "label": label[:500],
                "basis": "telegram_text_url_entity" if hidden else "telegram_url_entity",
                "offset": start,
                "length": length,
                "offset_unit": "utf16_code_units",
            }
        )
    return links, {
        "basis": "telegram_message_entities",
        "entities_total": len(entities),
        "entities_examined": min(200, len(entities)),
        "entities_truncated": len(entities) > 200,
        "url_entities_omitted": omitted,
        "links_captured": len(links),
        "entity_limit": 200,
        "text_scope": "retained_message_text",
    }


def _peer(peer) -> dict | None:
    for cls, kind, field in (
        (types.PeerChannel, "channel", "channel_id"),
        (types.PeerChat, "chat", "chat_id"),
        (types.PeerUser, "user", "user_id"),
    ):
        if isinstance(peer, cls) and (identifier := getattr(peer, field)) > 0:
            return {"kind": kind, "id": identifier}
    return None


def _date(value) -> str | None:
    return value.isoformat() if value else None


def _name(value) -> str | None:
    return value[:500] if isinstance(value, str) else None


def forward_attribution(header) -> dict | None:
    if header is None:
        return None
    peer = _peer(getattr(header, "from_id", None))
    post = getattr(header, "channel_post", None)
    post = post if isinstance(post, int) and post > 0 else None
    imported = bool(getattr(header, "imported", False))
    # Imported foreign history is not evidence of an original Telegram permalink.
    url = (
        f"https://t.me/c/{peer['id']}/{post}" if peer and peer["kind"] == "channel" and post and not imported else None
    )
    return {
        "basis": "telegram_forward_header",
        "origin_verified": False,
        "origin_fetched": False,
        "origin_peer": peer,
        "origin_name": _name(getattr(header, "from_name", None)),
        "origin_published_at": _date(getattr(header, "date", None)),
        "origin_channel_post_id": post,
        "origin_url": url,
        "origin_url_basis": "numeric_channel_header_candidate" if url else None,
        "post_author": _name(getattr(header, "post_author", None)),
        "imported": imported,
        "saved_context": {
            "peer": _peer(getattr(header, "saved_from_peer", None)),
            "message_id": getattr(header, "saved_from_msg_id", None),
            "sender_peer": _peer(getattr(header, "saved_from_id", None)),
            "sender_name": _name(getattr(header, "saved_from_name", None)),
            "date": _date(getattr(header, "saved_date", None)),
        },
    }

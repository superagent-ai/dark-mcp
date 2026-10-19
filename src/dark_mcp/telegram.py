import asyncio
import re
from contextlib import asynccontextmanager, suppress

from telethon import TelegramClient, errors, functions, types

from dark_mcp.config import Settings
from dark_mcp.errors import DataAccessError
from dark_mcp.models import limit_value, now, parse_date
from dark_mcp.sources import SourceCatalog
from dark_mcp.telegram_metadata import forward_attribution, message_links


def channel_key(value: str) -> str:
    value = value.strip().removeprefix("https://t.me/").removeprefix("@").rstrip("/")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}|-100[0-9]+", value):
        raise ValueError("Use a channel username or a -100-prefixed channel ID")
    return value.lower()


def query_value(query: str) -> str:
    query = query.strip()
    if not query or len(query) > 500:
        raise ValueError("Use a nonempty query of at most 500 characters")
    return query


class TelegramConnector:
    def __init__(self, settings: Settings, catalog: SourceCatalog | None = None, store=None):
        self.settings = settings
        self.lock = asyncio.Lock()
        self.catalog = catalog
        self.store = store

    def configured(self) -> bool:
        return bool(
            self.settings.telegram_enabled
            and self.settings.telegram_api_id
            and self.settings.telegram_api_hash
            and self.settings.session_path.is_file()
        )

    def _check(self, *, global_search: bool = False) -> None:
        if not self.settings.telegram_enabled:
            raise DataAccessError(
                "telegram_disabled", "Enable Telegram after arranging permission for the intended data use"
            )
        if not self.configured():
            raise DataAccessError(
                "telegram_not_authenticated",
                "Configure Telegram API credentials and run dark-mcp telegram-login locally",
            )
        if global_search and not self.settings.telegram_global_search:
            raise DataAccessError(
                "global_search_disabled", "Global Telegram search is disabled in the server configuration"
            )

    @asynccontextmanager
    async def session(self, *, global_search: bool = False):
        self._check(global_search=global_search)
        async with self.lock:
            if self.store is None:
                from dark_mcp.evidence import EvidenceStore

                self.store = EvidenceStore(self.settings.prepare_data_dir())
            token = self.store.claim_connector("telegram", self.settings.request_timeout + 15)
            client = None
            try:
                client = TelegramClient(
                    str(self.settings.session_path),
                    self.settings.telegram_api_id,
                    self.settings.telegram_api_hash.get_secret_value(),
                    flood_sleep_threshold=0,
                    request_retries=0,
                    connection_retries=1,
                    receive_updates=False,
                    timeout=10,
                )
                async with asyncio.timeout(self.settings.request_timeout):
                    await client.connect()
                    if not await client.is_user_authorized():
                        raise DataAccessError(
                            "telegram_not_authenticated", "Session expired; run dark-mcp telegram-login locally"
                        )
                    yield client
            except (errors.FloodWaitError, errors.FloodPremiumWaitError) as exc:
                error = DataAccessError(
                    "telegram_rate_limited",
                    "Telegram requires a wait before retrying",
                    retry_after=exc.seconds,
                )
                # Persist the provider deadline while still holding the shared lease,
                # before disconnect/release lets any queued caller approach the account.
                self.store.record_retry("telegram", error)
                raise error from exc
            except errors.RPCError as exc:
                raise DataAccessError(
                    "telegram_rpc_error", f"Telegram rejected the request ({type(exc).__name__})"
                ) from exc
            except (TimeoutError, ConnectionError, OSError) as exc:
                raise DataAccessError("telegram_unavailable", "Telegram connection failed or timed out") from exc
            finally:
                try:
                    if client is not None:
                        with suppress(Exception):
                            async with asyncio.timeout(5):
                                await client.disconnect()
                finally:
                    self.store.release_connector("telegram", token)

    def _allowed_channel(self, channel: str) -> str | int:
        key = channel_key(channel)
        allowed = {channel_key(item) for item in self.settings.telegram_channels}
        if self.settings.telegram_catalog_sources:
            allowed.update((self.catalog or SourceCatalog()).telegram_targets())
        if key not in allowed:
            raise DataAccessError(
                "channel_not_allowed",
                "Add this channel to DARK_MCP_TELEGRAM_CHANNELS or enable DARK_MCP_TELEGRAM_CATALOG_SOURCES",
            )
        return int(key) if key.startswith("-100") else key

    @staticmethod
    async def _entity(client: TelegramClient, channel: str | int):
        try:
            entity = await client.get_entity(channel)
        except ValueError as exc:
            raise DataAccessError("channel_not_found", "Channel could not be resolved by this session") from exc
        if not isinstance(entity, types.Channel) or not entity.broadcast:
            raise DataAccessError("not_a_channel", "Only broadcast channels are supported")
        return entity

    @staticmethod
    def _username(entity) -> str | None:
        return entity.username or next(
            (item.username for item in (getattr(entity, "usernames", None) or []) if item.active), None
        )

    @staticmethod
    def _channel(entity) -> dict:
        username = TelegramConnector._username(entity)
        return {
            "id": f"-100{entity.id}",
            "title": entity.title,
            "username": username,
            "url": f"https://t.me/{username}" if username else None,
        }

    @staticmethod
    def _message(message, entity) -> dict:
        text = message.message or ""
        peer_id = getattr(getattr(message, "peer_id", None), "channel_id", entity.id)
        channel = TelegramConnector._channel(entity)
        if peer_id != entity.id:
            channel = {"id": f"-100{peer_id}", "title": None, "username": None, "url": None}
        path = channel["username"] or f"c/{peer_id}"
        edited_at = getattr(message, "edit_date", None)
        reply = getattr(message, "reply_to", None)
        links, link_capture = message_links(text[:16000], getattr(message, "entities", None))
        return {
            "id": message.id,
            "channel": channel,
            "text": text[:16000],
            "truncated": len(text) > 16000,
            "published_at": message.date.isoformat() if message.date else None,
            "url": f"https://t.me/{path}/{message.id}",
            "has_media": message.media is not None,
            "edited_at": edited_at.isoformat() if edited_at else None,
            "reply_to_message_id": getattr(reply, "reply_to_msg_id", None),
            "links": links,
            "link_capture": link_capture,
            "forward": forward_attribution(getattr(message, "fwd_from", None)),
            "acquisition": {"method": "telegram_mtproto", "network_read": True, "source_identity_verified": False},
        }

    async def channels(self, query: str, limit: int = 10) -> dict:
        query, limit = query_value(query), limit_value(limit, 50)
        async with self.session(global_search=True) as client:
            response = await client(functions.contacts.SearchRequest(q=query, limit=limit, broadcasts=True))
            items = [
                self._channel(chat) for chat in response.chats if isinstance(chat, types.Channel) and chat.broadcast
            ][:limit]
        return {"results": items, "coverage": "Telegram channel discovery; not exhaustive", "collected_at": now()}

    async def messages(
        self,
        channel: str,
        limit: int = 20,
        before_id: int = 0,
        query: str | None = None,
        since: str | None = None,
        after_id: int = 0,
        oldest_first: bool = False,
        thread_id: int | None = None,
    ) -> dict:
        self._check()
        target = self._allowed_channel(channel)
        limit_value(limit)
        if before_id < 0 or after_id < 0 or (before_id and (after_id or oldest_first)):
            raise ValueError("before_id must be zero or a positive message ID")
        if thread_id is not None and (thread_id <= 0 or query is not None):
            raise ValueError("Thread reads require a positive root ID; Telegram cannot search within replies")
        date = parse_date(since)
        if query is not None:
            query = query_value(query)
        async with self.session() as client:
            entity = await self._entity(client, target)
            items = []
            scanned, scanned_ids, reached_since = 0, [], False
            options = {"limit": limit, "offset_id": before_id, "search": query}
            if after_id or oldest_first:
                options.update(min_id=after_id, reverse=True)
            if thread_id is not None:
                options["reply_to"] = thread_id
            async for message in client.iter_messages(entity, **options):
                if scanned >= limit:
                    break
                scanned += 1
                scanned_ids.append(message.id)
                if date and message.date and message.date < date:
                    if after_id or oldest_first:
                        continue
                    reached_since = True
                    break
                items.append(self._message(message, entity))
        return {
            "results": items,
            "channel": self._channel(entity),
            "next_before_id": min(scanned_ids) if scanned == limit and not reached_since and not oldest_first else None,
            "next_after_id": max(scanned_ids) if scanned_ids and (after_id or oldest_first) else None,
            "page_full": scanned == limit and not reached_since,
            "scanned_count": scanned,
            "since_boundary_reached": reached_since,
            "thread_root": {"channel": self._channel(entity), "message_id": thread_id} if thread_id else None,
            "coverage": "Selected channel as accessible to the configured account",
            "collected_at": now(),
        }

    async def message(self, channel: str, message_id: int) -> dict:
        """Directly recheck a known post; absence is not proof of deletion."""
        self._check()
        target = self._allowed_channel(channel)
        if message_id <= 0:
            raise ValueError("Use a positive message ID")
        async with self.session() as client:
            entity = await self._entity(client, target)
            message = await client.get_messages(entity, ids=message_id)
            result = self._message(message, entity) if message and not isinstance(message, types.MessageEmpty) else None
        return {
            "results": [result] if result else [],
            "channel": self._channel(entity),
            "collected_at": now(),
            "coverage": "Direct post lookup; absence can indicate deletion or changed access",
        }

    async def changes(
        self, channel: str, pts: int | None = None, expected_id: str | None = None, limit: int = 100
    ) -> dict:
        """Read explicit MTProto channel updates. An unavailable difference is not inferred as deletion."""
        self._check()
        target = self._allowed_channel(channel)
        limit_value(limit)
        async with self.session() as client:
            entity = await self._entity(client, target)
            identity = self._channel(entity)
            if pts is not None and expected_id != identity["id"]:
                raise DataAccessError("source_identity_changed", "Channel identity changed; update cursor retained")
            if pts is None:
                full = await client(functions.channels.GetFullChannelRequest(channel=entity))
                next_pts = getattr(full.full_chat, "pts", None)
                if next_pts is None:
                    raise DataAccessError(
                        "updates_unavailable", "This account cannot initialize a channel update cursor"
                    )
                return {
                    "channel": identity,
                    "results": [],
                    "deleted_message_ids": [],
                    "next_pts": next_pts,
                    "initialized": True,
                    "gap_detected": False,
                    "has_more": False,
                    "collected_at": now(),
                }
            difference = await client(
                functions.updates.GetChannelDifferenceRequest(
                    channel=entity,
                    filter=types.ChannelMessagesFilterEmpty(),
                    pts=pts,
                    limit=limit,
                    force=False,
                )
            )
            gap = isinstance(difference, types.updates.ChannelDifferenceTooLong)
            next_pts = getattr(difference, "pts", None)
            if gap:
                next_pts = getattr(difference.dialog, "pts", None)
            if next_pts is None:
                raise DataAccessError("updates_unavailable", "Telegram returned no usable update cursor")
            if next_pts < pts:
                raise DataAccessError("updates_cursor_regressed", "Telegram returned an older cursor; state retained")
            messages = list(getattr(difference, "new_messages", None) or getattr(difference, "messages", None) or [])
            deleted = []
            for update in getattr(difference, "other_updates", []):
                if isinstance(update, types.UpdateEditChannelMessage):
                    messages.append(update.message)
                if isinstance(update, types.UpdateDeleteChannelMessages) and update.channel_id == entity.id:
                    deleted.extend(update.messages)
            results = [
                self._message(message, entity) for message in messages if not isinstance(message, types.MessageEmpty)
            ]
        return {
            "channel": identity,
            "results": results,
            "deleted_message_ids": list(dict.fromkeys(deleted)),
            "next_pts": next_pts,
            "initialized": False,
            "gap_detected": gap,
            "has_more": not getattr(difference, "final", True),
            "collected_at": now(),
            "coverage": "Server-returned updates since stored pts; a gap can omit older events",
        }

    async def posts(self, query: str, limit: int = 20, cursor: dict | None = None) -> dict:
        query, limit = query_value(query), limit_value(limit)
        cursor = cursor or {}
        offset_rate, offset_id = int(cursor.get("offset_rate", 0)), int(cursor.get("offset_id", 0))
        if offset_rate < 0 or offset_id < 0:
            raise ValueError("Cursor offsets must be nonnegative")
        if cursor and not (offset_rate > 0 and offset_id > 0 and cursor.get("channel")):
            raise ValueError("Use the complete next_cursor returned by a previous search")
        async with self.session(global_search=True) as client:
            # A new query may use a daily free slot or a server-cached free search.
            # Subsequent pages are free and do not need another quota preflight.
            if not cursor:
                check = await client(functions.channels.CheckSearchPostsFloodRequest(query=query))
                if not check.query_is_free and check.remains <= 0:
                    raise DataAccessError(
                        "telegram_search_quota",
                        "No free public post search is available; dark-mcp never spends Stars",
                    )
            peer = types.InputPeerEmpty()
            if cursor.get("channel"):
                key = channel_key(cursor["channel"])
                peer = await client.get_input_entity(int(key) if key.startswith("-100") else key)
            response = await client(
                functions.channels.SearchPostsRequest(
                    query=query,
                    limit=limit,
                    offset_rate=offset_rate,
                    offset_id=offset_id,
                    offset_peer=peer,
                )
            )
            entities = {chat.id: chat for chat in response.chats if isinstance(chat, types.Channel) and chat.broadcast}
            items = [
                self._message(message, entities[message.peer_id.channel_id])
                for message in response.messages
                if isinstance(message.peer_id, types.PeerChannel) and message.peer_id.channel_id in entities
            ]
            next_cursor = None
            if response.messages:
                last = response.messages[-1]
                entity = entities.get(getattr(last.peer_id, "channel_id", None))
                if entity:
                    next_cursor = {
                        "offset_rate": getattr(response, "next_rate", None) or int(last.date.timestamp()),
                        "offset_id": last.id,
                        "channel": f"-100{entity.id}",
                    }
        return {
            "results": items,
            "next_cursor": next_cursor,
            "coverage": "Telegram public post search; not exhaustive",
            "collected_at": now(),
        }

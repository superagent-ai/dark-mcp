import asyncio
import re
from contextlib import asynccontextmanager, suppress

from telethon import TelegramClient, errors, functions, types

from dark_mcp.config import Settings
from dark_mcp.errors import DataAccessError
from dark_mcp.models import limit_value, now, parse_date
from dark_mcp.sources import SourceCatalog


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
    def __init__(self, settings: Settings, catalog: SourceCatalog | None = None):
        self.settings = settings
        self.lock = asyncio.Lock()
        self.catalog = catalog

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
            try:
                async with asyncio.timeout(self.settings.request_timeout):
                    await client.connect()
                    if not await client.is_user_authorized():
                        raise DataAccessError(
                            "telegram_not_authenticated", "Session expired; run dark-mcp telegram-login locally"
                        )
                    yield client
            except errors.FloodWaitError as exc:
                raise DataAccessError(
                    "telegram_rate_limited",
                    "Telegram requires a wait before retrying",
                    retry_after=exc.seconds,
                ) from exc
            except errors.RPCError as exc:
                raise DataAccessError(
                    "telegram_rpc_error", f"Telegram rejected the request ({type(exc).__name__})"
                ) from exc
            except (TimeoutError, ConnectionError, OSError) as exc:
                raise DataAccessError("telegram_unavailable", "Telegram connection failed or timed out") from exc
            finally:
                with suppress(Exception):
                    async with asyncio.timeout(5):
                        await client.disconnect()

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
        path = TelegramConnector._username(entity) or f"c/{entity.id}"
        return {
            "id": message.id,
            "channel": TelegramConnector._channel(entity),
            "text": text[:16000],
            "truncated": len(text) > 16000,
            "published_at": message.date.isoformat() if message.date else None,
            "url": f"https://t.me/{path}/{message.id}",
            "has_media": message.media is not None,
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
    ) -> dict:
        self._check()
        target = self._allowed_channel(channel)
        limit_value(limit)
        if before_id < 0:
            raise ValueError("before_id must be zero or a positive message ID")
        date = parse_date(since)
        if query is not None:
            query = query_value(query)
        async with self.session() as client:
            entity = await self._entity(client, target)
            items = []
            async for message in client.iter_messages(
                entity,
                limit=limit,
                offset_id=before_id,
                search=query,
            ):
                if date and message.date and message.date < date:
                    break
                items.append(self._message(message, entity))
        return {
            "results": items,
            "next_before_id": items[-1]["id"] if len(items) == limit else None,
            "coverage": "Selected channel as accessible to the configured account",
            "collected_at": now(),
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

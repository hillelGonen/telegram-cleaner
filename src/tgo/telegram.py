import copy
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Self

from telethon import TelegramClient, utils
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.help import GetAppConfigRequest
from telethon.tl.functions.messages import (
    GetDialogFiltersRequest,
    GetFullChatRequest,
    UpdateDialogFilterRequest,
    UpdateDialogFiltersOrderRequest,
)
from telethon.tl.types import (
    Channel,
    Chat as TlChat,
    DialogFilter,
    DialogFilterChatlist,
    InputNotifyPeer,
    InputPeerNotifySettings,
    InputPeerSelf,
    JsonArray,
    JsonObject,
    TextWithEntities,
    User,
)

from tgo.models import Chat, ChatType, Folder, FolderFlags, FolderKind, FolderLimits
from tgo.settings import Settings

# Telethon sleeps automatically on FloodWait errors up to this many seconds.
FLOOD_SLEEP_THRESHOLD = 120
MUTE_FOREVER = 2**31 - 1


class NotLoggedIn(Exception):
    pass


class TelethonMapper:
    """Converts Telethon / TL objects into tgo models."""

    @staticmethod
    def chat_type(entity) -> ChatType:
        if isinstance(entity, User):
            return ChatType.BOT if entity.bot else ChatType.USER
        if isinstance(entity, Channel):
            return ChatType.CHANNEL if entity.broadcast else ChatType.GROUP
        return ChatType.GROUP  # Chat / ChatForbidden

    @staticmethod
    def chat_title(entity) -> str:
        if isinstance(entity, User):
            name = " ".join(p for p in (entity.first_name, entity.last_name) if p)
            return name or ("Deleted account" if entity.deleted else str(entity.id))
        return getattr(entity, "title", None) or str(entity.id)

    @staticmethod
    def is_muted(notify_settings, now: datetime) -> bool:
        mute_until = getattr(notify_settings, "mute_until", None)
        return mute_until is not None and mute_until > now

    @classmethod
    def chat(cls, dialog, seen_at: datetime) -> Chat:
        e = dialog.entity
        return Chat(
            id=dialog.id,
            type=cls.chat_type(e),
            title=cls.chat_title(e),
            username=getattr(e, "username", None),
            members=getattr(e, "participants_count", None),
            last_msg_date=dialog.date,
            unread=dialog.unread_count or 0,
            muted=cls.is_muted(dialog.dialog.notify_settings, seen_at),
            archived=dialog.archived,
            pinned=dialog.pinned,
            contact=bool(getattr(e, "contact", False)),
            creator=bool(getattr(e, "creator", False)),
            seen_at=seen_at,
        )

    @staticmethod
    def peer_ids(peers, self_id: int) -> list[int]:
        out = []
        for p in peers:
            if isinstance(p, InputPeerSelf):
                out.append(self_id)
                continue
            try:
                out.append(utils.get_peer_id(p))
            except (TypeError, ValueError):
                pass  # unknown peer type, skip
        return out

    @classmethod
    def folder(cls, f, position: int, self_id: int) -> Folder | None:
        if isinstance(f, DialogFilter):
            kind = FolderKind.FILTER
            flags = FolderFlags(**{k: bool(getattr(f, k)) for k in FolderFlags.model_fields})
            exclude = cls.peer_ids(f.exclude_peers, self_id)
        elif isinstance(f, DialogFilterChatlist):
            kind, flags, exclude = FolderKind.CHATLIST, FolderFlags(), []
        else:
            return None  # DialogFilterDefault = "All chats"
        return Folder(
            id=f.id,
            title=getattr(f.title, "text", f.title),  # TextWithEntities in newer layers
            kind=kind,
            emoticon=f.emoticon,
            position=position,
            flags=flags,
            include_peers=cls.peer_ids(f.include_peers, self_id),
            exclude_peers=exclude,
            pinned_peers=cls.peer_ids(f.pinned_peers, self_id),
        )

    @classmethod
    def json_value(cls, v) -> Any:
        """TL JSONValue tree -> plain Python."""
        if isinstance(v, JsonObject):
            return {item.key: cls.json_value(item.value) for item in v.value}
        if isinstance(v, JsonArray):
            return [cls.json_value(x) for x in v.value]
        return getattr(v, "value", None)  # JsonString / JsonNumber / JsonBool / JsonNull

    @staticmethod
    def filter_id(f) -> int:
        return getattr(f, "id", 0)  # DialogFilterDefault ("All chats") has no id

    @staticmethod
    def new_filter(filter_id: int, title: str, emoticon: str | None, peers: list) -> DialogFilter:
        return DialogFilter(
            id=filter_id, title=TextWithEntities(text=title, entities=[]), emoticon=emoticon,
            pinned_peers=[], include_peers=peers, exclude_peers=[],
        )

    @staticmethod
    def with_peers(live: DialogFilter, peers: list, emoticon: str | None) -> DialogFilter:
        """Copy of a live folder with a new include list; flags, title, pins stay as they are."""
        updated = copy.copy(live)
        updated.include_peers = peers
        if emoticon is not None:
            updated.emoticon = emoticon
        return updated

    @staticmethod
    def folder_limits(config: dict, premium: bool) -> FolderLimits:
        tier = "premium" if premium else "default"
        defaults = FolderLimits(premium=premium)
        return FolderLimits(
            premium=premium,
            folders=int(config.get(f"dialog_filters_limit_{tier}", defaults.folders)),
            chats_per_folder=int(
                config.get(f"dialog_filters_chats_limit_{tier}", defaults.chats_per_folder)
            ),
        )


class TelegramGateway:
    """All Telegram network access goes through here."""

    def __init__(self, settings: Settings):
        api_id, api_hash = settings.require_api()
        settings.ensure_data_dir()
        self._client = TelegramClient(
            str(settings.session_path),
            api_id,
            api_hash,
            flood_sleep_threshold=FLOOD_SLEEP_THRESHOLD,
            device_model="tgo organizer",
        )
        self._entities: dict[int, Any] = {}
        self._me: User | None = None

    async def __aenter__(self) -> Self:
        await self._client.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.disconnect()

    async def login(self) -> User:
        await self._client.start()  # prompts for phone / code / password
        return await self.me()

    async def logout(self) -> None:
        if await self._client.is_user_authorized():
            await self._client.log_out()

    async def is_authorized(self) -> bool:
        return await self._client.is_user_authorized()

    async def ensure_authorized(self) -> None:
        if not await self._client.is_user_authorized():
            raise NotLoggedIn("Not logged in. Run `tgo login` first.")

    async def me(self) -> User:
        if self._me is None:
            self._me = await self._client.get_me()
        return self._me

    async def chats(self, seen_at: datetime) -> AsyncIterator[Chat]:
        async for dialog in self._client.iter_dialogs():
            self._entities[dialog.id] = dialog.entity
            yield TelethonMapper.chat(dialog, seen_at)

    async def raw_filters(self) -> list:
        """Folders as TL objects, in Telegram order (incl. DialogFilterDefault)."""
        res = await self._client(GetDialogFiltersRequest())
        return list(getattr(res, "filters", res))  # newer layers wrap in messages.DialogFilters

    async def folders(self) -> list[Folder]:
        me = await self.me()
        folders = [TelethonMapper.folder(f, i, me.id) for i, f in enumerate(await self.raw_filters())]
        return [f for f in folders if f]

    async def self_id(self) -> int:
        return (await self.me()).id

    async def input_peer(self, peer_id: int):
        """InputPeer from the session cache (filled by `tgo scan`)."""
        return await self._client.get_input_entity(peer_id)

    async def save_filter(self, filter_id: int, flt) -> None:
        await self._client(UpdateDialogFilterRequest(id=filter_id, filter=flt))

    async def delete_filter(self, filter_id: int) -> None:
        await self._client(UpdateDialogFilterRequest(id=filter_id))

    async def reorder_filters(self, order: list[int]) -> None:
        await self._client(UpdateDialogFiltersOrderRequest(order=order))

    # chat actions (cleanup)

    async def leave_chat(self, chat_id: int) -> None:
        """Leave channel/group; for private chats delete the history (for you only)."""
        await self._client.delete_dialog(await self.input_peer(chat_id), revoke=False)

    async def set_archived(self, chat_id: int, archived: bool) -> None:
        await self._client.edit_folder(await self.input_peer(chat_id), folder=1 if archived else 0)

    async def set_muted(self, chat_id: int, muted: bool) -> None:
        settings = InputPeerNotifySettings(mute_until=MUTE_FOREVER if muted else 0)
        peer = InputNotifyPeer(await self.input_peer(chat_id))
        await self._client(UpdateNotifySettingsRequest(peer=peer, settings=settings))

    async def mark_read(self, chat_id: int) -> None:
        await self._client.send_read_acknowledge(await self.input_peer(chat_id))

    async def folder_limits(self) -> FolderLimits:
        me = await self.me()
        res = await self._client(GetAppConfigRequest(hash=0))
        config = TelethonMapper.json_value(res.config) or {}
        return TelethonMapper.folder_limits(config, bool(me.premium))

    async def chat_details(self, chat_id: int) -> tuple[str | None, int | None]:
        """Description + member count of a channel/group (one request)."""
        entity = self._entities.get(chat_id) or await self._client.get_entity(chat_id)
        if isinstance(entity, Channel):
            full = await self._client(GetFullChannelRequest(entity))
            return full.full_chat.about or None, full.full_chat.participants_count
        if isinstance(entity, TlChat):
            full = await self._client(GetFullChatRequest(entity.id))
            return full.full_chat.about or None, getattr(entity, "participants_count", None)
        return None, None

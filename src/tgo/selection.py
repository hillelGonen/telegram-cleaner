import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from tgo.folder_index import FolderIndex
from tgo.models import Chat, ChatType, Folder

TELEGRAM_SERVICE_ID = 777000  # login codes etc.


class ChatFilter(BaseModel):
    """All set criteria must hold (AND)."""

    types: list[ChatType] = Field(default_factory=list)
    idle_days: int | None = Field(None, ge=0, description="No message for at least N days")
    title: str | None = Field(None, description="Regex on title, case-insensitive")
    muted: bool | None = None
    unread_min: int | None = Field(None, ge=0)

    @field_validator("title")
    @classmethod
    def _valid_regex(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"bad regex {v!r}: {e}") from e
        return v

    @classmethod
    def keywords(cls, words: str, **kw) -> "ChatFilter":
        """Filter on any of the space-separated words in the title."""
        parts = [re.escape(w) for w in words.split()]
        return cls(title="|".join(parts) if parts else None, **kw)

    def matches(self, chat: Chat, now: datetime) -> bool:
        if self.types and chat.type not in self.types:
            return False
        if self.idle_days is not None and chat.last_msg_date is not None \
                and (now - chat.last_msg_date).days < self.idle_days:
            return False
        if self.title and not re.search(self.title, chat.title, re.IGNORECASE):
            return False
        if self.muted is not None and chat.muted != self.muted:
            return False
        if self.unread_min is not None and chat.unread < self.unread_min:
            return False
        return True


class Protection(StrEnum):
    SELF = "Saved Messages"
    SERVICE = "Telegram service chat"
    CREATOR = "you own it"
    PINNED = "pinned"


class ProtectedChat(BaseModel):
    chat: Chat
    reason: Protection


class Selection(BaseModel):
    chats: list[Chat]
    protected: list[ProtectedChat] = Field(default_factory=list)


class ChatSelector:
    """Picks chats from the last scan by a ChatFilter, never returning protected ones."""

    def __init__(self, chats: list[Chat], folders: list[Folder], scanned_at: datetime,
                 self_id: int | None = None):
        self.chats = chats
        self.index = FolderIndex(folders, chats)
        self.scanned_at = scanned_at
        self.self_id = self_id

    def select(self, flt: ChatFilter) -> Selection:
        picked, protected = [], []
        for chat in self.chats:
            if not flt.matches(chat, self.scanned_at):
                continue
            if reason := self._protection(chat):
                protected.append(ProtectedChat(chat=chat, reason=reason))
            else:
                picked.append(chat)
        picked.sort(key=lambda c: (list(ChatType).index(c.type), c.title.casefold()))
        return Selection(chats=picked, protected=protected)

    def _protection(self, chat: Chat) -> Protection | None:
        if chat.id == self.self_id:
            return Protection.SELF
        if chat.id == TELEGRAM_SERVICE_ID:
            return Protection.SERVICE
        if chat.creator:
            return Protection.CREATOR
        if chat.pinned:
            return Protection.PINNED
        return None

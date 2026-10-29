from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class ChatType(StrEnum):
    CHANNEL = "channel"
    GROUP = "group"
    USER = "user"
    BOT = "bot"


class Chat(BaseModel):
    id: int  # Telethon marked peer id, unique across types
    type: ChatType
    title: str
    username: str | None = None
    members: int | None = None
    last_msg_date: datetime | None = None
    unread: int = 0
    muted: bool = False
    archived: bool = False
    pinned: bool = False
    contact: bool = False  # user is in contacts (folder flags use it)
    creator: bool = False  # you own this channel/group
    seen_at: datetime

    @property
    def is_broadcast_or_group(self) -> bool:
        return self.type in (ChatType.CHANNEL, ChatType.GROUP)

    @property
    def link(self) -> str | None:
        """Public link to rejoin / reopen; None for private chats."""
        return f"https://t.me/{self.username}" if self.username else None

    @property
    def handle(self) -> str:
        return f"@{self.username}" if self.username else ""


class FolderKind(StrEnum):
    FILTER = "filter"
    CHATLIST = "chatlist"  # shared folder (invite link)


class FolderFlags(BaseModel):
    """Category rules of a folder, e.g. "all channels", "exclude muted"."""

    contacts: bool = False
    non_contacts: bool = False
    groups: bool = False
    broadcasts: bool = False
    bots: bool = False
    exclude_muted: bool = False
    exclude_read: bool = False
    exclude_archived: bool = False

    def includes_type(self, chat: Chat) -> bool:
        match chat.type:
            case ChatType.USER:
                return self.contacts if chat.contact else self.non_contacts
            case ChatType.CHANNEL:
                return self.broadcasts
            case ChatType.GROUP:
                return self.groups
            case ChatType.BOT:
                return self.bots

    def excludes_state(self, chat: Chat) -> bool:
        return (
            (self.exclude_muted and chat.muted)
            or (self.exclude_archived and chat.archived)
            or (self.exclude_read and chat.unread == 0)
        )

    @property
    def has_type_rules(self) -> bool:
        """Shows chats by type ("all channels"...), so the folder is valid without listed chats."""
        return any((self.contacts, self.non_contacts, self.groups, self.broadcasts, self.bots))

    def active(self) -> list[str]:
        return [name for name, on in self if on]


class Folder(BaseModel):
    id: int
    title: str
    kind: FolderKind = FolderKind.FILTER
    emoticon: str | None = None
    position: int = 0
    flags: FolderFlags = Field(default_factory=FolderFlags)
    include_peers: list[int] = Field(default_factory=list)
    exclude_peers: list[int] = Field(default_factory=list)
    pinned_peers: list[int] = Field(default_factory=list)

    @property
    def listed(self) -> set[int]:
        """Chats added to the folder by hand (pinned ones count too)."""
        return set(self.include_peers) | set(self.pinned_peers)

    @property
    def label(self) -> str:
        return f"{self.emoticon} {self.title}" if self.emoticon else self.title

    def shows(self, chat: Chat) -> bool:
        if chat.id in self.exclude_peers:
            return False
        if chat.id in self.listed:
            return True
        return self.flags.includes_type(chat) and not self.flags.excludes_state(chat)


class FolderLimits(BaseModel):
    """Account limits from Telegram app config (Premium raises them)."""

    premium: bool = False
    folders: int = 10
    chats_per_folder: int = 100


class ScanResult(BaseModel):
    chats: int
    removed: int
    folders: int

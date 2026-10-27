from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from tgo.folder_index import FolderIndex
from tgo.models import Chat, ChatType, Folder, FolderLimits


class ReportOptions(BaseModel):
    dead_days: int = Field(90, ge=1, description="Idle this many days = dead")
    unread_min: int = Field(300, ge=1, description="Unread count that counts as a pileup")
    near_limit: float = Field(0.8, gt=0, le=1, description="Folder fill ratio to warn at")
    include_private: bool = Field(False, description="Include users/bots in 'no folder'")


class IdleChat(BaseModel):
    chat: Chat
    idle_days: int | None  # None = no message at all


class MultiFolderChat(BaseModel):
    chat: Chat
    folders: list[str]


class FolderUsage(BaseModel):
    folder: Folder
    listed: int
    limit: int
    near_limit: bool

    @computed_field
    @property
    def ratio(self) -> float:
        return self.listed / self.limit if self.limit else 0.0


class CleanupReport(BaseModel):
    scanned_at: datetime
    options: ReportOptions
    limits: FolderLimits | None
    unfoldered: list[Chat]
    unfoldered_private: int  # users/bots in no folder (hidden unless include_private)
    dead: list[IdleChat]
    unread_pileup: list[Chat]
    multi_folder: list[MultiFolderChat]
    folder_usage: list[FolderUsage]
    folder_count: int

    @property
    def folder_count_near_limit(self) -> bool:
        return bool(self.limits) and self.folder_count >= self.limits.folders * self.options.near_limit


class CleanupReporter:
    """Builds a CleanupReport from the last scan. Pure: no network, no db."""

    def __init__(self, chats: list[Chat], folders: list[Folder], scanned_at: datetime,
                 limits: FolderLimits | None = None, options: ReportOptions | None = None):
        self.chats = chats
        self.folders = folders
        self.scanned_at = scanned_at
        self.limits = limits
        self.options = options or ReportOptions()
        self.index = FolderIndex(folders, chats)

    def build(self) -> CleanupReport:
        unfoldered, private = self._unfoldered()
        return CleanupReport(
            scanned_at=self.scanned_at,
            options=self.options,
            limits=self.limits,
            unfoldered=unfoldered,
            unfoldered_private=private,
            dead=self._dead(),
            unread_pileup=self._unread_pileup(),
            multi_folder=self._multi_folder(),
            folder_usage=self._folder_usage(),
            folder_count=len(self.folders),
        )

    def _unfoldered(self) -> tuple[list[Chat], int]:
        loose = [c for c in self.chats if self.index.in_no_folder(c)]
        private = [c for c in loose if not c.is_broadcast_or_group]
        shown = loose if self.options.include_private else [c for c in loose if c.is_broadcast_or_group]
        type_order = list(ChatType)
        shown.sort(key=lambda c: (type_order.index(c.type), c.title.lower()))
        return shown, 0 if self.options.include_private else len(private)

    def _idle_days(self, chat: Chat) -> int | None:
        if chat.last_msg_date is None:
            return None
        return (self.scanned_at - chat.last_msg_date).days

    def _dead(self) -> list[IdleChat]:
        dead = [
            IdleChat(chat=c, idle_days=self._idle_days(c))
            for c in self.chats
            if c.is_broadcast_or_group
        ]
        dead = [d for d in dead if d.idle_days is None or d.idle_days >= self.options.dead_days]
        dead.sort(key=lambda d: float("inf") if d.idle_days is None else d.idle_days, reverse=True)
        return dead

    def _unread_pileup(self) -> list[Chat]:
        pile = [c for c in self.chats if c.unread >= self.options.unread_min]
        return sorted(pile, key=lambda c: c.unread, reverse=True)

    def _multi_folder(self) -> list[MultiFolderChat]:
        out = []
        for c in self.chats:
            listing = self.index.listing(c)
            if len(listing) > 1:
                out.append(MultiFolderChat(chat=c, folders=[f.title for f in listing]))
        return sorted(out, key=lambda m: len(m.folders), reverse=True)

    def _folder_usage(self) -> list[FolderUsage]:
        limit = self.limits.chats_per_folder if self.limits else FolderLimits().chats_per_folder
        usage = [
            FolderUsage(folder=f, listed=len(f.listed), limit=limit,
                        near_limit=len(f.listed) >= limit * self.options.near_limit)
            for f in self.folders
        ]
        return sorted(usage, key=lambda u: u.listed, reverse=True)


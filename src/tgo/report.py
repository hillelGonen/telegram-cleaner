from datetime import datetime

from pydantic import BaseModel, Field

from tgo.folder_index import FolderIndex
from tgo.models import Chat, Folder


class ReportOptions(BaseModel):
    dead_days: int = Field(365, ge=1, description="Idle this many days = dead")
    unread_min: int = Field(1000, ge=1, description="Unread count that counts as noisy")


class CleanupReport(BaseModel):
    """What the overview shows: channels/groups that need attention."""

    options: ReportOptions
    dead: list[Chat]  # no message for dead_days, or never
    unread_pileup: list[Chat]
    unfoldered: list[Chat]


class CleanupReporter:
    """Builds a CleanupReport from the last scan. Pure: no network, no db."""

    def __init__(self, chats: list[Chat], folders: list[Folder], scanned_at: datetime,
                 options: ReportOptions | None = None):
        self.groups = [c for c in chats if c.is_broadcast_or_group]
        self.index = FolderIndex(folders, chats)
        self.scanned_at = scanned_at
        self.options = options or ReportOptions()

    def build(self) -> CleanupReport:
        return CleanupReport(
            options=self.options,
            dead=[c for c in self.groups if self._is_dead(c)],
            unread_pileup=sorted((c for c in self.groups if c.unread >= self.options.unread_min),
                                 key=lambda c: c.unread, reverse=True),
            unfoldered=[c for c in self.groups if self.index.in_no_folder(c)],
        )

    def _is_dead(self, chat: Chat) -> bool:
        if chat.last_msg_date is None:
            return True
        return (self.scanned_at - chat.last_msg_date).days >= self.options.dead_days

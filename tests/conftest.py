from datetime import UTC, datetime, timedelta

import pytest

from tgo.models import Chat, ChatType, Folder, FolderFlags

SCAN = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def make_chat(id: int, type: ChatType = ChatType.CHANNEL, idle_days: int | None = 1, **kw) -> Chat:
    return Chat(
        id=id, type=type, title=kw.pop("title", f"chat {id}"),
        last_msg_date=None if idle_days is None else SCAN - timedelta(days=idle_days),
        seen_at=SCAN, **kw,
    )


def make_folder(id: int, title: str, include=(), exclude=(), pinned=(), **flags) -> Folder:
    return Folder(id=id, title=title, include_peers=list(include), exclude_peers=list(exclude),
                  pinned_peers=list(pinned), flags=FolderFlags(**flags))


@pytest.fixture
def scan_time() -> datetime:
    return SCAN

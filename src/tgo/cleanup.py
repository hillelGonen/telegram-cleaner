import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field
from telethon.errors import FloodWaitError, RPCError

from tgo.models import Chat, ChatType, utcnow


class CleanupAction(StrEnum):
    LEAVE = "leave"
    ARCHIVE = "archive"
    UNARCHIVE = "unarchive"
    MUTE = "mute"
    UNMUTE = "unmute"
    READ = "read"

    @property
    def irreversible(self) -> bool:
        return self == CleanupAction.LEAVE

    @property
    def concurrency(self) -> int:
        """Chats handled at the same time. Telegram limits leaving hardest; when we still go
        too fast it answers with a flood wait, which Telethon sleeps through by itself."""
        return 3 if self == CleanupAction.LEAVE else 8

    @property
    def local_update(self) -> dict[str, Any] | None:
        """How the local db changes for a done chat; None = remove the chat."""
        return {
            CleanupAction.LEAVE: None,
            CleanupAction.ARCHIVE: {"archived": True},
            CleanupAction.UNARCHIVE: {"archived": False},
            CleanupAction.MUTE: {"muted": True},
            CleanupAction.UNMUTE: {"muted": False},
            CleanupAction.READ: {"unread": 0},
        }[self]

    @property
    def inverse(self) -> "CleanupAction | None":
        """Action that undoes this one; None if it can't be undone."""
        return {
            CleanupAction.ARCHIVE: CleanupAction.UNARCHIVE,
            CleanupAction.UNARCHIVE: CleanupAction.ARCHIVE,
            CleanupAction.MUTE: CleanupAction.UNMUTE,
            CleanupAction.UNMUTE: CleanupAction.MUTE,
        }.get(self)

    def describe(self, chat: Chat) -> str:
        if self == CleanupAction.LEAVE:
            return "delete chat" if chat.type in (ChatType.USER, ChatType.BOT) else "leave"
        return self.value


class ActionGateway(Protocol):
    async def leave_chat(self, chat_id: int) -> None: ...
    async def set_archived(self, chat_id: int, archived: bool) -> None: ...
    async def set_archived_many(self, chat_ids: list[int], archived: bool) -> None: ...
    async def set_muted(self, chat_id: int, muted: bool) -> None: ...
    async def mark_read(self, chat_id: int) -> None: ...


class ActionLogEntry(BaseModel):
    at: datetime
    action: CleanupAction
    chat_id: int
    title: str
    type: ChatType
    username: str | None = None
    link: str | None = None  # to rejoin public chats
    ok: bool
    error: str | None = None
    batch: str | None = None  # one menu run; None in logs written before batches existed
    undo_of: str | None = None  # batch this entry undid


class ActionLog:
    """Append-only JSON-lines record of every cleanup action."""

    def __init__(self, path: Path):
        self.path = path

    def append(self, entry: ActionLogEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")

    def entries(self) -> list[ActionLogEntry]:
        if not self.path.exists():
            return []
        return [ActionLogEntry.model_validate_json(line)
                for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def last_undoable_batch(self) -> list[ActionLogEntry]:
        """Successful entries of the newest archive/mute batch that wasn't undone yet."""
        entries = self.entries()
        undone = {e.undo_of for e in entries if e.undo_of}
        undoable = (CleanupAction.ARCHIVE, CleanupAction.MUTE)
        for e in reversed(entries):
            if e.batch and e.ok and not e.undo_of and e.action in undoable and e.batch not in undone:
                return [x for x in entries if x.batch == e.batch and x.ok]
        return []

    def left(self) -> list[ActionLogEntry]:
        """Chats left / deleted, newest first."""
        return [e for e in reversed(self.entries()) if e.action == CleanupAction.LEAVE and e.ok]


class FailedChat(BaseModel):
    chat: Chat
    error: str


class CleanupResult(BaseModel):
    action: CleanupAction
    batch: str
    done: list[Chat] = Field(default_factory=list)
    failed: list[FailedChat] = Field(default_factory=list)
    skipped: list[Chat] = Field(default_factory=list)  # not attempted after a stop
    stopped: str | None = None


class CleanupExecutor:
    """Runs one action over chats, several at a time, logging each one. Archiving goes in
    batches of up to ARCHIVE_BATCH chats per request. Per-chat errors don't stop the run;
    a long flood wait does."""

    ARCHIVE_BATCH = 100

    def __init__(self, gateway: ActionGateway, log: ActionLog):
        self.gw = gateway
        self.log = log

    def _call(self, action: CleanupAction, chat_id: int) -> Awaitable[None]:
        match action:
            case CleanupAction.LEAVE:
                return self.gw.leave_chat(chat_id)
            case CleanupAction.ARCHIVE | CleanupAction.UNARCHIVE:
                return self.gw.set_archived(chat_id, action == CleanupAction.ARCHIVE)
            case CleanupAction.MUTE | CleanupAction.UNMUTE:
                return self.gw.set_muted(chat_id, action == CleanupAction.MUTE)
            case CleanupAction.READ:
                return self.gw.mark_read(chat_id)

    async def run(self, action: CleanupAction, chats: list[Chat],
                  on_step: Callable[[Chat], None] = lambda _: None,
                  on_done: Callable[[Chat], None] = lambda _: None,
                  undo_of: str | None = None, concurrency: int | None = None) -> CleanupResult:
        """on_done runs after each successful chat (save progress)."""
        run = _Run(self, action, on_step, on_done, undo_of)
        if action in (CleanupAction.ARCHIVE, CleanupAction.UNARCHIVE):
            await run.archive_batches(chats, self.ARCHIVE_BATCH, concurrency or action.concurrency)
        else:
            await run.parallel(chats, concurrency or action.concurrency)
        return run.finish(chats)


class _Run:
    """State of one executor run."""

    def __init__(self, ex: CleanupExecutor, action: CleanupAction, on_step, on_done, undo_of):
        self.ex = ex
        self.action = action
        self.on_step = on_step
        self.on_done = on_done
        self.undo_of = undo_of
        self.result = CleanupResult(action=action, batch=uuid.uuid4().hex[:12])

    def _record(self, chat: Chat, error: str | None) -> None:
        self.ex.log.append(ActionLogEntry(
            at=utcnow(), action=self.action, chat_id=chat.id, title=chat.title, type=chat.type,
            username=chat.username, link=chat.link, ok=error is None, error=error,
            batch=self.result.batch, undo_of=self.undo_of,
        ))
        if error:
            self.result.failed.append(FailedChat(chat=chat, error=error))
        else:
            self.result.done.append(chat)
            self.on_done(chat)

    def _stop(self, e: FloodWaitError) -> None:
        if not self.result.stopped:
            self.result.stopped = f"Telegram asked to wait {e.seconds}s; stopped. Try again later."

    async def parallel(self, chats: list[Chat], concurrency: int) -> None:
        sem = asyncio.Semaphore(concurrency)

        async def one(chat: Chat) -> None:
            async with sem:
                if self.result.stopped:
                    return
                self.on_step(chat)
                try:
                    await self.ex._call(self.action, chat.id)
                except FloodWaitError as e:
                    self._stop(e)
                    return
                except (RPCError, ValueError) as e:  # ValueError: chat not in session cache
                    self._record(chat, f"{type(e).__name__}: {e}")
                else:
                    self._record(chat, None)

        await asyncio.gather(*(one(c) for c in chats))

    async def archive_batches(self, chats: list[Chat], size: int, concurrency: int) -> None:
        archived = self.action == CleanupAction.ARCHIVE
        for i in range(0, len(chats), size):
            chunk = chats[i:i + size]
            if self.result.stopped:
                return
            for chat in chunk:
                self.on_step(chat)
            try:
                await self.ex.gw.set_archived_many([c.id for c in chunk], archived)
            except FloodWaitError as e:
                self._stop(e)
                return
            except (RPCError, ValueError):
                await self.parallel(chunk, concurrency)  # one by one, to find the bad chat
            else:
                for chat in chunk:
                    self._record(chat, None)

    def finish(self, chats: list[Chat]) -> CleanupResult:
        r = self.result
        order = {c.id: i for i, c in enumerate(chats)}
        r.done.sort(key=lambda c: order[c.id])
        r.failed.sort(key=lambda f: order[f.chat.id])
        handled = {c.id for c in r.done} | {f.chat.id for f in r.failed}
        r.skipped = [c for c in chats if c.id not in handled] if r.stopped else []
        return r

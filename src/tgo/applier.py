import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field
from telethon.errors import RPCError
from telethon.tl.types import DialogFilter

from tgo.backup import BackupStore, FolderBackup
from tgo.models import FolderLimits
from tgo.telegram import TelethonMapper

FIRST_FILTER_ID = 2  # 0 = "All chats", 1 = archive


class FolderGateway(Protocol):
    """The part of TelegramGateway that apply/restore need (faked in tests)."""

    async def raw_filters(self) -> list[Any]: ...
    async def self_id(self) -> int: ...
    async def input_peer(self, peer_id: int) -> Any: ...
    async def save_filter(self, filter_id: int, flt: Any) -> None: ...
    async def delete_filter(self, filter_id: int) -> None: ...
    async def reorder_filters(self, order: list[int]) -> None: ...


class WriteResult(BaseModel):
    backup: Path | None = None  # None when nothing was written (checks failed)
    done: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)  # found before writing; nothing changed
    failed: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.failed is None and not self.problems


class FolderWriter:
    """Shared plumbing: live state, peer ids, backups, paced writes."""

    WRITE_DELAY = 0.3  # seconds between folder writes, keeps clear of flood limits

    def __init__(self, gateway: FolderGateway, store: BackupStore,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.gw = gateway
        self.store = store
        self.sleep = sleep
        self._self_id: int | None = None

    async def live(self) -> list[Any]:
        return await self.gw.raw_filters()

    async def peer_id(self, peer: Any) -> int:
        if self._self_id is None:
            self._self_id = await self.gw.self_id()
        return TelethonMapper.peer_ids([peer], self._self_id)[0]

    async def peer_ids(self, peers: list[Any]) -> set[int]:
        return {await self.peer_id(p) for p in peers}

    async def backup(self, filters: list[Any], reason: str) -> Path:
        if self._self_id is None:
            self._self_id = await self.gw.self_id()
        return self.store.save(FolderBackup.of(filters, self._self_id, reason))

    async def run(self, backup: Path, steps: list[tuple[str, Callable[[], Awaitable[None]]]],
                  on_step: Callable[[str], None]) -> WriteResult:
        result = WriteResult(backup=backup)
        for i, (label, step) in enumerate(steps):
            on_step(label)
            try:
                await step()
            except (RPCError, ValueError) as e:  # ValueError: peer not in session cache
                result.failed, result.error = label, f"{type(e).__name__}: {e}"
                return result
            result.done.append(label)
            if i < len(steps) - 1:
                await self.sleep(self.WRITE_DELAY)
        return result


class FolderEditor(FolderWriter):
    """Adds chats to a folder by name, creating it if needed. Backs up first."""

    @staticmethod
    def _title(f: Any) -> str | None:
        return getattr(getattr(f, "title", None), "text", None)

    async def add_chats(self, name: str, chat_ids: list[int], emoticon: str | None = None,
                        on_step: Callable[[str], None] = lambda _: None) -> WriteResult:
        live = await self.live()
        backup = await self.backup(live, f"before adding to {name}")
        existing = next((f for f in live if isinstance(f, DialogFilter) and self._title(f) == name), None)
        if existing is None:
            used = {TelethonMapper.filter_id(f) for f in live}
            filter_id = next(i for i in range(FIRST_FILTER_ID, 256) if i not in used)
            step = self._creator(filter_id, name, emoticon, chat_ids)
            label = f"create {name}"
        else:
            step = self._updater(existing, chat_ids)
            label = f"update {name}"
        return await self.run(backup, [(label, step)], on_step)

    async def apply_moves(self, moves: dict[int, str | None], limits: FolderLimits,
                          delete: frozenset[str] = frozenset(),
                          on_step: Callable[[str], None] = lambda _: None) -> WriteResult:
        """Put each chat in `moves` into exactly the named folder (None = no folder), taking it
        out of any other own folder. Folders that don't exist yet are created; own folders named
        in `delete` are deleted (their chats stay in Telegram). Checks everything first; writes
        nothing if a check fails."""
        live = await self.live()
        own = [f for f in live if isinstance(f, DialogFilter)]
        titles = {self._title(f) for f in live if self._title(f)}
        steps, problems = [], []

        doomed = [f for f in own if self._title(f) in delete]
        missing = set(delete) - {self._title(f) for f in doomed}
        if missing:
            problems.append(f"Can't delete {', '.join(sorted(missing))}: not one of your own folders.")
        clash = sorted({t for t in moves.values() if t in delete})
        if clash:
            problems.append(f"Can't move chats into {', '.join(clash)}: it is being deleted.")
        for f in doomed:
            steps.append((f"delete {self._title(f)}", self._deleter(f.id)))

        for f in own:
            if f in doomed:
                continue
            name = self._title(f)
            leaving = {cid for cid, target in moves.items() if target != name}
            include = [p for p in f.include_peers if await self.peer_id(p) not in leaving]
            pinned = [p for p in f.pinned_peers if await self.peer_id(p) not in leaving]
            present = await self.peer_ids(include) | await self.peer_ids(pinned)
            adds = [cid for cid, target in moves.items() if target == name and cid not in present]
            if len(include) == len(f.include_peers) and len(pinned) == len(f.pinned_peers) and not adds:
                continue  # untouched
            count = len(include) + len(pinned) + len(adds)
            if count > limits.chats_per_folder:
                problems.append(f"'{name}' would have {count} chats; the limit is {limits.chats_per_folder}.")
            if count == 0 and not TelethonMapper.has_type_rules(f):
                problems.append(f"'{name}' would be empty, and Telegram doesn't allow empty folders. "
                                "Leave at least one chat in it (or delete the folder in Telegram).")
            steps.append((f"update {name}", self._mover(f, include, pinned, adds)))

        new = sorted({t for t in moves.values() if t and t not in titles})
        if len([f for f in live if TelethonMapper.filter_id(f)]) - len(doomed) + len(new) > limits.folders:
            problems.append(f"Too many folders: your account allows {limits.folders}.")
        used = {TelethonMapper.filter_id(f) for f in live}
        free_ids = (i for i in range(FIRST_FILTER_ID, 256) if i not in used)
        for name in new:
            chat_ids = [cid for cid, target in moves.items() if target == name]
            if len(chat_ids) > limits.chats_per_folder:
                problems.append(f"'{name}' would have {len(chat_ids)} chats; "
                                f"the limit is {limits.chats_per_folder}.")
            steps.append((f"create {name}", self._creator(next(free_ids), name, None, chat_ids)))

        if problems:
            return WriteResult(problems=problems)
        if not steps:
            return WriteResult()
        reason = "before deleting folders" if doomed and not moves else "before sorting folders"
        backup = await self.backup(live, reason)
        return await self.run(backup, steps, on_step)

    def _deleter(self, filter_id: int):
        return lambda: self.gw.delete_filter(filter_id)

    def _mover(self, live: DialogFilter, include: list, pinned: list, adds: list[int]):
        async def step():
            new_peers = [await self.gw.input_peer(pid) for pid in adds]
            updated = TelethonMapper.with_peers(live, include + new_peers, None)
            updated.pinned_peers = pinned
            await self.gw.save_filter(live.id, updated)
        return step

    def _creator(self, filter_id: int, name: str, emoticon: str | None, chat_ids: list[int]):
        async def step():
            peers = [await self.gw.input_peer(pid) for pid in chat_ids]
            await self.gw.save_filter(filter_id, TelethonMapper.new_filter(filter_id, name, emoticon, peers))
        return step

    def _updater(self, live: DialogFilter, chat_ids: list[int]):
        async def step():
            present = await self.peer_ids(live.include_peers) | await self.peer_ids(live.pinned_peers)
            adds = [await self.gw.input_peer(pid) for pid in chat_ids if pid not in present]
            updated = TelethonMapper.with_peers(live, live.include_peers + adds, None)
            await self.gw.save_filter(live.id, updated)
        return step


class RestorePreview(BaseModel):
    restore: list[str] = Field(default_factory=list)  # changed or deleted since backup
    delete: list[str] = Field(default_factory=list)  # created after backup
    reorder: bool = False

    @property
    def empty(self) -> bool:
        return not (self.restore or self.delete or self.reorder)


class Restorer(FolderWriter):
    """Puts own folders back exactly as in a backup. Shared folders are left alone."""

    def __init__(self, gateway: FolderGateway, store: BackupStore, backup: FolderBackup, **kw):
        super().__init__(gateway, store, **kw)
        self.target = backup

    @staticmethod
    def _editable(f: Any) -> bool:
        return isinstance(f, DialogFilter)

    async def _diff(self, live: list[Any]):
        live_by_id = {TelethonMapper.filter_id(f): f for f in live}
        wanted = [(s, s.tl()) for s in self.target.folders]
        restore = [(s, f) for s, f in wanted
                   if self._editable(f) and bytes(live_by_id.get(s.id, b"")) != bytes(f)]
        wanted_ids = {s.id for s in self.target.folders}
        delete = [f for f in live if self._editable(f) and f.id not in wanted_ids]

        live_order = [TelethonMapper.filter_id(f) for f in live if TelethonMapper.filter_id(f) not in
                      {d.id for d in delete}]
        live_order += [s.id for s, _ in restore if s.id not in live_order]
        order = [i for i in self.target.order if i in live_order]
        order += [i for i in live_order if i not in order]
        return restore, delete, order, order != live_order

    async def preview(self) -> RestorePreview:
        restore, delete, _, reorder = await self._diff(await self.live())
        return RestorePreview(
            restore=[s.folder.title for s, _ in restore if s.folder],
            delete=[f.title.text for f in delete],
            reorder=reorder,
        )

    async def restore(self, on_step: Callable[[str], None] = lambda _: None) -> WriteResult:
        live = await self.live()
        backup = await self.backup(live, "before restore")
        restore, delete, order, reorder = await self._diff(live)

        steps = [(f"restore {s.folder.title}", self._saver(s.id, f)) for s, f in restore]
        steps += [(f"delete {f.title.text}", self._deleter(f.id)) for f in delete]
        if reorder:
            steps.append(("reorder folders", lambda: self.gw.reorder_filters(order)))
        return await self.run(backup, steps, on_step)

    def _saver(self, filter_id: int, flt: Any):
        return lambda: self.gw.save_filter(filter_id, flt)

    def _deleter(self, filter_id: int):
        return lambda: self.gw.delete_filter(filter_id)

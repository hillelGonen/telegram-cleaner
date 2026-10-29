import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar

from rich.console import Console

from tgo.applier import FolderEditor, Restorer
from tgo.backup import BackupStore
from tgo.cleanup import ActionLog, CleanupAction, CleanupExecutor, CleanupResult
from tgo.folder_index import FolderIndex
from tgo.models import Chat, ChatType, Folder, FolderKind, FolderLimits, utcnow
from tgo.prompter import Option, Prompter
from tgo.report import CleanupReporter
from tgo.repository import Repository
from tgo.scanner import Scanner
from tgo.selection import ChatFilter, ChatSelector
from tgo.settings import Settings
from tgo.telegram import TelegramGateway
from tgo.text import TerminalText
from tgo.views import ChatTableView, OverviewView, ResultView

T = TypeVar("T")

SCAN_MAX_AGE = timedelta(hours=1)
FOLDER_NAME_MAX = 12  # Telegram limit

AGES = [("1 month", 30), ("3 months", 90), ("6 months", 180), ("1 year", 365), ("2 years", 730),
        ("Any age", 0)]
TYPES = [("Private chats", ChatType.USER, True), ("Bots", ChatType.BOT, False),
         ("Groups", ChatType.GROUP, True), ("Channels", ChatType.CHANNEL, False)]
CLEAN_ACTIONS = [("Delete / leave", CleanupAction.LEAVE),
                 ("Archive (hide; undoable)", CleanupAction.ARCHIVE),
                 ("Mute (undoable)", CleanupAction.MUTE)]
# Unread count is the "I don't read this" signal: active channels never look old.
UNREAD_LEVELS = [50, 100, 300, 1000]
UNREAD_ACTIONS = [("Leave", CleanupAction.LEAVE),
                  ("Mute (undoable)", CleanupAction.MUTE),
                  ("Archive (hide; undoable)", CleanupAction.ARCHIVE),
                  ("Mark as read", CleanupAction.READ)]
ALL_TYPES = "all"
VERBS = {CleanupAction.LEAVE: "Leave / delete", CleanupAction.ARCHIVE: "Archive",
         CleanupAction.UNARCHIVE: "Unarchive", CleanupAction.MUTE: "Mute",
         CleanupAction.UNMUTE: "Unmute", CleanupAction.READ: "Mark as read"}


class TgoApp:
    """The interactive menu. Sync on the outside (prompts); Telegram jobs run on one private
    asyncio loop that is only running during a job, because the prompt library starts its own
    loop. The Telegram connection is opened once and reused for the whole session."""

    def __init__(self, settings: Settings, prompter: Prompter, console: Console,
                 gateway_factory: Callable[[], Any] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.settings = settings
        self.ask = prompter
        self.console = console
        self.text = TerminalText(bidi=settings.reorder_rtl)
        self.gateway_factory = gateway_factory or (lambda: TelegramGateway(settings))
        self.sleep = sleep
        self.store = BackupStore(settings.backups_dir)
        self.log = ActionLog(settings.actions_log_path)
        self.chats_view = ChatTableView(console, self.text)
        self.result_view = ResultView(console, self.text)
        settings.ensure_data_dir()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tg: Any = None

    # plumbing

    def repo(self) -> Repository:
        return Repository(self.settings.db_path)

    def telegram(self, work: Callable[[Any], Awaitable[T]]) -> T:
        """Run a job on the shared connection (connects on first use)."""
        async def go():
            if self._tg is None:
                tg = self.gateway_factory()
                await tg.__aenter__()
                self._tg = tg
            return await work(self._tg)
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(go())

    def close(self) -> None:
        if self._loop is None:
            return
        if self._tg is not None:
            self._loop.run_until_complete(self._tg.__aexit__(None, None, None))
            self._tg = None
        self._loop.close()
        self._loop = None

    async def _save_folders(self, tg) -> None:
        folders = await tg.folders()
        with self.repo() as repo:
            repo.replace_folders(folders)
            repo.commit()

    def load(self) -> tuple[list[Chat], list[Folder], Repository]:
        repo = self.repo()
        return repo.chats(), repo.folders(), repo

    # entry

    def run(self) -> None:
        try:
            self._menu()
        finally:
            self.close()

    def _menu(self) -> None:
        self.ensure_login()
        self.ensure_fresh()
        menu = [
            Option("📊 Overview", self.overview),
            Option("🧹 Clean up old chats", self.clean_old),
            Option("📭 Channels & groups I don't read", self.not_reading),
            Option("📁 Sort into folders", self.sort_folders),
            Option("↩️  Undo", self.undo),
            Option("🔄 Rescan now", lambda: self.ensure_fresh(force=True)),
            Option("🚪 Exit", None),
        ]
        while True:
            self.console.print()
            screen = self.ask.select("What do you want to do?", menu)
            if screen is None:
                return
            screen()

    def ensure_login(self) -> None:
        async def check(tg):
            if not await tg.is_authorized():
                self.console.print("[bold]Log in to Telegram[/bold] (phone, code, 2FA password if set)")
                me = await tg.login()
                self.console.print(f"[green]Logged in as {self.text(me.first_name)}[/green]")
        self.telegram(check)

    def ensure_fresh(self, force: bool = False) -> None:
        with self.repo() as repo:
            last = repo.last_scan
            if not force and last and utcnow() - last < SCAN_MAX_AGE:
                return
            with self.console.status("Reading your chats…"):
                result = self.telegram(lambda tg: Scanner(tg, repo).run())
        self.console.print(f"[dim]Scanned {result.chats} chats, {result.folders} folders.[/dim]")

    # screens

    def overview(self) -> None:
        chats, folders, repo = self.load()
        with repo:
            scanned_at, limits = repo.last_scan, repo.folder_limits
        report = CleanupReporter(chats, folders, scanned_at).build()
        OverviewView(self.console, self.text).render(chats, report, FolderIndex(folders, chats),
                                                     limits, scanned_at)

    def clean_old(self) -> None:
        options = [Option("All types", ALL_TYPES)] + [Option(label, t, on) for label, t, on in TYPES]
        types = self.ask.checkbox("Which chats?", options)
        if not types:
            return
        if ALL_TYPES in types:
            types = list(ChatType)
        age = self.ask.select("No message for at least…", [Option(label, d) for label, d in AGES])
        if age is None:
            return
        if age == 0 and not self.ask.confirm("Include chats of any age (also active ones)?", default=False):
            return
        action = self.ask.select("What to do with them?", [Option(label, a) for label, a in CLEAN_ACTIONS])
        if action is None:
            return
        self._pick_and_run(ChatFilter(types=types, idle_days=age or None), action)

    def not_reading(self) -> None:
        unread = self.ask.select("Channels/groups with at least…",
                                 [Option(f"{n:,} unread", n) for n in UNREAD_LEVELS])
        if unread is None:
            return
        action = self.ask.select("What to do with them?", [Option(label, a) for label, a in UNREAD_ACTIONS])
        if action is None:
            return
        flt = ChatFilter(types=[ChatType.CHANNEL, ChatType.GROUP], unread_min=unread,
                         muted=False if action == CleanupAction.MUTE else None)
        self._pick_and_run(flt, action)

    def sort_folders(self) -> None:
        chats, folders, repo = self.load()
        with repo:
            scanned_at, limits = repo.last_scan, repo.folder_limits or FolderLimits()
        editable = [f for f in folders if f.kind == FolderKind.FILTER]
        options = [Option(f"{f.label} ({len(f.listed)} chats)", f) for f in editable]
        options.append(Option("＋ New folder…", "new"))
        choice = self.ask.select("Which folder?", options)
        if choice is None:
            return
        if choice == "new":
            if len(folders) >= limits.folders:
                self.console.print(f"[red]You already have {len(folders)}/{limits.folders} folders.[/red]")
                return
            name = (self.ask.text(f"Folder name (max {FOLDER_NAME_MAX} characters):") or "").strip()
            if not name:
                return
            if len(name) > FOLDER_NAME_MAX:
                self.console.print(f"[red]Too long ({len(name)} characters).[/red]")
                return
            folder = next((f for f in folders if f.title == name), None)
        else:
            folder, name = choice, choice.title
        if folder and folder.kind != FolderKind.FILTER:
            self.console.print("[red]That's a shared folder; it can't be edited.[/red]")
            return

        words = self.ask.text("Words to look for in channel/group names (space-separated):")
        if not words or not words.strip():
            return
        flt = ChatFilter.keywords(words, types=[ChatType.CHANNEL, ChatType.GROUP])
        already = folder.listed if folder else set()
        found = [c for c in chats if flt.matches(c, scanned_at) and c.id not in already]
        found.sort(key=lambda c: c.title.casefold())
        if not found:
            self.console.print("No matching channels/groups that aren't already in it.")
            return

        index = FolderIndex(folders, chats)
        picked = self._review(found, index, scanned_at, f"Add to '{name}'")
        if not picked:
            return
        room = limits.chats_per_folder - len(already)
        if len(picked) > room:
            self.console.print(f"[red]'{name}' has room for {room} more chats; "
                               f"you picked {len(picked)}.[/red]")
            return
        if not self.ask.confirm(f"Add {len(picked)} chats to '{name}'?", default=False):
            return

        async def work(tg):
            editor = FolderEditor(tg, self.store, sleep=self.sleep)
            result = await editor.add_chats(name, [c.id for c in picked])
            await self._save_folders(tg)
            return result
        self.result_view.folder_write(self.telegram(work))

    def undo(self) -> None:
        options = []
        backups = self.store.list()
        if backups:
            last = self.store.load(backups[-1])
            options.append(Option(f"Undo last folder change ({last.reason}, "
                                  f"{last.created_at:%Y-%m-%d %H:%M} UTC)", "folders"))
        batch = self.log.last_undoable_batch()
        if batch:
            options.append(Option(f"Undo last {batch[0].action.value} of {len(batch)} chats "
                                  f"({batch[0].at:%Y-%m-%d %H:%M} UTC)", "batch"))
        options.append(Option("Show chats I left / deleted (rejoin links)", "left"))
        choice = self.ask.select("Undo what?", options)
        if choice == "folders":
            self._undo_folders(backups[-1])
        elif choice == "batch":
            self._undo_batch(batch)
        elif choice == "left":
            self.result_view.left_chats(self.log.left())

    # helpers

    def _pick_and_run(self, flt: ChatFilter, action: CleanupAction) -> None:
        chats, folders, repo = self.load()
        with repo:
            scanned_at, self_id = repo.last_scan, repo.self_id
        selector = ChatSelector(chats, folders, scanned_at, self_id)
        selection = selector.select(flt)
        self.chats_view.protected(selection.protected)
        if not selection.chats:
            self.console.print("Nothing found.")
            return
        picked = self._review(selection.chats, selector.index, scanned_at, VERBS[action])
        if not picked:
            return
        if action.irreversible:
            self.chats_view.leave_warning(picked)
        if not self.ask.confirm(f"{VERBS[action]} {len(picked)} chats?", default=False):
            return
        self._run(action, picked)

    def _review(self, chats: list[Chat], index: FolderIndex, scanned_at, verb: str) -> list[Chat] | None:
        self.console.print(f"\n[bold]{len(chats)} found[/bold]")
        self.chats_view.render(chats, index, scanned_at)
        how = self.ask.select("Which ones?", [
            Option(f"All {len(chats)}", "all"),
            Option("Let me untick some", "pick"),
            Option("Cancel", None),
        ])
        if how == "all":
            return chats
        if how == "pick":
            options = [Option(f"{c.title}  ({c.type.value})", c, checked=True) for c in chats]
            return self.ask.checkbox(f"{verb}: untick the ones to keep", options)
        return None

    def _run(self, action: CleanupAction, chats: list[Chat], undo_of: str | None = None) -> CleanupResult:
        with self.repo() as repo:
            def save(chat: Chat) -> None:  # per chat: an interrupted run keeps its progress
                repo.update_chats([chat.id], action.local_update)
                repo.commit()

            async def work(tg):
                executor = CleanupExecutor(tg, self.log)
                return await executor.run(action, chats, lambda c: self.result_view.step(action, c),
                                          save, undo_of=undo_of)
            try:
                result = self.telegram(work)
            except KeyboardInterrupt:
                self.console.print("[yellow]Stopped. Finished chats are saved.[/yellow]")
                raise
        self.result_view.cleanup(result)
        return result

    def _undo_folders(self, path) -> None:
        backup = self.store.load(path)

        async def preview(tg):
            return await Restorer(tg, self.store, backup, sleep=self.sleep).preview()
        p = self.telegram(preview)
        if p.empty:
            self.console.print("Folders already match.")
            return
        self.result_view.restore_preview(p)
        if not self.ask.confirm("Restore folders like this?", default=False):
            return

        async def work(tg):
            result = await Restorer(tg, self.store, backup, sleep=self.sleep).restore()
            await self._save_folders(tg)
            return result
        self.result_view.folder_write(self.telegram(work))

    def _undo_batch(self, batch) -> None:
        action = batch[0].action.inverse
        with self.repo() as repo:
            by_id = {c.id: c for c in repo.chats()}
        chats = [by_id[e.chat_id] for e in batch if e.chat_id in by_id]
        if not chats:
            self.console.print("Those chats are gone; nothing to undo.")
            return
        if not self.ask.confirm(f"{VERBS[action]} {len(chats)} chats?", default=False):
            return
        self._run(action, chats, undo_of=batch[0].batch)

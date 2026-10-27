from collections import Counter
from datetime import datetime

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from tgo.applier import RestorePreview, WriteResult
from tgo.cleanup import ActionLogEntry, CleanupAction, CleanupResult
from tgo.folder_index import FolderIndex
from tgo.models import Chat, ChatType, FolderLimits
from tgo.report import CleanupReport
from tgo.selection import ProtectedChat
from tgo.text import TerminalText


class BaseView:
    def __init__(self, console: Console, text: TerminalText):
        self.console = console
        self.t = text

    def chat_title(self, chat: Chat) -> str:
        title = self.t(chat.title)
        if chat.username:
            title += f" [dim]@{chat.username}[/dim]"
        if chat.muted:
            title += " 🔕"
        if chat.archived:
            title += " [dim](archived)[/dim]"
        return title

    def names(self, names: list[str]) -> str:
        return ", ".join(self.t(n) for n in names)


class OverviewView(BaseView):
    def render(self, chats: list[Chat], report: CleanupReport, index: FolderIndex,
               limits: FolderLimits | None, scanned_at: datetime) -> None:
        types = Counter(c.type for c in chats)
        o = report.options
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_row("Channels", str(types[ChatType.CHANNEL]))
        t.add_row("Groups", str(types[ChatType.GROUP]))
        t.add_row("Private chats", str(types[ChatType.USER]))
        t.add_row("Bots", str(types[ChatType.BOT]))
        t.add_row("", "")
        t.add_row(f"[yellow]Dead[/yellow] (no message for {o.dead_days}+ days)", str(len(report.dead)))
        t.add_row(f"[yellow]Noisy[/yellow] ({o.unread_min:,}+ unread)", str(len(report.unread_pileup)))
        t.add_row("[yellow]Channels/groups in no folder[/yellow]", str(len(report.unfoldered)))
        self.console.print(f"\n[bold]Your Telegram[/bold] [dim](scan {scanned_at:%Y-%m-%d %H:%M} UTC)[/dim]")
        self.console.print(t)

        limit = limits.folders if limits else 10
        per = limits.chats_per_folder if limits else 100
        ft = Table(title=f"Folders {len(index.folders)}/{limit}", title_justify="left")
        ft.add_column("Folder")
        ft.add_column("Chats", justify="right")
        for f in index.folders:
            ft.add_row(self.t(f.label), f"{len(f.listed)}/{per}")
        self.console.print(ft)


class ChatTableView(BaseView):
    def render(self, chats: list[Chat], index: FolderIndex, scanned_at: datetime,
               limit: int = 15) -> None:
        t = Table()
        for col in ("Title", "Type", "Members", "Unread", "Idle days", "Folders"):
            t.add_column(col, justify="right" if col in ("Members", "Unread", "Idle days") else "left")
        for c in chats[:limit]:
            idle = (scanned_at - c.last_msg_date).days if c.last_msg_date else None
            t.add_row(self.chat_title(c), c.type.value, str(c.members or ""), str(c.unread or ""),
                      "never" if idle is None else str(idle),
                      self.names([f.title for f in index.showing(c)]))
        self.console.print(t)
        if len(chats) > limit:
            self.console.print(f"[dim]…and {len(chats) - limit} more[/dim]")

    def protected(self, protected: list[ProtectedChat]) -> None:
        if protected:
            reasons = Counter(p.reason.value for p in protected)
            self.console.print("[dim]Skipped (protected): " +
                               ", ".join(f"{n} {r}" for r, n in reasons.items()) + "[/dim]")

    def leave_warning(self, chats: list[Chat]) -> None:
        private = sum(1 for c in chats if not c.link and c.type in (ChatType.CHANNEL, ChatType.GROUP))
        dms = sum(1 for c in chats if c.type in (ChatType.USER, ChatType.BOT))
        self.console.print("[red]This can't be undone.[/red]")
        if private:
            self.console.print(f"[red]{private} private channels/groups need a new invite link to rejoin.[/red]")
        if dms:
            self.console.print(f"[red]{dms} private chats/bots: history is deleted (for you only).[/red]")


class ResultView(BaseView):
    def step(self, action: CleanupAction, chat: Chat) -> None:
        self.console.print(f"  … {action.describe(chat)} {self.t(chat.title)}")

    def cleanup(self, r: CleanupResult) -> None:
        color = "green" if not (r.failed or r.stopped) else "yellow"
        msg = f"{len(r.done)} done, {len(r.failed)} failed"
        if r.skipped:
            msg += f", {len(r.skipped)} not attempted"
        self.console.print(f"[{color}]{msg}[/{color}]")
        for f in r.failed:
            self.console.print(f"  [red]✗[/red] {self.t(f.chat.title)}: {escape(f.error)}")
        if r.stopped:
            self.console.print(f"[yellow]{r.stopped}[/yellow]")

    def folder_write(self, r: WriteResult) -> None:
        if r.ok:
            self.console.print("[green]Done.[/green] [dim](undo from the Undo menu)[/dim]")
        else:
            self.console.print(f"[red]Failed: {escape(r.error or '')}[/red] Nothing else was changed.")

    def restore_preview(self, p: RestorePreview) -> None:
        for title in p.restore:
            self.console.print(f"  [yellow]~ restore[/yellow] {self.t(title)}")
        for title in p.delete:
            self.console.print(f"  [red]- remove[/red] {self.t(title)} [dim](created after)[/dim]")
        if p.reorder:
            self.console.print("  [blue]↕ folder order[/blue]")

    def left_chats(self, entries: list[ActionLogEntry], limit: int = 30) -> None:
        if not entries:
            self.console.print("You haven't left any chats with tgo.")
            return
        t = Table(title=f"Left / deleted ({len(entries)})", title_justify="left")
        for col in ("When (UTC)", "Chat", "Type", "Rejoin"):
            t.add_column(col)
        for e in entries[:limit]:
            t.add_row(f"{e.at:%Y-%m-%d %H:%M}", self.t(e.title), e.type.value, e.link or "[dim]—[/dim]")
        self.console.print(t)
        if len(entries) > limit:
            self.console.print(f"[dim]…and {len(entries) - limit} more (full list: data/actions.jsonl)[/dim]")

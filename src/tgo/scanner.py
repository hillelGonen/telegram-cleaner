from collections.abc import Callable

from tgo.models import ScanResult, utcnow
from tgo.repository import Repository
from tgo.telegram import TelegramGateway

# (stage, done, total) — stage is "chats"
ProgressFn = Callable[[str, int, int | None], None]


class Scanner:
    """Pulls chats, folders and limits from Telegram into the repository."""

    def __init__(self, gateway: TelegramGateway, repo: Repository,
                 on_progress: ProgressFn = lambda *a: None):
        self.gateway = gateway
        self.repo = repo
        self.on_progress = on_progress

    async def run(self) -> ScanResult:
        await self.gateway.ensure_authorized()
        seen_at = utcnow()

        chats = []
        async for chat in self.gateway.chats(seen_at):
            chats.append(chat)
            self.on_progress("chats", len(chats), None)
        self.repo.save_chats(chats)
        removed = self.repo.remove_unseen(seen_at)

        folders = await self.gateway.folders()
        self.repo.replace_folders(folders)
        self.repo.folder_limits = await self.gateway.folder_limits()

        self.repo.self_id = await self.gateway.self_id()
        self.repo.last_scan = seen_at
        self.repo.commit()
        return ScanResult(chats=len(chats), removed=removed, folders=len(folders))

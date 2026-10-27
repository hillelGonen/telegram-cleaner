from tgo.models import Chat, Folder


class FolderIndex:
    """Which folders show / list each chat, computed once per command."""

    def __init__(self, folders: list[Folder], chats: list[Chat]):
        self.folders = folders
        self._shown: dict[int, list[Folder]] = {
            c.id: [f for f in folders if f.shows(c)] for c in chats
        }

    def showing(self, chat: Chat) -> list[Folder]:
        return self._shown.get(chat.id, [])

    def in_no_folder(self, chat: Chat) -> bool:
        return not self.showing(chat)

    def listing(self, chat: Chat) -> list[Folder]:
        """Folders the chat was explicitly added to (ignores category flags)."""
        return [f for f in self.folders if chat.id in f.listed]

    def shown_count(self, folder: Folder) -> int:
        return sum(1 for fs in self._shown.values() if folder in fs)

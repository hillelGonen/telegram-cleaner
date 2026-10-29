"""Fake Telegram + fake prompter for tests. Folders are real TL objects."""
import asyncio

from telethon import utils
from telethon.errors import RPCError
from telethon.tl.types import (
    DialogFilter,
    DialogFilterChatlist,
    DialogFilterDefault,
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
    TextWithEntities,
)

from tgo.prompter import Option
from tgo.telegram import TelethonMapper

SELF_ID = 999


def ch(n: int) -> InputPeerChannel:
    return InputPeerChannel(n, n * 10)


def marked(n: int) -> int:
    """Marked (tgo) id of channel n."""
    return utils.get_peer_id(ch(n))


def title(t: str) -> TextWithEntities:
    return TextWithEntities(text=t, entities=[])


def live_filters() -> list:
    return [
        DialogFilterDefault(),
        DialogFilter(id=2, title=title("News"), pinned_peers=[ch(5)], include_peers=[ch(1), ch(2)],
                     exclude_peers=[], broadcasts=True, emoticon="📰", color=3),
        DialogFilterChatlist(id=3, title=title("Shared"), pinned_peers=[], include_peers=[ch(4)]),
    ]


class FakeGateway:
    """Folder + chat-action side of TelegramGateway, in memory."""

    def __init__(self, filters: list | None = None):
        self.filters = filters if filters is not None else live_filters()
        self.calls: list[tuple] = []
        self.fail: dict[str, Exception] = {}
        self.connects = 0
        self.in_flight = self.max_in_flight = 0

    async def __aenter__(self):
        self.connects += 1
        return self

    async def __aexit__(self, *exc):
        self.connects -= 1

    async def is_authorized(self):
        return True

    async def ensure_authorized(self):
        pass

    async def self_id(self):
        return SELF_ID

    def _record(self, *call):
        self.calls.append(call)
        key = " ".join(str(c) for c in call)
        for pattern, err in self.fail.items():
            if pattern in key:
                raise err

    # folders

    async def raw_filters(self):
        return list(self.filters)

    async def folders(self):
        mapped = [TelethonMapper.folder(f, i, SELF_ID) for i, f in enumerate(self.filters)]
        return [f for f in mapped if f]

    async def input_peer(self, peer_id: int):
        real, cls = utils.resolve_id(peer_id)
        if cls.__name__ == "PeerChannel":
            return ch(real)
        return InputPeerChat(real) if cls.__name__ == "PeerChat" else InputPeerUser(real, 0)

    async def save_filter(self, filter_id, flt):
        self._record("save", filter_id)
        for i, f in enumerate(self.filters):
            if TelethonMapper.filter_id(f) == filter_id:
                self.filters[i] = flt
                return
        self.filters.append(flt)

    async def delete_filter(self, filter_id):
        self._record("delete", filter_id)
        self.filters = [f for f in self.filters if TelethonMapper.filter_id(f) != filter_id]

    async def reorder_filters(self, order):
        self._record("reorder")
        by_id = {TelethonMapper.filter_id(f): f for f in self.filters}
        self.filters = [by_id[i] for i in order]

    # chat actions

    async def leave_chat(self, chat_id):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)  # let other tasks start, like a network call would
            self._record("leave", chat_id)
        finally:
            self.in_flight -= 1

    async def set_archived(self, chat_id, archived):
        self._record("archive" if archived else "unarchive", chat_id)

    async def set_archived_many(self, chat_ids, archived):
        self._record("archive_many" if archived else "unarchive_many", tuple(chat_ids))

    async def set_muted(self, chat_id, muted):
        self._record("mute" if muted else "unmute", chat_id)

    async def mark_read(self, chat_id):
        self._record("read", chat_id)


def rpc_error(message: str) -> RPCError:
    return RPCError(None, message, 400)


class FakePrompter:
    """Scripted answers, in order.
    select: label substring · checkbox: list of label substrings, or "all" (keep pre-ticked)
    text: str · confirm: bool · None anywhere = user cancelled."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[str] = []

    def _next(self, message):
        self.asked.append(message)
        assert self.answers, f"no scripted answer for: {message}"
        return self.answers.pop(0)

    @staticmethod
    def _find(options: list[Option], label: str) -> Option:
        hits = [o for o in options if label in o.label]
        assert hits, f"{label!r} not in {[o.label for o in options]}"
        return hits[0]

    def select(self, message, options):
        a = self._next(message)
        return None if a is None else self._find(options, a).value

    def checkbox(self, message, options):
        a = self._next(message)
        if a is None:
            return None
        if a == "all":
            return [o.value for o in options if o.checked]
        return [self._find(options, label).value for label in a]

    def text(self, message, default=""):
        return self._next(message)

    def confirm(self, message, default=False):
        return self._next(message)

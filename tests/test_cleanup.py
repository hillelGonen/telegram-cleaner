import asyncio

import pytest
from conftest import SCAN, make_chat
from fakes import FakeGateway, rpc_error
from pydantic import ValidationError
from telethon.errors import FloodWaitError

from tgo.cleanup import ActionLog, CleanupAction, CleanupExecutor
from tgo.models import ChatType
from tgo.repository import Repository
from tgo.selection import ChatFilter, ChatSelector, Protection

SELF = 999
CHATS = [
    make_chat(1, title="Old movies", idle_days=400, members=50, username="oldmov"),
    make_chat(2, title="Live news", idle_days=1, unread=900, muted=True),
    make_chat(3, ChatType.GROUP, title="Dead group", idle_days=200),
    make_chat(4, title="My channel", idle_days=500, creator=True),
    make_chat(5, title="Pinned old", idle_days=300, pinned=True),
    make_chat(6, ChatType.USER, title="Dana", idle_days=900),
    make_chat(SELF, ChatType.USER, title="Me", idle_days=999),
    make_chat(777000, ChatType.USER, title="Telegram", idle_days=999),
    make_chat(7, title="Never posted", idle_days=None),
]


def select(**kw):
    return ChatSelector(CHATS, [], SCAN, SELF).select(ChatFilter(**kw))


def titles(sel):
    return [c.title for c in sel.chats]


# selection

def test_idle_filter_and_protections():
    sel = select(idle_days=180)
    assert titles(sel) == ["Never posted", "Old movies", "Dead group", "Dana"]  # by type, then title
    assert {p.chat.title: p.reason for p in sel.protected} == {
        "My channel": Protection.CREATOR, "Pinned old": Protection.PINNED,
        "Me": Protection.SELF, "Telegram": Protection.SERVICE,
    }


def test_types_unread_muted_title():
    assert titles(select(idle_days=180, types=[ChatType.CHANNEL])) == ["Never posted", "Old movies"]
    assert titles(select(unread_min=500, muted=True)) == ["Live news"]
    assert titles(select(unread_min=500, muted=False)) == []
    assert titles(select(title="^old")) == ["Old movies"]  # "Pinned old" is protected


def test_keywords_filter():
    flt = ChatFilter.keywords("news  movies", types=[ChatType.CHANNEL])
    assert flt.title == "news|movies"
    assert titles(ChatSelector(CHATS, [], SCAN, SELF).select(flt)) == ["Live news", "Old movies"]
    assert ChatFilter.keywords("a.b (c").title == r"a\.b|\(c"  # literal, not regex
    assert ChatFilter.keywords("   ").title is None


def test_filter_validation():
    with pytest.raises(ValidationError):
        ChatFilter(title="(")
    with pytest.raises(ValidationError):
        ChatFilter(idle_days=-1)


# executor + log

async def no_sleep(_):
    pass


def run(executor, action, chats, **kw):
    return asyncio.run(executor.run(action, chats, **kw))


@pytest.mark.parametrize("action,call", [
    (CleanupAction.LEAVE, "leave"), (CleanupAction.ARCHIVE, "archive"),
    (CleanupAction.UNARCHIVE, "unarchive"), (CleanupAction.MUTE, "mute"),
    (CleanupAction.UNMUTE, "unmute"), (CleanupAction.READ, "read"),
])
def test_executor_dispatch(tmp_path, action, call):
    gw = FakeGateway()
    result = run(CleanupExecutor(gw, ActionLog(tmp_path / "a.jsonl"), no_sleep), action, CHATS[:2])
    assert gw.calls == [(call, 1), (call, 2)]
    assert [c.id for c in result.done] == [1, 2]


def test_executor_continues_on_error_and_logs(tmp_path):
    log = ActionLog(tmp_path / "a.jsonl")
    gw = FakeGateway()
    gw.fail["leave 2"] = rpc_error("CHANNEL_PRIVATE")
    result = run(CleanupExecutor(gw, log, no_sleep), CleanupAction.LEAVE, CHATS[:3])
    assert [c.id for c in result.done] == [1, 3]
    assert "CHANNEL_PRIVATE" in result.failed[0].error
    entries = log.entries()
    assert [(e.chat_id, e.ok) for e in entries] == [(1, True), (2, False), (3, True)]
    assert {e.batch for e in entries} == {result.batch}
    assert entries[0].link == "https://t.me/oldmov" and entries[1].link is None
    assert [e.chat_id for e in log.left()] == [3, 1]  # newest first, ok only


def test_executor_stops_on_flood_wait(tmp_path):
    gw = FakeGateway()
    gw.fail["leave 2"] = FloodWaitError(None, capture=600)
    result = run(CleanupExecutor(gw, ActionLog(tmp_path / "a.jsonl"), no_sleep),
                 CleanupAction.LEAVE, CHATS[:3])
    assert [c.id for c in result.done] == [1]
    assert [c.id for c in result.skipped] == [2, 3]
    assert "600" in result.stopped


def test_pacing_and_progress_callback(tmp_path):
    sleeps, saved = [], []

    async def record(seconds):
        sleeps.append(seconds)

    ex = CleanupExecutor(FakeGateway(), ActionLog(tmp_path / "a.jsonl"), record)
    chats = [CHATS[0], CHATS[5], CHATS[2]]  # channel, user, group
    run(ex, CleanupAction.LEAVE, chats, on_done=lambda c: saved.append(c.id))
    assert sleeps == [1.0, 0.3]  # pace by the chat just handled; no sleep after the last
    assert saved == [1, 6, 3]
    sleeps.clear()
    run(ex, CleanupAction.LEAVE, chats, delay=0)
    assert sleeps == [0, 0]


def test_last_undoable_batch(tmp_path):
    log = ActionLog(tmp_path / "a.jsonl")
    ex = CleanupExecutor(FakeGateway(), log, no_sleep)
    assert log.last_undoable_batch() == []
    archived = run(ex, CleanupAction.ARCHIVE, CHATS[:2])
    run(ex, CleanupAction.LEAVE, CHATS[2:3])  # not undoable, doesn't hide the archive batch
    batch = log.last_undoable_batch()
    assert [e.chat_id for e in batch] == [1, 2] and batch[0].batch == archived.batch

    run(ex, CleanupAction.UNARCHIVE, CHATS[:2], undo_of=archived.batch)
    assert log.last_undoable_batch() == []  # undone


def test_old_log_without_batches_loads(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_text('{"at":"2026-10-26T05:45:50Z","action":"leave","chat_id":1,"title":"x",'
                    '"type":"user","ok":true}\n', encoding="utf-8")
    log = ActionLog(path)
    assert log.entries()[0].batch is None and len(log.left()) == 1
    assert log.last_undoable_batch() == []


def test_action_properties():
    assert CleanupAction.LEAVE.irreversible and CleanupAction.LEAVE.inverse is None
    assert CleanupAction.ARCHIVE.inverse == CleanupAction.UNARCHIVE
    assert CleanupAction.MUTE.inverse == CleanupAction.UNMUTE
    assert CleanupAction.READ.local_update == {"unread": 0}
    assert CleanupAction.LEAVE.describe(CHATS[5]) == "delete chat"
    assert CleanupAction.LEAVE.describe(CHATS[0]) == "leave"


def test_repository_update_chats(tmp_path):
    with Repository(tmp_path / "t.db") as repo:
        repo.save_chats(CHATS[:3])
        repo.update_chats([1], CleanupAction.LEAVE.local_update)
        repo.update_chats([2], CleanupAction.READ.local_update)
        repo.update_chats([3], CleanupAction.ARCHIVE.local_update)
        by_id = {c.id: c for c in repo.chats()}
    assert 1 not in by_id and by_id[2].unread == 0 and by_id[3].archived

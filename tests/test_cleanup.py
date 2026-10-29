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

def run(executor, action, chats, **kw):
    return asyncio.run(executor.run(action, chats, **kw))


@pytest.mark.parametrize("action,calls", [
    (CleanupAction.LEAVE, [("leave", 1), ("leave", 2)]),
    (CleanupAction.MUTE, [("mute", 1), ("mute", 2)]),
    (CleanupAction.UNMUTE, [("unmute", 1), ("unmute", 2)]),
    (CleanupAction.READ, [("read", 1), ("read", 2)]),
    (CleanupAction.ARCHIVE, [("archive_many", (1, 2))]),  # one request for many chats
    (CleanupAction.UNARCHIVE, [("unarchive_many", (1, 2))]),
])
def test_executor_dispatch(tmp_path, action, calls):
    gw = FakeGateway()
    result = run(CleanupExecutor(gw, ActionLog(tmp_path / "a.jsonl")), action, CHATS[:2])
    assert sorted(gw.calls) == sorted(calls)
    assert [c.id for c in result.done] == [1, 2]


def test_executor_continues_on_error_and_logs(tmp_path):
    log = ActionLog(tmp_path / "a.jsonl")
    gw = FakeGateway()
    gw.fail["leave 2"] = rpc_error("CHANNEL_PRIVATE")
    result = run(CleanupExecutor(gw, log), CleanupAction.LEAVE, CHATS[:3])
    assert [c.id for c in result.done] == [1, 3]
    assert "CHANNEL_PRIVATE" in result.failed[0].error
    entries = log.entries()
    assert sorted((e.chat_id, e.ok) for e in entries) == [(1, True), (2, False), (3, True)]
    assert {e.batch for e in entries} == {result.batch}
    links = {e.chat_id: e.link for e in entries}
    assert links[1] == "https://t.me/oldmov" and links[2] is None
    assert sorted(e.chat_id for e in log.left()) == [1, 3]  # ok only


def test_executor_stops_on_flood_wait(tmp_path):
    gw = FakeGateway()
    gw.fail["leave 2"] = FloodWaitError(None, capture=600)
    result = run(CleanupExecutor(gw, ActionLog(tmp_path / "a.jsonl")),
                 CleanupAction.LEAVE, CHATS[:5], concurrency=1)
    assert [c.id for c in result.done] == [1]
    assert [c.id for c in result.skipped] == [2, 3, 4, 5]
    assert "600" in result.stopped


def test_parallel_with_limit_and_progress_callback(tmp_path):
    gw = FakeGateway()
    saved = []
    many = [make_chat(i, idle_days=1) for i in range(100, 120)]
    result = run(CleanupExecutor(gw, ActionLog(tmp_path / "a.jsonl")), CleanupAction.LEAVE, many,
                 on_done=lambda c: saved.append(c.id))
    assert 1 < gw.max_in_flight <= CleanupAction.LEAVE.concurrency
    assert sorted(saved) == [c.id for c in many]
    assert [c.id for c in result.done] == [c.id for c in many]  # reported in input order


def test_archive_batches_and_falls_back_per_chat(tmp_path):
    log = ActionLog(tmp_path / "a.jsonl")
    many = [make_chat(i, idle_days=1) for i in range(1, 251)]
    gw = FakeGateway()
    ex = CleanupExecutor(gw, log)
    run(ex, CleanupAction.ARCHIVE, many)
    assert [len(c[1]) for c in gw.calls] == [100, 100, 50]

    gw = FakeGateway()
    gw.fail["archive_many"] = rpc_error("PEER_ID_INVALID")
    gw.fail["archive 2"] = rpc_error("PEER_ID_INVALID")
    result = run(CleanupExecutor(gw, log), CleanupAction.ARCHIVE, many[:3])
    assert [c.id for c in result.done] == [1, 3] and result.failed[0].chat.id == 2


def test_last_undoable_batch(tmp_path):
    log = ActionLog(tmp_path / "a.jsonl")
    ex = CleanupExecutor(FakeGateway(), log)
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

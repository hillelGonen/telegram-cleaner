import asyncio

import pytest
from fakes import SELF_ID, FakeGateway, ch, live_filters, marked, rpc_error

from tgo.applier import FolderEditor, Restorer
from tgo.backup import BackupStore, FolderBackup
from tgo.models import Folder, FolderLimits
from tgo.telegram import TelethonMapper


async def no_sleep(_):
    pass


def run(coro):
    return asyncio.run(coro)


def include_ids(flt) -> list[int]:
    return [TelethonMapper.peer_ids([p], SELF_ID)[0] for p in flt.include_peers]


@pytest.fixture
def store(tmp_path):
    return BackupStore(tmp_path / "backups")


def test_add_to_existing_folder_keeps_everything_else(store):
    gw = FakeGateway()
    editor = FolderEditor(gw, store, sleep=no_sleep)
    result = run(editor.add_chats("News", [marked(3), marked(1), marked(5)]))
    assert result.ok and result.done == ["update News"]
    news = gw.filters[1]
    assert include_ids(news) == [marked(1), marked(2), marked(3)]  # 1 present, 5 pinned
    assert news.pinned_peers == [ch(5)] and news.broadcasts and news.color == 3 and news.emoticon == "📰"
    assert result.backup.exists()


def test_create_new_folder_with_free_id(store):
    gw = FakeGateway()
    result = run(FolderEditor(gw, store, sleep=no_sleep).add_chats("Tech", [marked(6)]))
    assert result.done == ["create Tech"]
    tech = gw.filters[-1]
    assert (tech.id, tech.title.text, include_ids(tech)) == (4, "Tech", [marked(6)])


def test_shared_folder_name_creates_new_not_edit(store):
    gw = FakeGateway()
    run(FolderEditor(gw, store, sleep=no_sleep).add_chats("Shared", [marked(6)]))
    assert gw.filters[2].include_peers == [ch(4)]  # shared one untouched


def test_failure_reported(store):
    gw = FakeGateway()
    gw.fail["save"] = rpc_error("FILTER_INCLUDE_INVALID")
    result = run(FolderEditor(gw, store, sleep=no_sleep).add_chats("News", [marked(3)]))
    assert not result.ok and "FILTER_INCLUDE_INVALID" in result.error


def test_restore_undoes_folder_change(store):
    original = live_filters()
    gw = FakeGateway()
    run(FolderEditor(gw, store, sleep=no_sleep).add_chats("News", [marked(3)]))
    applied = run(FolderEditor(gw, store, sleep=no_sleep).add_chats("Tech", [marked(6)]))

    # latest backup = before "Tech" was created
    restorer = Restorer(gw, store, store.load(applied.backup), sleep=no_sleep)
    assert run(restorer.preview()).delete == ["Tech"]
    assert run(restorer.restore()).ok
    assert "Tech" not in [getattr(getattr(f, "title", None), "text", None) for f in gw.filters]

    first = store.list()[0]
    assert run(Restorer(gw, store, store.load(first), sleep=no_sleep).restore()).ok
    assert [bytes(f) for f in gw.filters] == [bytes(f) for f in original]


def test_restore_recreates_deleted_and_reorders(store):
    gw = FakeGateway()
    backup = store.load(store.save(FolderBackup.of(gw.filters, SELF_ID, "t")))
    gw.filters = [gw.filters[2], gw.filters[0]]  # News deleted, order changed
    restorer = Restorer(gw, store, backup, sleep=no_sleep)
    assert run(restorer.preview()).restore == ["News"]
    assert run(restorer.restore()).ok
    assert [TelethonMapper.filter_id(f) for f in gw.filters] == [0, 2, 3]


def test_backup_store_roundtrip(store):
    filters = live_filters()
    path = store.save(FolderBackup.of(filters, SELF_ID, "test"))
    again = store.save(FolderBackup.of(filters, SELF_ID, "test"))
    assert path != again and store.resolve("latest") == again
    loaded = store.load(path)
    assert [bytes(s.tl()) for s in loaded.folders] == [bytes(f) for f in filters]
    assert loaded.titles == ["News", "Shared"]
    assert isinstance(loaded.folders[1].folder, Folder)


# moves from the browser sorter

LIMITS = FolderLimits()


def test_moves_between_folders_and_out(store):
    gw = FakeGateway()  # News: include 1, 2 · pinned 5 · Shared (chatlist): 4
    moves = {marked(1): "Tech", marked(5): None, marked(3): "News"}
    result = run(FolderEditor(gw, store, sleep=no_sleep).apply_moves(moves, LIMITS))
    assert result.ok and result.done == ["update News", "create Tech"]
    news, tech = gw.filters[1], gw.filters[-1]
    assert include_ids(news) == [marked(2), marked(3)] and news.pinned_peers == []
    assert news.broadcasts and news.color == 3  # untouched settings
    assert (tech.title.text, include_ids(tech)) == ("Tech", [marked(1)])
    assert gw.filters[2].include_peers == [ch(4)]  # shared folder never edited


def test_moves_checked_before_writing(store):
    gw = FakeGateway()
    gw.filters[1].broadcasts = False  # no type rules: may not become empty
    moves = {marked(1): None, marked(2): None, marked(5): None}
    result = run(FolderEditor(gw, store, sleep=no_sleep).apply_moves(moves, LIMITS))
    assert not result.ok and "would be empty" in result.problems[0]
    assert gw.calls == [] and result.backup is None and store.list() == []

    small = FolderLimits(folders=3, chats_per_folder=2)
    result = run(FolderEditor(gw, store, sleep=no_sleep).apply_moves(
        {marked(3): "News", marked(6): "A", marked(7): "B"}, small))
    assert any("limit is 2" in p for p in result.problems)
    assert any("Too many folders" in p for p in result.problems)


def test_no_moves_writes_nothing(store):
    gw = FakeGateway()
    result = run(FolderEditor(gw, store, sleep=no_sleep).apply_moves({marked(1): "News"}, LIMITS))
    assert result.ok and result.done == [] and gw.calls == []

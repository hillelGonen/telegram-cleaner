import sqlite3
from datetime import timedelta

from conftest import make_chat, make_folder

from tgo.models import ChatType, FolderLimits
from tgo.repository import Repository


def test_chats_roundtrip_and_remove_unseen(tmp_path, scan_time):
    with Repository(tmp_path / "t.db") as repo:
        repo.save_chats([make_chat(1, title="חדשות", unread=3, members=500), make_chat(2)])

        later = scan_time + timedelta(hours=1)
        repo.save_chats([make_chat(1, title="A2").model_copy(update={"seen_at": later})])
        assert repo.remove_unseen(later) == 1

        [chat] = repo.chats()
        assert (chat.title, chat.members) == ("A2", None)  # rescan replaces values
        assert chat.type == ChatType.CHANNEL
        assert chat.last_msg_date == scan_time - timedelta(days=1)


def test_folders_meta_and_limits(tmp_path, scan_time):
    with Repository(tmp_path / "t.db") as repo:
        assert repo.last_scan is None and repo.folder_limits is None
        repo.replace_folders([make_folder(2, "News", include=[1], broadcasts=True)])
        repo.last_scan = scan_time
        repo.folder_limits = FolderLimits(premium=True, folders=30, chats_per_folder=200)
        repo.commit()

    with Repository(tmp_path / "t.db") as repo:
        [f] = repo.folders()
        assert f.include_peers == [1] and f.flags.broadcasts
        assert repo.last_scan == scan_time
        assert repo.folder_limits.chats_per_folder == 200


def test_migrates_v1_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE chats (id INTEGER PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL,
            username TEXT, about TEXT, members INTEGER, last_msg_date TEXT,
            unread INTEGER NOT NULL DEFAULT 0, muted INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0,
            contact INTEGER NOT NULL DEFAULT 0, full_fetched_at TEXT, seen_at TEXT NOT NULL);
        CREATE TABLE folders (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO chats (id, type, title, seen_at) VALUES (1, 'channel', 'A', '2026-10-25T15:20:56+00:00');
        INSERT INTO meta VALUES ('last_scan', '2026-10-25T15:20:56+00:00');
    """)
    conn.close()

    with Repository(path) as repo:
        assert [c.title for c in repo.chats()] == ["A"]
        assert repo.folders() == []  # old folders dropped, refilled by next scan
        repo.save_chats(repo.chats())  # all current columns exist
        assert repo.get_meta("schema_version") == "3"


def test_migrates_v2_adds_creator_and_forces_rescan(tmp_path, scan_time):
    path = tmp_path / "v2.db"
    with Repository(path) as repo:
        repo.save_chats([make_chat(1)])
        repo.last_scan = scan_time
        repo.commit()
    conn = sqlite3.connect(path)  # turn it back into a v2 db
    conn.executescript("ALTER TABLE chats DROP COLUMN creator; UPDATE meta SET value='2' "
                       "WHERE key='schema_version';")
    conn.commit()
    conn.close()

    with Repository(path) as repo:
        assert repo.chats()[0].creator is False
        assert repo.last_scan is None  # must rescan before acting
        assert repo.get_meta("schema_version") == "3"

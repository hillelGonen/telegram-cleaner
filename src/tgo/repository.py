import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Self

from tgo.models import Chat, Folder, FolderLimits

SCHEMA_VERSION = "3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id            INTEGER PRIMARY KEY,
    type          TEXT NOT NULL,
    title         TEXT NOT NULL,
    username      TEXT,
    about         TEXT,                  -- unused (v1-v3)
    members       INTEGER,
    last_msg_date TEXT,
    unread        INTEGER NOT NULL DEFAULT 0,
    muted         INTEGER NOT NULL DEFAULT 0,
    archived      INTEGER NOT NULL DEFAULT 0,
    pinned        INTEGER NOT NULL DEFAULT 0,
    contact       INTEGER NOT NULL DEFAULT 0,
    creator       INTEGER NOT NULL DEFAULT 0,  -- you own this channel/group
    details_fetched_at TEXT,             -- unused (v1-v3)
    seen_at       TEXT NOT NULL
);

-- Folders are a snapshot replaced on every scan; stored as Pydantic JSON.
CREATE TABLE IF NOT EXISTS folders (
    id       INTEGER PRIMARY KEY,
    position INTEGER NOT NULL,
    data     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns refreshed on every scan.
SCAN_COLS = (
    "type", "title", "username", "members", "last_msg_date",
    "unread", "muted", "archived", "pinned", "contact", "creator", "seen_at",
)


class Repository:
    """SQLite storage for chats, folder snapshots and scan metadata."""

    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def commit(self) -> None:
        self._conn.commit()

    def _migrate(self) -> None:
        version = None
        if self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone():
            version = self.get_meta("schema_version")
        if version is None and self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='chats'"
        ).fetchone():
            # v1: old column name + column-based folders table. Folders refill on next scan.
            self._conn.execute("ALTER TABLE chats RENAME COLUMN full_fetched_at TO details_fetched_at")
            self._conn.execute("DROP TABLE IF EXISTS folders")
            version = "2"
        if version == "2":
            # v3: creator flag. Unknown until rescanned, so force a scan before any command
            # (cleanup must never treat an owned chat as not owned).
            self._conn.execute("ALTER TABLE chats ADD COLUMN creator INTEGER NOT NULL DEFAULT 0")
            self._conn.execute("DELETE FROM meta WHERE key='last_scan'")
        self._conn.executescript(SCHEMA)
        self.set_meta("schema_version", SCHEMA_VERSION)
        self.commit()

    # chats

    def save_chats(self, chats: list[Chat]) -> None:
        cols = ("id", *SCAN_COLS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in SCAN_COLS)
        self._conn.executemany(
            f"INSERT INTO chats ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            [self._row(c, cols) for c in chats],
        )

    @staticmethod
    def _iso(dt: datetime | None) -> str | None:
        """Single timestamp format for storage, so string comparisons match."""
        return dt.isoformat() if dt else None

    @classmethod
    def _row(cls, chat: Chat, cols: tuple[str, ...]) -> dict:
        row = chat.model_dump(mode="json", include=set(cols))
        for k in row:
            if isinstance(v := getattr(chat, k), datetime):
                row[k] = cls._iso(v)
        return row

    def remove_unseen(self, seen_at: datetime) -> int:
        """Drop chats not seen in the given scan (left / deleted)."""
        cur = self._conn.execute("DELETE FROM chats WHERE seen_at != ?", (self._iso(seen_at),))
        return cur.rowcount

    def update_chats(self, chat_ids: list[int], fields: dict | None) -> None:
        """Mirror an action locally. fields=None removes the chats (left / deleted)."""
        if fields is None:
            self._conn.executemany("DELETE FROM chats WHERE id=?", [(i,) for i in chat_ids])
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self._conn.executemany(f"UPDATE chats SET {sets} WHERE id=?",
                               [(*fields.values(), i) for i in chat_ids])

    def chats(self) -> list[Chat]:
        return [Chat.model_validate(dict(r)) for r in self._conn.execute("SELECT * FROM chats")]

    # folders

    def replace_folders(self, folders: list[Folder]) -> None:
        self._conn.execute("DELETE FROM folders")
        self._conn.executemany(
            "INSERT INTO folders (id, position, data) VALUES (?, ?, ?)",
            [(f.id, f.position, f.model_dump_json()) for f in folders],
        )

    def folders(self) -> list[Folder]:
        rows = self._conn.execute("SELECT data FROM folders ORDER BY position")
        return [Folder.model_validate_json(r["data"]) for r in rows]

    # meta

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    @property
    def last_scan(self) -> datetime | None:
        v = self.get_meta("last_scan")
        return datetime.fromisoformat(v) if v else None

    @last_scan.setter
    def last_scan(self, at: datetime) -> None:
        self.set_meta("last_scan", self._iso(at))

    @property
    def self_id(self) -> int | None:
        v = self.get_meta("self_id")
        return int(v) if v else None

    @self_id.setter
    def self_id(self, user_id: int) -> None:
        self.set_meta("self_id", str(user_id))

    @property
    def folder_limits(self) -> FolderLimits | None:
        v = self.get_meta("folder_limits")
        return FolderLimits.model_validate_json(v) if v else None

    @folder_limits.setter
    def folder_limits(self, limits: FolderLimits) -> None:
        self.set_meta("folder_limits", limits.model_dump_json())

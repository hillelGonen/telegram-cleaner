import base64
from datetime import datetime
from pathlib import Path
from typing import Self

from pydantic import BaseModel
from telethon.extensions import BinaryReader

from tgo.models import Folder, utcnow
from tgo.telegram import TelethonMapper


class FolderSnapshot(BaseModel):
    id: int
    folder: Folder | None  # readable copy; None for "All chats"
    raw: str  # base64 TL bytes: exact folder incl. access hashes, used for restore

    def tl(self):
        return BinaryReader(base64.b64decode(self.raw)).tgread_object()


class FolderBackup(BaseModel):
    created_at: datetime
    reason: str
    folders: list[FolderSnapshot]

    @classmethod
    def of(cls, filters: list, self_id: int, reason: str) -> Self:
        return cls(
            created_at=utcnow(),
            reason=reason,
            folders=[
                FolderSnapshot(
                    id=TelethonMapper.filter_id(f),
                    folder=TelethonMapper.folder(f, i, self_id),
                    raw=base64.b64encode(bytes(f)).decode(),
                )
                for i, f in enumerate(filters)
            ],
        )

    @property
    def order(self) -> list[int]:
        return [s.id for s in self.folders]

    @property
    def titles(self) -> list[str]:
        return [s.folder.title for s in self.folders if s.folder]


class BackupStore:
    """JSON folder backups in data/backups/, newest last by name."""

    def __init__(self, directory: Path):
        self.dir = directory

    def save(self, backup: FolderBackup) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        stem = f"folders-{backup.created_at:%Y%m%d-%H%M%S}"
        path, n = self.dir / f"{stem}.json", 1
        while path.exists():
            n += 1
            path = self.dir / f"{stem}-{n}.json"
        path.write_text(backup.model_dump_json(indent=2), encoding="utf-8")
        return path

    def list(self) -> list[Path]:
        return sorted(self.dir.glob("folders-*.json"), key=lambda p: p.stat().st_mtime) \
            if self.dir.exists() else []

    def resolve(self, name: str = "latest") -> Path:
        if name == "latest":
            backups = self.list()
            if not backups:
                raise FileNotFoundError("No backups yet.")
            return backups[-1]
        for candidate in (Path(name), self.dir / name, self.dir / f"{name}.json"):
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(f"Backup {name!r} not found. See `tgo backups`.")

    @staticmethod
    def load(path: Path) -> FolderBackup:
        return FolderBackup.model_validate_json(path.read_text(encoding="utf-8"))

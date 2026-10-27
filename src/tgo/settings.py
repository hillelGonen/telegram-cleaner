from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    pass


class Settings(BaseSettings):
    """Loaded from environment / .env. API credentials only needed for Telegram commands."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    tg_api_id: int | None = None
    tg_api_hash: SecretStr | None = None
    data_dir: Path = Field(Path("data"), validation_alias="TGO_DATA_DIR")
    # Reorder RTL text (Hebrew/Arabic) for terminals without bidi support.
    # Set TGO_BIDI=false if your terminal already renders RTL correctly.
    bidi: bool = Field(True, validation_alias="TGO_BIDI")

    @property
    def session_path(self) -> Path:
        # Telethon appends ".session" itself
        return self.data_dir / "tgo"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tgo.db"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def actions_log_path(self) -> Path:
        return self.data_dir / "actions.jsonl"

    def ensure_data_dir(self) -> Path:
        """Create the data dir, owner-only: it holds the session (= full account access),
        your chat list and the action log."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)
        for path in self.data_dir.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        return self.data_dir

    def require_api(self) -> tuple[int, str]:
        if self.tg_api_id is None or self.tg_api_hash is None:
            raise ConfigError(
                "TG_API_ID / TG_API_HASH missing. Copy .env.example to .env and fill them "
                "(get them at https://my.telegram.org)."
            )
        return self.tg_api_id, self.tg_api_hash.get_secret_value()

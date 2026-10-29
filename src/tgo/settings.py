import os
import re
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    pass


def home_dir() -> Path:
    """Per-user config + data for an installed `tgo` (override with TGO_HOME)."""
    return Path(os.getenv("TGO_HOME", "~/.tgo")).expanduser()


def _default_data_dir() -> Path:
    # A checkout that already has ./data keeps using it; everyone else gets ~/.tgo/data.
    local = Path("data")
    if (local / "tgo.session").exists() or (local / "tgo.db").exists():
        return local
    return home_dir() / "data"


class Settings(BaseSettings):
    """Loaded from environment, ~/.tgo/.env, then ./.env (later wins).
    API credentials are only needed for Telegram commands."""

    model_config = SettingsConfigDict(env_file=(home_dir() / ".env", ".env"), extra="ignore",
                                      env_ignore_empty=True)  # a copied .env.example has blanks

    tg_api_id: int | None = None
    tg_api_hash: SecretStr | None = None
    data_dir: Path = Field(default_factory=_default_data_dir, validation_alias="TGO_DATA_DIR")
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
                "Telegram API id/hash missing. Run `tgo` in a terminal to set them up, or put "
                f"TG_API_ID / TG_API_HASH in {home_dir() / '.env'} "
                "(get them at https://my.telegram.org)."
            )
        return self.tg_api_id, self.tg_api_hash.get_secret_value()


API_HASH_RE = re.compile(r"^[0-9a-f]{32}$")


def save_credentials(api_id: int, api_hash: str) -> Path:
    """Write the API id/hash to ~/.tgo/.env (owner-only), keeping any other lines."""
    if not API_HASH_RE.match(api_hash):
        raise ValueError("api_hash must be 32 hex characters")
    home = home_dir()
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    path = home / ".env"
    keep = []
    if path.exists():
        keep = [line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.startswith(("TG_API_ID=", "TG_API_HASH="))]
    path.write_text("\n".join([*keep, f"TG_API_ID={api_id}", f"TG_API_HASH={api_hash}"]) + "\n",
                    encoding="utf-8")
    path.chmod(0o600)
    return path

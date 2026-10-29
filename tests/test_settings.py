import stat

import pytest

from tgo.settings import ConfigError, Settings, _default_data_dir, save_credentials

HASH = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TGO_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    for var in ("TG_API_ID", "TG_API_HASH", "TGO_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "home"


def test_save_credentials_owner_only_and_keeps_other_lines(home):
    home.mkdir()
    (home / ".env").write_text("TGO_BIDI=false\nTG_API_ID=1\n")
    path = save_credentials(12345, HASH)
    assert path.read_text() == f"TGO_BIDI=false\nTG_API_ID=12345\nTG_API_HASH={HASH}\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    s = Settings(_env_file=path)
    assert s.require_api() == (12345, HASH) and s.bidi is False


def test_save_credentials_rejects_bad_hash(home):
    with pytest.raises(ValueError):
        save_credentials(1, "not-a-hash")


def test_missing_and_blank_credentials(home, tmp_path):
    env = tmp_path / ".env"
    env.write_text("TG_API_ID=\nTG_API_HASH=\n")  # copied .env.example
    with pytest.raises(ConfigError, match="Run `tgo`"):
        Settings(_env_file=env).require_api()


def test_default_data_dir(home, tmp_path):
    assert _default_data_dir() == home / "data"  # installed tool, fresh folder
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tgo.db").write_text("")
    assert str(_default_data_dir()) == "data"  # existing checkout keeps ./data

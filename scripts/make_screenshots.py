"""Make the README screenshots from made-up chats (no Telegram account involved).

    uv run python scripts/make_screenshots.py

Writes docs/screenshots/{terminal.svg, sorter.png, sorter-help.png}. Needs Google Chrome.
"""
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import timedelta
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from conftest import SCAN  # noqa: E402
from fakes import SELF_ID, FakeGateway  # noqa: E402

from tgo.menu import TgoApp  # noqa: E402
from tgo.models import Chat, ChatType, Folder, FolderFlags, FolderLimits  # noqa: E402
from tgo.repository import Repository  # noqa: E402
from tgo.settings import Settings  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# (title, username, type, unread, idle days)
CHATS = [
    ("כאן חדשות", "kan_news_demo", ChatType.CHANNEL, 214, 0),
    ("Ynet מבזקים", "ynet_demo", ChatType.CHANNEL, 1890, 0),
    ("חדשות 13", None, ChatType.CHANNEL, 4620, 0),
    ("World News Daily", "worldnews_demo", ChatType.CHANNEL, 812, 1),
    ("أخبار الآن", None, ChatType.CHANNEL, 2310, 0),
    ("Tech Radar", "techradar_demo", ChatType.CHANNEL, 96, 2),
    ("Python Tips", "pytips_demo", ChatType.CHANNEL, 1430, 3),
    ("AI Weekly", "aiweekly_demo", ChatType.CHANNEL, 57, 5),
    ("Dev Memes", None, ChatType.CHANNEL, 3305, 0),
    ("ספורט 5", None, ChatType.CHANNEL, 2880, 0),
    ("Champions League", "ucl_demo", ChatType.CHANNEL, 640, 1),
    ("מזג אוויר", None, ChatType.CHANNEL, 120, 0),
    ("Crypto Signals", None, ChatType.CHANNEL, 4900, 400),
    ("Old Movies Archive", None, ChatType.CHANNEL, 0, 900),
    ("מתכונים", "recipes_demo", ChatType.CHANNEL, 77, 12),
    ("Family 👨‍👩‍👧", None, ChatType.GROUP, 12, 0),
    ("Class of 2015", None, ChatType.GROUP, 0, 700),
    ("Reminder bot", None, ChatType.BOT, 0, 30),
    ("Dana", None, ChatType.USER, 2, 1),
    ("Avi", None, ChatType.USER, 0, 500),
]
FOLDERS = [("חדשות", "📰", [0, 1, 3]), ("Tech", "💻", [5, 6]), ("Sport", "⚽", [9])]


def build_data(data_dir: Path) -> Settings:
    settings = Settings(_env_file=None, tg_api_id=1, tg_api_hash="x",
                        **{"TGO_DATA_DIR": data_dir, "TGO_BIDI": False})
    settings.ensure_data_dir()
    chats = [Chat(id=-1000000000000 - i, type=t, title=title, username=user, unread=unread,
                  members=(i * 7919) % 90000 + 300, last_msg_date=SCAN - timedelta(days=idle),
                  muted=i in (8, 12), seen_at=SCAN)
             for i, (title, user, t, unread, idle) in enumerate(CHATS)]
    folders = [Folder(id=10 + n, title=name, emoticon=emoji, position=n,
                      include_peers=[chats[i].id for i in members],
                      flags=FolderFlags())
               for n, (name, emoji, members) in enumerate(FOLDERS)]
    with Repository(settings.db_path) as repo:
        repo.save_chats(chats)
        repo.replace_folders(folders)
        repo.folder_limits = FolderLimits()
        repo.last_scan = SCAN
        repo.self_id = SELF_ID
        repo.commit()
    return settings


def terminal_svg(settings: Settings) -> None:
    console = Console(record=True, width=74, force_terminal=True, color_system="truecolor")
    app = TgoApp(settings, None, console, gateway_factory=FakeGateway)
    q = "[bold #5fafff]?[/] [bold]What do you want to do?[/]"
    console.print(f"{q} [#ff9f43]📊 Overview[/]")
    app.overview()
    console.print()
    console.print(f"{q} [dim](Use arrow keys)[/]")
    items = ["📊 Overview", "🧹 Clean up old chats", "📭 Channels & groups I don't read",
             "📁 Sort into folders", "↩️  Undo", "🔄 Rescan now", "🚪 Exit"]
    for i, item in enumerate(items):
        console.print(f" [bold #5fafff]»[/] [#5fafff]{item}[/]" if i == 3 else f"   {item}")
    console.save_svg(str(OUT / "terminal.svg"), title="tgo")


def browser_shots(settings: Settings) -> None:
    urls = []
    webbrowser.open = lambda url: urls.append(url) or True
    app = TgoApp(settings, _Prompter(), Console(quiet=True), gateway_factory=FakeGateway)
    worker = threading.Thread(target=app.sort_in_browser, daemon=True)
    worker.start()
    while not urls:
        time.sleep(0.05)
    profile = tempfile.mkdtemp()
    for name in ("sorter-help.png", "sorter.png"):  # 1st visit shows the help, 2nd doesn't
        subprocess.run([CHROME, "--headless=new", f"--user-data-dir={profile}", "--hide-scrollbars",
                        "--window-size=1440,760", "--force-device-scale-factor=2",
                        "--virtual-time-budget=4000", f"--screenshot={OUT / name}", urls[0]],
                       check=True, capture_output=True)
    shutil.rmtree(profile, ignore_errors=True)
    _post_done(urls[0])
    worker.join(timeout=5)


class _Prompter:  # sort_in_browser asks nothing; satisfies the constructor
    pass


def _post_done(url: str) -> None:
    import urllib.request
    port, token = url.split(":")[2].split("/")[0], url.split("t=")[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/done", data=b"{}",
                                 headers={"X-Tgo-Token": token}, method="POST")
    urllib.request.urlopen(req, timeout=5)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        settings = build_data(Path(tmp) / "data")
        terminal_svg(settings)
        browser_shots(settings)
    for f in sorted(OUT.iterdir()):
        print(f.relative_to(ROOT), f"{f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()

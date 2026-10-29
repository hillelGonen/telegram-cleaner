# telegram-cleaner

[![CI](https://github.com/hillelGonen/telegram-cleaner/actions/workflows/ci.yml/badge.svg)](https://github.com/hillelGonen/telegram-cleaner/actions/workflows/ci.yml)

A small menu that helps you clean up and organize Telegram: delete old chats, leave dead groups,
quiet noisy channels, and sort channels into folders.

![Sorting channels into folders in the browser](https://raw.githubusercontent.com/hillelGonen/telegram-cleaner/main/docs/screenshots/sorter.png)

## Install
```
pipx install tg-cleaner      # or: uv tool install tg-cleaner
tgo
```
Python 3.12+. Plain `pip install tg-cleaner` works too; pipx/uv keep the tool in its own
environment. Latest code straight from GitHub:
`pipx install git+https://github.com/hillelGonen/telegram-cleaner`

On first run `tgo` asks for your Telegram API id and hash (get them at https://my.telegram.org →
"API development tools") and saves them to `~/.tgo/.env`. Your login and data live in `~/.tgo/`,
readable only by you. `~/.tgo/data/tgo.session` gives full access to your account; never share it.

Update: `pipx upgrade tg-cleaner` · Remove: `pipx uninstall tg-cleaner` (then delete `~/.tgo`).
With uv: `uv tool upgrade tg-cleaner` / `uv tool uninstall tg-cleaner`.

## Use
```
tgo
```
<img src="https://raw.githubusercontent.com/hillelGonen/telegram-cleaner/main/docs/screenshots/terminal.svg" alt="tgo menu and overview in the terminal" width="560">

First time it asks you to log in (phone, code from Telegram, 2FA password if set).
It reads your chats (again whenever the last read is over an hour old), then shows:

| Menu | What it does |
|---|---|
| 📊 Overview | Counts: channels, groups, private chats, bots; dead (1y+), noisy (1000+ unread), in no folder; folders |
| 🧹 Clean up old chats | Pick types (or All types) + age (1 month … 2 years) → delete/leave, archive or mute |
| 📭 Channels & groups I don't read | 50 / 100 / 300 / 1000+ unread (they still post, you don't read) → leave, mute, archive or mark read |
| 📁 Sort into folders | **In your browser (recommended):** drag channels, groups and bots between folders, ✕ to take one out of a folder, 🗑 to delete a folder, – to minimize a column, ＋ to make a folder; then Save. Or in the terminal: add by words like `news חדשות أخبار`, or delete folders |
| ↩️ Undo | Undo the last folder change or last archive/mute; list chats you left with rejoin links |
| 🔄 Rescan now | Read your chats again |

<img src="https://raw.githubusercontent.com/hillelGonen/telegram-cleaner/main/docs/screenshots/sorter-help.png" alt="The browser sorter explains its features on first use" width="720">

The browser sorter runs only on your computer (`127.0.0.1`, a random port and a secret link),
so no website can reach it. It writes to Telegram only when you click Save, after showing what
will change, and saves a backup first, so Undo can put your folders back.

Every action shows the list first. You can take all, untick some, or cancel, and then confirm
with y/N. Nothing happens before that.

Other command: `tgo logout`.

## Safety
- Never touched: chats/groups you own, pinned chats, Saved Messages, Telegram's service chat.
- **Leaving / deleting can't be undone.** Public chats can be rejoined from the links in Undo;
  private channels/groups need a new invite link. Deleting a private chat removes its history
  for you only.
- Folder changes save a full backup first (`data/backups/`), so Undo restores them exactly.
  Only the folder's chat list changes; its flags, pins, icon and color stay.
- Every action is logged in `data/actions.jsonl`.
- Ctrl+C anytime. Chats already done are saved, so running again continues where you stopped.

## Hebrew / Arabic
Titles are flipped for terminals that print right-to-left text backwards (macOS Terminal, most).
If yours already shows RTL correctly (text looks reversed), set `TGO_BIDI=false` in `.env`.

## Code layout
```
src/tgo/
  cli.py           entry: `tgo` opens the menu, `tgo logout`
  menu.py          TgoApp: the menu screens
  prompter.py      Prompter protocol + QuestionaryPrompter (arrow-key menus)
  views.py         Rich tables/messages
  settings.py      Settings (.env)
  models.py        Chat, Folder, FolderFlags, FolderLimits (Pydantic)
  telegram.py      TelegramGateway (all network) + TelethonMapper
  repository.py    Repository (SQLite)
  scanner.py       Scanner (Telegram -> Repository)
  selection.py     ChatFilter + ChatSelector (protected chats)
  cleanup.py       CleanupExecutor + ActionLog (batches, undo)
  applier.py       FolderEditor (add / move chats between folders), Restorer (undo)
  sorter.py        browser sorter: board state + local server (sorter.html is the page)
  backup.py        FolderBackup + BackupStore
  folder_index.py  which folders show each chat
  report.py        counts for the overview
  text.py          bidi + markup escaping
```

## Development
Screenshots in this README come from made-up chats: `uv run python scripts/make_screenshots.py`
(needs Google Chrome).

Release: bump `version` in pyproject.toml, commit, then `git tag vX.Y.Z && git push --tags`;
the Publish workflow tests, builds and uploads to PyPI.

```
git clone https://github.com/hillelGonen/telegram-cleaner && cd telegram-cleaner
uv sync
uv run tgo           # run from the checkout (uses ./data if it exists, else ~/.tgo/data)
uv run pytest        # tests (no Telegram account needed; Telegram is faked)
uv run ruff check .  # lint
```
CI runs both on every push.

## License
MIT, see [LICENSE](LICENSE).

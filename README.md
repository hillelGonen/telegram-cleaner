# telegram-cleaner

[![CI](https://github.com/hillelGonen/telegram-cleaner/actions/workflows/ci.yml/badge.svg)](https://github.com/hillelGonen/telegram-cleaner/actions/workflows/ci.yml)

A small menu that helps you clean up and organize Telegram: delete old chats, leave dead groups,
quiet noisy channels, and sort channels into folders.

## Setup (once)
1. Get `api_id` + `api_hash` at https://my.telegram.org → "API development tools".
2. `cp .env.example .env` and fill `TG_API_ID`, `TG_API_HASH`.
3. `uv sync`

`data/tgo.session` gives full access to your account. Keep it private; it is gitignored.

## Use
```
uv run tgo
```
First time it asks you to log in (phone, code from Telegram, 2FA password if set).
It reads your chats (again whenever the last read is over an hour old), then shows:

| Menu | What it does |
|---|---|
| 📊 Overview | Counts: channels, groups, private chats, bots; dead (1y+), noisy (1000+ unread), in no folder; folders |
| 🧹 Clean up old chats | Pick types (or All types) + age (1 month … 2 years) → delete/leave, archive or mute |
| 📭 Channels & groups I don't read | 50 / 100 / 300 / 1000+ unread (they still post, you don't read) → leave, mute, archive or mark read |
| 📁 Sort into folders | Pick a folder (or make one), type words like `news חדשות أخبار` → matching channels are added |
| ↩️ Undo | Undo the last folder change or last archive/mute; list chats you left with rejoin links |
| 🔄 Rescan now | Read your chats again |

Every action shows the list first. You can take all, untick some, or cancel, and then confirm
with y/N. Nothing happens before that.

Other command: `uv run tgo logout`.

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
  applier.py       FolderEditor (add to folder), Restorer (undo)
  backup.py        FolderBackup + BackupStore
  folder_index.py  which folders show each chat
  report.py        counts for the overview
  text.py          bidi + markup escaping
```

## Development
```
uv run pytest        # tests (no Telegram account needed; Telegram is faked)
uv run ruff check .  # lint
```
CI runs both on every push.

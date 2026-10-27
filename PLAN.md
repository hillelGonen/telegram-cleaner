# Telegram Organizer — Plan

Personal tool to tidy up a news-heavy Telegram account: see the mess, delete/leave old chats,
quiet noisy channels, sort channels into folders, undo.

## Decisions
- **Stack:** Python 3.12+, Telethon (MTProto user client), SQLite, Pydantic, Rich, questionary.
- **Interface:** one interactive menu (`tgo`). Only other command: `tgo logout`.
- **Safety pattern everywhere:** show list → take all / untick some / cancel → y/N confirm.
- **Folders:** direct "add matching channels to folder X" (no rules file, no plan/apply step).
- Simplified 2026-10-26 from a 13-command CLI (YAML rules, plan/apply, many flags).

## Constraints
- Bot API can't read user chats or folders → log in as the user (MTProto).
- User gets own `api_id` / `api_hash` at https://my.telegram.org.
- `*.session` = full account access. Local only, gitignored.
- Folders = "dialog filters" (`GetDialogFilters`, `UpdateDialogFilter`). Shared folders (chatlists)
  can't be edited this way.
- Folder limits differ for Premium → read from `help.GetAppConfig`.
- Respect FloodWait: Telethon sleeps up to 120s; longer → stop, user reruns.

## Menu
| Screen | Does |
|---|---|
| Overview | counts + folders |
| Clean up old chats | types + age → leave/delete, archive, mute |
| Channels & groups I don't read | unread threshold → leave, mute, archive, mark read |
| Sort into folders | folder + words → add matching channels/groups |
| Undo | restore last folder backup · reverse last archive/mute batch · rejoin links |

## Done
1. ✅ Login + scan to SQLite
2. ✅ Overview counts
3. ✅ Folder writes with exact backups + restore
4. ✅ Bulk leave/archive/mute/read with protections, log, batches, undo
5. ✅ Simplified to one interactive menu

## Maybe later
- Claude suggests a folder for unsorted channels (needs `ANTHROPIC_API_KEY`).
- Auto-sort newly joined channels in the background.

# AI Conversation Cleaner

English · [简体中文](README.zh-CN.md)

A Windows utility for reviewing and deleting **local conversations** from Codex, Claude Code, Grok Build, Cursor, Google Antigravity, DeepSeek Harness, and OpenCode. Search, inspect, select, preview, then delete—with a default backup, a backup folder you choose, or no backup.

**Public preview · Windows x64 · Simplified Chinese interface.** An independent community project, not affiliated with the supported applications' vendors.

> Deletion changes application data directly. Keep the target application and its CLI/background processes closed. Try the isolated demo first and keep backups enabled until you have verified compatibility with your installed versions.

## Download and run

Download `AIConversationCleaner-Windows-x64.zip` from [Releases](https://github.com/xing-skyline/ai-conversation-cleaner/releases), extract it, and double-click **`AIConversationCleaner.exe`**. Python, Node.js and CMD are not required. Source builds use the filename `AI会话清理器.exe`.

The executable opens a browser page served only on `127.0.0.1` with a per-launch access token. It is a local application, not a hosted service. Click **退出工具** (“Exit tool”) to stop the current instance, or close its last browser tab: the backend then exits after a 3-second refresh grace period. Other tabs for that instance keep it alive. Deletion, backup and other active requests finish before shutdown. A lost/crashed browser connection expires after approximately 3 minutes; a browser that never connects times out after 2 minutes. A tab frozen/discarded by the browser for that long may require relaunching the EXE. Headless `--no-browser` use is not timed out unless a browser page has connected.

The executable is **unsigned**. Verify release SHA-256 checksums. If Windows blocks it, do not disable system security protections; review the source or build it yourself instead.

## Usage

1. Select an application tab; search titles, project paths or IDs.
2. Use **详情** (“Details”) to inspect available previews, then select individual conversations or all filtered results. Empty, archived and recognized leftover records can also be selected.
3. Choose **删除前备份** (“Backup before deletion”):

   | Option | Behavior |
   | --- | --- |
   | 备份到默认目录 | Back up first, using the default location. |
   | 手动选择备份目录 | Open the Windows folder picker or enter an absolute path. Backups go under `selected-folder/AIConversationCleaner/app/timestamp`. |
   | 不备份，直接删除 | No conversation/database backup. Committed changes cannot be automatically restored after an error. |

4. Exit the target application, its CLI and background processes.
5. Click **删除所选任务**, review titles/IDs and the backup policy, then click **备份并删除** or **直接删除（不备份）**. No typed confirmation is required.
6. Check the result, then reopen the original application.

Each fresh launch defaults to backup; no-backup is never remembered automatically. A preview binds the IDs and backup policy to a short-lived, single-use token. Changed data requires a fresh preview. Each batch supports up to 500 conversations.

## Supported storage

| Application | Recognized local deletion scope |
| --- | --- |
| Codex | Local desktop catalog, thread state, recognized message/goal/queue/memory records, session index and owned JSONL rollouts. Attempts the installed CLI's `thread/delete`, then removes known remnants. Remote catalog entries are preserved. |
| Claude Code | `.claude/projects` transcripts and owned sidecars/subdirectories, session indexes, matching history entries and recognized session auxiliaries. |
| Grok Build | `.grok/sessions` directories, prompt history, active-session entries and search/FTS indexes. |
| Cursor | Recognized composer headers/data, owned message/checkpoint/diff keys, workspace indexes and independent transcripts. Shared content-addressed blobs are preserved. |
| Antigravity | Recognized conversation, annotation, brain and recording files; local summary database and IDE summary index. Binary bodies are not decoded. |
| DeepSeek Harness | `.dsh/sessions` including compressed logs, projection caches and workspace/archive membership. Displays cached titles and first-prompt summaries, not the full compressed transcript. |
| OpenCode | `opencode.db` sessions, messages/parts, todos, share metadata, input/context projections and exact-session event records; recognized legacy JSON and session diffs. Preserves projects, accounts, credentials, shared tool outputs and remote shares. Parent sessions require explicitly selecting their child sessions. |

This is **actual removal from recognized active local storage**, not an archive toggle. It is **not secure erasure**: old backups, generic logs, shared caches, database free pages, cloud history and OS recovery mechanisms may retain data. Project source/documents, credentials, settings, skills and plugins are not cleanup targets.

Default per-user Windows locations are supported. Codex also supports `CODEX_HOME` / `--home`; OpenCode defaults to `%USERPROFILE%/.local/share/opencode`, honors `XDG_DATA_HOME` and `OPENCODE_DB` (except in-memory databases), and uses the standard `opencode.db` unless explicitly overridden. Other applications' custom homes, WSL, SSH/remote hosts, cloud agents and unrelated editor extensions are outside scope. Codex does not have to be installed to open the other tabs.

OpenCode's SQLite adapter was checked against the v1.18.31 schema. Older recognized `storage/session`, `storage/message`, `storage/part` and `storage/session_diff` JSON files are also handled. Unknown session-related database tables stop deletion instead of being silently skipped. OpenCode database backups can include account/credential tables because the backup is a complete database copy; keep them private.

These internal upstream formats can change; **compatibility with every version is not guaranteed**. DeepSeek cache versions 5/7, shared projection version 3 and workspace version 2 are recognized. Several unexpected structures stop processing, but not every upstream change can be detected. Known executable and Node/Bun paths are checked; custom launchers/plugins may escape detection, so closing the target application yourself remains mandatory.

## Backups, recovery and privacy

Default backup locations:

- Codex: `%USERPROFILE%/.codex/backups/codex-thread-cleaner` (legacy name retained for compatibility).
- Others: `%LOCALAPPDATA%/AIConversationCleaner/backups/<app>` under the default Windows profile layout.

Backed-up batches include `result.json`, original/backup path mappings, database copies and selected files. SQLite uses its online backup API. Recoverable failures attempt rollback while the target app stays closed. Crashes or concurrent writes may require manual recovery; incomplete operations block further deletion. **Never overwrite a database with an old backup after new conversations have been created.** There is no one-click full-database restore.

All modes keep a small `.operations` record of IDs, state and paths—not titles, message bodies or database copies. No-backup failures are reported as `failed_no_backup`, never as a successful rollback. Existing backups are not removed automatically.

The cleaner includes no analytics, account login or intentional cloud-conversation API calls. The optional Codex subprocess follows its own configuration. A user-selected network backup destination receives that copy. Backups, journals, screenshots and `--inventory` output may contain private information: **do not upload them to issues or commits**. See [SECURITY.md](SECURITY.md).

## Source and development

Requires Windows and Python 3.11+. Runtime code uses the standard library; Windows PowerShell provides folder selection and known Node/Bun process checks. The interface is currently Simplified Chinese.

```powershell
git clone https://github.com/xing-skyline/ai-conversation-cleaner.git
cd ai-conversation-cleaner
python run.py
python run.py --demo                    # Synthetic temporary Codex data
python run.py --app cursor --inventory  # Read-only; output can be private
python run.py --home C:\Example\CodexData
```

`源码启动.cmd` is only a source-development convenience, not an EXE requirement.

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
python tools/check_public_files.py

# Avoid bundling packages from a personal Python environment
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

Normal tests use isolated synthetic fixtures and do not delete real conversations. Optional developer probes are documented in [CONTRIBUTING.md](CONTRIBUTING.md). CI tests on Windows, checks public files, builds in a clean environment, smoke-tests the EXE against synthetic data, and packages checksums and dependency notices. Rebuild steps are provided; **byte-for-byte reproducibility is not claimed**.

## License

This project is open source under the [MIT License](LICENSE). You may use, modify and redistribute it, including commercially, provided the copyright and license notices are retained. The software is provided without warranty. Third-party components retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

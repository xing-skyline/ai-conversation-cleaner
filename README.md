# AI Conversation Cleaner

English · [简体中文](README.zh-CN.md)

A Windows and macOS utility for reviewing and deleting **local conversations** from Codex, Claude Code, Grok Build, Cursor, Google Antigravity, DeepSeek Harness, and OpenCode. Search, inspect, select, preview, then delete. Deletion does not back up unless you choose a folder.

**Public preview · Windows x64 / macOS Apple Silicon and Intel · Simplified Chinese interface.** An independent community project, not affiliated with the supported applications' vendors.

> Deletion changes application data directly. Keep the target application and its CLI/background processes closed. Try the isolated demo first. Choose “备份到指定目录” until you have verified compatibility with your installed versions.

## Download and run

Download `AIConversationCleaner-Windows-x64.zip` from [Releases](https://github.com/xing-skyline/ai-conversation-cleaner/releases), extract it, and double-click **`AIConversationCleaner.exe`**. Python, Node.js and CMD are not required. Source builds use the filename `AI会话清理器.exe`.

On macOS, download `AIConversationCleaner-macOS-arm64.zip` (Apple Silicon) or `AIConversationCleaner-macOS-x86_64.zip` (Intel) from [Releases](https://github.com/xing-skyline/ai-conversation-cleaner/releases), extract it, and double-click **`AIConversationCleaner.app`** or drag it into Applications. The bundle includes Python; no separate Python or Node.js installation is required. Source builds produce `dist/AIConversationCleaner.app`.

The application opens a browser page served only on `127.0.0.1` with a per-launch access token. It is a local application, not a hosted service. Click **退出工具** (“Exit tool”) to stop the current instance, or close its last browser tab: the backend then exits after a 3-second refresh grace period. Other tabs for that instance keep it alive. Deletion, backup and other active requests finish before shutdown. A lost/crashed browser connection expires after approximately 3 minutes; a browser that never connects times out after 2 minutes. A tab frozen/discarded by the browser for that long may require relaunching the application. Headless `--no-browser` use is not timed out unless a browser page has connected.

The Windows executable is **unsigned**. macOS bundles use ad-hoc signing, without Developer ID signing or Apple notarization. Verify release SHA-256 checksums. If the OS blocks it, do not disable system security protections; review the source or build it yourself instead.

## Usage

1. Select an application tab; search titles, project paths or IDs.
2. Use **详情** (“Details”) to inspect available previews, then select individual conversations or all filtered results. Empty, archived and recognized leftover records can also be selected.
3. Choose **删除前备份** (“Backup before deletion”):

   | Option | Behavior |
   | --- | --- |
   | 不备份，直接删除 | Default. No conversation or database copy. Committed changes cannot be automatically restored after an error. |
   | 备份到指定目录 | Open the native folder picker or enter an absolute path. Backups go under `selected-folder/AIConversationCleaner/app/timestamp`. |

4. Exit the target application, its CLI and background processes.
5. Click **删除所选任务**, review titles/IDs and the backup policy, then click **备份并删除** or **直接删除（不备份）**. No typed confirmation is required.
6. Check the result, then reopen the original application.

Each fresh launch defaults to no backup and does not remember the previous choice. A preview binds the IDs and backup policy to a short-lived, single-use token. Changed data requires a fresh preview. Each batch supports up to 500 conversations.

## Supported storage

| Application | Recognized local deletion scope |
| --- | --- |
| Codex | Local desktop catalog, thread state, recognized message/goal/queue/memory records, inbox references, automation runs, visualization suggestions, session index and owned JSONL rollouts. Attempts the installed CLI's `thread/delete`, then removes known remnants. Remote catalog entries/suggestions and automation definitions are preserved. |
| Claude Code | `.claude/projects` transcripts and owned sidecars/subdirectories, session indexes, matching history entries and recognized session auxiliaries. |
| Grok Build | `.grok/sessions` directories, prompt history, active-session entries and search/FTS indexes. |
| Cursor | Recognized composer headers/data, owned message/checkpoint/diff keys, workspace indexes and independent transcripts. Shared content-addressed blobs are preserved. |
| Antigravity | Recognized conversation, annotation, brain and recording files; local summary database and IDE summary index. Binary bodies are not decoded. |
| DeepSeek Harness | `.dsh/sessions` including compressed logs, projection caches and workspace/archive membership. Displays cached titles and first-prompt summaries, not the full compressed transcript. |
| OpenCode | `opencode.db` sessions, messages/parts, todos, share metadata, input/context projections and exact-session event records; recognized legacy JSON and session diffs. Preserves projects, accounts, credentials, shared tool outputs and remote shares. Parent sessions require explicitly selecting their child sessions. |

This is **actual removal from recognized active local storage**, not an archive toggle. It is **not secure erasure**: old backups, generic logs, shared caches, database free pages, cloud history and OS recovery mechanisms may retain data. Project source/documents, credentials, settings, skills and plugins are not cleanup targets.

Default per-user Windows and macOS locations are supported. Codex also supports `CODEX_HOME` / `--home`; OpenCode defaults to `~/.local/share/opencode`, honors `XDG_DATA_HOME` and `OPENCODE_DB` (except in-memory databases), and uses the standard `opencode.db` unless explicitly overridden. Other applications' custom homes, WSL, SSH/remote hosts, cloud agents and unrelated editor extensions are outside scope. Codex does not have to be installed to open the other tabs.

OpenCode's SQLite adapter was checked against the v1.18.31 schema. Older recognized `storage/session`, `storage/message`, `storage/part` and `storage/session_diff` JSON files are also handled. Unknown session-related database tables stop deletion instead of being silently skipped. OpenCode database backups can include account/credential tables because the backup is a complete database copy; keep them private.

Codex catalog auxiliaries were checked against desktop 26.915.4065.0. `inbox_items` and `automation_runs` have no host field and match selected thread IDs; a matching ID also present in a non-local catalog host stops deletion because ownership is ambiguous. `live_visualization_suggestions` is restricted to the local host and selected threads. Unknown related tables still block deletion and are now reported during preview.

These internal upstream formats can change; **compatibility with every version is not guaranteed**. DeepSeek per-session projection cache versions 3–7, shared projection version 3 and workspace version 2 are recognized. Deletion removes the whole session directory, including v3/v4 compressed logs, nested files and pinned entries. Several unexpected structures stop processing, but not every upstream change can be detected. Known executable and Node/Bun paths are checked; custom launchers/plugins may escape detection, so closing the target application yourself remains mandatory. DSH Desktop can keep running after its window closes; quit it from the tray or end every matching process before deleting.

## Backups, recovery and privacy

Nothing is copied unless you choose “备份到指定目录”. A backed-up batch then includes `result.json`, path mappings, database copies and selected files under `selected-folder/AIConversationCleaner/app/timestamp`.

Operation journals and the operation lock stay in a fixed directory. They record IDs, state and paths, not message bodies (`~` is the current user's home directory):

- Codex: `~/.codex/backups/codex-thread-cleaner` (relative to `CODEX_HOME` when overridden). The legacy directory name is retained for the existing lock.
- Other apps on Windows: `%LOCALAPPDATA%/AIConversationCleaner/backups/<app>` under the default profile layout.
- Other apps on macOS: `~/Library/Application Support/AIConversationCleaner/backups/<app>`.

On macOS, Cursor indexes use `~/Library/Application Support/Cursor/User`, and Antigravity indexes use `~/Library/Application Support/Antigravity/User`. Other recognized session files use the same home-relative `.codex`, `.claude`, `.grok`, `.cursor/projects`, `.gemini`, `.dsh` and `.local/share/opencode` layouts. `~` means the current user's home directory.

SQLite copies use its online backup API. Recoverable failures attempt rollback while the target app stays closed. Crashes or concurrent writes may require manual recovery; incomplete operations block further deletion. **Never overwrite a database with an old backup after new conversations have been created.** There is no one-click full-database restore.

No-backup failures are reported as `failed_no_backup`, never as a successful rollback. Existing backups are not removed automatically.

The cleaner includes no analytics, account login or intentional cloud-conversation API calls. The optional Codex subprocess follows its own configuration. A user-selected network backup destination receives that copy. Backups, journals, screenshots and `--inventory` output may contain private information: **do not upload them to issues or commits**. See [SECURITY.md](SECURITY.md).

## Source and development

Requires Windows or macOS, and Python 3.11+. Runtime code uses the standard library, Windows PowerShell, or macOS system utilities (`ps`, `osascript`, `open`). Both platforms share the cleanup, backup, recovery and interface code. The interface is currently Simplified Chinese.

```powershell
git clone https://github.com/xing-skyline/ai-conversation-cleaner.git
cd ai-conversation-cleaner
python run.py
python run.py --demo                    # Synthetic temporary Codex data
python run.py --app cursor --inventory  # Read-only; output can be private
python run.py --home C:\Example\CodexData
```

On macOS:

```bash
python3 run.py
python3 run.py --demo
python3 run.py --app cursor --inventory  # Read-only; output can be private
python3 run.py --home "$HOME/.codex"
```

Alternatively, double-click `源码启动.command` in Finder; it locates the project virtual environment or an installed Python 3.11+. The `.cmd` and `.command` launchers are only for source use, not packaged applications.

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
python tools/check_public_files.py

# Avoid bundling packages from a personal Python environment
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

Build on macOS (targets the running Python architecture):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
./build-macos.sh .venv/bin/python
.venv/bin/python tools/smoke_executable.py dist/AIConversationCleaner.app/Contents/MacOS/AIConversationCleaner
.venv/bin/python tools/package_release.py
open dist/AIConversationCleaner.app
```

The build uses a directory-based PyInstaller `.app`; ZIP packaging preserves symlinks, permissions and signatures. See [PyInstaller's bundle documentation](https://pyinstaller.org/en/stable/usage.html#building-macos-app-bundles). Folder selection uses the native macOS dialog; opening backups uses Finder. When exiting Codex, also exit the ChatGPT desktop app if it hosts Codex.

Normal tests use isolated synthetic fixtures and do not delete real conversations. Optional developer probes are documented in [CONTRIBUTING.md](CONTRIBUTING.md). CI is configured for Windows x64 and macOS arm64/x86_64: public-file checks, clean builds, synthetic packaged smoke tests, checksums and dependency notices. macOS also verifies the extracted bundle signature. Rebuild steps are provided; **byte-for-byte reproducibility is not claimed**.

## License

This project is open source under the [MIT License](LICENSE). You may use, modify and redistribute it, including commercially, provided the copyright and license notices are retained. The software is provided without warranty. Third-party components retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

# Contributing

Use Windows or macOS and Python 3.11+ (CI uses 3.13). Prefer explicit ownership checks over broad filename matching. Keep changes scoped to recognized storage formats.

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
python tools/check_public_files.py
```

Normal tests use synthetic temporary data and injected process detectors. Add a failing fixture-based test before changing deletion, backups or recovery. Check that unselected conversations survive, paths stay in scope, stale previews are rejected, and failures are reported accurately.

## Optional local probes

- `python -m tests.probe_official`: requires Codex CLI. Creates/deletes a synthetic empty session under a temporary home; makes no model request.
- `python -m tests.probe_node_guard`: requires Node. Starts only an inert test-owned process, verifies detection, then terminates only that process.
- `python -m tests.browser_demo --url-file .test-artifacts/demo-url.txt`: serves all seven tabs with isolated Codex/OpenCode fixtures. Open the generated local URL to test selection, detail, backup choices and button-only deletion; use Exit tool afterward. The URL contains a temporary token; do not publish it or screenshots with local paths.
- `python -m tests.verify_local_copies --backup-mode default` (also `custom` / `none`): reads real local stores, copies recognized data to temporary directories and deletes only those copies. Reports can contain private usage counts and stay under ignored `.local-reports/`. This is an explicit opt-in check; do not run against someone else's profile without authorization.

Never commit real dumps, personal-task screenshots, inventory output or local validation reports.

## Build

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

On macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
./build-macos.sh .venv/bin/python
.venv/bin/python tools/smoke_executable.py dist/AIConversationCleaner.app/Contents/MacOS/AIConversationCleaner
.venv/bin/python tools/package_release.py
```

Build macOS bundles on a Mac of the target architecture. The CI matrix uses [GitHub's Apple Silicon and Intel runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). Tests include native data paths, Node/Bun/native process matching, folder integration and cross-process operation locks. Cursor's macOS path follows the [vendor's documented data location](https://docs.cursor.com/en/troubleshooting/troubleshooting-guide).

GitHub CI builds the public archive in a clean environment and smoke-tests the executable. Packaging uses an explicit public file list. Do not publish binaries gathered from a personal environment as official assets.

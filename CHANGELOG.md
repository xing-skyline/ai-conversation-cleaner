# Changelog

## 1.3.2 — Cursor 3.23 and Antigravity 2.19 cleanup compatibility

- Verify Cursor 3.23.23 inventory and deletion on isolated copies of installed storage, and exercise modern Cursor storage in packaged Windows/macOS smoke tests, including search-only records, native deletion markers and cloud-cache preservation.

- Detect and delete Antigravity 2.x per-conversation SQLite databases, WAL/SHM sidecars and raw hub summary entries, alongside legacy `.pb` bodies and IDE indexes. Check cascade ownership before deleting a database and expose body-only, hub-only and orphan-sidecar remnants.
- Use consistent SQLite backups for selected conversation databases, including committed WAL content, and recreate deleted databases during rollback without replaying stale sidecars. Include body, WAL and hub changes in preview validation; check physical remnants before reporting success.
- Detect Antigravity's renamed `language_server.exe` / `language_server` by application path while leaving unrelated language servers alone. Add synthetic deletion, recovery, ownership, stale-preview and process regressions, and verify Windows 2.19.1 storage on isolated local copies.

- Include local conversation search rows, FTS entries and reconciliation candidates in Cursor cleanup, backups, rollback and stale-preview detection; preserve cloud-cache entries even when IDs match.
- Remove owned drafts, root-writer diagnostics, mailbox cursors, file content and partial inline-diff records, plus recognized pinned/selected references. Surface search-only and owned-key-only remnants for cleanup.
- Match native deletion tombstones for modern composer headers while retaining legacy support; exclude completed deletion markers from the inventory.
- Validate recognized storage schemas before deletion and check actual keys and index remnants afterward, including full-text rows that lost their metadata mapping.
- Add synthetic coverage for ownership boundaries, native markers, schema changes, interrupted cleanup and database rollback, plus an app-specific isolated-copy verification option.

## 1.3.1 — optional backup and DeepSeek Harness 2.x

- Default to no backup. The only other choice is a folder selected for that deletion; the built-in backup location is no longer offered.
- Remove a DeepSeek Harness 2.x session directory as a whole, including `session.v4.jsonl.zstd`, the leftover v3 file, nested artifacts and pinned workspace entries. Accept per-session projection cache versions 3–7.
- Explain that closing the DSH Desktop window can leave the process running, and that a running process can write the session back.

## 1.3.0 — macOS support

- Use native macOS storage and backup directories while keeping all seven adapters and backup policies.
- Detect macOS desktop/CLI processes, Electron helpers and known Node/Bun launch paths; refuse deletion when process inspection fails.
- Add native folder selection, Finder backup opening, POSIX path display, Codex CLI discovery for Finder launches, and nonblocking process locks.
- Add a double-click source launcher, a standalone `.app` build, and Apple Silicon/Intel CI packages with preserved bundle symlinks and signatures.
- Cover platform paths, process matching, folder integration and cross-process exclusion with isolated regressions.
- Ignore suffixed Codex database backup files when identifying live versioned databases; multiple actual live versions still block cleanup.
- Report Windows lock contention correctly without reading a byte already locked by another process.
- Publish checksummed Windows, Apple Silicon and Intel packages together only after all release builds pass.

## 1.2.2 — Codex catalog auxiliary records

- Adapt `inbox_items`, `automation_runs` and `live_visualization_suggestions`, checked against Codex desktop 26.915.4065.0.
- Remove only selected thread references; preserve automation definitions and host-scoped remote suggestions. Refuse ambiguous cross-host IDs in legacy tables that have no host field.
- Check schema compatibility at preview time and include auxiliary changes in stale-preview detection.
- Add synthetic regressions and packaged deletion checks for these tables, unrelated-data preservation and backup rollback.

## 1.2.1 — Exit when the browser closes

- Exit the backend after the last browser page closes, with a refresh grace period and a heartbeat fallback for lost browser connections.
- Keep other open tabs alive and finish active operations before shutting down; reject new work after an explicit exit request.
- Allow the exit endpoint to work independently of the currently selected application.
- Verify browser lifecycle transitions and packaged process exit; no changes to conversation deletion scope.

## 1.2.0 — OpenCode and Windows path compatibility

- Add OpenCode SQLite/legacy JSON session cleanup, including event records and dependent-session checks.
- Remove typed deletion confirmation; retain the preview, warning button, bound backup policy and single-use plan.
- Normalize Windows extended drive/UNC paths before containment comparisons while still resolving junctions and rejecting out-of-scope/device paths.
- Regression checks cover extended Codex rollouts, exact OpenCode ownership, account/project preservation, backup modes and rollback.

## 1.1.1 — MIT-licensed public preview

- Adopt the MIT License and update English/Chinese licensing documentation.
- Require the project license in downloadable release packages.
- No changes to conversation deletion or backup behavior.

## 1.1.0 — Public preview

- Six local application adapters; search, details, selection and deletion previews.
- Default/custom/no-backup policies bound to explicit confirmation.
- Known-process checks, path/ownership validation, stale-plan rejection and recovery journals.
- Windows executable and isolated synthetic demo.
- Sanitized examples, bilingual documentation and privacy-aware CI packaging.
- Startup without Codex installed; additional Node/Bun launch-path checks.

Experimental release: no guarantee of compatibility with every upstream version. The interface is Simplified Chinese.

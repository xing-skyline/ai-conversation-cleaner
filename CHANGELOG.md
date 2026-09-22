# Changelog

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

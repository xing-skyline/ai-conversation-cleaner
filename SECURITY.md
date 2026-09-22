# Security and privacy

This tool intentionally deletes application data. It is not a forensic eraser, an official migration tool, or a substitute for independent backups.

## Reporting

Do not publish real conversation databases, transcripts, journals, access tokens, backups, private paths or account details in issues. Use GitHub private vulnerability reporting if available. Otherwise open a minimal issue requesting a private contact channel, without exploit details or private data.

For ordinary bugs, include tool/app versions, the OS and a **synthetic** reproduction. Redact screenshots. Do not attach unchanged `--inventory` output.

## Boundaries

- APIs require a per-launch token. Loopback binding, Host/Origin checks, no-store responses, CSP and single-use deletion plans reduce accidental or cross-site requests.
- This is not a security boundary against malware or another process already running as the same Windows user.
- Conversation text is untrusted; the UI uses text nodes, not HTML interpolation.
- Keep target apps closed. Detection covers known processes, not every launcher/plugin or remote environment.
- Backups and journals are private user data. Network backup destinations are explicit user-directed copies.
- Rollback is not a cross-database/filesystem atomic transaction. Crashes and concurrent writes can require manual recovery.
- No-backup mode cannot restore committed deletion. Existing backups/shared caches remain.
- Upstream formats can change. Try the demo and review targets carefully after upgrades.
- The EXE is unsigned. Verify checksums; do not disable OS security controls.

## Publication hygiene

Only reviewed source and synthetic fixtures belong in Git. Local reports, generated databases, inventories, binaries and backups are ignored. Build public binaries on clean runners rather than a developer's personal environment. Automated checks are safeguards, not proof that all private data or vulnerabilities have been found.

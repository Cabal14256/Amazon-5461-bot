# Migration notes

## Codex migration — 2026-08-03

The newest outer project files were migrated from the rollback source at
`C:\Users\Admin\projects\amazon-5461-bot` into the clean Git repository at
`C:\Users\Admin\Documents\Amazon-5461-bot`.

- Added `AGENTS.md` and repo-scoped Codex skills under `.agents/skills/`.
- Preserved the Hermes source and heavy evidence directories as a read-only rollback archive.
- Copied local account, ledger, batch, workbook, and brand-pack assets into Git-ignored paths.
- Archived Hermes project memory and portable session history under Git-ignored `migration/private/`.
- Paused the legacy one-minute Hermes watchdog before the copy.
- Replaced Hermes provider discovery with repo-local `LLM_*` configuration.
- Replaced the empty Hermes watchdog hook with `runtime/codex_signal.json` and a deterministic checker.
- Quarantined account-specific and ephemeral scripts under Git-ignored `legacy/scripts-one-off/`.
- Moved captured Seller Central page samples from tracked `knowledge/` paths into the private migration archive; future error samples go to ignored `runtime/evidence/error-samples/`.
- Redacted historical email, AdsPower profile, and Case ID values from portable Codex references.

The active private account file is `runtime/private/accounts.json`. It is intentionally ignored by Git. The public shape-only example is `config/accounts.example.json`.

## Earlier project reorganization

Before this Codex migration, the project had been reorganized for an OpenClaw-style workspace:

- code, scripts, knowledge, documentation, and tests were separated from runtime outputs;
- the ledger moved under `runtime/state/`;
- evidence, logs, generated outputs, account configuration, brand packs, and workbooks were treated as local-only data;
- an earlier nested project copy was created but later became stale.

That history is retained only for provenance. The current outer Codex repository and its `AGENTS.md`, `PROJECT.md`, and `docs/CURRENT_STATE.md` are authoritative.

## Layout normalization — 2026-08-16

- Consolidated the remaining top-level `evidence/` files into
  `runtime/evidence/` with no path collisions.
- Moved the local source workbook and environment backup into
  `runtime/private/`.
- Reorganized tracked documentation into `docs/plans/`, `docs/migration/`, and
  `docs/reference/`.
- Moved account- or real-brand-specific archive details out of tracked docs and
  into ignored `migration/private/` storage.

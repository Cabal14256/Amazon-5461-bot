# Changelog

## 2026-08-05

- Classify explicit Spanish Amazon rejection wording as `declined` during Case follow-up.
- Fill the required contact email on newer MX GTIN Exemption form variants when present.
- Recognize Product Identity `kat-link` elements as valid "Select Brand" entry points.
- Prioritize real 5461 form fields over stale Brand Clarification text left on the underlying page.
- Wait for and recover a half-loaded approval panel after Connect Brand instead of selecting the brand twice.
- Added regression coverage for brand-link discovery, state precedence, and Connect Brand loading recovery.

## 2026-08-03

- Added persistent delayed 5461 Case follow-up tasks and a hidden sequential worker.
- Added generic Case-detail extraction, Amazon-message classification, and explicit
  `approved` / `declined` / `action_required` / `pending` semantics.
- Integrated Case outcomes with SQLite and the Excel application registry.
- Reused the production marketplace switcher for country-specific Case reads.
- Fixed marketplace-switch evidence screenshots passing `timeout` to `str()`.

## 0.1.0-openclaw-ready - 2026-06-07

### Added

- OpenClaw workspace files: `AGENTS.md`, `TOOLS.md`, and Skill package `skills/amazon-5461-automation/`.
- Project files: `README.md`, `PROJECT.md`, `CHANGELOG.md`, `pyproject.toml`, and `MIGRATION-NOTES.md`.
- Unified CLI wrapper: `python -m cli.amazon5461`.
- Runtime directories under `runtime/` for evidence, logs, state, exports, and private config.
- Private account config examples and `AMAZON5461_ACCOUNTS_PATH` support.

### Changed

- Moved root debug files, one-off logs, and legacy scripts into `archive/`.
- Moved `ledger.db` from `data/` to `runtime/state/` and updated `config/settings.yaml`.
- Replaced real `config/accounts.json` with an empty placeholder; examples live in `config/accounts.example.json`.
- Updated `.gitignore` to protect private configs, runtime artifacts, screenshots, Excel files, brand packs, and picture assets.
- Added pytest configuration so `pytest` only collects files under `tests/`.

### Security

- Real uploaded account records were not kept in the active project config.
- Future real account config should be stored locally in `runtime/private/accounts.json`.

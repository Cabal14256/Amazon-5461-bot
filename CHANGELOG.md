# Changelog

## 2026-08-16

- Keep `false_approved`, `pending`, and `verification_pending` Case tasks in
  scheduled follow-up until effective approval, explicit rejection, or the
  global six-check limit; unresolved sixth checks move to manual review and do
  not advance reapplication routes.
- Add `假过` to the Excel registry status dictionary with a dedicated style.
- Consolidated all active evidence output under `runtime/evidence/` and moved
  the remaining legacy top-level evidence into that partition without
  overwriting existing files.
- Moved the local 5461 source workbook and environment backup into
  `runtime/private/`; onboarding now prefers the private template location.
- Grouped historical plans, migration notes, and status references under
  `docs/plans/`, `docs/migration/`, and `docs/reference/`, with a new
  `docs/README.md` navigation page.
- Removed account- and brand-specific historical indexes/examples from the
  tracked documentation tree and retained them under ignored
  `migration/private/` paths.
- Added repository-wide editor and line-ending conventions, expanded generated
  file ignores, and locked the canonical evidence defaults with a regression
  test.

## 2026-08-15

- Hardened the legacy synchronous 5461 helper before publication: submission
  timestamps now import `datetime` explicitly, and browser cleanup uses an
  explicit backward-compatible `keep_browser_open` option instead of an
  undefined name.
- Removed temporary web-console credentials and account-specific UAT
  identifiers from tracked documentation while retaining non-sensitive test
  outcomes.

## 2026-08-14

- Recognize both singular and plural Add Product approval summaries such as
  `1 application required` and `2 applications required`, including arbitrary
  restriction counts, before opening the `View` application entry.
- Stop treating isolated 429 responses from Amazon Katal/monitoring telemetry
  endpoints as account rate limits. Structured non-telemetry 429/410001
  evidence still takes precedence and remains human-handled.
- Classify failures that cannot find the 5461 entry/form as
  `semantic_control_missing`, refresh deduplicated incident evidence on every
  recurrence, and preserve the existing 0.60 auto-triage threshold.
- Capture a bounded failure-page URL, visible-text excerpt, recognized state,
  and approval-control probes before the read-only Dashboard check, then add
  those fields to the redacted AI evidence bundle.

## 2026-08-12

- Removed the different-user (dual) approval requirement and the two-phase
  one-time-token confirmation flow for real submissions. `POST
  /api/jobs/prepare-submit` and `POST /api/jobs/confirm-submit` are replaced
  by a single reviewer-gated `POST /api/jobs/submit` that validates,
  preflights and queues the job directly; the `submit_confirmations` DB
  helpers/table creation, the dispatch-layer `missing_submit_confirmation`
  check, and the `web.submit_require_dual_approval` /
  `web.submit_confirm_ttl_minutes` settings are gone. The reviewer+ role
  gate, preflight, brand cap, `submit_enabled` master switch and audit
  trail remain.
- Hardened the Case follow-up worker lifecycle: the launcher records worker
  PID metadata only after the child has taken the worker lock (the lock is
  authoritative; the health check trusts either file's live PID), and stale
  `running` follow-ups are requeued as soon as the claiming worker PID is
  verifiably dead (`case_followups.claimed_pid`), with the one-hour threshold
  kept only as a fallback for rows without a claimant PID.
- Queued web jobs now expose a read-time `queue_reason` annotation
  (`waiting_case_followup` / `waiting_profile_lock` / `waiting_serial_queue`)
  on list/detail/SSE payloads, shown in the console as a Chinese hint instead
  of a bare queued status.
- Adopted the shared desktop-client ChatGPT session as the standard Codex CLI
  identity; it is no longer documented as a temporary fallback pending a
  dedicated service identity.
- Changed the web console real-submission default to enabled. The explicit
  prepare/confirm token flow, preflight, audit trail, brand cap and current
  different-user approval check remain mandatory. Dual approval is retained
  for now and scheduled for a later policy change.
- Confirmed Feishu Bitable real writes remain disabled by default.

## 2026-08-11

- Added stage 4 real submission approval to the web console: two-phase
  `POST /api/jobs/prepare-submit` (operator+) and
  `POST /api/jobs/confirm-submit` (reviewer+) flow behind the
  `web.submit_enabled` master switch (originally default off; changed to on
  by the 2026-08-12 policy decision).
- One-time confirmation tokens are returned exactly once and stored only as
  sha256 hashes in the new `submit_confirmations` table, with TTL
  (`web.submit_confirm_ttl_minutes`), a rebound params digest, and optional
  dual approval (`web.submit_require_dual_approval`, default on: confirmer
  must differ from preparer).
- Added synchronous submit preflight (`src/jobs/preflight.py`): blockers
  reject prepare with 422, warnings are advisory; a single submit is capped
  at `web.submit_max_brands` (default 5).
- Dispatch-layer fail-safe: a `submit` job without a consumed confirmation
  is failed as `missing_submit_confirmation` and never spawned; confirmed
  jobs run `cli.amazon5461 run --submit` through the existing serial queue.
- Frontend: red real-submission wizard mode with confirmation screen,
  reviewer-only confirm panel on the job detail page, and a
  `terminated_unknown_state` banner requiring a Dashboard / View Selling
  Applications check before rerun.
- All prepare/confirm/rejection actions are audited in `web_audit_events`;
  semantics documented in `docs/runbooks/runbook-web-console.md` section 9.

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
- Project files: `README.md`, `PROJECT.md`, `CHANGELOG.md`, `pyproject.toml`, and `docs/migration/migration-notes.md`.
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

## 2026-08-11 (stage 5)

- Added persistent incident detection (`repair_incidents` table,
  `src/incidents/` signature/detector/evidence-bundle package): failure
  signals are classified at intake, aggregated by PII-free signature, scored
  for confidence, and bundled with redacted evidence (screenshots withheld).
- Environment/human categories (429, CAPTCHA, 2FA, login expiry, account
  risk, brand block, config missing, Amazon platform error, business
  uncertain) never enter the repair-candidate queue; `business_uncertain`
  is skipped for dry-runs. `codex_signal.json` dual-writes for one release
  cycle; the batch failure boundary records incidents as a side channel.
- Web API: incidents list/detail/close (operator+ close, audited) plus
  `incidents_open` in the overview payload; bundle downloads reuse the
  evidence allowlist. Frontend: pending-page incident groups, repair center
  on real data (mock demo preserved), overview incident banner.
- Added `scripts/replay_incidents.py` offline classifier replay over
  historical batch states for false-positive validation and UI seeding.

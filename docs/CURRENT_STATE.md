# Current State

This repository is the clean Codex migration target for the Amazon 5461 automation previously operated through Hermes Agent.

## Canonical locations

- Active Codex repository: `C:\Users\Admin\Documents\Amazon-5461-bot`
- Read-only Hermes rollback source: `C:\Users\Admin\projects\amazon-5461-bot`
- Hermes state archive source: `C:\Users\Admin\AppData\Local\hermes`

The old source contains an outdated nested copy at `amazon-5461-bot\amazon-5461-bot`. Do not use it as the code source. The outer files were newer and were selected for this migration.

## Migrated active assets

- Python source, CLI, scripts, tests, configuration templates, knowledge, and project documentation
- 29 local brand packs under ignored `brand_packs/`
- Local account configuration under ignored `runtime/private/`
- Ledger state and current batch data under ignored `runtime/state/` and `data/`
- Required `5461信息模版.xlsx` under ignored `runtime/private/templates/`
- Repo-scoped Codex skills under `.agents/skills/`

## Archived rather than copied into the active tree

- About 2.1 GiB of runtime evidence
- About 692 MiB of legacy top-level evidence
- About 406 MiB of duplicate source pictures
- The old nested project copy and its virtual environment

These remain in the read-only Hermes rollback source. Project memory and exported sessions are stored under ignored `migration/private/`.

The private export contains 106 project sessions and 12,315 message rows in portable JSONL files, plus 186 project-memory files, the four original Hermes skills, the paused cron definition, and relevant global-memory files. Stored reasoning and system prompts were intentionally omitted from the portable session export.

## Runtime transition

- The Hermes watchdog job `amazon-5461-hermes-watchdog` was paused for migration.
- Do not resume that job after cutover; it ran every minute, accumulated thousands of runs, and consumed model quota even when no useful signal was present.
- Active Python code no longer reads Hermes provider configuration or invokes the Hermes CLI.
- The replacement is implemented as `runtime/codex_signal.json`, written only after deterministic state-loop recovery is exhausted.
- Inspect it with `python scripts/check_codex_signal.py`; acknowledge it with `--mark-handled` after diagnosis.
- Optional page analysis reads only the local `LLM_*` configuration from `.env` and does not invoke a model unless the application explicitly requests analysis.

Twenty-five account-specific, generated, or ephemeral CDP scripts were quarantined under ignored `legacy/scripts-one-off/`. Their original copies remain in the Hermes rollback source.

Forty captured Seller Central example files were moved out of tracked `knowledge/` directories into ignored `migration/private/knowledge-examples/` because they contained account-level page content. Future stuck-state samples are written to ignored `runtime/evidence/error-samples/`. Portable skill references were scrubbed of historical email, AdsPower profile, and Case ID values.

## Repository layout normalization

The 2026-08-16 cleanup made `runtime/evidence/` the only active evidence root.
The remaining 248 files (45.32 MiB) under the old top-level `evidence/` were
merged without collisions and without deleting evidence. The onboarding source
workbook and the retained environment backup now live under ignored
`runtime/private/` subdirectories.

Tracked documentation is grouped by purpose under `docs/plans/`,
`docs/migration/`, and `docs/reference/`; `docs/README.md` is the navigation
entry point. Historical indexes and examples containing account or real-brand
metadata were retained only under ignored `migration/private/` paths.

## Current operational policy

Read `AGENTS.md` and `PROJECT.md`. Default to diagnosis/dry-run, preserve evidence, and require explicit authorization for a real submission.

## Delayed Case follow-up

Real 5461 submissions that capture a reliable Case ID now create a persistent
SQLite follow-up task. A hidden worker waits for the configurable delay, opens
the matching AdsPower profile, reuses the shared marketplace switcher, reads the
Case detail messages, and updates the local registry only for an explicit reply
outcome. `Answered` is not treated as approval. Due tasks are grouped by account
and marketplace, so consecutive brands reuse one browser session and one
marketplace selection. On attach, the worker closes
all accumulated tabs except the exact `start.adspower.net` host; it closes its
single work tab when that account/marketplace group finishes and removes any
extra popup tabs after each Case. Group cleanup then stops the matching AdsPower
profile so the browser does not remain open after the follow-up finishes.
Before any browser check, an exact account/site/brand already recorded as
effectively `approved` is completed without reopening its Case. This shortcut
does not apply to `false_approved`, `Answered`, pending, unknown, or technical
states.
Only an effective `approved` result or an explicit `declined` result completes
automatic follow-up. `false_approved`, `pending`, and `verification_pending`
stay on the same Case and continue at `retry_interval_hours` for at most six
automatic checks in total. If check 6 is still unresolved, the exact result is
preserved as `manual_review` and no further automatic check is scheduled.
Unclassified Amazon replies use deterministic multilingual templates first,
then a read-only Codex classifier. Transient Codex timeout/unavailable/process/
schema failures receive one delayed retry tracked independently from Case
browser attempts; the second failure degrades to manual review. The per-call
timeout is 300 seconds, and partial Codex events, stderr, and a diagnostic
record are retained on timeout. Explicit English "not approved / not accepting
applications" replies and Spanish "finalizamos la revisión y aceptamos tu/su
solicitud" replies are handled locally; an approval reply still must pass the
existing effective permission verification. ChatGPT/Cloudflare HTTP 403 is
recorded distinctly as `forbidden` and receives a 30-minute cooldown before
the one allowed retry.
An explicit Case approval now triggers effective checks in the same account and
marketplace. The normal path
requires the exact brand under Manage Your Brands and Add Product advancing to
Description. If the confirmed brand portfolio omits the brand, the worker opens
Add brand, searches the exact brand, selects only a screen-protector-related
result, and uses the brand-specific "You are approved to list ... products"
message as the decisive fallback. A stale/empty portfolio alone is never
`false_approved` / `假过`. Technical, login, CAPTCHA, 2FA, ambiguous search
results, and unrecognised page states never become `假过`. See
`docs/runbooks/runbook-case-followup.md`.

At scheduling time the task can now bind read-only to one existing Feishu
Bitable record using account, brand, SKU, marketplace, and the original EU
country option. Repeat markers such as `比利时1` are preserved. Zero, multiple,
or same-site multi-round matches remain unbound instead of being guessed. The
binding stores no Feishu credentials or cell contents, and real Bitable writes
remain disabled by default.

EU non-UK submissions prefer the unique existing row containing the UK country
option as their shared Feishu progress target. The latest EU country result
overwrites that row's single `5461进度` value. Multiple matching UK rows remain
ambiguous and are never guessed.

When enabled, Feishu scheduling now also idempotently plans or creates a missing
actual-country detail row from the exact submitted SKU, title, and statement.
That detail row does not receive a progress value. The UK shared row is created
only when account-specific UK material exists and passes account/site/brand SKU
validation; otherwise the result remains explicitly `missing_uk_materials`.
Formula, lookup, Case reply, and time fields are never synthesized.

AdsPower connections now validate the active profile, DevTools HTTP endpoint,
and Playwright CDP handshake separately. A failed handshake uses a bounded
30-second timeout and one full profile restart. Follow-up checks detach and stop
the profile after group cleanup, and a same-account circuit breaker defers
sibling tasks after recovery is exhausted. SOCKS proxy failures remain distinct
from CDP failures and never change a business result.

## Finite reapplication routes

Explicitly authorized campaigns can continue an explicitly declined
application through a finite regional route: `US -> MX` for NA, and
`UK -> BE -> DE -> SE -> NL -> FR` for EU. The next site is scheduled two hours
after the rejection. Effective approval stops the campaign; rejection at the
last site becomes `route_exhausted`. False approval stays on the same linked
Case in `waiting_case`; manual/unknown Case replies and every technical failure
pause rather than advance. Campaign and attempt rows are linked to the exact
Case follow-up in SQLite, and each attempt has unique state and log files.

The campaign CLI requires the account, brand, region, `--submit`, and `--yes`
before any real run can be dispatched. A command without `--submit` is a
read-only preflight. See `docs/runbooks/runbook-reapplication-campaigns.md`.

MX now follows the same shared-progress policy as EU: its result targets the US
row's `5461进度`, while the exact MX detail row remains separate. Missing US and
MX rows are created only from account-specific site material behind the
existing Feishu write gate.

Submissions that may have completed without exposing a Case ID now open View
Selling Applications in a fresh tab first. If no unique exact-brand ID is
available, a persistent `case_id_recoveries` queue checks the correct regional
dashboard later. It reads IDs hidden in dashboard `kat-link[label]` attributes,
refuses to guess between multiple same-brand Cases, and hands a recovered ID to
the existing delayed Case-detail worker. Reapplication campaigns remain in
`waiting_case_id` during this process and cannot advance countries from a
missing or ambiguous Case ID.

## Read-only web console (plan stage 2)

The internal LAN console from
`docs/plans/plan-internal-console-codex-repair-2026-08-04.md` stage 2 is implemented
and verified read-only:

- Backend `src/web/` (FastAPI) serves only GET business endpoints plus auth
  login/logout. No endpoint can start, submit, stop or signal automation.
- Local-account auth: PBKDF2-HMAC-SHA256 password hashes in `web_users`,
  HMAC-signed session cookie (HttpOnly, 12 h), login rate limiting. Manage
  users with `scripts/manage_web_users.py`; the session secret comes from
  `WEB_SESSION_SECRET` in `.env`.
- Read APIs cover health, catalog (accounts are stripped of username,
  AdsPower profile and entry URL), jobs, applications with the authoritative
  status read model (Case reply > Dashboard same-day > explicit Case ID >
  local batch state), Case follow-ups, Case ID recoveries, reapplication
  campaigns, and evidence files guarded by an allowlist root with text
  redaction.
- `src/db.py` connections now use WAL + busy_timeout; batch state writes go
  through `src/state_files.atomic_write_json`. `web_users` and
  `web_audit_events` are added by an idempotent migration.
- Frontend `frontend/` (Vue 3 + Naive UI) talks to the real `/api` by
  default; set `VITE_USE_MOCK=1` to fall back to mock data. It adds a login
  page, route guards, and the reapplication page.
- Run it with `scripts/run_web_console.py`; HTTPS uses the self-signed cert
  from `scripts/make_dev_cert.py` (stored git-ignored under
  `runtime/private/certs/`). Deployment, firewall CIDR restriction and
  auto-start steps live in `docs/runbooks/runbook-web-console.md`.
- Verified: 133 pytest tests pass (30 new under `tests/web/`), unauthenticated
  requests return 401, catalog responses contain no secret fields, evidence
  path traversal is rejected, and HTTPS login + health check succeed.
- A temporary bootstrap administrator was created during verification; rotate
  or disable that local credential before LAN exposure. Credential values are
  intentionally not recorded in tracked documentation.

Stage 3 (job queue) is implemented on top of the same console:

- Whitelisted `diagnose` / `dry_run` jobs are created from the wizard
  (`POST /api/jobs/diagnose|dry-run`, operator+), persisted in
  `automation_jobs` / `automation_job_items`, and executed globally serial
  under `profile_locks`; request-stop is graceful (next brand boundary),
  force-terminate is admin-only and lands in `terminated_unknown_state`.
- Job reads are DB-primary with SSE (`GET /api/jobs/{id}/events`: job status
  + redacted log increments, Last-Event-ID resume); the frontend detail page
  consumes SSE with a 5 s polling fallback. There is still no web entry point
  to a real submission (stage 4).
- Semantics (creation, serial dispatch, profile lock, stop, restart
  recovery, SSE) are documented in `docs/runbooks/runbook-web-console.md`
  section 8.
- Dry-run is a real no-submit walk since 2026-08-10: `run_full_5461_batch.py`
  passes `no_submit=dry_run` into `submit_5461_from_add_product`, which fills
  the 5461 form in a real AdsPower browser and stops before every
  `kat-button#submit_button` click (evidence: `no_submit_before_submit.png`,
  status `dry_run`). Downstream registration (ledger, follow-up, recovery,
  Feishu, Excel) stays gated off for `dry_run`; covered by
  `tests/test_no_submit_dry_run.py`. UAT via the console (2026-08-10) covered
  an already-approved combination that correctly stopped as uncertain and a
  second combination that filled the form, short-circuited before the submit
  click, captured evidence, and produced zero ledger side effects.

Stage 4 (real submission) is implemented on top of the same queue:

- Real submissions go through a single explicit endpoint, gated by
  `web.submit_enabled` (default `true`; when explicitly switched off,
  the endpoint returns 403 and no path can create a submit job).
  `POST /api/jobs/submit` (reviewer+) runs whitelist validation plus a
  synchronous preflight and creates the `submit` job directly in
  `queued`; the dispatcher picks it up from the serial queue and the
  runner executes `cli.amazon5461 run --submit`. A single submit is
  capped at `web.submit_max_brands` (default 5) brands; no batch or
  multi-account real submission exists.
- Policy change (2026-08-12): the two-phase prepare/confirm one-time
  token flow and the different-user (dual) approval requirement were
  removed. The `submit_confirmations` helpers/table creation and the
  `web.submit_require_dual_approval` / `web.submit_confirm_ttl_minutes`
  settings are gone (tables in existing databases are simply left
  unused). The reviewer+ role gate, preflight, brand cap, the
  `submit_enabled` master switch and the audit trail all remain.
- Preflight blockers (inactive/profile-less account, missing site config,
  missing brand-pack upload files, brand count over the cap) reject the
  request with 422 and no job; warnings (unreachable AdsPower API,
  in-flight case follow-up, no recent successful dry-run) are advisory
  only.
- Every submit/rejection is written to `web_audit_events` with actor,
  timestamp, non-secret params detail (account/site/brands) and job id.
- The frontend adds a red "real submission" wizard mode with a color-coded
  preflight display; any `terminated_unknown_state` job shows a banner
  requiring a Dashboard / View Selling Applications check before any
  rerun. Semantics are documented in
  `docs/runbooks/runbook-web-console.md` section 9.
- Historical UAT under the former two-phase flow (2026-08-11) exercised
  `run --submit` against an already-approved combination, so it was an
  Amazon-side no-op by design. It found no authorization entry and stopped
  `uncertain` with zero ledger side effects. A real submission to a brand that
  actually needs authorization still requires separate explicit authorization.

Stage 5 (persistent incident detection) is implemented:

- Failure signals now persist in the `repair_incidents` table with
  classification, signature-based aggregation (`occurrence_count` grows
  instead of overwriting a single slot), confidence scoring, and a redacted
  evidence bundle under `runtime/evidence/incidents/<id>/` (screenshots are
  withheld by default in stage 5). `runtime/codex_signal.json` still exists
  and dual-writes; it will be retired after one release cycle.
- Classification splits at intake: environment/human categories (captcha,
  2FA, login expired, account risk, 429/410001, brand block, config missing,
  Amazon platform error, business uncertain) never enter the repair-candidate
  queue; the seven anomaly classes (selector_missing, state_unknown,
  dom_contract_changed, navigation_changed, semantic_control_missing,
  flow_loop_exhausted, result_contract_changed) feed the repair center.
  Exclusions win over candidates; `business_uncertain` is not recorded for
  dry-runs. Account/brand/case-id/long numbers never enter the signature.
- Signal entry points: state-loop recovery exhaustion (dual-write via
  `write_pending_signal`) and the per-brand failure boundary in
  `run_full_5461_batch.py` (side-channel, never interrupts the batch).
- Web API: `GET /api/incidents` (filters + pagination),
  `GET /api/incidents/{id}` (detail + bundle file list, downloads via the
  existing `/api/evidence/file` allowlist), `POST /api/incidents/{id}/close`
  (operator+, audited); overview gains `incidents_open`. Frontend: incident
  group cards on the pending page, the repair center lists real repair
  candidates with evidence links, and the overview shows an incident banner.
- `scripts/replay_incidents.py` replays historical batch states offline
  through the classifier (dry by default, `--write` to persist) — used to
  verify that known 429/CAPTCHA/2FA samples never land in the repair queue.
- Semantics are documented in `docs/runbooks/runbook-web-console.md`
  section 10. Codex triage/patch generation is stage 6-7 scope.

Stage 6 (Codex read-only triage) is implemented
(`docs/plans/plan-stage-6-triage-2026-08-11.md`):

- Add Product approval-entry detection accepts any singular/plural count
  (`N restriction(s)` / `N application(s) required`). Incident intake uses
  structured network metadata to ignore isolated 429 responses from Katal
  telemetry endpoints; genuine non-telemetry 429/410001 evidence remains a
  human-handling exclusion. Missing 5461 entry/form failures are repair-class
  `semantic_control_missing`, so a repeated occurrence reaches the configured
  0.60 Codex auto-triage threshold.
- Failed/uncertain Add Product paths capture a bounded pre-Dashboard page
  summary (URL, visible-text source, recognized state, and selector probes)
  for the redacted Codex evidence bundle, instead of exposing only the later
  Dashboard result to read-only triage.

- `src/codex_client/` drives the Codex CLI (installed globally, codex-cli
  0.147.0) as a read-only subprocess, using the ChatGPT login session shared
  with the desktop client. This shared session is the adopted operating
  identity, not a temporary fallback. Invocation uses `codex exec --json --output-schema
  triage-schema.json -o <file> -s read-only -C <repo_root> -`, prompt via
  stdin, timeout configurable (`codex.timeout_sec`, 300s). On Windows the
  npm `.CMD` shim is wrapped in `cmd.exe /c` so it spawns without a shell.
- Triage output follows blueprint §16.1 stage A (seven classifications,
  confidence, safe_to_generate_patch, requires_human_review,
  missing_evidence) with a strict schema (every property key required —
  the Codex API rejects partial `required` lists). JSONL events land in
  `runtime/logs/repair/<job_id>/codex-events.jsonl`, validated results in
  `runtime/state/repair/<job_id>/result.json`; schema-invalid output is
  retried exactly once, then closed as `schema_invalid`.
- New `codex_repair_jobs` table (blueprint §9.6 full fields; stage 6 only
  uses `stage='triage'`), plus a nullable `detail` column on
  `web_audit_events`. Successful triage moves the incident `open → triaged`
  without overwriting the detector classification; failures leave it `open`
  and retryable. Every outcome (ok/timeout/unavailable/quota_exceeded/
  schema_invalid/error) is audited with `trigger=manual|auto`.
- API: `POST /api/incidents/{id}/triage` (operator+, synchronous),
  `GET /api/incidents/{id}` gains `latest_triage`,
  `GET /api/incidents/{id}/repair-jobs` (viewer+). Auto-triage runs as a
  web background scanner (`AutoTriageRunner`, poll interval
  `codex.auto_triage_poll_seconds`): open incidents with a repair-class
  classification and confidence ≥ `codex.auto_triage_min_confidence` (0.60)
  are triaged once per `last_seen_at`; excluded categories are blocked a
  second time here, and failures only write audit — the main automation is
  never affected. `codex.daily_call_limit` caps real Codex calls per day.
- Frontend repair center shows a "Codex 判因" card next to the detector
  classification (result, confidence, reason, missing evidence, job status,
  JSONL log download), a retry-triage button (operator+), a disabled
  "生成补丁" placeholder (stage 7), and a degradation banner when Codex is
  unavailable or over quota.
- Prompt template lives at `knowledge/prompts/codex_triage_prompt.md` with
  untrusted-data wrapping; screenshots stay withheld. Patch generation,
  worktrees, and release gates remain stage 7-8 scope.

Stage 7 (isolated patch generation) is implemented
(`docs/plans/plan-stage-7-patch-2026-08-11.md`):

- `src/repair/worktree.py` creates an isolated git worktree per patch job
  (`<codex.worktree_root>/repair-<job_id>/`, branch
  `codex/repair-<incident_id>-<short_signature>`). The baseline is HEAD plus
  the production tree's uncommitted **tracked** diff (`git diff --cached`
  then `git diff`, in that order) applied and committed as
  `baseline: uncommitted production state at <sha>` (Day-0 decision 2); the
  baseline SHA is recorded on the job. The production tree is only ever
  read — all mutations target the worktree and its own branch; a stale
  branch/worktree from a failed attempt is evicted on retry. The redacted
  evidence copy lives untracked in `.repair-evidence/` and is
  pathspec-excluded from every `git add`. Retention cleanup
  (`scripts/cleanup_repair_worktrees.py`, `--dry-run` supported) removes
  worktrees of terminal patch jobs older than
  `codex.worktree_retention_days` (14) and prunes metadata.
- `src/codex_client/patch.py` reuses the stage-6 subprocess plumbing with
  `-s workspace-write -C <worktree>` and the strict
  `schemas/repair-result-schema.json` (summary / changed_files /
  risk_level R0–R3 / requires_human_review / tests_added / tests_ran /
  tests_passed / notes). Startup preconditions (blueprint §16.1B): latest
  triage succeeded with a code/selector classification and
  `safe_to_generate_patch=true`, incident `triaged` (never closed), no
  active patch job — `codex_repair_jobs` gained a partial unique index
  `UNIQUE(incident_id) WHERE stage='patch' AND status IN
  ('running','patch_ready','validating')` with same-transaction
  check-and-insert (`create_patch_job`, conflict → 409). Schema-invalid
  output is retried exactly once within the same job row (the unique index
  forbids a second active row).
- Risk gate (blueprint §17.3): the stricter of Codex's self-assessed
  `risk_level` and the backend's file-path review (`config/selectors/` +
  `tests/` → R0, `src/` → R1, navigation/form-step paths → R2,
  submit/legal/authorization paths → R3, out-of-scope → ≥R2) wins. R3 is
  always refused; R2 proceeds only with the operator's explicit
  `allow_r2=true`. Refused patches close the job as `validation_failed`
  with the computed risk recorded, and the incident returns to `triaged`.
- `src/repair/diff_scan.py` implements blueprint §18.1 as an extensible
  rule table over the parsed diff: allowed-scope enforcement, private-path
  references, sensitive content (reuses `src/capture/redact.py`
  `SENSITIVE_PATTERNS` plus AdsPower-profile/account-id/Case-ID extras on
  added lines), `--submit`-gate net removal, CAPTCHA/2FA/rate-limit
  bypasses, Draft/no-Case-ID → terminal mappings, evidence/Dashboard/
  human-review removal, unbounded retries / weakened cooldowns, and live
  Seller Central calls from tests. A passing diff is committed as the
  branch's second commit (`codex: repair incident <id>`, SHA recorded) and
  stored at `runtime/state/repair/<job_id>/patch.diff`; job →
  `patch_ready`, incident `patching → patch_ready`.
- Incident state machine opens `triaged → patching → patch_ready`; every
  failure returns to `triaged` (retryable). New job columns
  `baseline_sha` / `patch_sha`; job status vocabulary adds `patch_ready` /
  `validation_failed` (`validating` reserved for stage 8).
- API (`src/web/api/repair_jobs.py`): `POST
  /api/incidents/{id}/generate-patch` (operator+, `allow_r2` default
  false; 409 not-triaged/closed/conflict, 422 unsafe/r3, 503 disabled —
  all audited `patch_generate`), `GET /api/repair-jobs/{id}` (viewer+,
  job + result.json), `GET /api/repair-jobs/{id}/diff` (viewer+,
  patch.diff text, path reconstructed inside `codex_state_root` only).
  Approve/release endpoints remain stage 8.
- New config (`codex:` section): `patch_timeout_sec` (600),
  `worktree_root`, `worktree_retention_days`, `patch_allowed_paths`
  (`config/selectors/`, `src/executor/`, `src/capture/`, `tests/`).
- Frontend repair center: the "生成补丁" button is live for triaged
  incidents with `safe_to_generate_patch=true` (R2 asks for explicit
  `allow_r2` confirmation; 409 conflict is handled), and a patch section
  shows branch/worktree, risk level, changed files, Codex summary/tests,
  and the full diff in the existing `DiffViewer`. Approve/reject buttons
  are disabled placeholders until stage 8.
- Evidence-bundle backfill: `scripts/backfill_incident_bundles.py`
  (dry-run default, `--write` to persist) rebuilt bundles for all 183
  replay-created incidents from historical batch-state files
  (state trace + run context; DOM/screenshot artifacts honestly marked
  absent). Known limitation: redaction over-mangles timestamps inside the
  trace (digit runs match the phone pattern) — safe but noisy.
- Tests: 67 new (worktree lifecycle/dedup/cleanup/private-file exclusion,
  §18.1 rule coverage + clean-diff no-false-positive, patch precondition
  branches/timeout/schema-retry/risk strictness, and an end-to-end API
  test where mock Codex edits a real selector in the isolated worktree
  while the fixture production tree's `git status`/HEAD stay byte-identical).
  Full suite: 327 passed; vue-tsc and vite build green. No merge/release
  action exists in this stage.
- Real-Codex patch UAT has no qualifying input yet: re-triaging a backfilled
  historical incident still returns
  `insufficient_evidence` (the trace lacks DOM/selector probes), so no
  real incident currently yields `safe_to_generate_patch=true`. Decision
  (2026-08-12): wait for the next genuine failure — new incidents are
  captured with live page evidence (DOM summary, selector probes) and
  will serve as the real patch UAT input. The first real patch run must start
  from a committed stage 5–7 baseline so the isolated worktree contains all
  repair modules and can execute its in-worktree tests.

## Pre-publication verification (2026-08-15)

- The current suite contains 392 tests and passes in full. Frontend
  `vue-tsc --noEmit` and the Vite production build also pass.
- Stage 8 has database state/approval helper scaffolding and targeted DB
  coverage only. Automated validation gates, approval/release endpoints,
  production merge/revert behavior, and canary release flow are not yet
  implemented.
- Stage 9 hardening and the one-week controlled trial have not started.

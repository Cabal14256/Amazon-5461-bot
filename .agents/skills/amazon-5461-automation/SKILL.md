---
name: amazon-5461-automation
description: Run, diagnose, monitor, or repair the Amazon Seller Central 5461, catalog authorization, brand verification, and GTIN exemption automation in this repository. Use for AdsPower or Playwright execution, batch submissions, Dashboard result checks, stuck-page analysis, selectors, evidence review, retries, and account/site/brand-specific failures. Do not use it to bypass CAPTCHA, 2FA, platform controls, or the repository's explicit-submit requirement.
---

# Amazon 5461 Automation

Operate the repository's Amazon Seller Central automation while preserving its human-review and evidence requirements.

## Establish context

1. Work from the repository root. Do not use the legacy Hermes path.
2. Read `AGENTS.md`, `PROJECT.md`, `docs/CURRENT_STATE.md`, and the relevant files under `config/` before acting.
3. Treat `runtime/private/accounts.json`, `.env`, account spreadsheets, cookies, tokens, and browser profile data as secrets. Inspect them only when necessary and never print their values.
4. If the requested account is absent, invoke `$amazon-5461-account-onboarding` before attempting a batch.

## Choose the execution mode

- Default to diagnosis or dry-run.
- Require an explicit user request naming the real account, site, brands, and intent before any submission.
- Keep `Draft`, `Submitted no Case ID`, `Under Review`, `Approved`, `Declined`, `Already Approved`, `Not Found`, and technical failure as distinct states.
- Treat missing Case ID as uncertain until Dashboard, Selling Applications, retained tab state, or final evidence is checked.
- Stop and request human review for CAPTCHA, 2FA, login expiry, account-risk warnings, unknown destructive UI, repeated 410001/429 responses, or unclear submission scope.

## Preflight

Use the project interpreter when it exists:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m cli.amazon5461 diagnose --account <account> --brand <brand> --site <site>
.\.venv\Scripts\python.exe -m cli.amazon5461 dry-run --account <account> --brand <brand> --site <site>
```

Verify the AdsPower local API and target profile before opening Seller Central. Confirm the account/site/brand mapping and statement files before a real run.

## Run long batches safely on Windows

Full batches take several minutes per brand. Start them as hidden background processes with separate stdout/stderr logs. Do not use a foreground shell timeout, a trailing `&`, or a pipe to `head`/`tail`.

```powershell
$batchArgs = @(
  'scripts/run_full_5461_batch.py',
  '--accounts', '<account>',
  '--brands', '<brand1,brand2>',
  '--site', '<site>'
)
Start-Process -FilePath '.\.venv\Scripts\python.exe' `
  -ArgumentList $batchArgs `
  -WorkingDirectory (Get-Location) `
  -WindowStyle Hidden `
  -RedirectStandardOutput 'runtime\logs\<label>.out.log' `
  -RedirectStandardError 'runtime\logs\<label>.err.log' `
  -PassThru
```

Record the PID, follow the actual log files, and check `data/batch_state.json` after completion. Do not infer completion from a quiet terminal.

If state-loop recovery is exhausted, inspect the deterministic local handoff:

```powershell
.\.venv\Scripts\python.exe scripts\check_codex_signal.py
```

Review the referenced evidence before acknowledging it with `--mark-handled --note "<summary>"`. Do not restore the historical one-minute Hermes watchdog.

## Verify results

1. Read `data/batch_state.json` before another batch can overwrite it.
2. Inspect the newest log and final-state evidence.
3. Use local image inspection for screenshots. Imported Hermes references may say `vision_analyze`; in Codex, inspect the image with the available local image tool.
4. When any result is failed, partial, missing a Case ID, or conflicts with the screenshot, run the Dashboard checker and classify the visible row.
5. Report each brand separately with account, site, final state, Case ID when present, evidence path, and required next action.

## Route detailed cases

Read the entire relevant reference before acting:

- Background execution and result recovery: `references/batch-monitoring-and-reporting.md`
- Core business invariants: `references/business-rules.md`
- New account/brand/site combinations: `references/new-account-brand-site-prereqs.md`
- Selectors and Shadow DOM: `references/selectors.md`, `references/close-panel-shadow-dom-2026-06.md`
- Brand selection: `references/brand-selection-failure-pattern.md`, `references/brand-selection-render-timing-2026-06.md`
- Dashboard interpretation: `references/dashboard-manual-read-pattern.md`, `references/dashboard-misread-fix.md`
- GTIN exemption variants: `references/gtin.md`, `references/gtin-exemption-form-2026-06.md`
- Rate limits and edge cases: `references/edge-cases.md`, `references/troubleshooting.md`
- Imported Hermes environment history: `references/hermes-env-debugging.md`, `references/hermes-troubleshooting-addendum.md`

Treat Hermes-specific tool names, cron commands, and absolute paths in imported references as historical notation. Translate them to the current repository, shell, local image inspection, and the deterministic Codex signal workflow above.

## Preserve evidence and security

- Keep screenshots, traces, logs, exports, ledgers, and private configuration under ignored runtime/data paths.
- Never stage or commit `runtime/private/`, `data/`, `brand_packs/`, `.env`, spreadsheets, logs, screenshots, or migration archives.
- Never forge brand claims, documents, account identity, or application evidence.
- Prefer a slower auditable run over aggressive retries.

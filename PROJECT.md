# Amazon 5461 Bot Project

## Purpose

This project supports safe, evidence-driven Amazon Seller Central 5461 / GTIN / brand-authorization workflow automation inside a Codex local project.

## Allowed actions

- Read project code, page knowledge, selectors, and non-sensitive runtime summaries.
- Run syntax checks, unit tests, diagnostics, and dry-runs.
- Collect screenshots, HTML snapshots, and structured evidence under `runtime/evidence/`.
- Check Dashboard / Selling Applications status before changing final result state.
- Generate repair plans and selector updates for human review.

## Disallowed actions

- Do not bypass CAPTCHA, 2FA, login checks, platform risk controls, or rate limits.
- Do not forge materials, account identity, documents, evidence, or brand claims.
- Do not print, summarize, commit, or store real passwords, cookies, tokens, API keys, or email credentials.
- Do not perform real submit actions unless the user explicitly asks for a real run.
- Do not retry repeatedly after 429, 410001, CloudFront chunk failures, login expiry, or account risk signals.

## Default execution policy

- Default mode is read-only diagnosis or dry-run.
- Real submission requires an explicit `--submit` or equivalent user instruction.
- Automatic reapplication is a continuation of an explicitly scoped real
  submission only when the reapplication auto-authorization switch has been
  deliberately enabled. An inclusive persisted Case-row waterline may restrict
  automatic enrollment to one approved rejection and later records. The finite
  route, audit source, and technical-failure pause rules remain mandatory.
- `Draft`, `Submitted no Case ID`, `Declined`, `Already Approved`, `Under Review`, and `Not Found` must remain separate states.
- Missing Case ID is not automatically success or failure; check Dashboard / Selling Applications first.
- Seller Central login expiry, CAPTCHA, 2FA, account-risk, or an unknown auth
  page pauses the exact AdsPower profile. A durable submit-click fence is
  written before the click; once fenced, recovery may reconcile Dashboard/Case
  only and must never click Submit again. See
  `docs/runbooks/runbook-auth-recovery.md`.

## Runtime layout

```text
runtime/evidence/   screenshots, HTML snapshots, traces
runtime/logs/       run logs
runtime/state/      ledger database and machine state
runtime/exports/    generated human-readable reports
runtime/private/    local-only account config and other secrets
runtime/private/templates/  local-only business workbooks and templates
runtime/codex_signal.json  local pending-diagnosis handoff (Git-ignored)
```

## Private account config

The active real account config should live at:

```text
runtime/private/accounts.json
```

Normally this is auto-detected when `runtime/private/accounts.json` exists. Optional override:

```powershell
Optional override: $env:AMAZON5461_ACCOUNTS_PATH="runtime/private/accounts.json"
```

`config/accounts.json` should remain absent and must not contain real accounts.

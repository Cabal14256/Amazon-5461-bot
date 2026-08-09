# Amazon 5461 Bot — Codex Working Agreement

## Purpose and source of truth

- This repository automates Amazon Seller Central catalog authorization, brand verification, 5461, and GTIN exemption workflows with Python, Playwright, and AdsPower.
- Treat the outer migrated code in this repository as canonical. The legacy Hermes source at `C:\Users\Admin\projects\amazon-5461-bot` is a read-only rollback archive unless the user explicitly asks to change it.
- Read `PROJECT.md` for the operating boundary and `docs/CURRENT_STATE.md` for migration and runtime context.

## Safety boundary

- Default to read-only diagnosis or dry-run.
- A real Seller Central submission requires an explicit user request that identifies the account, site, brands, and intent. Do not infer authorization from a prior batch or migration request.
- Never bypass CAPTCHA, 2FA, login checks, platform risk controls, or rate limits.
- Never forge documents, brand claims, account identity, or evidence.
- Keep `Draft`, `Submitted no Case ID`, `Under Review`, `Approved`, `Declined`, `Already Approved`, `Not Found`, and technical failure distinct.
- Missing Case ID is not automatically failure. Check Dashboard/Selling Applications and final evidence before deciding.
- After repeated 410001/429 responses, stop and cool down; do not hammer the service.

## Secrets and local-only data

- Never print, summarize, upload, stage, or commit passwords, cookies, tokens, API keys, email credentials, or full account records.
- The canonical local account file is `runtime/private/accounts.json`. `config/accounts.json` must remain absent; `config/accounts.example.json` is the public template.
- Treat `.env`, `runtime/private/`, `data/`, `brand_packs/`, spreadsheets, evidence, screenshots, logs, databases, `migration/private/`, and `legacy/` as local-only material.
- When reading private files, return only the minimum non-secret facts needed for the task.

## Environment and commands

- Native Windows and PowerShell are the primary environment.
- Use `.\.venv\Scripts\python.exe` once the project environment exists.
- Setup: `python -m venv .venv`, then `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` and install the development dependencies declared in `pyproject.toml`.
- Verify code changes with `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` and an appropriate targeted command. Run Ruff when changing Python broadly.
- Use diagnosis or dry-run before a real execution.

## Long-running automation

- Full batches take minutes per brand. Launch them as hidden background processes with explicit stdout/stderr log files and retain the PID.
- With PowerShell `Start-Process`, always include `-WindowStyle Hidden`.
- Do not use a trailing shell `&`, pipe a live batch into `head`/`tail`, or assume a foreground timeout means the batch failed.
- Read `data/batch_state.json` promptly because a later batch can overwrite it.

## Evidence and reporting

- Preserve critical screenshots, page state, logs, and Dashboard evidence for every uncertain outcome.
- Inspect screenshots directly and reconcile them with the batch state and Dashboard.
- Report per-brand outcomes with evidence paths. Do not collapse mixed outcomes into a single success/failure label.
- When `runtime/codex_signal.json` is pending, inspect it with `scripts/check_codex_signal.py`, diagnose the referenced evidence, then mark it handled with a short audit note.
- Do not recreate a one-minute model watchdog. The local signal is intentionally deterministic and model-free until a pending failure needs attention.

## Skills

- Use `.agents/skills/amazon-5461-automation` for execution, diagnosis, result checks, selectors, and failures.
- Use `.agents/skills/amazon-5461-account-onboarding` when an account is missing from local configuration.
- Imported Hermes references contain historical tool names and absolute paths. Translate them to the current repository, PowerShell, local image inspection, and Codex mechanisms.

## Change discipline

- Preserve unrelated local changes and business data.
- Prefer configuration and shared helpers over account-specific one-off scripts.
- Update tests and durable documentation when behavior changes.
- Do not commit generated evidence, private state, virtual environments, migration archives, or business-sensitive assets.

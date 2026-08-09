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
- Required `5461信息模版.xlsx` as an ignored local workbook
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

## Current operational policy

Read `AGENTS.md` and `PROJECT.md`. Default to diagnosis/dry-run, preserve evidence, and require explicit authorization for a real submission.

## Delayed Case follow-up

Real 5461 submissions that capture a reliable Case ID now create a persistent
SQLite follow-up task. A hidden worker waits for the configurable delay, opens
the matching AdsPower profile, reuses the shared marketplace switcher, reads the
Case detail messages, and updates the local registry only for an explicit reply
outcome. `Answered` is not treated as approval. An explicit Case approval now
triggers effective checks in the same account and marketplace. The normal path
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

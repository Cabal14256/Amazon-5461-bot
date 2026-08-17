---
name: amazon-5461-account-onboarding
description: Onboard a new Amazon seller account into this repository's 5461 automation by resolving its AdsPower profile, adding local private account configuration, synchronizing brand packs, generating account-specific statement files, and validating account/site/brand prerequisites. Use when a requested bare account number or account ID is absent from runtime/private/accounts.json. Do not expose credentials or launch a real submission unless separately and explicitly requested.
---

# Amazon 5461 Account Onboarding

Prepare a missing account for a later diagnosis, dry-run, or explicit batch.

## Guardrails

1. Work from the repository root and read `AGENTS.md` and `PROJECT.md`.
2. Keep account configuration only in `runtime/private/accounts.json`; never recreate active accounts in `config/accounts.json`.
3. Keep the local 5461 source workbook under `runtime/private/templates/`; do not place business workbooks in the repository root.
4. Do not print usernames, emails, tokens, cookies, or full account records.
5. Onboarding authorizes local configuration changes only. It does not authorize a Seller Central submission.

## Workflow

1. Normalize the requested account number, account ID, site, and target brands.
2. Check whether the account already exists without printing the record.
3. Query the AdsPower local API and match the intended profile by account number, profile name, remark, and expected site. Stop on ambiguity.
4. Run the repository onboarding/audit entry points with the project interpreter:

```powershell
.\.venv\Scripts\python.exe auto_add_account_data.py <brand> <account> --site <site>
.\.venv\Scripts\python.exe scripts\audit_account_config.py <account> --brand <brand> --site <site>
```

5. Generate or update account-specific statement files for every requested brand/site combination.
6. Verify required manifest fields, marketplace coverage, statement file presence, non-empty localized descriptions, and AdsPower profile resolution.
7. Perform a diagnosis or dry-run handoff to `$amazon-5461-automation`. Do not add `--submit` unless the user separately authorizes the real run.

## Route detailed cases

Read the entire relevant reference before acting:

- Canonical statement structure and localization: `references/statement-file-format.md`
- Safe generation patterns: `references/statement-generation-patterns.md`
- Empty-description defect: `references/statement-file-description-bug.md`
- Adjacent-account generation: use the corresponding troubleshooting reference in `$amazon-5461-automation`
- False-negative Under Review results: `references/retry-under-review-false-negative.md`
- Final screenshot classification: `references/final-state-screenshot-classification.md`

Use `scripts/gen_account_statements.py` only after inspecting its inputs and confirming it writes inside this repository. Review generated files before any batch.

## Completion report

Report only non-secret facts: normalized account ID, site, matched AdsPower profile ID suffix if useful, brands prepared, files created or updated, validation result, and the safest next command.

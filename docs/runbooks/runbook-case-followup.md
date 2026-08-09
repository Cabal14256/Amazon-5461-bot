# Runbook: delayed 5461 Case follow-up

## Purpose

After a real 5461 submission produces a reliable Case ID, the batch stores a
follow-up task in `runtime/state/ledger.db`. A hidden worker waits until the task
is due, opens the matching AdsPower profile, switches to the submitted
marketplace with `src.marketplace_switcher.switch_marketplace`, and reads the
Case detail page.

The configurable delay `n` is:

```yaml
case_followup:
  delay_hours: 24.0
```

Changing `delay_hours` affects newly scheduled submissions. One run can override
it with `--case-followup-delay-hours`.

## Feishu record binding

When `feishu_bitable.enabled` and `bind_on_schedule` are true, scheduling a
Case also performs a read-only lookup in the existing Bitable. The lookup uses
the configured fields `账号 + 品牌 + SKU`, then validates the submitted site
against `国家` or `国家EU` locally. It stores only the matched `record_id`, the
original country option, binding status, and a short reason in SQLite.

It does not create a Feishu row and does not write Case IDs, replies, or times.
Real Bitable updates remain blocked while `write_enabled: false`.

For repeated EU applications, pass the exact option so suffixes are preserved:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 run `
  --account eu_store_000 --site BE --brand WILLONE --submit `
  --feishu-country-option 比利时1
```

If the option is omitted, automatic binding is allowed only when one record and
one option map uniquely to the submitted marketplace. Values such as `荷兰` and
`荷兰1` on the same record are intentionally treated as ambiguous.

Test a binding without submitting or changing Feishu:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 feishu-bind-check `
  --account eu_store_000 --site BE --brand WILLONE --sku SAMPLE-SKU `
  --country-option 比利时1
```

## Outcome rules

- `Answered` is a Case transport status, not an application result.
- The newest message whose sender is `Amazon` is authoritative.
- Explicit approval wording triggers, but does not itself pass, the effective
  approval verification.
- The normal success path requires the exact brand under Manage Your Brands and
  Add Product advancing from Product Identity to Description.
- A confirmed Manage Your Brands page that omits the brand is not a failure by
  itself. The worker must click Add brand, search the exact brand, select one
  unambiguous screen-protector-related result, click Connect this brand, and
  read the resulting panel.
- `You are approved to list <exact brand> products. You can close this panel and
  continue listing.` is decisive approval even if the portfolio or Add Product
  check produced a conflicting result. An explicit Connect brand failure is
  `false_approved` / Excel `假过`.
- A missing selector, search timeout, or ambiguous same-brand/category result in
  the Connect brand fallback becomes retryable `verification_pending`, not
  `假过`.
- When the brand is already present in the portfolio, Add Product explicitly
  remaining restricted/cannot advance after required fields are filled is
  `false_approved` / Excel `假过`.
- Navigation errors, unknown layouts, missing selectors, login, CAPTCHA, and 2FA
  are never classified as `假过`; they become retryable `verification_pending`
  or `blocked`.
- Explicit decline wording becomes `declined` / Excel `已拒绝`.
- Requests for evidence or documents become `action_required` / Excel `待补充材料`.
- An unclassified Amazon reply becomes `answered_unknown` / Excel `待人工复核`.
- No Amazon message becomes `pending` and is rescheduled after
  `retry_interval_hours`.
- Login, CAPTCHA, and 2FA become `blocked`; the worker does not bypass them.

## Commands

Read one Case without changing records:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 case-check `
  --account us_store_000 --site BE --brand WILLONE --case-id 12345678901
```

Read and register one terminal result:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 case-check `
  --account us_store_000 --site BE --brand WILLONE --case-id 12345678901 --register
```

Process tasks that are due now:

```powershell
.\.venv\Scripts\python.exe scripts\run_case_followups.py --once
```

Wait for future tasks until the queue is empty:

```powershell
.\.venv\Scripts\python.exe scripts\run_case_followups.py --watch
```

## Evidence and recovery

Evidence is stored under:

```text
runtime/evidence/case_followups/<date>/<account>/<site>/<brand>/<case_id>/<timestamp>/
```

An approved Case adds `approved_verification/manage_brand/` (including a
`connect_brand/` subdirectory when the portfolio omitted the brand),
`approved_verification/add_product/`, and
`approved_verification_result.json` below that directory. The Add Product check
may click only the Product Identity Continue/Next validation action (including
the new UI's step-validation Submit). It never clicks Apply to sell and never
submits a 5461 application.

The worker writes separate stdout/stderr logs under `runtime/logs/` and its PID
metadata under `runtime/state/case_followup_worker.json`. SQLite claims are
transactional, duplicate Case IDs are idempotent, and a task left in `running`
for more than one hour is returned to the retry queue on the next worker start.

Checks run sequentially. Do not deliberately run a submission batch and a Case
follow-up against the same AdsPower profile at the same time.

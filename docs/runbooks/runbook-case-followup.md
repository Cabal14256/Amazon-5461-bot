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
Case first checks the existing Bitable and then binds the Case to its progress
record. The lookup uses `账号 + 品牌 + SKU` and validates the site against
`国家` or `国家EU` locally. It stores only the matched `record_id`, original
country option, binding status, and a short reason in SQLite.

With `create_missing_records: true`, a missing submission-detail row is planned
from the exact account/site/brand SKU, title, and statement used by the run.
The detail row's `5461进度` remains empty because the UK row is the single EU
progress target. A missing UK target is created only from an account-specific
UK statement whose SKU matches the account, UK site, and brand. Missing UK
materials remain `missing_uk_materials`; the worker never fabricates them.

The existing table's `父记录` cells are empty, so new rows leave that field
empty as well. Formula and lookup fields are never written. Case IDs, replies,
and times are also not written because those fields are absent from the table.
All creates and field updates remain dry-run while `write_enabled: false`.

For EU sites other than the UK, binding first looks for the unique existing
account-and-brand row that represents the UK country option. That row is the
shared progress target, so the latest EU country's result overwrites its single
`5461进度` value. Scheduling also preserves the UK option and adds the exact
submitted option to `国家EU`. Multiple matching UK rows remain unbound rather
than guessed.

For repeated EU applications, pass the exact option so suffixes are preserved:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 run `
  --account eu_store_000 --site BE --brand DEMO_WILL --submit `
  --feishu-country-option 比利时1
```

If the option is omitted, automatic binding is allowed only when one record and
one option map uniquely to the submitted marketplace. Values such as `荷兰` and
`荷兰1` on the same record are intentionally treated as ambiguous.

Test a binding without submitting or changing Feishu:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 feishu-bind-check `
  --account eu_store_000 --site BE --brand DEMO_WILL --sku SAMPLE-SKU `
  --country-option 比利时1
```

## Outcome rules

- `Answered` is a Case transport status, not an application result.
- The newest message whose sender is `Amazon` is authoritative.
- Explicit approval wording triggers, but does not itself pass, the effective
  approval verification.
- The normal success path requires the exact brand under Manage Your Brands and
  Add Product advancing from Product Identity to Description.
- If the Seller Central menu does not expose Manage Your Brands, the worker
  opens the stable regional page directly: `sellercentral.amazon.com/manage-your-brands`
  for North America or `sellercentral.amazon.co.uk/manage-your-brands` for Europe.
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
- Explicit acceptance wording such as "completed our review and accepted your
  application" is approval wording; explicit brand/GTIN decline wording and
  supported local-language rejection templates are decline wording. This
  includes the German template `mussten wir Ihren Antrag ablehnen`.
- Requests for evidence or documents become `action_required` / Excel `待补充材料`.
- An unclassified Amazon reply is sent to Codex in a read-only sandbox with a
  strict JSON schema. A high-confidence `declined` or `action_required`
  result enters the normal automatic registration path. A high-confidence
  `approved` result must still pass the existing effective approval checks.
  Codex unavailable/timeout/process/schema errors are rescheduled after
  `ai_reply_classification.retry_interval_minutes`, with a separate persistent
  `ai_attempt_count`; after `max_attempts` they become `answered_unknown` /
  Excel `待人工复核`. Low confidence, contradiction, or `unknown` move to
  manual review immediately because another identical AI call would add no
  evidence. HTTP/Cloudflare 403 is recorded distinctly as `forbidden` and uses
  `forbidden_retry_interval_minutes` (30 minutes by default) so the worker does
  not hammer a blocked ChatGPT session. The default is two AI attempts with a
  300-second per-call timeout.
- No Amazon message becomes `pending` and is rescheduled after
  `retry_interval_hours`.
- Before opening AdsPower, a task whose exact account/site/brand already has a
  canonical effective `approved` status is completed as an approved skip. A
  `false_approved`, `Answered`, pending, unknown, or technical state is never
  skipped by this rule.
- Login, CAPTCHA, and 2FA become `blocked`; the worker does not bypass them.

## Commands

When the submit flow has no Case ID, it first opens View Selling Applications
in a fresh tab. US/MX use `sellercentral.amazon.com/hz/myqdashboard`; EU sites
use the authenticated shared `sellercentral.amazon.co.uk/hz/myqdashboard`
portal after switching to the target marketplace. The lookup exact-matches the
brand and reads Case IDs from the row's hidden `kat-link[label]` as well as
visible text. Multiple Case IDs are never guessed.

If the first lookup still has no unique Case ID, SQLite queues a recovery after
10 minutes and retries every 30 minutes, up to 12 checks. Once one unique Case
ID is found, the original submission timestamp is retained and the normal
Case-detail follow-up is scheduled. Run the recovery queue manually with:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 case-id-recoveries --watch
```

Read one Case without changing records:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 case-check `
  --account us_store_000 --site BE --brand DEMO_WILL --case-id 12345678901
```

Read and register one terminal result:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 case-check `
  --account us_store_000 --site BE --brand DEMO_WILL --case-id 12345678901 --register
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
runtime/evidence/case_id_recovery/<date>/<account>/<site>/<brand>/<timestamp>/
```

Codex reply classification writes `codex-events.jsonl`, `stderr.log`,
`diagnostic.json`, and a validated `result.json` (when successful) beneath the
Case evidence directory. Timeout diagnostics are retained instead of being
discarded.

An approved Case adds `approved_verification/manage_brand/` (including a
`connect_brand/` subdirectory when the portfolio omitted the brand),
`approved_verification/add_product/`, and
`approved_verification_result.json` below that directory. The Add Product check
may click only the Product Identity Continue/Next validation action (including
the new UI's step-validation Submit). It never clicks Apply to sell and never
submits a 5461 application.

The worker writes separate stdout/stderr logs under `runtime/logs/` and its PID
metadata under `runtime/state/case_followup_worker.json`. The launcher records
that metadata only after the child has taken the worker lock
(`runtime/state/case_followup_worker.lock`, or
`case_followup_targeted_<key>.lock` for a targeted worker); the lock is the
authoritative liveness signal and the health check trusts either file's live
PID. SQLite claims are transactional and record the claiming worker PID in
`case_followups.claimed_pid`; duplicate Case IDs are idempotent. A task left
in `running` by a verifiably dead worker PID is returned to the retry queue on
the next worker start (well within the five-minute health-check cadence); the
one-hour time threshold remains only as a fallback for rows without a recorded
claimant PID.

Checks run sequentially. Do not deliberately run a submission batch and a Case
follow-up against the same AdsPower profile at the same time.

Each worker pass claims a bounded batch and groups checks by account and
marketplace. Consecutive brands in the same group reuse one browser connection
and the initial marketplace selection. At group startup, every existing profile
tab is closed except tabs whose exact host is `start.adspower.net`; the worker
then opens one work tab, removes extra popup tabs after every Case, and closes
the work tab when the group ends. After Playwright detaches, the worker stops
that AdsPower profile so the browser window does not remain open. This cleanup
does not submit or resubmit any application.

The normal worker claims only the oldest due account/marketplace group in one
transaction. Other groups remain `pending`/`retry` until a worker actually
starts them. When the due backlog reaches the configured threshold, up to three
different AdsPower profiles may run concurrently; `profile_locks` still makes
the same profile strictly serial. A submit batch starts a worker targeted to
the follow-up IDs it just created, so older unrelated accounts cannot block the
new batch.

On Windows, install the model-free five-minute health check once:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_case_followup_healthcheck.ps1
```

It only checks the persisted queue/worker PID and starts the hidden worker when
needed. It does not inspect Amazon itself and does not replace platform safety
controls.

Detached follow-up workers use the Windows system timezone. The launcher
deliberately removes an inherited `TZ` environment variable on Windows because
the C runtime does not reliably understand IANA values such as
`Asia/Taipei`; inheriting one can make the child process evaluate local,
timezone-naive `scheduled_at` values as UTC and delay checks by eight hours.
POSIX workers preserve `TZ` normally.

## AdsPower/CDP recovery

Browser attachment uses three readiness layers: AdsPower active state, TCP plus
DevTools `/json/version`, and a real Playwright CDP handshake. The handshake has
an explicit 30-second timeout. If any layer fails, the manager fully stops and
restarts the profile once with `--remote-allow-origins=*`, waits for DevTools to
become ready, and retries the handshake once.

Case checks detach Playwright when an account/marketplace group finishes and
then stop that AdsPower profile through the local AdsPower API. The profile lock
remains held until cleanup completes, so the next task cannot attach while the
browser is closing.

If recovery is exhausted, the result remains a retryable technical error. With
`connection_circuit_breaker: true`, other due tasks for the same account are
deferred to the same error-retry time instead of each waiting for another CDP
timeout. A Seller Central `ERR_SOCKS_CONNECTION_FAILED` is stored separately as
`proxy_connection_failed`; it is a proxy/network fault, not a Case outcome.

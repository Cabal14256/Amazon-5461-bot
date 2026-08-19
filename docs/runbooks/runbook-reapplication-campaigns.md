# Finite reapplication campaigns

## Business routes

- NA: `US -> MX`
- EU: `UK -> BE -> DE -> SE -> NL -> FR`
- Only explicit `declined` schedules the next site after two hours.
- `approved` stops the campaign as `passed`.
- `false_approved`, `pending`, and `verification_pending` keep the current
  attempt in `waiting_case`; the same Case continues to be checked until an
  effective approval or explicit rejection is confirmed, up to the global
  six-check limit. An unresolved sixth check pauses the campaign for manual
  review instead of advancing the route.
- A rejection at the last site stops as `route_exhausted`.
- `action_required`, `answered_unknown`, login/CAPTCHA/2FA blocks, missing Case
  IDs, browser failures, rate limits, and other technical failures pause or
  retry the current site. They never advance the route.

MX and US use the US Feishu row's single `5461进度`. EU sites use the UK row.
The newest marketplace result intentionally overwrites that shared cell. The
actual-country detail row remains separate and contains the exact submitted
SKU, title, and statement.

## Safety and authorization

A campaign is finite and stores one row per route attempt in SQLite. Real
submission is impossible unless the campaign itself has
`submit_authorized=1`.

Automatic continuation is controlled by:

```yaml
reapplication:
  auto_authorize_declined_cases: false
  auto_backfill_declined_cases: false
  auto_backfill_limit: 100
  auto_backfill_min_followup_id: 0
```

When `auto_authorize_declined_cases` is enabled for an explicitly approved
operational scope, a completed Case whose exact result is `declined`
automatically creates the next-site campaign. The original real submission's
exact account/site/brand scope is carried forward through only the configured
finite route. The campaign records `authorization_source=automatic_decline`.
No second click is required after each rejection.

When `auto_backfill_declined_cases` is also enabled, Web-console startup and a
normal Case-worker startup scan up to `auto_backfill_limit` unlinked historical
explicit declines. Creation is transactionally idempotent, so repeated scans
do not create duplicate campaigns. Backfill is off by default because enabling
it can schedule real submissions for existing records; enable it only after
reviewing the exact affected account/site/brand set.

`auto_backfill_min_followup_id` is an inclusive persistent waterline over
`case_followups.id`. A positive value applies to both startup backfill and the
real-time automatic-decline hook: records below it are never auto-authorized,
the record at the waterline is included, and later records remain eligible.
The manual recovery API remains able to list and recover older records. Use `0`
only when the explicitly approved scope truly includes all history.

Manual CLI/Web creation remains an audited recovery path. The supported CLI
requires both `--submit` and `--yes`, plus an explicit account, brand, and
region. Manual Case creation records `authorization_source=manual_case`.

The Web console also lets Reviewer/Admin users manually recover from one completed Case
whose normalized result is exactly `declined`. Account, brand, source site,
route and next site are derived again on the server. The first request must
confirm the exact remaining configured route, pass submit preflight and have
no conflicting active campaign. It records the declined Case as the completed
source attempt and schedules only the next site.

`source_case_followup_id` is the idempotency key. Once a campaign exists, a
repeat POST returns that persisted campaign and route with `created=false`,
`preflight=[]` and worker reason `existing_campaign`; it does not rerun
preflight or launch another worker, and request data cannot rewrite the route.
Historical declined Cases are converted only while both automatic switches are
enabled. Last-route declines, unsupported sites, already-linked Cases and
non-declined outcomes are never enrolled.

Read-only preflight (no database write, browser launch, or submission):

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 reapply-start `
  --account 000 --brand DEMO_HOME --region EU
```

Explicitly authorized campaign:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 reapply-start `
  --account 000 --brand DEMO_HOME --region EU --submit --yes
```

Use `--start-site DE` only when intentionally importing work at a later point
in the route. Use `--no-worker` to create an authorized campaign without
dispatching it immediately.

Status and manual worker commands:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 reapply-status
.\.venv\Scripts\python.exe -m cli.amazon5461 reapply-worker --watch
```

## Runtime evidence

- Campaign and attempt state: `runtime/state/ledger.db`
- Per-attempt batch state: `runtime/state/reapplications/campaign_<id>/`
- Per-attempt logs: `runtime/logs/reapplications/campaign_<id>/`
- Seller Central screenshots and page evidence remain under
  `runtime/evidence/`.

The console's Reapplication page shows whether automatic mode and historical
backfill are enabled, and labels each campaign as `automatic_decline` or a
manual recovery source.

`waiting_case_id` and `waiting_case` are active automatic states. The console
labels them as "正在找回 Case ID" and "等待 Case 最终回复" respectively; it
must not translate either state to "待人工介入". Human-intervention wording is
reserved for `manual_review`, `blocked`, or an explicitly created incident.

The dispatcher is sequential. A submitted attempt with no reliable Case ID
enters `waiting_case_id`; the read-only recovery worker checks View Selling
Applications without advancing the route. It becomes `waiting_case` only after
one unique exact-brand Case ID is recovered. Exhausted or ambiguous recovery
pauses for manual review and never moves to another country.

All browser-using workers for one AdsPower profile share the same SQLite
`profile_locks` mutex. A reapplication holds it for the full child batch; Case
follow-up and Case-ID recovery must yield while it is held. Case-ID recovery
does not consume a Dashboard-check attempt when it yields. A process-backed
lock may be reclaimed before its TTL only when its encoded owner PID is known
and verifiably dead. Do not infer browser ownership from a task row whose
status merely says `running`; the profile lock is the ownership authority.

The durable submit fence normally forbids a second click. The only supported
same-attempt exception is an exact Dashboard `Draft`, together with new
explicit retry authorization. Confirmation may come from either the linked
Case-ID recovery queue or the immediate post-submit checker, but the latter is
accepted only when its persisted state matches the exact account/site/brand/
attempt, Dashboard navigation succeeded, one exact Catalog Authorization row
matched, no Case ID exists, and retained evidence is present. The worker stores
this as `final_result=draft` / “待继续提交”, not a generic batch failure. The
recovery command archives the old per-attempt batch state, resets only the
exact checkpoint, and keeps the old evidence. `Draft` must never be inferred
from a missing Case ID, a browser error, or an operator guess.

Read-only confirmation and explicitly authorized recovery use the same exact
scope:

```powershell
.\.venv\Scripts\python.exe -m cli.amazon5461 reapply-resume-draft `
  --attempt-id <id> --account <account> --site <site> --brand <brand>

.\.venv\Scripts\python.exe -m cli.amazon5461 reapply-resume-draft `
  --attempt-id <id> --account <account> --site <site> --brand <brand> `
  --submit --yes
```

Seller Central 登录中断由账号级认证阻塞接管。提交前中断保持当前 attempt 为
`waiting_login`；提交点击安全边界之后中断进入 `waiting_reconciliation`，后续只做
Selling Applications/Case 结果确认，绝不再次点击提交。登录恢复、前端操作和证据说明
见 `docs/runbooks/runbook-auth-recovery.md`。

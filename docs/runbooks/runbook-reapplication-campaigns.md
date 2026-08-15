# Finite reapplication campaigns

## Business routes

- NA: `US -> MX`
- EU: `UK -> BE -> DE -> SE -> NL -> FR`
- `declined` and `false_approved` schedule the next site after two hours.
- `approved` stops the campaign as `passed`.
- A rejection or false approval at the last site stops as `route_exhausted`.
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
`submit_authorized=1`. The supported CLI requires both `--submit` and `--yes`,
plus an explicit account, brand, and region.

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

The dispatcher is sequential. A submitted attempt with no reliable Case ID
enters `waiting_case_id`; the read-only recovery worker checks View Selling
Applications without advancing the route. It becomes `waiting_case` only after
one unique exact-brand Case ID is recovered. Exhausted or ambiguous recovery
pauses for manual review and never moves to another country.

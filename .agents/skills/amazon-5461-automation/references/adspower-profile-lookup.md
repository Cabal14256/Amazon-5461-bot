# Resolving a bare account number to an AdsPower profile

## When to use this

Someone references an account by a bare number or short code (e.g. "634",
"634EU") and/or a brand name, and searching the project's own data files
(`config/accounts.json`, `runtime/private/accounts.json`, `brand_packs/`)
for that number turns up **zero matches**. Do not conclude the account
doesn't exist from that alone — in this project the numeric id is first a
literal AdsPower profile `name`, and it is common for a profile to exist in
AdsPower before it has ever been onboarded into `accounts.json` (i.e. before
any brand/site has been run for it yet).

## Fastest ground truth: query the AdsPower Local API directly

The project's own `accounts.json` only reflects accounts that have already
been onboarded (via `ensure_account_exists` / `auto_add_account_data.py`).
The authoritative source for "does this profile exist at all" is AdsPower
itself, not the project's JSON files.

```bash
curl -s "http://127.0.0.1:50325/api/v1/user/list?page=1&page_size=10" | head -c 2000
```

This hits AdsPower's Local API directly (default port `50325`) and works
even when the `adspower-browser`/`ads` CLI wrapper reports
`"Adspower runtime is not running"` — that CLI error just means the CLI's
own session token/wrapper hasn't been started with `ads start -k <KEY>`. As
long as the AdsPower desktop app process is already running (check with
`tasklist | grep -i adspower` on Windows — look for `AdsPower Global.exe`),
the Local API answers directly with no `ads start` step needed.

The response's `list[].name` field is the bare number/label you're matching
on (e.g. `"634"`). Each entry also carries `user_id` (the AdsPower profile
id used elsewhere as `adspower_profile_id` in `accounts.json`), proxy info,
and `username` (the account's login email) — useful for cross-referencing
which numbered profile maps to which project `account_id`. The project's
convention is `us_store_<N>` ⇔ AdsPower profile named `<N>`.

If `page_size=10` doesn't surface the profile you want (newest profiles
sort first), increase `page_size` or add `&page=2` etc.

## What it means once resolved

- **Profile exists in AdsPower but not in `accounts.json`** → this is a new
  account that needs onboarding before any batch can run against it. Follow
  the normal "new account+brand+site combo" prerequisite: register the
  account (letting the email resolver pull the login email off the AdsPower
  profile if possible), then generate the per-account/per-site brand doc
  pairs with `auto_add_account_data.py <BRAND> <account_id> --site <SITE>`
  for every brand involved, before calling `run_full_5461_batch.py`.
- **Profile does not exist in AdsPower either** → the number was likely a
  typo, a different project's numbering, or hasn't been created yet in
  AdsPower — surface that to the user rather than assuming onboarding is
  simply missing.

---

## Fast path: account already onboarded (confirmed 2026-07-17, account 647EU)

When the user says "647EU在比利时申请: JZG, WILLONE, uShield, JavoYion" (or any "NNNxx在YY申请" pattern) and the account already exists in `runtime/private/accounts.json` with the correct marketplace, **skip onboarding entirely**. The only pre-checks needed are:

1. Confirm account is in `accounts.json` with the target marketplace:
```bash
python -c "import json; d=json.load(open('runtime/private/accounts.json')); \
[print(a['account_id'], a.get('marketplace','')) for a in d['accounts'] if '647' in a['account_id']]"
```

2. Confirm each brand's manifest lists the target site in `supported_marketplaces`:
```bash
python -c "import json; \
[print(b, json.load(open(f'brand_packs/{b}/manifest.json')).get('supported_marketplaces',[])) \
for b in ['JZG','WILLONE','uShield','JavoYion']]"
```

3. Launch the batch directly — no `auto_add_account_data.py` needed:
```bash
python scripts/run_full_5461_batch.py \
  --accounts us_store_647 \
  --brands JZG,WILLONE,uShield,JavoYion \
  --site BE \
  --submit
```

Use `terminal(background=true, notify_on_complete=true)`. The batch script auto-detects stdin EOF (non-interactive mode) and skips confirmation prompts — no `--yes` flag required when stdin is closed.

The account id convention is `us_store_<N>` where N is the numeric part of the user's label (647EU → `us_store_647`).

---

## Single new account onboarding (confirmed 2026-07-14, account 645EU)

When the user names a single account+brand list (e.g. "645EU在英国申请: HOMEMO, JZG …") and that account is not yet in `accounts.json`:

### Step 1 — verify the account exists in AdsPower

```bash
.venv/Scripts/python.exe scripts/adspower_profile_sync.py --discover 645
```

Expected output (account missing but profile matched):
```
Account: us_store_645
  Account exists: False
  Matched profile: {profile_id: 'k1xxxxxx', name: '645', confidence: 100, ...}
  Action: account_missing
```

If `action: account_missing` → the AdsPower profile exists but `accounts.json` has no record. Proceed to onboarding. If the profile itself is not found, surface the gap to the user.

### Step 2 — onboard all brands for that account in one call

`--all-brands` creates the account record AND writes brand docs for every brand under `brand_packs/`:

```bash
.venv/Scripts/python.exe auto_add_account_data.py --all-brands --site UK 645
```

The first brand call creates the `us_store_645` record (sets `marketplace`, `adspower_profile_id`, `email`, etc. from the AdsPower profile). Subsequent brands reuse the same record. Check that `"status": "active"` appears in the output.

### Step 3 — verify account was written

```bash
python -c "
import json
d=json.load(open('runtime/private/accounts.json'))
found=[a for a in d['accounts'] if a['account_id']=='us_store_645']
print(found[0]['status'], found[0]['adspower_profile_id'])
"
```

### Step 3b — fix manifest divergences for target site (run before every new batch)

**Fixed at the source 2026-07-17**: `update_manifest_site_statement_files()` in
`auto_add_account_data.py` now auto-appends the target site to
`supported_marketplaces` every time it syncs a statement file for that site.
New onboarding runs (this step, or `--all-brands`) should no longer produce
divergences going forward — the brands listed below (V-PORYADKU, uShield,
IKABO, JZG, mocodi, MoShieldwish, MP-MALL, and 15 more across MX/UK) were all
bulk-fixed on 2026-07-17 and the audit came back clean.

Still run the audit script as a safety net before every new-site batch — it's
cheap and catches the rare case of a hand-edited manifest or a code
regression, not because divergences are expected anymore.

```bash
python -c "
import json, glob
SITE = 'MX'   # change to the target site
for mf in glob.glob('brand_packs/*/manifest.json'):
    d = json.load(open(mf))
    has_stmt = SITE in d.get('5461', {}).get('statement_files', {})
    in_supported = SITE in d.get('supported_marketplaces', [])
    if has_stmt and not in_supported:
        print(d['brand_name'], '→ has statement_files.' + SITE + ' but missing from supported_marketplaces')
"
```

For each brand printed: open the manifest and add the missing site to
`supported_marketplaces`. The batch will run correctly either way (the script
uses `statement_files`), but fixing the manifest keeps future audits clean and
avoids manual confusion at launch time.

### Step 4 — run the batch normally

```bash
.venv/Scripts/python.exe scripts/run_full_5461_batch.py \
  --accounts 645 --brands "HOMEMO,JZG,V-PORYADKU,VASG,WILLONE,uShield,JavoYion" --site UK
```

**Confirmed 2026-07-14 (account 645 UK):** 7/7 brands succeeded (Case IDs redacted) after this onboarding sequence.

---

## Multi-account onboarding + parallel batch launch (confirmed 2026-07-14)

When the user provides two or more account+brand lists for the same site
(e.g. "643EU: VASG, uShield; 644EU: HOMEMO, WILLONE — all BE"), the full
flow is:

### Step 1 — resolve AdsPower profile IDs for all accounts

```bash
curl -s "http://127.0.0.1:50325/api/v1/user/list?page=1&page_size=100" \
  | python -c "
import json,sys
data=json.load(sys.stdin)
for u in data['data']['list']:
    name=u.get('name','')
    if any(x in name for x in ['643','644']):   # adjust filter
        print(name, '|', u['user_id'], '|', u.get('username',''))
"
```

### Step 2 — onboard each (account × brand × site) combo

Run `auto_add_account_data.py` once per brand per account.  
The first call for a new account sets `"created": true`; subsequent brand
calls for the same account return `"created": false` (reuses the existing
record). Both are success — check `"sku"` to confirm the brand doc was written.

```bash
.venv/Scripts/python auto_add_account_data.py VASG    us_store_643 --site BE
.venv/Scripts/python auto_add_account_data.py uShield us_store_643 --site BE
.venv/Scripts/python auto_add_account_data.py HOMEMO  us_store_644 --site BE
.venv/Scripts/python auto_add_account_data.py WILLONE us_store_644 --site BE
```

### Step 3 — verify brand packs support the target site

```bash
cat brand_packs/<BRAND>/manifest.json | python -c \
  "import json,sys; d=json.load(sys.stdin); print(d.get('supported_marketplaces',[]))"
```

Confirm `BE` (or target site) appears before proceeding. If the site is absent
but `5461.statement_files.<SITE>` exists, add the site to `supported_marketplaces`
in the manifest — the batch will run either way, but fix it to keep audits clean.
See `brand-pack-manifest-pitfalls.md` for the full audit script.

### Step 4 — launch one batch per account in parallel (different accounts = safe)

Different accounts use different AdsPower profiles → no shared browser
session → can run concurrently.

```bash
# Account 643 — log to logs/batch_643_be_vasg_ushield.log
.venv/Scripts/python scripts/run_full_5461_batch.py \
  --accounts us_store_643 --brands VASG,uShield --site BE \
  --no-confirm -y > logs/batch_643_be_vasg_ushield.log 2>&1

# Account 644 — launched as second background process
.venv/Scripts/python scripts/run_full_5461_batch.py \
  --accounts us_store_644 --brands HOMEMO,WILLONE --site BE \
  --no-confirm -y > logs/batch_644_be_homemo_willone.log 2>&1
```

Each launched with `terminal(background=true, notify_on_complete=true)`.

**Same account, different sites → sequential only** (shared browser session).  
**Different accounts, any sites → parallel is safe.**

### Key flag reminders
- `--no-confirm -y` — skips all confirmation prompts; required for background runs.
- `--state-file <path>` — optional but useful for retries; omit for first runs.
- Log goes to `logs/` by convention; name it `batch_<account>_<site>_<brands>.log`.


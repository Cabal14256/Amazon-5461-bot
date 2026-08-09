# New account+brand+site combo prerequisite

Before running the batch for an account/brand/site combination that hasn't
been used before, check `brand_packs/<BRAND>/docs/` for
`5461_statement_<site>.account_<id>.txt` and
`gtin_exemption_statement_<site>.account_<id>.txt`. If missing, generate
them first with:

```bash
# Single brand
.venv/Scripts/python.exe auto_add_account_data.py <BRAND> <account_id> --site <SITE>

# All brands at once (faster for new accounts)
.venv/Scripts/python.exe auto_add_account_data.py --all-brands <account_id> --site <SITE>
```

**PITFALL — venv invocation**: Always use `.venv/Scripts/python.exe`
(explicit `.exe`). Using `python`, `python3`, or `source .venv/Scripts/activate && python`
fails with `ModuleNotFoundError: No module named 'playwright'` even inside
the activate step. This applies to `run_full_5461_batch.py` and all other
project scripts.

Run this for each brand/site needed — the batch script will fail or use
stale data if these per-account/site doc files don't exist yet. This applies
even if the account has already run other sites/brands before; each
(account, brand, site) triple needs its own generated doc pair.

## Checking existing brand doc coverage

Quick verification loop before launching a batch:

```bash
for brand in BRAND1 BRAND2 BRAND3; do
  f="brand_packs/$brand/docs/5461_statement_us.account_634.txt"
  [ -f "$f" ] && echo "OK: $brand" || echo "MISSING: $brand"
done
```

Replace `us` and `634` with the target site/account.

## accounts.json structure

`runtime/private/accounts.json` is a **dict** with a top-level `"accounts"`
key, not a top-level list. Correct access pattern:

```python
with open('runtime/private/accounts.json') as f:
    data = json.load(f)
accts = data['accounts']   # list of account dicts
match = [a for a in accts if '634' in str(a.get('account_id', ''))]
```

Iterating `json.load(...)` directly will iterate dict keys (`'accounts'`,
etc.) and fail with `AttributeError: 'str' object has no attribute 'get'`.

## --brands flag syntax (PITFALL)

`run_full_5461_batch.py --brands` takes a **single comma-separated string**, NOT space-separated multiple args:

```bash
# CORRECT
.venv/Scripts/python.exe scripts/run_full_5461_batch.py --accounts 641 --brands "HOMEMO,JZG,WILLONE" --site uk --no-confirm

# WRONG — produces "unrecognized arguments" error
.venv/Scripts/python.exe scripts/run_full_5461_batch.py --accounts 641 --brands HOMEMO JZG WILLONE --site uk
```

Also accepts `all` to run every brand in the library.

## Multi-site sequencing for same account

When running US + MX on the same account, do NOT run both concurrently —
same AdsPower profile means shared browser session. Correct pattern:
1. Launch US batch (background), wait for it to fully exit (exit_code 0).
2. Then launch MX batch (background).

See `references/batch-monitoring-and-reporting.md` for the sequencing
details and polling recipe.

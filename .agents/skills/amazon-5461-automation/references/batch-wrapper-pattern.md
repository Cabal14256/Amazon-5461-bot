# Batch Wrapper Pattern for New Accounts

## Naming convention
Wrapper scripts live in the project root as `_run_{NUM}_batch_wrapper.py`, e.g.:
- `_run_643_batch_wrapper.py`
- `_run_644_batch_wrapper.py`

## Standard structure
1. **US batch first** — all brands via `--site US`
2. **MX batch second** — usually only `V-PORYADKU` via `--site MX`
3. Both use `--yes` flag (non-interactive confirm)
4. State files go in `data/` with pattern `batch_{NUM}_{site}_{DATE}.json`

## Invocation
The batch runner is `scripts/run_full_5461_batch.py`, called with:
```
--accounts us_store_{NUM}
--brands   BRAND1,BRAND2,...   (comma-separated, no spaces)
--site     US | MX
--state-file data/batch_{NUM}_{site}_{DATE}.json
--yes
```

## How to create a wrapper for a new account
1. Copy `templates/batch-wrapper.py` from this skill.
2. Fill in `ACCOUNT_ID`, `DATE`, `US_BRANDS`, `MX_BRANDS`.
3. Save as `_run_{NUM}_batch_wrapper.py` in the project root.
4. Run via background terminal (so it survives the Hermes session):
   ```
   cd /c/Users/Admin/projects/amazon-5461-bot
   .venv/Scripts/python.exe _run_{NUM}_batch_wrapper.py 2>&1
   ```

## Account config lookup
Account info (AdsPower profile, marketplace configs, entry URLs) lives in:
`runtime/private/accounts.json` → `data["accounts"]` (list of dicts).

To find a specific account:
```python
python -c "import json; data=json.load(open('runtime/private/accounts.json')); \
  [print(json.dumps(a, ensure_ascii=False)) for a in data['accounts'] if '644' in str(a.get('account_id',''))]"
```

## Greenlet noise
`greenlet.error: Cannot switch to a different thread` lines in the log are
Playwright async noise — they do NOT indicate a failure. Ignore them.
Real success signal: `[✓] 提取到 Case ID`.

## Standard US brand set (as of 2026-07)
Used consistently across accounts 641–644+:
```
WILLONE,JZG,MP-MALL,XDesign,uShield,OUNNE,VASG,JavoYion,MoShieldwish
```
MX usually: `V-PORYADKU`

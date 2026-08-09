# Batch Brand Onboarding for a New Account

## When to use

User supplies a list of brand names + an account number (e.g. "641US") and says
"自动搜索添加" or "add these brands to this account". Goal: generate all brand-pack
docs so the batch runner can immediately execute.

---

## Step-by-step

### 1. Resolve the AdsPower profile

```bash
curl -s "http://127.0.0.1:50325/api/v1/user/list?page=1&page_size=100" \
  | python -c "
import json, sys
data = json.load(sys.stdin)
lst  = data.get('data', {}).get('list', [])
# match on the bare number the user gave (e.g. '641')
matches = [p for p in lst if '641' in str(p.get('name',''))]
print(json.dumps(matches, indent=2, ensure_ascii=False))
"
```

Key fields to capture: `user_id` (= `adspower_profile_id`), `username` (login email).
If nothing returns, increase `page_size` or add `&page=2`.

### 2. Check whether account already exists in accounts.json

```bash
python -c "
import json
with open('runtime/private/accounts.json') as f:
    data = json.load(f)
accs = data['accounts']   # note: top-level key is 'accounts', NOT a bare list
matches = [a for a in accs if '641' in str(a.get('account_id',''))
           or '<adspower_profile_id>' in str(a.get('adspower_profile_id',''))]
print(json.dumps(matches, indent=2, ensure_ascii=False))
"
```

- **Account present** → skip to step 3.
- **Account missing** → onboard first (see existing adspower-profile-lookup.md).

### 3. Check which brand_packs already have docs for this account/site

```bash
python -c "
import os
brands = ['BRAND1', 'BRAND2']   # fill in
site   = 'US'                   # or MX, UK, etc.
acct   = '641'

for brand in brands:
    bp    = f'brand_packs/{brand}'
    files = os.listdir(f'{bp}/docs') if os.path.exists(f'{bp}/docs') else []
    hits  = [f for f in files if acct in f and site.lower() in f.lower()]
    print(f'  [{\"OK\" if hits else \"NEED_ADD\"}] {brand}: {hits}')
"
```

### 4. Run auto_add_account_data.py for each missing brand/site

Always use the project venv:

```bash
# US brands (loop)
for brand in BRAND1 BRAND2 BRAND3; do
  echo "=== $brand US ==="
  .venv/Scripts/python.exe auto_add_account_data.py "$brand" us_store_641 --site US 2>&1 | tail -5
done

# MX brand (single)
.venv/Scripts/python.exe auto_add_account_data.py "BRAND_MX" us_store_641 --site MX 2>&1
```

### 5. Verify docs were written

```bash
python -c "
import os
brands_us = ['BRAND1', ...]
for brand in brands_us:
    docs = os.listdir(f'brand_packs/{brand}/docs')
    hits = [f for f in docs if '641' in f and 'us' in f.lower()]
    print(f'  {brand}: {hits}')
"
```

Each brand should show two files:
- `5461_statement_us.account_641.txt`
- `gtin_exemption_statement_us.account_641.txt`

(MX counterparts use `_mx` suffix.)

---

## Pitfalls

- **`accounts.json` is a dict with key `"accounts"`, NOT a bare list.** Iterating
  `json.load(f)` directly raises `AttributeError: 'str' object has no attribute 'get'`.
  Always do `data['accounts']` first.

- **System python vs project venv.** The bare `python` in this session resolves to
  the Hermes venv (missing `openpyxl` etc.). Always call `.venv/Scripts/python.exe`
  for project scripts.

- **account_id format.** The project uses `us_store_<N>` (e.g. `us_store_641`).
  Pass that form to `auto_add_account_data.py`, not just the bare number.

- **MX marketplace config.** `us_store_641` has MX configured under
  `marketplace_configs.MX` even though its primary `marketplace` is `UK`. The script
  handles this automatically when `--site MX` is passed — no manual config needed.

- **"自动搜索添加" = this workflow.** When the user says this phrase, execute all
  5 steps above without asking for further clarification.

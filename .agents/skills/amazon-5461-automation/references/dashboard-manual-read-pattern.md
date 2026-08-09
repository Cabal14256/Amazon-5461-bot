# Manual Dashboard Read Pattern

Use this when `check_case_log.py` returns "未找到 Case ID" (exit code 1) and you need to verify whether a brand actually has a case.

## Root cause of check_case_log.py false negatives

The Amazon Case Dashboard at `/hz/myqdashboard` does **not** display a Case ID column. It shows:
- Application name (brand name)
- Application type (Catalog Authorization / GTIN Exemption)
- Changed (date)
- Status (Approved / Under review / Declined / Draft)

`check_case_log.py` searches for numeric Case ID strings and finds none → reports "未找到" → exits with code 1. This is a false negative. The brand IS on the Dashboard, it just has no visible Case ID to extract.

## Manual verification script (_tmp_dashboard_read.py)

Write this to the project root and run it:

```python
import sys, time
sys.path.insert(0, '.')
from src.browser_manager import BrowserManager

account_id = 'us_store_NNN'  # change as needed
manager = BrowserManager()

try:
    page, config = manager.connect_by_account(account_id)
    print(f"[OK] connected, URL: {page.url}")

    if 'myqdashboard' not in page.url:
        dashboard_url = 'https://sellercentral.amazon.com/hz/myqdashboard/ref=xx_myqd_favb_xx'
        page.goto(dashboard_url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(5)

    text = page.evaluate('() => document.body.innerText')
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    print(f"[total lines] {len(lines)}")

    # print all lines — Dashboard is short (~100 lines)
    for i, l in enumerate(lines):
        print(f"[{i}] {l}")

except Exception as e:
    print(f"ERROR: {e}")
    import traceback; traceback.print_exc()
finally:
    try:
        manager.close()
    except:
        pass
```

Run with: `./.venv/Scripts/python _tmp_dashboard_read.py 2>&1`
Clean up: `rm _tmp_dashboard_read.py`

## Interpreting the output

Dashboard rows are groups of 4–5 consecutive lines:
```
[N]   BrandName
[N+1] Catalog Authorization  (or GTIN Exemption)
[N+2] Jun 17, 2026
[N+3] Approved               (or Under review / Declined / Draft)
[N+4] Expected decision date: Jun 20, 2026   (only if Under review)
```

The page shows ~10 most recent entries. If more than 10 brands were submitted, scroll or use the search box to find older ones.

## Decision after reading

| Dashboard result | Action |
|---|---|
| Brand present, Approved | ✅ Done. No re-run needed. |
| Brand present, Under review | ✅ Done. Wait for Amazon decision. |
| Brand present, Declined | Handle decline separately. Do NOT re-run without investigating. |
| Brand present, Draft | Resume draft flow. |
| Brand absent (truly not found) | Safe to re-run. |

## Confirmed cases (2026-06-17)

- **616US uShield**: `check_case_log.py` reported "未找到" × 2. Manual read showed `uShield / Catalog Authorization / Jun 17, 2026 / Approved`. Brand was already approved — two retry runs were unnecessary.
- **Root cause**: Dashboard layout has no Case ID column. Script regex finds nothing, exits 1. Always do manual read before re-running.

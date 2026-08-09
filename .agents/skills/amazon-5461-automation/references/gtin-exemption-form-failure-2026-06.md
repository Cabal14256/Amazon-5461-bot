# GTIN Exemption Form — Failure Pattern & Fix Path (2026-06-21)

## Summary

Some brands on some markets (confirmed: uShield on MX) trigger a **GTIN Exemption Request** form
instead of the standard Catalog Authorization (`cat_auth_mo`) form. The current automation does not
fill this form correctly, leaving a Draft in Dashboard.

---

## Affected Session

- Account: `us_store_620`, Site: MX
- Brand: uShield
- Date: 2026-06-21
- Dashboard outcome: `uShield-Electrónicos` → GTIN Exemption → **Draft**

---

## Form Differences

| Attribute | Catalog Authorization (cat_auth_mo) | GTIN Exemption |
|---|---|---|
| Panel title | "Apply to sell" | "GTIN Exemption Request for Electrónicos and uShield" |
| Mandatory fields | Product title, Manufacturer, Product Description | **Product Name** (only) |
| Email/Phone | Yes (Email required, Phone optional) | No |
| File uploads | Yes | Yes (brand images) |
| State detection | `5461_FORM_OPEN` | `GTIN_EXEMPTION` |

---

## What the Script Does vs. What It Should Do

### Current (broken) path

1. `check_page_state()` detects `GTIN_EXEMPTION` from page text containing "GTIN Exemption"
2. Code at line ~3163 in `flow_submit_5461.py` treats this as "5461 panel not open yet (410001 symptom)"
3. Waits 30s, re-clicks Apply to sell
4. But the GTIN Exemption form **was already open** — re-clicking opens it again as a shell
5. `fill_5461_form()` is called — fills product_title/manufacturer/description using `cat_auth_mo` selectors
6. These selectors don't exist in the GTIN Exemption form → fields skipped
7. `Product Name` (the only real required field) is never filled
8. Submit clicked → validation fails → "Please enter the missing information." (orange bubble)
9. Script polls for Case ID → timeout → `failed`, tab kept open

**Log signature:**
```
[阶段 2/3] 页面状态: GTIN_EXEMPTION
[阶段 2.5] GTIN_EXEMPTION 状态，等待 30 秒后重新点击 Apply to sell...
  - 填写 Email: seller@example.com（历史值已脱敏）
  - 上传图片 (6 张)...
  - 勾选复选框...
    复选框已勾选（5 个）
[阶段 3/3] 点击提交...
[阶段 3/3] 等待提交处理完成...
[阶段 3/3] 等待 Case ID 出现...
```
Then timeout → `提交后未检测到成功提示，请人工检查`

---

## Product Name Field Selectors (to probe)

These selectors should be tried in order when in GTIN Exemption form:

```python
GTIN_EXEMPTION_SELECTORS = [
    'kat-input[id*="product_name"]',
    'kat-input[id*="product-name"]',
    'kat-input[label*="Product Name"]',
    'kat-input[placeholder*="Product"]',
    'input[id*="product_name"]',
    'input[placeholder*="Product Name"]',
]
```

The Product Name value to fill: use the brand's `product_title` from brand pack (same as
`item_name` in the main flow), e.g. `"uShield Screen Protector for iPhone"`.

---

## Detecting GTIN Exemption Form Is Already Open

In `check_page_state()` / `_probe_5461_panel()`, add detection for the GTIN form fields:

```python
# GTIN Exemption form is open (has real fields) — different from "GTIN_EXEMPTION page not loaded"
has_gtin_form = bool(page.query_selector('kat-input[id*="product_name"]'))
# or from page text:
has_gtin_form = 'GTIN Exemption Request' in page_text and 'Product Name' in page_text
```

When `has_gtin_form` is True, do NOT wait/retry Apply to sell — go directly to form fill.

---

## Required Code Fix (flow_submit_5461.py, ~line 3163)

```python
# BEFORE (current broken code):
if state['page_type'] in ['GTIN_EXEMPTION', 'PRODUCT_IDENTITY_UPC']:
    print("\n[阶段 2.5] GTIN_EXEMPTION 状态，等待 30 秒后重新点击 Apply to sell...")
    time.sleep(30)
    retry_result = filler.click_apply_to_sell()
    ...

# AFTER (correct logic):
if state['page_type'] in ['GTIN_EXEMPTION', 'PRODUCT_IDENTITY_UPC']:
    # Check if GTIN form is already open with real fields
    gtin_form_open = page.evaluate("""() => {
        return !!document.querySelector('kat-input[id*="product_name"]') ||
               (document.body.innerText || '').includes('Product Name') &&
               (document.body.innerText || '').includes('GTIN Exemption Request');
    }""")
    if gtin_form_open:
        print("[阶段 2.5] GTIN Exemption 表单已打开，填写 Product Name 并提交...")
        # Fill Product Name
        for sel in ['kat-input[id*="product_name"]', 'kat-input[label*="Product Name"]']:
            if page.locator(sel).count() > 0:
                fill_katal_input(page, sel, item_name)
                break
        # Upload images (reuse existing logic)
        # ... existing upload code ...
        # Submit
        # ... existing submit code ...
    else:
        # Original 410001/not-loaded path: wait and retry
        print("\n[阶段 2.5] GTIN_EXEMPTION 状态（表单未加载），等待 30 秒后重新点击 Apply to sell...")
        time.sleep(30)
        retry_result = filler.click_apply_to_sell()
        ...
```

---

## Manual Recovery Steps

When a brand has a GTIN Exemption Draft in Dashboard:

1. Open AdsPower for the affected account
2. Navigate to: `https://sellercentral.amazon.com.mx/hz/myqdashboard` (or use `check_case_log.py`)
3. Find the brand row (e.g. "uShield-Electrónicos"), click **"Go to application"**
4. In the GTIN Exemption form, fill **Product Name** (e.g. "uShield Screen Protector")
5. Verify images are still uploaded (they usually persist from the Draft save)
6. Click **Submit**
7. Wait for Case ID to appear, record it

**Note:** Direct `page.goto()` to `amazon.com.mx` dashboard from a non-authenticated Playwright
context triggers a signin redirect. Use `check_case_log.py` (which handles MX auth internally)
or do it manually in AdsPower.

---

## Why GTIN Exemption vs. Catalog Authorization?

Amazon routes brands to GTIN Exemption when:
- The brand does not have a registered GS1 barcode/UPC
- The product type requires barcode exemption for the target category/marketplace
- MX site has different exemption requirements vs. US for the same brand

The same brand (uShield) may go through Catalog Authorization on US but GTIN Exemption on MX.
This is not a script bug in brand selection — it is Amazon's routing logic. The fix is to support
both form types in `fill_5461_form()`.

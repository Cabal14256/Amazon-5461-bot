# GTIN Exemption Request Form — MX / Electrónicos (2026-06-21)

## Trigger Conditions
Certain brands on certain MX categories (confirmed: uShield on Electrónicos) trigger
a **GTIN Exemption Request** popup from the Apply to sell button instead of the standard
Catalog Authorization (cat_auth_mo) form.

Detection signal: `check_page_state()` returns `GTIN_EXEMPTION` after the Apply to sell click.
Panel text contains: `"GTIN Exemption Request for Electrónicos and <BRAND>"`.

## Form Structure
Only ONE required field in the popup:
- `* Product Name` — free text input

Plus the standard:
- Document upload (6 images, same as cat_auth_mo)
- Checkboxes (6 items — image/branding requirements)

The original variant had no email, phone, or product-description field. Newer MX
variants may add a required contact email and an optional phone field below the
document list. Automation must fill the email when the field is present and must
not assume every GTIN form has the same contact section.

## Selectors (priority order)
```python
GTIN_PRODUCT_NAME_SELECTORS = [
    'kat-input[label="Product Name"]',
    'kat-input[id*="product_name"]',
    'kat-input[data-cy*="product_name"]',
    'kat-input[placeholder*="Product Name"]',
    'kat-input[placeholder*="product name"]',
    # fallback: first kat-input in visible panel via JS
]
```

## Product Name Value Source
Extract from `gtin_exemption_statement_mx.account_NNN.txt`:
```python
import re
m = re.search(r"Item name[：:]\s*(.+)", statement_text or "")
product_name_value = m.group(1).strip() if m else f"{brand_name} Screen Protector"
```
Example for uShield MX: `"uShield Protector de Pantalla Compatible de 6.10 Pulgadas, Sin Burbujas, HD-Clear 2+2 piezas"`

## Code Fix Location
`src/flow_submit_5461.py` — `fill_5461_form()` function:
- Added GTIN Exemption detection block at function entry (before SELECTORS dict)
- Detects via panel text contains `"GTIN Exemption"`
- Fills Product Name, uploads images, checks checkboxes, then returns early

`src/flow_submit_5461.py` — two GTIN_EXEMPTION state handler blocks (~line 3283 and ~line 3713):
- Both now check if panel is already open (`panel-visible="true"` + `"GTIN Exemption"` in panel text)
- If panel already open → set `has_5461_form=True`, `page_type='5461_FORM_OPEN'`, skip 30s wait
- If panel not open → existing 30s wait + retry logic (for genuine 410001 cases)

## Previous Broken Behavior
1. `fill_5461_form` had no GTIN branch → Product Name empty → validation error
   `"Please enter the missing information."` → Submit button disabled
2. Main flow saw `GTIN_EXEMPTION` state → assumed panel not open → waited 30s → retried
   `click_apply_to_sell` → panel reopened as GTIN Exemption → infinite loop
3. Result: brand kept as `failed`, left a Draft on Dashboard

## Dashboard After GTIN Exemption Submission
- Shows as `GTIN Exemption` type (not `Catalog Authorization`)
- Application name: `<BRAND>-Electrónicos` (with category suffix)
- Same Dashboard false-negative applies: `check_case_log.py` won't find a numeric Case ID
  because GTIN Exemption entries may not show Case ID in the same column

## Affected Brands (confirmed)
- uShield on 620MX (2026-06-21)
- Likely: any brand in Electrónicos category where Amazon hasn't granted GTIN exemption yet

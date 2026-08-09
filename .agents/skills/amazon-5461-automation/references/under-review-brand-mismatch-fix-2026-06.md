# Under Review Case ID Brand-Mismatch Fix (2026-06-27)

## Problem

Same-tab reuse path: after WILLONE succeeded, `reset_page_for_next_brand()` JS-forced
`kat-panel-wrapper[panel-visible="true"]` to `panel-visible=false`. JZG was then started
on the same tab (skipping `page.goto`). When JZG's Apply to sell click fired, Amazon
re-rendered the **same panel DOM node** — still containing WILLONE's Case ID
(`<CASE_ID_REDACTED>`) and "Under review" status. `fill_5461_form` read the panel, saw
`under_review`, set `self.under_review_case_id = <CASE_ID_REDACTED>`, and returned `True`
without filling any fields. JZG was marked completed with WILLONE's Case ID.

Log signature (before fix):
```
[FormFiller] panel 内检测到 Under Review Case ID: <CASE_ID_REDACTED>，无需重新申请
[阶段 2/3] Under Review Case ID: <CASE_ID_REDACTED>，跳过表单填写
[清理] Under Review（Case ID: <CASE_ID_REDACTED>），无需重新申请
[页面] JZG 页面已关闭（已确认成功/Case ID）
```

Detection: two consecutive brands in `batch_state.json` share the exact same `case_id`.

## Root Cause

`reset_page_for_next_brand()` (line ~2367 in `flow_submit_5461.py`) calls
`close_5461_panel(page)` which sets `panel-visible="false"` via JS. This is cosmetic —
the DOM node is not removed. Amazon's React component re-shows the same panel
(with stale content) when the next brand's Apply to sell is clicked.

The Under Review check in `form_filler.py` (inside `click_apply_to_sell`, ~line 1650)
only validated:
1. A numeric Case ID is present in panel text  
2. Context around it contains "under review" / "decision expected"

It did **not** validate whether the panel's brand name matched the current brand.

## Fix Location

**File:** `src/form_filler.py`  
**Method:** `click_apply_to_sell` → panel shell branch (~line 1654)

### JS change
Added extraction of `panel_brand` from `[data-cy="application_tile_header"]`:
```javascript
const headerEl = panel.querySelector('[data-cy="application_tile_header"]');
const headerText = headerEl ? (headerEl.innerText || headerEl.textContent || '') : '';
const brandMatch = headerText.match(/for\s+(.+)$/i);
const panelBrand = brandMatch ? brandMatch[1].trim() : null;
return {case_id: m[1], status, context, panel_brand: panelBrand};
```

### Python change
Under Review branch now does a brand-name check before accepting the Case ID:
```python
elif panel_status == 'under_review':
    panel_brand = panel_case.get('panel_brand')
    brand_match_ok = True
    if panel_brand and hasattr(self, 'brand_name') and self.brand_name:
        brand_match_ok = (
            panel_brand.lower() == self.brand_name.lower()
            or self.brand_name.lower() in panel_brand.lower()
            or panel_brand.lower() in self.brand_name.lower()
        )
    if not brand_match_ok:
        print(f"[FormFiller] panel 内 Under Review Case ID: {panel_case_id}，"
              f"但品牌不匹配（panel={panel_brand!r} vs current={self.brand_name!r}），"
              f"忽略旧 panel，继续填表")
        # fall through — do NOT set under_review_case_id
    else:
        print(f"[FormFiller] panel 内检测到 Under Review Case ID: {panel_case_id}"
              f"（品牌={panel_brand!r}），无需重新申请")
        self.under_review_case_id = panel_case_id
        self.under_review_case_context = panel_case.get('context')
        return True
```

Safe fallback: if `panel_brand` is `None` (header element not found in DOM),
`brand_match_ok` stays `True` and the old behavior is preserved.

## Log Signature After Fix

```
[FormFiller] panel 内 Under Review Case ID: <redacted>，但品牌不匹配（panel='WILLONE' vs current='JZG'），忽略旧 panel，继续填表
```
Then proceeds to normal form fill for JZG.

## Affected Accounts / Batches

| Date | Account | Brand (victim) | Stale brand | Stale Case ID |
|------|---------|---------------|-------------|---------------|
| 2026-06-26 | us_store_627 | uShield | WILLONE | redacted |
| 2026-06-27 | us_store_626 | JZG | WILLONE | redacted |

Both cases: WILLONE was the first brand in a same-tab reuse sequence.

## Verification

```bash
cd projects/amazon-5461-bot
./.venv/Scripts/python -m pytest tests/ -x -q
# 4 tests passed

./.venv/Scripts/python -c "
def brand_match(panel_brand, current_brand):
    if not panel_brand or not current_brand:
        return True
    return (
        panel_brand.lower() == current_brand.lower()
        or current_brand.lower() in panel_brand.lower()
        or panel_brand.lower() in current_brand.lower()
    )
assert brand_match('WILLONE', 'JZG') == False
assert brand_match('WILLONE', 'WILLONE') == True
assert brand_match(None, 'JZG') == True
print('All assertions passed')
"
```

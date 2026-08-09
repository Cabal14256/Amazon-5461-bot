# close_5461_panel — Shadow DOM Root Cause (2026-06-27)

## What happened

626US batch: JZG received WILLONE's Case ID (<CASE_ID_REDACTED>) via same-tab reuse.
Log signature:
```
[FormFiller] panel 内检测到 Under Review Case ID: <CASE_ID_REDACTED>，无需重新申请
[阶段 2/3] Under Review Case ID: <CASE_ID_REDACTED>，跳过表单填写
[页面] JZG 页面已关闭（已确认成功/Case ID）
```

## Root cause

`close_5461_panel()` was using `document.querySelectorAll()` which **cannot pierce Shadow DOM**.

The close button HTML:
```html
<kat-panel ...>
  <template shadowrootmode="open">   ← kat-panel's Shadow DOM
    <div class="header">
      <button class="close" type="button" part="panel-close-button" aria-label="close">
```

`button[aria-label="close"]` and `button[part="panel-close-button"]` are **inside** `kat-panel`'s
shadow root. `document.querySelectorAll()` never finds them. The function fell through to the
JS force-hide fallback: `p.setAttribute('panel-visible', 'false'); p.style.display = 'none'`.

This hides the panel visually but does NOT fire the React close event. When the next brand
triggers Apply to sell, Amazon restores the same panel instance (with the old brand's Case ID)
and sets `panel-visible=true` again.

## Fix (`src/flow_submit_5461.py` — `close_5461_panel()`)

Use **Playwright `page.locator()`** which automatically pierces Shadow DOM:

```python
# PRIMARY — aria-label is reliable with Playwright shadow piercing
btn = page.locator('button[aria-label="close"]').first
if btn.count() > 0:
    btn.click(timeout=3000)
    return {"closed": "clicked", "selector": "button[aria-label=close] (shadow piercing)"}

# SECONDARY — part attribute piercing is inconsistent in Playwright
btn = page.locator('button[part="panel-close-button"]').first
if btn.count() > 0:
    btn.click(timeout=3000)
    ...

# FALLBACK — JS force-hide (no React event, last resort only)
```

Confirmed working in 626US batch2: log shows
`closed: clicked, selector: button[aria-label=close] (shadow piercing)`
with no subsequent brand-mismatch warnings.

## Defence-in-depth (`src/form_filler.py`)

JS evaluate in the Under Review check now also extracts `panel_brand` from
`[data-cy="application_tile_header"]` ("Application to create new ASINs for BRAND").
If `panel_brand` does not match `self.brand_name` (case-insensitive substring), the
Under Review signal is ignored and form filling proceeds normally.

This guard fires even if the close-button click somehow fails to fire the React event.

## Key lesson

**Any Amazon Katal Shadow DOM element that needs to be clicked must use Playwright
`page.locator()`, not `page.evaluate()` + `document.querySelectorAll()`.**
`querySelectorAll` is fine for reading attributes/text of the host element, but cannot
reach inside shadow roots for interaction targets.

# Same-Tab Reuse: Panel Residue Root Cause (2026-06-27)

## Confirmed via CDP live inspection (626US batch)

### What actually happens after a successful submit

After Amazon delivers a Case ID and the UI shows "Under review", it **auto-hides** the panel by removing
the `panel-visible` attribute entirely — it does NOT fire a close event or clear the DOM content.

CDP inspection result (taken immediately after `[✓] 提取到 Case ID: <CASE_ID_REDACTED>`):

```json
{
  "panel_count": 2,
  "panels": [
    {
      "visible_attr": null,           // ← attribute gone, not "false"
      "has_shadow": true,
      "close_btn": {
        "part": "panel-close-button",
        "aria_label": "close",
        "visible": false              // ← offsetParent === null
      }
    }
  ]
}
```

The panel DOM — including the old brand's Case ID and "Under review" text — **stays in memory**.

### Why close_5461_panel() fails

The close button `<button part="panel-close-button" aria-label="close">` lives inside `kat-panel`'s
own Shadow DOM:

```
kat-panel-wrapper (light DOM)
  └── kat-panel (light DOM / slot)
        └── #shadow-root  ← kat-panel's Shadow DOM
              └── div.header
                    └── button[part="panel-close-button"]  ← target
```

| Approach | Result |
|---|---|
| `document.querySelectorAll('button[part="panel-close-button"]')` | Not found — can't pierce Shadow DOM |
| `page.locator('button[aria-label="close"]')` global search | Finds & "clicks" but button is `visible=false` → no React event fires |
| `kat-panel.shadowRoot.querySelector('button.close').click()` | Reaches button, but `visible=false` means click is a no-op when panel already hidden |

**Core insight: by the time `reset_page_for_next_brand()` is called, the panel is already hidden.
There is nothing to close. The panel content persists silently.**

### Why the next brand gets the wrong Case ID

When the next brand runs Apply to sell, Amazon re-opens the **same panel instance** with the old
content (old brand name, old Case ID, "Under review") still rendered. `fill_5461_form` reads the panel,
sees Under Review + a valid Case ID, and accepts it without checking the brand name.

### Fixes applied

**Fix 1 — brand name validation (`src/form_filler.py`)**

JS evaluate in the Under Review check now also reads `[data-cy="application_tile_header"]`:
```
"Application to create new ASINs for BRAND"
```
Python side compares `panel_brand` to `self.brand_name` (case-insensitive, substring). Mismatch → log
`panel 内 Under Review Case ID: X，但品牌不匹配（panel='A' vs current='B'），忽略旧 panel，继续填表`
and fall through to normal form fill. `None` panel_brand → allow (safe fallback).

**Fix 2 — shadowRoot click (`src/flow_submit_5461.py` `close_5461_panel`)**

Priority order:
1. `kat-panel.shadowRoot.querySelector('button[part="panel-close-button"]')` + dispatch mousedown/mouseup/click
2. `kat-panel-wrapper.shadowRoot` deep search (wrapper → inner kat-panel → shadowRoot)
3. `page.locator('kat-panel').locator('button[aria-label="close"]')` chained locator
4. Escape key + JS force `panel-visible=false` / `display:none` (last resort)

This correctly handles the case where the panel IS visible (e.g. mid-retry recovery paths).
For the post-submit auto-hidden case, Fix 1 (brand name check) is the primary defence.

### Recommended long-term fix

Disable same-tab reuse (`page_ready_for_reuse`) entirely, or do a `page.reload()` between brands.
The 15-30s saving from skipping `page.goto()` is not worth the residual-panel mis-attribution risk.
The panel state can only be fully cleared by a page navigation.

### Confirmed instances

| Session | Account | Brand misattributed | Stale Case ID source |
|---|---|---|---|
| 2026-06-26 | 627UK | uShield ← WILLONE | redacted |
| 2026-06-27 | 626US | JZG ← WILLONE | redacted |

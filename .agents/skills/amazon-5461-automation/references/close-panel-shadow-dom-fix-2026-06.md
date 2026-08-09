# close_5461_panel — Shadow DOM Root Cause & Fix (2026-06-27)

## Problem

`close_5461_panel()` was calling `document.querySelectorAll('button[part="panel-close-button"]')`.
This silently found nothing because the close button lives **inside `kat-panel`'s own Shadow DOM**:

```html
<kat-panel data-testid="kat-panel-QualificationWidget">
  <template shadowrootmode="open">        ← kat-panel shadow root
    <div class="header">
      <button class="close" part="panel-close-button" aria-label="close">
        <kat-icon name="exit" ...></kat-icon>
      </button>
    </div>
  </template>
</kat-panel>
```

`document.querySelectorAll` does NOT pierce Shadow DOM. The function fell through to the JS
attribute-force fallback: `p.setAttribute('panel-visible', 'false')`. This hid the panel
visually but never fired any React close event. On the next brand's Apply to sell click,
Amazon re-rendered the **same panel instance** with the previous brand's Case ID still inside.

## Symptoms

- Same-tab reuse: brand B reads brand A's `Under Review` Case ID from panel, skips form fill.
- Log (old, broken): `[FormFiller] panel 内检测到 Under Review Case ID: <CASE_ID_REDACTED>，无需重新申请`
  immediately after a brand switch — Case ID belongs to the *previous* brand.
- `batch_state.json`: two consecutive brands share the exact same `case_id`.
- Confirmed: 626US 2026-06-27 — JZG received WILLONE's case_id `<CASE_ID_REDACTED>`.

## Fix — `src/flow_submit_5461.py` `close_5461_panel()`

Four methods tried in order:

1. **`kat-panel.shadowRoot.querySelector()`** — direct shadow DOM access, dispatches
   `mousedown + mouseup + click` native MouseEvents. Log: `closed: shadow_click`.
2. **`kat-panel-wrapper.shadowRoot`** — for versions where wrapper contains the panel; also
   walks one level deeper into nested `kat-panel.shadowRoot`.
3. **Playwright chain locator** — `page.locator('kat-panel').locator('button[aria-label="close"]')`
   — chains through the shadow host, more reliable than a global `page.locator()` search.
4. **Escape key + JS attribute force** — last resort only.

Key: `shadowRoot.querySelector(...).click()` + explicit MouseEvent dispatch is what actually
triggers Amazon's React close handler. The old JS `element.click()` on a light-DOM handle
was not reaching the shadow DOM button at all.

## Second Defence — `src/form_filler.py` brand-name validation

Even if the panel is not properly closed, `fill_5461_form` now validates the panel's brand
before accepting an `under_review` short-circuit:

- JS extracts `[data-cy="application_tile_header"]` inner text:
  `"Application to create new ASINs for <BRAND>"`
- Python compares extracted brand against `self.brand_name` (case-insensitive, substring match).
- **Mismatch** → log warning, fall through to normal form fill.
  Log: `panel 内 Under Review Case ID: xxx，但品牌不匹配（panel='WILLONE' vs current='JZG'），忽略旧 panel，继续填表`
- **Match or header not found** → accept as genuine Under Review, set `under_review_case_id`.
  Log: `panel 内检测到 Under Review Case ID: xxx（品牌='JZG'），无需重新申请`

## Selector notes

- `button[part="panel-close-button"]` — exists in shadow DOM, reliable via `shadowRoot.querySelector`
- `button[aria-label="close"]` — same element, also works via Playwright chain locator
- `button.close` — CSS class fallback inside shadow root
- Playwright `page.locator('button[aria-label="close"]')` global search — finds the button but
  `.click()` does not reliably fire React's synthetic event; confirmed broken in practice.
- `document.querySelectorAll('button[aria-label="close"]')` — cannot pierce shadow DOM at all.

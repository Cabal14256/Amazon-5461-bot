# Amazon New UI: "Apply to sell" → kat-link "View" (2026-07-15)

## What changed

Amazon rolled out a new Product Identity UI for `interactive/listing/workflow/create` URLs that replaces the old `kat-button` "Apply to sell" with:

- A `kat-link variant="link"` element containing text "View"
- Surrounding text: "1 restriction" and "1 application required" (rendered via `kat-alert`)
- The container is a plain `div.kat-col-xs-12.kat-col-sm-7` with a `kat-box` inside — no special data-cy or data-testid on the restriction block itself

Clicking the "View" kat-link opens a `kat-panel-wrapper` slide-in panel with the full 5461 form (same fields as before).

## Panel changes

The `kat-panel-wrapper` that contains the 5461 form in the new UI:
- Has **no** `data-testid="kat-panel-wrapper-QualificationWidget"` — the testid is empty (`data-testid="kat-panel-wrapper-"`) or absent entirely
- `panel-visible` attribute is `""` (empty string), NOT `"true"` — despite the panel being open and visible
- Panel content still contains: `"Listing approval"`, `"Brands require approval"`, `"Submit required information"`
- Form fields inside are identical: `kat-input#question-cat_auth_mo_question_string_id_product_title`, `manufacturer`, `product_description`, `contact_info_email_input`, `input[id*="document_upload"][id*="document_input"]`

## Symptoms of hitting the old code path

When the new UI is present but the code only looks for "apply to sell" text:
- `check_page_state` returns `PRODUCT_IDENTITY_NEW_UI` instead of `NEEDS_APPROVAL_NEW_UI`
- `has_apply_button: {found: false}` — stage 2 skips authorization entirely
- Stage 1.8 Continue/Submit clicks succeed but page stays on `product_identity` URL
- `final_state.png` shows the form with "1 restriction / 1 application required" at the bottom
- `DashboardCheck` navigation fails (stays on product_identity URL)
- Brand marked as `failed`, tab retained

## Fixes applied (src/flow_submit_5461.py + src/form_filler.py)

### 1. `check_page_state` — detect "application required" before falling through to `PRODUCT_IDENTITY_NEW_UI`

```python
if 'interactive/listing/workflow/create' in current_url.lower():
    has_application_required = (
        '1 application required' in page_text
        or 'application required' in page_text_lower
        or ('1 restriction' in page_text and 'view' in page_text_lower)
    )
    if has_application_required:
        state['page_type'] = 'NEEDS_APPROVAL_NEW_UI'
        state['needs_auth'] = True
        return state
    state['page_type'] = 'PRODUCT_IDENTITY_NEW_UI'
    return state
```

### 2. `has_apply_button` JS probe — add kat-link "View" detection

```javascript
const katLinks = document.querySelectorAll('kat-link');
for (const lnk of katLinks) {
    const text = (lnk.textContent || lnk.getAttribute('label') || '').trim().toLowerCase();
    if (text === 'view') {
        const bodyText = document.body.innerText;
        if (bodyText.includes('application required') || bodyText.includes('1 restriction')) {
            return { found: true, text: 'kat-link-view-application-required' };
        }
    }
}
// Also check body text
if (bodyText.includes('application required') || bodyText.includes('1 application')) {
    return { found: true, text: 'application_required_in_body' };
}
```

### 3. `_click_apply_to_sell_human` — click kat-link "View" before JS fallback

```python
# After failing to find "apply to sell" button text:
try:
    view_loc = self.page.locator('kat-link:has-text("View")')
    if view_loc.count() > 0:
        view_loc.first.scroll_into_view_if_needed(timeout=3000)
        self._human_delay(0.3, 0.8, "View链接点击前等待")
        view_loc.first.click(timeout=5000)
        return {"clicked": True, "location": "kat-link-view-playwright", "text": "View"}
except Exception as e:
    pass  # fall through to JS
```

JS fallback also queries `document.querySelectorAll('kat-link')` for text === 'view'.

### 4. `_probe_5461_panel` — expand panel selector beyond QualificationWidget

```javascript
let panel = document.querySelector('kat-panel-wrapper[data-testid="kat-panel-wrapper-QualificationWidget"]');
if (!panel) {
    const allWrappers = document.querySelectorAll('kat-panel-wrapper');
    for (const w of allWrappers) {
        const t = w.innerText || w.textContent || '';
        if (t.includes('Listing approval') || t.includes('listing approval') ||
            t.includes('cat_auth_mo') || t.includes('product_title') ||
            t.includes('Brands require approval') || t.includes('Submit required information')) {
            panel = w;
            break;
        }
    }
}
```

## Verification

After fixes: WILLONE submitted successfully (fill_5461_form ran, form fields populated, submit clicked). JZG and subsequent brands also entered fill stage. The `kat-link-view-playwright` location string appears in logs confirming new path is taken.

## DOM snapshot (new UI, as of 2026-07-15)

```html
<!-- Restriction block -->
<div class="kat-col-xs-12 kat-col-sm-7">
  <kat-box>
    <div class="...">  <!-- "1 restriction" + View -->
      <span class="...">1 restriction</span>
      <kat-link class="..." variant="link">View</kat-link>
    </div>
    <kat-alert>  <!-- "1 application required" -->
      <span>1 application required</span>
    </kat-alert>
  </kat-box>
</div>

<!-- Panel (no QualificationWidget testid, panel-visible="") -->
<kat-panel-wrapper size="small" variant="overlay" panel-visible="">
  <kat-panel>
    Brands require approval
    Listing approval for {BRAND}
    Submit required information
    ... (same 5461 form fields as before)
  </kat-panel>
</kat-panel-wrapper>
```

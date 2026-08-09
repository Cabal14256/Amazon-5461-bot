# Troubleshooting Reference

> Last updated: 2026-04-28

## Exception Handling Strategy

When automation stalls or throws errors, follow this 6-step diagnostic flow:

1. **Screenshot** — Capture current page state to confirm if stuck on loading, popup not closing, or unexpected redirect
2. **Scan page elements** — Check if key elements exist (5461 popup, Next button, error messages)
3. **Analyze visible text** — Read page text to determine current state:
   - `"Brand Authorization Required"` → Need to click Apply to sell
   - `"Listing approval"` or `seller-qualification` in URL → 5461 form is open
   - `"Need help?"` → Generic page, may need refresh
   - `"Unable to list... not accepting applications"` → Brand is hard-blocked by Amazon
4. **Check URL** — Use `page.url` to determine flow position:
   - `/description` → Already has permission (no 5461 needed)
   - `/product-identity` or similar → Still on Add Product form
5. **Attempt auto-recovery**:
   - Popup not showing → Force via JS: `display='block'; visibility='visible'; zIndex='9999'`
   - Button disabled → Try clicking inner shadowRoot button
   - Form validation fails → Check field values (uppercase brand, no duplicates)
   - Page load timeout → Increase wait time or retry
6. **If unrecoverable** → Pause execution, log error state, wait for human intervention

---

## Common Bug Types

| Bug | Cause | Fix |
|-----|-------|-----|
| Text duplicated in input | Field not cleared before typing | Use `Ctrl+A` + `Delete` before input |
| `type_like_human` signature error | Called with `(selector, text)` but method only accepts `text` | Use `fill_kat_input_js` instead |
| f-string brace nesting | JS code contains `{` `}` causing Python f-string conflict | Pass JS code as parameters instead of inline |
| Click hits wrong DOM node | f-string expansion bug clicks JSON data node | Use `kat-box[class*="clickable"]` for precise selection |
| `Locator.fill` fails on kat-input | kat-input is a Shadow DOM component | Fall back to JS fill method |
| Apply to sell popup invisible | `panelVisible='true'` but `display='none'` | Force-show via JS styling |

---

## Amazon-Side Errors

### Error 410001

**Symptom**: After clicking "Apply to sell", popup shows:
> `An unexpected error has occurred. Please try again later. Error: 410001`

**Cause**: Amazon internal server error (not a script issue).

**Resolution**:
- Wait 10-30 minutes, then retry
- If persistent across multiple brands on same account, try a different account
- If persistent across all accounts, Amazon platform may be experiencing issues

### Brand Hard Block

**Symptom**: Page shows:
> `Unable to list [BRAND] products and we are currently not accepting applications for approval`

**Cause**: Amazon has blocked this brand from new applications on this account.

**Resolution**:
- This brand cannot be applied for on this account
- Try a different account, or abandon this brand for this account
- Log this in the account ledger to avoid future retries

---

## Page State Quick Reference

```python
# Already has permission (no 5461 needed)
if "/description" in current_url:
    return "已有权限，进入产品详情页"

# Needs authorization
if "Brand Authorization Required" in page_text:
    # Click Apply to sell

# 5461 form open
if "Listing approval" in page_text or "seller-qualification" in page.url:
    # Fill 5461 form

# Brand selection page
if "Clarify which brand" in page_text or "kat-radio" in page_html:
    # Match brand keywords and click correct radio

# Stuck on Product Identity
if page_state == "PRODUCT_IDENTITY":
    # Check if form validation passed
    # May need to retry Next click or check for brand-specific issues
```

---

## Recovery Commands

### Force-show 5461 popup
```javascript
// After Apply to sell click
el.style.display = 'block';
el.style.visibility = 'visible';
el.style.zIndex = '9999';
```

### Clear and fill Shadow DOM input
```python
# Clear first (avoid text duplication)
page.evaluate('''() => {
    const el = document.querySelector('kat-input[name="brand-0-value"]');
    if (el && el.shadowRoot) {
        const input = el.shadowRoot.querySelector('input');
        input.focus();
        input.select();
        document.execCommand('delete');
    }
}''')

# Then fill via JS
page.evaluate(f'''(value) => {{
    const el = document.querySelector('kat-input[name="brand-0-value"]');
    if (el && el.shadowRoot) {{
        const input = el.shadowRoot.querySelector('input');
        input.value = value;
        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
        input.dispatchEvent(new Event('blur', {{ bubbles: true }}));
        input.dispatchEvent(new Event('focusout', {{ bubbles: true }}));
    }}
}}''', brand_name)
```

### Click historical application row to trigger new form
```python
# Precise selector for clickable application history rows
page.locator('kat-box[class*="clickable"]').first.click()
```

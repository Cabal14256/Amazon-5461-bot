# Edge Cases Reference

> Last updated: 2026-04-28

## Site switching / marketplace mismatch

Important: `sellercentral.amazon.com` does not guarantee the active site is United States.
Some accounts may open into Mexico or another region while keeping a similar URL pattern.

### What to check
- inspect the site switcher UI
- confirm the visible region label says `United States` when doing US verification

### If not on US
- switch via the site switcher
- or use a forced US marketplace URL when appropriate

## Amazon system error page

If the page shows an Amazon system processing error:
- wait about 30 seconds
- refresh
- success indicators include text such as:
  - `Item Name`
  - `Product Type`
  - `Item Type Keyword`

## Amazon internal error (Error 410001)

After clicking "Apply to sell", popup may show:
> `An unexpected error has occurred. Please try again later. Error: 410001`

This is an Amazon server-side error, not a script issue.
- Wait 10-30 minutes and retry
- If persistent across multiple brands on the same account, try a different account
- See [troubleshooting.md](troubleshooting.md) for full details

## Brand hard block

Amazon may show:
> `Unable to list [BRAND] products and we are currently not accepting applications for approval`

This brand cannot be applied for on this account. Try a different account or abandon the brand.

## Katal components / Shadow DOM

Some accounts/pages use Amazon Katal components:
- `kat-input`
- `kat-textarea`
- `kat-checkbox`
- `kat-select`

Standard selectors may fail.
Use Shadow DOM access to reach the inner control, set the value, and dispatch input/change events.

### Text duplication in inputs

**Symptom**: Input shows duplicated text (e.g., "brandbrand" instead of "brand")

**Cause**: Field not cleared before new input is entered.

**Fix**: Always clear before fill:
```python
# Use Ctrl+A + Delete to clear
page.evaluate('''() => {
    const el = document.querySelector('kat-input[name="brand-0-value"]');
    if (el && el.shadowRoot) {
        const input = el.shadowRoot.querySelector('input');
        input.focus();
        input.select();
        document.execCommand('delete');
    }
}''')
```

### f-string brace nesting in JS code

**Symptom**: Python f-string expansion breaks because JS code contains `{` `}`

**Fix**: Pass JS code as parameters instead of inline f-strings:
```python
# BAD: f-string with JS braces
page.evaluate(f'''() => {{ document.querySelector("{selector}").click() }}''')

# GOOD: Pass as parameter
page.evaluate('''(selector) => { document.querySelector(selector).click() }''', selector)
```

## Brand Authorization Required

Treat these as deterministic fail signals for verification:
- `Brand Authorization Required`
- `The ability to create or edit ASINs using this brand name is restricted to authorized sellers`
- `Apply to sell`

This should be recorded as FAIL rather than escalated for unnecessary human confirmation.

## Account-specific Add Product URL differences

Different stores may require different `displayPath` / `itemType` combinations.
Preferred strategy:
1. account-specific `entry_url`
2. fallback detection and fill for empty item type keyword
3. dynamic selection from config

## Smart item type matching

When exact config is unreliable:
- open the item type dropdown
- search for text containing:
  - `screen protector`
  - `screen protectors`
  - `protector`
- prefer exact/full phrase matches first

## Background page reset behavior

After clicking "Apply to sell", the background Product Identity page stays as-is.
The 5461 popup is independent — the background page does NOT auto-refresh after popup submission.
This is normal Amazon design, not a bug.

## "XXX" SKU suffix is a normal fallback, not an error

Some brands (observed: HOMEMO, JZG, VASG) don't have a real UK-specific item
model number in their source data. `auto_add_account_data.py` falls back to
`{account}-{SITE}-{brand}-XXX` in that case (see the `adapted_sku` fallback
in `auto_add_account_data.py`). This pattern is consistent across every past
UK account for these same brands (573, 580, 582, 593, 596, 603, 611, 613,
614, 615, 618, 621, 623, 624, 627, 628, 630, 632, ...) — it is NOT a sign of
a broken brand pack or a generation bug. Other brands (e.g. V-PORYADKU,
WILLONE, uShield, JavoYion) get a real 4-char model code instead (e.g.
`632-UK-WILLONE-DC67`).

Before assuming a bug, compare against the SKU pattern for the *same brand*
across other accounts/sites:
```bash
grep -h "SKU" brand_packs/<BRAND>/docs/5461_statement_uk*.txt | sort -u
```
If XXX shows up consistently for that brand across many older accounts too,
it's expected — proceed with the submission as-is.

## Case ID format variations

Case IDs come in multiple formats. Extraction logic must handle all:
- Standard long format: `<CASE_ID_REDACTED>` (11 digits)
- Labeled format: `Case ID - <CASE_ID_REDACTED>`
- Short alphanumeric: `ad671058`, `758695034` (6-9 chars, mixed)

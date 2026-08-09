# Business Rules Reference

## Core success rule for brand verification

Brand verification passes only when:
- Open Add Product
- Enter Brand Name
- Click Next
- Land on the Description page

If the page does not advance and instead shows authorization restriction text, that is a fail.

## High-risk boundaries

Keep these human-controlled unless policy is intentionally changed:
- captcha
- 2FA / verification steps
- final submit confirmation

## Unknown page policy

Use tiered handling:
1. Try local rules first.
2. If uncertain, capture evidence and analyze.
3. Apply by risk:
   - low + high confidence: auto-handle
   - medium: ask for human confirmation
   - high or low confidence: human takeover
4. Save human decisions as rules for future reuse.

## Brand verification page rules

Typical steps:
1. Check Manage Your Brands.
2. Open Add Product page.
3. Fill:
   - Brand Name
   - Item Name = `screen protector`
   - check `This product does not have a Product ID`
4. Click Next.
5. Judge pass/fail.
6. Save screenshots and state.

## Page reuse optimization

If verification fails and the page stays on Product Identity:
- reuse current page
- clear Brand Name only
- keep other required fields when valid

If verification passes and navigates to Description:
- open a fresh Add Product page for the next brand

## Data source rule

- Human-maintained master data may exist in Excel.
- Execution should read generated text/material files from `brand_packs/{brand}/docs/`.
- Avoid switching back to Excel at runtime.

## Language rule

Use local marketplace language for 5461 / GTIN materials:
- UK/US: English
- DE: German
- FR: French
- ES: Spanish
- IT: Italian

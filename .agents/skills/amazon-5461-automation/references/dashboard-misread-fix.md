# Dashboard Misread Fix — 2026-06-13

## Problem: brand reported as Draft when actually Under Review

### Observed symptom
Batch halts after submitting a brand. `batch_state.json` shows `status: failed`,
`dashboard_check.status: draft`. Manual inspection of the Dashboard shows the brand's
**Catalog Authorization** is actually **Under review**.

### Root cause 1 — ERR_ABORTED navigation (primary)

After a successful 5461 submit, Amazon's page is in an active-request / post-submit
state. `check_case_dashboard_for_brand` calls `page.goto(dashboard_url)`. The navigation
is aborted (`net::ERR_ABORTED`) because the current page blocks it. The page URL stays
on the Add Product URL (`/abis/listing/create/product_identity?...`).

The **old code** did not early-return on nav failure; it continued to call `_safe_text(page)`
on whatever page was currently loaded (the Add Product form), then passed that text to
`analyze_dashboard_text`. The Add Product form contains the brand name (in form fields),
so `_brand_windows` matched it. The form contains no "Under review" keyword, so
`STATUS_PATTERNS` fell through and returned `unknown` — but if any window text contained
"draft" (e.g. from the "Your listing details are saved in your draft" toast), it returned
`draft`.

**Fix** (`src/case_dashboard_checker.py`, `check_case_dashboard_for_brand`):
When `nav_ok=False`, set `result["status"] = "navigation_failed"` and return immediately
without calling `_safe_text` or `analyze_dashboard_text`. Callers treat `navigation_failed`
as unknown — the batch does NOT halt.

```python
if not nav_ok:
    result["error"] = f"dashboard_navigation_failed: {nav_note}"
    result["status"] = "navigation_failed"
    result["checked"] = True
    result["completed_at"] = datetime.now().isoformat()
    # save screenshot + json, then return
    return result
```

### Root cause 2 — GTIN Exemption prefix match (secondary, independent)

When a brand has **two** Dashboard rows:
- `MP-MALL` → Catalog Authorization → **Under review**
- `MP-MALL-Cell Phones & Accessories` → GTIN Exemption → **Draft**

The old `_brand_windows` did `text_lower.find(brand_lower)` (substring match). The GTIN
row name contains the brand name as a prefix, so both rows were captured in the same
window. `STATUS_PATTERNS` scanned the window and found "Draft" before "Under review"
(or found Draft in the GTIN row's section), returning `draft`.

**Fix** (`src/case_dashboard_checker.py`, `analyze_dashboard_text`):
Added `_parse_dashboard_rows(text)` which parses the structured `innerText` table (each
row = brand name line + application type line + status line). `analyze_dashboard_text`
now:
1. Parses all rows via `_parse_dashboard_rows`.
2. Filters to rows whose `name` **exactly equals** `brand_name` (case-insensitive) —
   no prefix/substring matching.
3. Prioritises **Catalog Authorization** rows over GTIN Exemption rows.
4. Within the winning type, sorts by status priority: `under_review` > `approved` >
   `draft` > `declined` > others.
5. Falls back to old `_brand_windows` path only if no structured rows are found
   (non-standard page layout).

### How to verify a fix worked

After submit, if `dashboard_result.json` shows `"status": "navigation_failed"`:
- This is correct behaviour — it means the Dashboard wasn't reachable immediately
  after submit (normal post-submit page state).
- The batch should NOT halt on `navigation_failed`.
- To get the real status, run `check_case_log.py` or check Dashboard manually.

If `dashboard_result.json` shows `"status": "draft"` and `"checked": true`:
- Verify `dashboard_navigation.ok == true` (nav succeeded).
- If nav succeeded and status is still draft, check whether the brand has a GTIN
  Exemption Draft row on Dashboard — that would be a real Draft, not a mis-read.
- Cross-check: open Dashboard manually and look at Catalog Authorization row only.

### Files changed
- `src/case_dashboard_checker.py` — `_parse_dashboard_rows()` added, `analyze_dashboard_text()` rewritten, `check_case_dashboard_for_brand()` early-returns on nav failure.

### Related edge cases in SKILL.md
- "Dashboard mis-reads brand as Draft when Catalog Authorization is Under Review"
- "Dashboard navigation ERR_ABORTED → false Draft verdict"
- "Phase 1.5 (ConnectBrand path) submits but never extracts Case ID"

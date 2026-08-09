# final_state.png Outcome Classification

When a brand is marked `failed` in the batch 执行摘要, always run
`vision_analyze` on `runtime/evidence/<date>/<account>/<brand>/submit_full/final_state.png`
before concluding failure or launching a retry. Three distinct visual states exist.

**Path format**: pass **Windows backslash paths** to `vision_analyze`
(`C:\Users\Admin\projects\amazon-5461-bot\runtime\evidence\...`).
MSYS forward-slash paths fail with "image file not found".

---

## State 1: Case ID visible ✅

**Visual**: Apply-to-sell panel shows application entry with clock icon, text
"Under review - Decision expected by N [Month] [Year]", and a **Case ID - NNNNNNNNNNN**
link with external-link icon.

**Action**: Record the Case ID. Success — no retry needed.

**Confirmed**: uShield BE 2026-08-01 (Case ID redacted in the portable reference).

---

## State 2: Under Review, no Case ID ✅

**Visual**: Apply-to-sell panel shows "Under review - Decision expected by N [Month]
[Year]" text and clock icon, but no Case ID link visible.

**Action**: Success. Record as "Under Review" in the report table. Do NOT retry.

**Confirmed**: uShield UK 2026-08-01; HOMEMO/WILLONE pattern across accounts 663–666.

---

## State 3: Submit button spinning ⏳ (needs tab check)

**Visual**: Apply-to-sell panel is open, all fields filled, 6 files uploaded,
email populated. The **Submit button shows a rotating spinner icon** (loading
animation) — submission was clicked but server response hadn't arrived when
the script's wait window expired.

**Action**:
1. Do NOT immediately retry — the submission was sent server-side.
2. Open the retained AdsPower tab for the brand and refresh/wait for the page
   to finish loading. It will typically resolve to Under Review or show a Case ID.
3. Only if the tab shows a blank/error/login page should you consider re-submitting.

**Confirmed**: JZG/VASG/WILLONE/JavoYion UK (2026-08-01); JZG/WILLONE BE (2026-08-01).

---

## State 4: Apply-to-sell panel open, no spinner, no Under Review ❌

**Visual**: Panel shows the upload form as if ready to fill, Submit button is
not spinning (either normal green or absent), no Under Review text, no Case ID.

**Possible causes**:
- Script never reached submit (upload timeout killed the flow before Submit click)
- Brand navigated to a fresh product listing (description page, no 5461 panel loaded)
- Login expired mid-batch

**Action**: Check the full log for context. If MONITOR shows `429=0` and the
description page path was reached, it's a "5461 panel never triggered" failure —
check Case Dashboard manually before rerunning.

---

## Triage recipe (batch of failures)

```python
# Run vision_analyze on all failed brands in parallel
brands = ['JZG', 'VASG', 'WILLONE', 'JavoYion']  # adjust per batch
date = '2026-08-01'
account = 'us_store_666'
for brand in brands:
    path = rf'C:\Users\Admin\projects\amazon-5461-bot\runtime\evidence\{date}\{account}\{brand}\submit_full\final_state.png'
    # vision_analyze(image_url=path, question='Under Review? Case ID? Submit spinning?')
```

Batch the `vision_analyze` calls in parallel (one tool call per brand, all in
the same assistant turn) — the runtime runs them concurrently, saving minutes
on a 5-brand failure set.

# Declined Recovery & Post-Submit Loop Edge Cases

## XDesign — Declined Case + New Application Fails (607US, 2026-06-12)

**Symptom:** Panel shows old Declined Case ID `<CASE_ID_REDACTED>`. DeclinedHandler clicks `>` successfully, enters `5461_FORM_OPEN` state, fields load. But after processing, Dashboard returns `not_found` — new Case ID never created.

**Root cause (suspected):** The new form opened from the Declined state but either:
- The Submit button was blocked/disabled silently, or
- The form submitted into another Declined/error state immediately.

**Resolution path:**
1. Open AdsPower profile for the account manually.
2. Navigate to Add Product → XDesign → Apply to sell.
3. Check whether the panel shows a new Draft, another Declined, or a blank new form.
4. If blank new form: fill and submit manually.
5. If new Declined immediately: the brand may be hard-blocked on this account — try a different account.

---

## JavoYion — Successful Submit but Close-Panel Loop Hangs (607US, 2026-06-12)

**Symptom:** Case ID `<CASE_ID_REDACTED>` extracted and confirmed Under Review. Script then entered close-panel recovery (triggered by 429 path). `kat-panel-wrapper[panel-visible="true"]` overlay intercepted all clicks on Apply to sell button. Playwright retried in a tight loop for 18+ minutes until killed manually.

**Log signature:**
```
[FormFiller] panel 内检测到 Under Review Case ID: <CASE_ID_REDACTED>，无需重新申请
[阶段 3/3] 429重试后 panel 仍半加载，执行 close-panel 多轮恢复...
...
<kat-panel-wrapper ... panel-visible="true"> intercepts pointer events
- retrying click action
```

**Fix needed in code:** After `Under Review Case ID` is logged, the flow must immediately `break` / `return success` and skip all subsequent close-panel and Apply-to-sell retry logic. The panel intercept is expected (panel is still open after success) — it is not a failure signal.

**Workaround used:** `process(action='kill')` to terminate the hung process, then re-ran only `uShield`.

---

## Batch Interactive Prompt Terminates Unattended Run

**Symptom:** When a brand hits 429/panel-shell failure, the batch script prints an interactive `Enter/r/q` prompt. In a background (unattended) run, no input arrives, the user's manual selection of `q` (quit) terminates the entire batch, leaving remaining brands as `pending`.

**Impact:** OUNNE, VASG, mocodi, JavoYion, uShield were left `pending` after XDesign triggered the prompt.

**Recommended fix:** Add `--non-interactive` flag to `run_full_5461_batch.py` that auto-skips failed brands instead of prompting. Default behavior for background/unattended runs should be skip-and-continue, not prompt-and-block.

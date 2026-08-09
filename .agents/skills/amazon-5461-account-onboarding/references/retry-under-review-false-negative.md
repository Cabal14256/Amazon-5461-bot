# Retry Under Review False-Negative Pattern

## Discovery: 2026-07-28 (account 660, UK batch, HOMEMO & JZG)

### Pattern

A brand's first submission goes through server-side (Amazon accepts it) but the
Case ID confirmation page never loads within the script's wait window, likely
due to 429 rate-limiting on the post-submit redirect. The brand is marked
`failed` in the 执行摘要 with:
```
✗ us_store_NNN / BRAND: failed
错误: 提交后未检测到成功提示，请人工检查
```

On retry, the script navigates to the product_identity page, the Apply to sell
panel opens, and **during form fill** the panel content reveals the existing
Under Review status with a valid Case ID:
```
[FormFiller] panel 内检测到 Under Review Case ID: NNNNNNNNNNN（品牌='BRAND'），无需重新申请
```

**However**, the batch script's 执行摘要 may still mark the brand as `failed`:
```
✗ us_store_NNN / BRAND: failed
错误: 到达 Description 页面，Product Identity 触发后仍未检测到 5461 入口
```

### Root cause

The Under Review detection fires during form fill when the panel content loads
and reveals the existing case. But the script's flow then continues past the
panel check — it navigates to the Description page, doesn't find a 5461 entry
(because the brand is already Under Review and the normal entry path is blocked),
and logs the "Description page" failure path. The Under Review Case ID was
correctly detected but never persisted to the brand's completion state.

### What to do when you see this

1. **Grep for `panel 内检测到 Under Review Case ID` in the retry log.**
   If it appeared, that Case ID is valid — include it in the final report.
2. **Ignore the 执行摘要 `失败` for that brand** — it's a false negative.
3. **Report the Case ID** with note "Under Review (retry 检测)" in the table.
4. Do NOT launch a second retry.

### Distinct from 429 close-panel recovery Under Review detection

- **429 close-panel path** (correctly handled): detected during 429 retry loop
  inside the same batch run, marked `completed`. Log: `[5461Panel] ✅ panel 内已检测到`
  followed by `[阶段 3/3] ✅ 429重试 recover`.
- **This pattern** (false negative): detected during retry form fill (not 429
  recovery), incorrectly marked `failed`. Log: `[FormFiller] panel 内检测到`
  followed by Description page fallback error.

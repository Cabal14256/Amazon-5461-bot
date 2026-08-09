# BRAND_SELECTION Failure Pattern

## When it fires

During Add Product flow, after clicking Product Identity to trigger the authorization panel, the page enters `BRAND_SELECTION` state but the script cannot locate either:
- The 5461 form (`kat-panel-wrapper` with visible content), or
- The `Apply to sell` button

## Log signature

```
[DEBUG] has_select_brand_text: True, has_brand_warning: True
[DEBUG] 检测到品牌选择弹窗（有品牌选项描述）
[阶段 2/3] Product Identity 触发后页面状态: BRAND_SELECTION
[阶段 2/3] Product Identity 触发后 Apply 检测: {'found': False, 'hasApprovalText': True, 'buttonText': ''}
[警告] 到达 Description 页面但未检测到 5461 表单或 Apply to sell 按钮。
[清理] 执行失败（uncertain），保留标签页用于调试...
>> [非交互模式] stdin EOF，自动跳过，继续下一个品牌
```

## Known affected brands (examples)

- **JZG** (610US, 2026-06-15) — keywords `['screen protector', 'phone case', 'protective']` present but not matched
- **VASG** (610US, 2026-06-15) — keywords present but not matched

Both resolved on first retry run.

## Recovery steps

1. **Check Dashboard first** — run `check_case_log.py <account> <brand> --site US` to confirm no Draft/Case was silently created.
2. **If Dashboard = not_found** — re-run the batch for just the failed brands:
   ```bash
   ./.venv/Scripts/python scripts/run_full_5461_batch.py --accounts <N> --brands "JZG,VASG" --site US
   ```
3. **If retry also fails** — open AdsPower manually, inspect the radio button labels shown on the brand selection page, and update `brand_packs/<BRAND>/manifest.json` `brand_selection_keywords` with a more precise match string.

## Root cause hypothesis

The `BRAND_SELECTION` state detection (`has_select_brand_text`) fires when the page text contains brand-selection-related phrases, but the actual radio button rendering may be delayed or the keyword match is too broad/narrow. A retry usually resolves it because the page re-renders cleanly without the residual state from the previous brand's panel.

## Distinction from 429

Unlike 429 (Shadow DOM elements not found, screenshot timeout 30s), BRAND_SELECTION failure:
- Page text IS loaded (length ~511k chars)
- `has_select_brand_text: True` — content is there
- Issue is specifically with radio button/Apply detection, not page load failure
- No CloudFront chunk errors in console logs

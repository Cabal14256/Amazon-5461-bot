# Same-tab Reuse Silent Bug — `page_ready_for_reuse` Dropped (2026-06-23)

## Symptom

Every brand opens a fresh tab even after a successful submission. Logs show:

```
[清理] 面板已关闭，页面就位可复用（url=...）   ← flow side: ready=True
[页面] JZG 页面已关闭（已确认成功/Case ID）     ← batch side: still closing
[页面] OUNNE — 新建独立标签页（context 内共 3 个）
```

`batch_state.json` shows `page_ready_for_reuse: null` for every item.

## Root Cause

`run_single_item()` in `scripts/run_full_5461_batch.py` (~line 509) calls
`submit_5461_from_add_product()` and then **reconstructs a new `result` dict**
from the flow's return value. The original dict had the correct
`page_ready_for_reuse` / `page_reuse_reason` keys set by `reset_page_for_next_brand()`,
but neither field was copied into the reconstructed dict.

Downstream in the batch loop (~line 754):

```python
page_reusable = bool(result and result.get("page_ready_for_reuse"))
if close_page and page_reusable:
    reuse_page = fresh_page          # ← never reached
elif close_page:
    fresh_page.close()               # ← always taken
```

`page_reusable` was permanently `False`, killing the optimisation for every run.

## Fix

In `run_single_item`, add the two missing fields to the reconstructed dict:

```python
result = {
    ...
    "marketplace_switched": bool(result.get("marketplace_switched")),
    "actual_marketplace":   result.get("actual_marketplace"),
    "page_ready_for_reuse": result.get("page_ready_for_reuse"),   # ← ADD
    "page_reuse_reason":    result.get("page_reuse_reason"),       # ← ADD
}
```

Fix applied 2026-06-23.

## Verification

After the fix, successful brands should log:

```
[页面] JZG 页面保留供下一品牌复用
[页面] OUNNE — 复用上一品牌标签页（跳过 goto）
```

And `batch_state.json` items will have `page_ready_for_reuse: true`.

## General Pattern Warning

Any time `run_single_item` or a similar wrapper rebuilds a `result` dict by
cherry-picking keys, it risks silently dropping fields that downstream logic
depends on. When adding new return fields to the flow, **always check whether
`run_single_item`'s dict needs to forward them too**.

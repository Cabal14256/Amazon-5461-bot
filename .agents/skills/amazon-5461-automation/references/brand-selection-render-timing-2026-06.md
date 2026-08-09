# Brand Selection Render Timing Bug — 2026-06-15

## Affected brands / accounts
- 613US: MP-MALL
- 613MX: V-PORYADKU
- Potentially any brand that triggers the "Clarify which brand" / ConnectBrand flow

## Symptom
Log shows:
```
[品牌选择] 弹窗 radio 已出现 (11 个)，等待 1s
[品牌选择] 查找弹窗内的品牌选项...
[品牌选择] 弹窗容器: none, 找到 0 个品牌选项 (页面共 11 个 radio)
[品牌选择] 未找到品牌选项
    错误: 品牌选择失败
```

Compare with a successful brand (JZG, same session):
```
[品牌选择] 弹窗 radio 已出现 (11 个)，等待 1s
[品牌选择] 弹窗容器: none, 找到 2 个品牌选项 (页面共 13 个 radio)
  [0] (kat-box-brand) text='JZG designs and manufactures premium tempered-glass...'
```

## Root cause

Two independent layers:

### Layer 1 — `dialogContainer` is always null
The code searches for brand-selection modal via `kat-modal` text content
(`clarify which brand`, `connect this brand`, etc.) and `[role="dialog"]`.
Amazon's actual brand-selection popup does NOT use these — it renders
`kat-box[data-testid="brand-info-option"]` elements directly in the page body,
not wrapped in any detectable modal. So `dialogContainer` is always `null`.

This means **all brands** go through the global fallback path (line ~977),
not the dialogContainer path. JZG works because the fallback finds
`kat-box[data-testid="brand-info-option"]` elements globally.

### Layer 2 — `kat-box` renders after `input[type="radio"]`
The existing wait loop polls for `input[type="radio"]` count > 0.
Amazon renders the attribute-filter radios (Required / Recommended / All)
before the brand `kat-box` elements. So the loop exits when it sees 11 radios
(all attribute-filter), but `kat-box[data-testid="brand-info-option"]` is still
being rendered.

JZG succeeded because by the time its flow ran, the kat-box had already loaded
(earlier in the batch, more time had passed). MP-MALL and V-PORYADKU ran later
with less warm-up, hitting the race condition.

### Layer 3 — global radio fallback picks up UI radios (secondary)
When kat-box count is 0, the global `input[type="radio"]` fallback was
NOT filtering radios whose `aria-label` is a generic UI word
(`"Required"`, `"Recommended"`, `"All attributes"`, `"Irrelevant attribute"`).
These would appear as "valid" options and the brand-name fallback matcher
would pick option[0] — wrong brand.

## CDP inspection (live session, 2026-06-15)
```json
{
  "radios": [
    {"name": "attribute_filter_radio_buttons-required", "aria": "Required"},
    {"name": "attribute_filter_radio_buttons-recommended", "aria": "Recommended"},
    {"name": "attribute_filter_radio_buttons-all", "aria": "All attributes"},
    {"name": "undefined", "aria": "Irrelevant attribute"},
    ...
  ],
  "modal_count": 5,
  "dialog_count": 6,
  "brand_boxes": 1
}
```
The 1 brand_box was from a prior brand's residual page — confirmed 0 during
actual batch execution.

## Fix applied (flow_submit_5461.py)

### 1. Additional kat-box wait loop (after the radio wait)
```python
# After the existing radio wait loop:
for _box_wait_i in range(10):
    _box_count = page.evaluate('''() => {
        return document.querySelectorAll('kat-box[data-testid="brand-info-option"]').length;
    }''')
    if _box_count > 0:
        print(f"[品牌选择] brand-info-option kat-box 已出现 ({_box_count} 个)，等待 {_box_wait_i}s")
        break
    time.sleep(1)
else:
    print("[品牌选择] 等待 10s 后 kat-box brand-info-option 仍为 0，继续尝试...")
```

### 2. aria-label blocklist in global radio fallback
```python
ariaLabel = (inp.getAttribute('aria-label') || '').trim().toLowerCase();
const uiLabels = ['required', 'recommended', 'all attributes',
                  'irrelevant attribute', 'value missing', 'relevant attribute'];
if (uiLabels.indexOf(ariaLabel) >= 0) { return; }
```

## Verification
After fix, re-run with the same brands. Expect log:
```
[品牌选择] brand-info-option kat-box 已出现 (N 个)，等待 Xs
[品牌选择] 弹窗容器: none, 找到 N 个品牌选项 (页面共 M 个 radio)
[品牌选择] 匹配成功 (...): [0] kat-box-brand
```

## Notes
- `dialogContainer` being null is NOT a bug for fixed — the global kat-box
  path works correctly when timing is right. The modal detection code is
  dead code for the current Amazon UI. Could be removed in a future cleanup.
- If kat-box wait still hits 0 after 10s, the popup may not have loaded
  at all (429 / CloudFront chunk failure). In that case, check console_logs
  for 429 errors and treat as a 429 failure, not a brand-selection failure.

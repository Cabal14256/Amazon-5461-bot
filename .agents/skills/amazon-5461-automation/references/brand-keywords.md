# Brand Selection Keywords Reference

> Last updated: 2026-06-01
> 当前规则：品牌选择关键词应写入 `brand_packs/{brand}/manifest.json` 的 `brand_selection_keywords` 字段；旧 `ai_analyzer.py` 不在当前项目树。

## Overview

Some brands trigger a "Clarify which brand" page with radio button options after clicking "Apply to sell" or during the 5461 process. The automation must match the correct brand using keywords from the brand文案.

## Brand Keyword Mapping

> 下表是历史/示例关键词。当前执行以各品牌 `brand_packs/{brand}/manifest.json` 中的 `brand_selection_keywords` 为准。

| Brand | Keywords for Matching |
|-------|----------------------|
| JZG | protective phone cases, screen protectors, Samsung, Google Pixel, Apple iPhone |
| MP-MALL | phone and tablet accessories, screen protectors, phone mounts, camera lens covers, smart watch protectors |
| XDesign | protective cases, battery packs, chargers, cables |
| OUNNE | Cell phone film, lens film, mobile phone film |
| VASG | phone cases, screen protectors, smartwatch, 手机保护壳, 手机保护膜, 手表保护膜 |

## How Brand Selection Works

1. When the "Clarify which brand" page appears, Amazon shows a list of radio buttons with brand options
2. Each option contains descriptive text about the brand's product category
3. The automation reads `brand_selection_keywords` from `brand_packs/{brand}/manifest.json`
4. It matches these keywords against the visible radio button options
5. Clicks the radio button with the highest keyword match score
6. Then clicks "Connect this brand" to proceed

## Selector for Brand Selection

Current precise selectors used for Connect brand flows:

```python
# Brand option row
'kat-box[data-testid="brand-info-option"]'

# Brand description content
'div[data-testid="brand-description-content"]'

# Radio input inside option row
'input[type="radio"][name="brand-record-selection-radio-button"]'

# Connect button
'kat-button[data-testid="connect-brand-button"]'
```

## Adding New Brands

When adding a new brand that requires selection:

1. Test the brand manually first to see if it triggers the selection page
2. Note the descriptive text shown for that brand's radio option
3. Add the brand and its keywords to `brand_packs/{brand}/manifest.json`:
   ```json
   "brand_selection_keywords": ["screen protector", "phone case"]
   ```
4. Keep the table above only as human-readable reference if useful.

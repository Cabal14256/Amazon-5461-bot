# Page Adapter Reference

> 2026-06-01 status note: This is historical design/reference material. The active project tree currently does not contain src/page_adapter.py; current behavior lives mainly in src/flow_submit_5461.py, src/form_filler.py, and knowledge/state files.

> 历史位置: `projects/amazon-5461-bot/src/page_adapter.py`
> 当前状态: 文件不在当前项目树；当前页面适配主要在 `src/flow_submit_5461.py`、`src/form_filler.py`、`knowledge/pages/*`。
> 最后更新: 2026-04-12

## 问题背景

不同 Amazon 账号的 Add Product 页面 URL 不同 (`displayPath` 参数差异)，导致:
- Item Type Keyword 下拉框状态不同
- 页面元素可能略有差异

## 解决方案

### 智能页面适配器

```python
from src.page_adapter import PageAdapter

adapter = PageAdapter(page)

# 分析页面结构
structure = adapter.analyze_page_structure()
# {
#     "is_product_identity_page": True,
#     "item_type_keyword_filled": False,
#     "brand_field_present": True,
#     ...
# }

# 获取执行策略
strategy = adapter.get_page_strategy()
# {
#     "need_fill_brand": True,
#     "need_select_item_type_keyword": True,
#     "need_check_no_product_id": True,
#     ...
# }

# 智能选择 Item Type Keyword
success, msg = adapter.smart_fill_item_type_keyword(
    keywords=["screen protector", "screen protectors", "protector"]
)
```

## 智能选择逻辑

匹配优先级:
1. **精确匹配**: "screen protector"
2. **复数匹配**: "screen protectors"
3. **模糊匹配**: "protector"

## 不同 Entry URL 的处理

| 场景 | 处理方式 |
|------|----------|
| Item Type Keyword 已预填充 | 跳过选择步骤 |
| Item Type Keyword 为空 | 智能匹配并选择 |
| No Product ID 已勾选 | 跳过勾选步骤 |
| 页面结构差异 | 自动检测可用选择器 |

## 使用示例（历史示例，不可直接执行）

> 当前项目树不存在 `scripts/verify_brands_batch_v2.py`。当前安全诊断入口是 `scripts/diagnose_no_submit_add_product.py`。

```python
# 历史示例，不可直接执行：当前项目树不存在 scripts/verify_brands_batch_v2.py。
python scripts/verify_brands_batch_v2.py us_store_517 BRAND_A BRAND_B  # 历史示例，不可直接执行：当前不存在

# 输出:
# [1/3] 验证品牌: BRAND_A
#   🔍 分析页面结构...
#      页面类型: product_identity
#      Item Type Keyword: 需选择
#      No Product ID: 需勾选
#   ✏️ 填写 Brand: BRAND_A
#   ☑️ 勾选 No Product ID...
#   🔽 选择 Item Type Keyword...
#      ✓ 已选择: Electronics > ... > Screen Protectors
#   ➡️ 点击 Next...
#   ✅ PASS: BRAND_A
```

## 回退机制

如果智能适配失败:
1. 截图保存到 `evidence/`
2. 提交 AI 分析 (如果启用 LLM)
3. 根据风险等级决定处理方式


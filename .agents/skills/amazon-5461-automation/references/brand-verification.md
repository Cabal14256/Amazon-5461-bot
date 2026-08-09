# Brand Verification / No-submit Diagnostic Reference

> 2026-06-01 status note: The old `verify_brand.py` and `scripts/smoke_verify_add_product.py` entry points are not present in the active tree. Current safe Add Product validation uses `scripts/diagnose_no_submit_add_product.py` unless/until a new verification CLI is restored.

> 位置: `projects/amazon-5461-bot/`
> 最后更新: 2026-04-12

## 流程概述

Add Product 页面验证品牌是否可售:

```
Add Product 页面
    ↓
填写 Item Name
    ↓
填写 Brand Name
    ↓
勾选 "No Product ID"
    ↓
选择 Item Type Keyword (如需要)
    ↓
点击 Next
    ↓
结果判断:
    - PASS: 进入产品详情页
    - FAIL: 显示 Brand Authorization Required
```

## 关键选择器

### Add Product 表单

```yaml
# Item Name
selector: 'kat-textarea[name="item_name-0-value"]'
type: Shadow DOM

# Brand Name
selector: 'kat-input[name="brand-0-value"]'
type: Shadow DOM

# No Product ID 复选框
selector: 'kat-checkbox:has-text("This product does not have a Product ID")'
type: Shadow DOM

# Next 按钮
selector: 'kat-button#next-button'
type: Shadow DOM
```

### 结果判断

```yaml
# PASS - 进入产品详情页
url_contains: "/description"

# FAIL - 品牌授权失败
selector: 'kat-alert:has-text("Brand Authorization Required")'
button: 'kat-button:has-text("Apply to sell")'
```

## 页面复用优化

当多个品牌验证失败时，复用同一页面只修改 Brand Name:

```
[1/3] BRAND_A → FAIL → 关闭页面 → 新开页面
[2/3] BRAND_B → FAIL → 复用页面 → 只改 Brand
[3/3] BRAND_C → FAIL → 复用页面 → 只改 Brand
```

## 执行脚本

### 安全诊断（当前推荐）

```bash
.\.venv\Scripts\python.exe scripts/diagnose_no_submit_add_product.py --account us_store_530 --brand OUNNE --site US
```

### 账号/profile/brand pack 预检查

```bash
.\.venv\Scripts\python.exe scripts/audit_account_config.py us_store_530 --brand OUNNE --site US
```

### 批量 dry-run

```bash
.\.venv\Scripts\python.exe scripts/run_full_5461_batch.py --accounts us_store_530 --brands OUNNE,WILLONE --site US --dry-run
```

> 历史说明：旧 `verify_brand.py` / `scripts/smoke_verify_add_product.py` 不在当前项目树。当前“验证”以 no-submit 安全诊断为准。

## 结果输出

```json
{
  "account_id": "us_store_530",
  "brand_name": "OUNNE",
  "status": "PASS",
  "message": "品牌验证通过",
  "screenshot": "evidence/us_store_530/OUNNE/verify/2026-04-10_16-30-00.png"
}
```

## 常见问题

### Brand Name 填写不生效

原因: JavaScript 直接设置 `input.value` 不会触发 Amazon 的表单验证。

解决: 使用逐字符输入 (`fill_char_by_char`) 或触发 `blur` 事件。

### Next 按钮被禁用

原因: 表单验证未通过。

检查项:
- Item Name 是否填写
- Brand Name 是否正确填写并触发验证
- No Product ID 是否勾选

### 页面未跳转

原因: 点击 Next 后页面未变化。

排查:
1. 查看截图确认当前页面状态
2. 检查是否有错误提示
3. 确认选择器是否匹配


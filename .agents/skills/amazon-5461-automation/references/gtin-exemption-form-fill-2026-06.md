# GTIN Exemption弹窗填写修复 (2026-06-21)

## 问题描述

某些品牌（如 uShield MX）在 Apply to sell 时，Amazon 弹出的不是标准 Catalog Authorization 表单（product_title/manufacturer/description/email），而是 **GTIN Exemption Request** 弹窗。

弹窗标题示例：`GTIN Exemption Request for Electrónicos and uShield`

该弹窗只有**一个必填字段**：`* Product Name`

旧脚本完全没有处理这个路径，导致：
- 提交时出现 "Please enter the missing information." 验证错误
- Submit 按钮灰色/不可用
- 表单留 Draft 状态，没有 Case ID

## 发现过程

- 620MX 批次：uShield 两次失败，error = "提交后未检测到成功提示，请人工检查"
- `03_before_submit.png` 截图确认：弹窗标题是 GTIN Exemption，Product Name 字段有红色边框（空）
- 其他品牌（mocodi/VASG/MP-MALL）走的是 Catalog Authorization 表单，没有此问题
- `check_page_state()` 检测到 "GTIN Exemption" 文字 → 返回 `GTIN_EXEMPTION` 状态
- 主流程遇到 `GTIN_EXEMPTION` → 认为弹窗未打开 → 等30秒重试 → 重试后弹窗依然是 GTIN Exemption → 死循环或最终 failed

## 修复方案

### 1. `fill_5461_form` 开头加 GTIN Exemption 检测

```python
# 在 fill_5461_form 最开头（SELECTORS 定义之前）
panel_text = page.evaluate(r"""() => {
    const panel = document.querySelector('kat-panel-wrapper[panel-visible="true"]')
               || document.querySelector('kat-panel-wrapper[data-testid*="QualificationWidget"]');
    return panel ? (panel.innerText || panel.textContent || "") : "";
}""")
if "GTIN Exemption" in (panel_text or "") or "GTIN Exemption" in page_body_text:
    # 走 GTIN Exemption 单字段路径
    ...
    return result  # 早退，不进入 cat_auth_mo 路径
```

Product Name 值来源：从 `statement_text` 的 `Item name：` 行提取，回退到 `{brand_name} Screen Protector`

Product Name 字段 selector 优先级：
1. `kat-input[label="Product Name"]`
2. `kat-input[id*="product_name"]`
3. `kat-input[data-cy*="product_name"]`
4. `kat-input[placeholder*="Product Name"]`
5. 通用回退：面板内第一个 `kat-input`（JS 直接 set value + dispatch events）

### 2. 主流程 GTIN_EXEMPTION 状态处理

两处 GTIN_EXEMPTION block（~line 3283 和 ~line 3716）均改为：

```python
# 先检查弹窗是否已打开
panel_text = page.evaluate(r"""() => {
    const p = document.querySelector('kat-panel-wrapper[panel-visible="true"]');
    return p ? (p.innerText || p.textContent || "") : "";
}""")
if "GTIN Exemption" in (panel_text or ""):
    # 弹窗已打开，直接进入填写（fill_5461_form 处理）
    state['has_5461_form'] = True
    state['page_type'] = '5461_FORM_OPEN'
else:
    # 弹窗未打开（真正的 410001 情况），等待重试
    time.sleep(30)
    retry_result = filler.click_apply_to_sell()
    ...
```

## 已知 GTIN Exemption 触发品牌

| 账号 | 品牌 | 站点 | 弹窗标题 |
|------|------|------|---------|
| us_store_620 | uShield | MX | GTIN Exemption Request for Electrónicos and uShield |

## 注意事项

- GTIN Exemption 弹窗里**没有** email/phone 字段，只有 Product Name + 文件上传 + 复选框
- 图片上传 selector 与 cat_auth_mo 路径相同：`input[id*="document_upload"][id*="document_input"]`
- 复选框处理与 cat_auth_mo 路径相同
- Submit 按钮 selector 与 cat_auth_mo 路径相同：`kat-button#submit_button`
- 提交后 Case ID 提取逻辑不变（90s 轮询）

## Dashboard 误判问题

Dashboard 上 GTIN Exemption 申请会产生**两条记录**：
- `<BRAND>` → Catalog Authorization（正常申请）
- `<BRAND>-Cell Phones & Accessories` → GTIN Exemption（可能是 Draft）

旧代码前缀匹配会误命中 GTIN Exemption 的 Draft 行返回 `draft`，实际 Catalog Authorization 已 Under review。
`case_dashboard_checker.py` 已修复此问题（exact brand name match + Catalog Authorization 优先）。

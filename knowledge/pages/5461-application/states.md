# 5461 Application 页面状态机

> 页面: 5461 申请表单
> URL 模式: /sq/approvalrequest, /seller-qualification, /listing/approval
> 最后更新: 2026-08-14

---

## state: application_type_selection
- **识别信号**: 右侧 Apply to sell 面板包含 `Application to create new ASINs for <当前品牌>`
- **检测方法**:
  - element: 可见 `kat-panel-wrapper`
  - text: 精确包含当前品牌的 `Application to create new ASINs for ...`
  - text: 同时存在 `Application to sell ... products` 也不影响本状态
- **推荐动作**: 只点击当前品牌的 create-new-ASINs 卡片；等待真实 5461 字段出现
- **下一状态列表**: form_loading, form_ready, application_type_unknown
- **优先级**: 100 (业务状态)

---

## state: application_sell_only
- **识别信号**: 面板只有 `Application to sell <品牌> products`，没有 create-new-ASINs 卡片
- **推荐动作**: 不自动点击，停止当前品牌并要求人工确认创建新 ASIN 的申请路径
- **下一状态列表**: done (human review)
- **优先级**: 100 (业务状态)

---

## state: application_type_unknown
- **识别信号**: 面板出现其他无法识别的 `Application to...` 组合
- **推荐动作**: 不点击；保存截图和 DOM 摘要，记录 `state_unknown` incident
- **下一状态列表**: done (human review)
- **优先级**: 800 (系统异常)

---

## state: form_loading
- **识别信号**: 表单还在加载
- **检测方法**:
  - url: 包含 `/sq/approvalrequest` 或 `/seller-qualification`
  - element: 页面中 kat-input 数量 < 3（表单字段尚未加载）
  - text: 页面含 "Loading" / "Please wait" /  spinner 图标
- **推荐动作**: 等待 3-5s，重新采集证据
- **下一状态列表**: form_ready, error_429, blank_page, login_expired
- **优先级**: 100 (业务状态)

---

## state: form_ready
- **识别信号**: 表单已加载，字段可填写（有 kat-input）
- **检测方法**:
  - url: 包含 `/sq/approvalrequest` 或 `/seller-qualification`
  - element: 存在 kat-input 元素（至少 3 个）
  - element: 存在 kat-button（Submit 或类似按钮）
  - text: 页面含 "Listing approval" / "Brand Authorization" / "Apply to sell"
- **推荐动作**: 按 field-map.json 填写表单字段 -> 上传文件
- **下一状态列表**: form_filled, error_410001, error_429
- **优先级**: 100 (业务状态)

---

## state: form_filled
- **识别信号**: 字段已填写，文件已上传，Submit 可点击
- **检测方法**:
  - element: 所有 required 字段的 kat-input 都有 value
  - element: Submit 按钮 disabled=false
  - text: 页面不含 "required" 验证错误提示
- **推荐动作**: 点击 Submit 按钮
- **下一状态列表**: submit_clicked, case_created, case_declined, server_error_on_submit
- **优先级**: 100 (业务状态)

---

## state: submit_clicked
- **识别信号**: 已点击提交，等待响应
- **检测方法**:
  - element: Submit 按钮显示 loading / spinner / disabled
  - text: 页面含 "Submitting" / "Please wait" / "Processing"
- **推荐动作**: 等待 5-10s，重新采集证据
- **下一状态列表**: case_created, case_declined, server_error_on_submit, error_429
- **优先级**: 100 (业务状态)

---

## state: case_created
- **识别信号**: Case ID 已出现在页面
- **检测方法**:
  - text: 页面含 "Case ID" / "case id" / "submitted" / "received" / "success"
  - text: 提取 Case ID 格式（通常为大写字母+数字组合，如 AB-1234567890）
- **推荐动作**: 提取并记录 Case ID -> 标记该品牌为已提交
- **下一状态列表**: done
- **优先级**: 100 (业务状态)

---

## state: case_declined
- **识别信号**: Declined + Case ID
- **检测方法**:
  - text: 页面含 "Declined" / "Rejected" / "Denied" + "Case ID"
  - text: 或页面含 "not approved" + "Case ID"
- **推荐动作**: 提取 Case ID -> 记录 declined -> 关闭弹窗 -> 等待后重试
- **下一状态列表**: form_ready, error_410001
- **优先级**: 100 (业务状态)

---

## state: server_error_on_submit
- **识别信号**: 提交后 "An error has occurred"
- **检测方法**:
  - text: 页面含 "An error has occurred" / "An error occurred" / "Something went wrong"
  - alerts: alert 元素出现错误提示
  - text: 不含 "Case ID" 或 "Declined"
- **推荐动作**: 等待 15s -> 刷新页面 -> 重新填写提交（最多 3 次）
- **下一状态列表**: form_loading, form_ready, error_410001
- **优先级**: 800 (系统异常)
- **是否终止**: 否 (可重试)

---

## 优先级规则

1. **系统异常状态** (优先级 >= 800) 优先于业务状态
2. **login_expired** (900) 为最高优先级，一旦匹配立即终止
3. 表单加载状态 (form_loading) 需先排除系统异常

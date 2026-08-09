# Add Product 页面状态机

> 页面: Add Product (Product Identity)
> URL 模式: /abis/listing/create/product_identity, /add-products, /product-type-chooser
> 最后更新: 2026-05-19

---

## state: add_product_start
- **识别信号**: URL 含 `/abis/listing/create/product_identity` 或 `/add-products`
- **检测方法**:
  - url: 包含 `product_identity` 或 `add-products`
  - text: 页面含 "Add a Product" / "I'm adding a product not sold on Amazon" / "product type"
  - element: 存在 Item Name / Brand 输入框
- **推荐动作**: 填写 Item Name -> Brand -> 勾选 No Product ID -> 选择 Browse Node
- **下一状态列表**: product_identity_filled, 5461_triggered, login_expired, error_410001, error_429, blank_page
- **优先级**: 100 (业务状态)

---

## state: product_identity_filled
- **识别信号**: 表单已填写完毕，Next 按钮变为可点击
- **检测方法**:
  - element: `kat-button[data-cy="next-button"]` 或类似 Next 按钮 disabled=false
  - text: 页面含已填写的 Item Name 和 Brand
- **推荐动作**: 点击 Next 按钮
- **下一状态列表**: already_approved, 5461_triggered, gtin_exemption, connect_brand, declined_case_shown, error_410001, error_429
- **优先级**: 100 (业务状态)

---

## state: 5461_triggered
- **识别信号**: 触发5461，页面有 "not approved" / "Apply to sell" / "5461" / "Brand Authorization Required"
- **检测方法**:
  - text: 页面含 "5461" / "not approved to create ASINs" / "Brand Authorization Required" / "Request approval" / "Apply to sell"
  - element: 出现 Apply to sell 按钮 (`kat-button[data-cy="seller-qualification-path-forward-button"]`)
- **推荐动作**: 点击 "Apply to sell" 或 "Request approval"
- **下一状态列表**: connect_brand, form_loading, declined_case_shown, already_approved, error_410001
- **优先级**: 100 (业务状态)

---

## state: gtin_exemption
- **识别信号**: 只需要 GTIN 豁免，无需品牌授权（页面状态 PRODUCT_IDENTITY_UPC_REQUIRED）
- **检测方法**:
  - text: 页面含 "GTIN exemption" / "UPC exemption" / "Product ID exemption" / "You do not have a Product ID"
  - text: 不含 "Brand Authorization" / "5461" / "not approved"
  - element: 出现 GTIN exemption 相关 checkbox 或链接
- **推荐动作**: 勾选 "This product does not have a Product ID" -> 选择 exemption reason -> 继续
- **下一状态列表**: product_identity_filled, already_approved
- **优先级**: 100 (业务状态)

---

## state: connect_brand
- **识别信号**: 弹出品牌选择（brex-widget，多个品牌单选）
- **检测方法**:
  - element: `div[data-cy="brex-widget"]` / `kat-radiobutton` / `input[type="radio"][name="brand-record-selection-radio-button"]`
  - text: 页面含 "Clarify which brand" / "connect this brand" / "multiple brands found"
- **推荐动作**: 按品牌名匹配 radio -> 点击 "Connect this brand"
- **下一状态列表**: form_loading, declined_case_shown
- **优先级**: 100 (业务状态)

---

## state: already_approved
- **识别信号**: 直接跳转到 description 页面，无需5461
- **检测方法**:
  - url: 包含 `/description` 或 `/listing/create/description`
  - text: 页面含 "Description" / "Product Description" / "Key Product Features"
  - text: 不含 "not approved" / "Brand Authorization" / "5461"
- **推荐动作**: 记录 "已有权限"，跳过该品牌
- **下一状态列表**: done
- **优先级**: 100 (业务状态)

---

## state: declined_case_shown
- **识别信号**: 弹窗显示 Declined + Case ID
- **检测方法**:
  - text: 页面含 "Declined" / "Rejected" / "Denied" + "Case ID"
  - text: Case ID 附近 30 字符内含 declined / rejected / denied
  - element: 出现关闭弹窗按钮或确认按钮
- **推荐动作**: 记录 Case ID -> 关闭弹窗 -> 随机等待 30-60s -> 重新点击 Apply to sell
- **下一状态列表**: 5461_triggered, form_loading, error_410001
- **优先级**: 100 (业务状态)

---

## state: login_expired
- **识别信号**: 重定向到 /signin
- **检测方法**:
  - url: 包含 `/signin` 或 `/ap/signin`
  - text: 页面含 "Sign in" / "Sign In" / "Login" / "password"
  - element: 出现 email/密码输入框
- **推荐动作**: 暂停，通知人工处理
- **下一状态列表**: (terminal)
- **优先级**: 900 (系统异常 - 最高)
- **是否终止**: 是

---

## state: error_410001
- **识别信号**: 页面有 "410001"
- **检测方法**:
  - text: 页面含 "410001"
  - alerts: alert 文本含 "410001"
- **推荐动作**: 等待 30-60s 后刷新重试，连续 3 次则暂停该品牌
- **下一状态列表**: add_product_start, login_expired, blank_page
- **优先级**: 800 (系统异常)
- **是否终止**: 否 (可重试)

---

## state: error_429
- **识别信号**: console/页面有 "429" / "Too Many Requests"
- **检测方法**:
  - text: 页面含 "429" / "Too Many Requests" / "rate limit" / "Request was throttled"
  - console: console 日志含 "429"
- **推荐动作**: 品牌间冷却 30s，降低并发频率
- **下一状态列表**: add_product_start
- **优先级**: 800 (系统异常)
- **是否终止**: 否 (可重试)

---

## state: blank_page
- **识别信号**: bodyText 长度 < 2000
- **检测方法**:
  - body_length: < 2000
  - text: bodyText 几乎为空或只有少量脚本
- **推荐动作**: 等待 10s 刷新，连续 3 次则暂停
- **下一状态列表**: add_product_start, login_expired, server_error
- **优先级**: 800 (系统异常)
- **是否终止**: 否 (可重试)

---

## state: server_error
- **识别信号**: Amazon 服务端错误（"An error occurred"）
- **检测方法**:
  - text: 页面含 "An error occurred" / "An error has occurred" / "We're sorry" / "Something went wrong"
  - text: 不含具体错误码（如 410001）
- **推荐动作**: 等待 15s 刷新，连续 3 次则暂停
- **下一状态列表**: add_product_start, login_expired, blank_page
- **优先级**: 800 (系统异常)
- **是否终止**: 否 (可重试)

---

## 优先级规则

1. **系统异常状态** (优先级 >= 800) 优先于业务状态
2. **login_expired** (900) 为最高优先级，一旦匹配立即终止
3. **blank_page** 需排除 login_expired 后再判断
4. 同一优先级下，按状态定义顺序匹配

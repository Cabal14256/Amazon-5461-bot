这是一个用于 Amazon Seller Central 品牌验证和 GTIN Exemption（5461）申请的自动化工具。

项目使用 Python、Playwright、AdsPower 和 SQLite，实现以下功能：

管理 Amazon 账号、站点和品牌批次。
通过 AdsPower 浏览器配置文件访问 Seller Central。
自动填写品牌验证、Product Identity 和 GTIN Exemption 申请表。
提取并记录 Case ID、申请状态、提交时间和错误信息。
保存截图、运行日志和批次状态，便于人工复核和失败重试。
对 410001、429 等限流或风控响应进行受控重试。
没有明确 Case ID 时保留浏览器标签页，不自动关闭，避免丢失人工复核证据。
修改代码时应优先保持现有流程、状态记录、安全限速和证据保留规则，不要泄露账号凭据、Cookie、Token 或其他敏感信息。

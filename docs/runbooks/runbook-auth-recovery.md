# Seller Central 强制登出安全续跑

本流程适用于普通 5461 提交、自动重新申请、Case ID 找回和 Case 跟进。
系统只检测登录状态并暂停/恢复任务，不会填写账号密码，也不会绕过 CAPTCHA、
2FA、账户风控或其他平台控制。

## 自动暂停规则

- 检测到登录过期、CAPTCHA、2FA、账户风控或无法识别的认证页后，立即停止刷新、
  导航和提交动作。
- Playwright 与浏览器断开，但准确的 AdsPower profile 和当前登录页面保持打开。
- 同一 profile 的普通提交、重新申请和 Case 跟进全部暂停；其他账号继续调度。
- 阻塞状态保存在 `account_auth_blocks`，提交边界保存在
  `submission_checkpoints`，服务和浏览器重启不会丢失。

## 提交前与提交后的区别

提交按钮点击派发前，系统先持久化 `submit_click_fenced_at`。

- 没有安全边界：状态为 `waiting_login`。登录恢复后只继续当前账号、站点、品牌，
  多品牌批次跳过已完成品牌，不推进下一站。
- 已有安全边界：状态为 `waiting_reconciliation`。系统禁止再次点击提交，10 分钟后
  首次核对 Selling Applications，此后每 30 分钟核对一次，最多 12 次。
- 已取得 Case ID：只暂停 Case 跟进，恢复后继续查询原 Case。
- Draft、长期未找到、多条记录歧义或结果冲突转入 `manual_review`，不自动重投。
- 重新申请只有在当前站得到明确 `declined` 后才推进；有效通过立即结束，最后一站拒绝
  以 `route_exhausted` 结束。

## 前端处理

顶部“账号需要重新登录”徽标和“待人工处理”页每 15 秒刷新。登录恢复卡片显示账号、
站点、品牌、脱敏 profile、阻塞类型、发生阶段、检测时间、下次自动检查和证据入口。

Reviewer/Admin 可以：

1. 点击“打开 AdsPower 登录”，在保留页面中人工完成登录/CAPTCHA/2FA/风控处理。
2. 点击“验证并继续”；若是提交后中断，按钮显示“验证并开始结果确认”。

Viewer/Operator 只能查看。所有按钮操作写入 `web_audit_events`。后端每 5 分钟做一次
被动登录检查；只有确定 Seller Central 已恢复登录，才执行与提交安全边界相符的动作。

相关 API：

- `GET /api/auth-blocks`
- `GET /api/auth-blocks/{id}`
- `POST /api/auth-blocks/{id}/open-profile`
- `POST /api/auth-blocks/{id}/verify-and-resume`

不要通过手工改库、删除检查点或重新运行提交命令来解除
`waiting_reconciliation`，这会破坏“不重复提交”的保证。

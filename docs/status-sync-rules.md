# 5461 状态同步规则

更新时间：2026-08-03

目标：统一 Evidence / Dashboard / batch_state / SQLite / Excel / ledger 的状态语义，避免同一品牌在不同文件里出现互相矛盾的状态。

---

## 状态优先级

1. **Case 详情最新 Amazon 回复正文** 优先级最高；Dashboard 可能仍显示 `Under review`。
2. **Dashboard 当天实测** 高于本地批次状态。
3. 明确 Case ID + 非 declined 上下文 > 页面按钮消失 / success 文案。
4. Draft ≠ failed。
5. no Case ID ≠ success。
6. 旧 declined Case ID 不能当成本次提交 Case ID。
7. `batch_state` 是执行过程记录，不是最终事实来源。

---

## 推荐写入顺序

1. 页面 / 提交结果；
2. Case ID / Dashboard 状态；
3. 状态规范化；
4. SQLite / Excel；
5. batch_state；
6. evidence summary；
7. `memory/status/amazon-5461-ledger.md` 摘要。

---

## 标准状态

| 状态 | 含义 |
|---|---|
| `success` | 明确 Case ID 且非 declined，或 Dashboard confirmed under_review/approved |
| `draft` | Dashboard / Selling Applications 发现 Draft，等待续接 |
| `declined` | 当前 Dashboard 或页面确认申请被拒 |
| `submitted_no_case_id_pending_dashboard` | 页面像是提交成功但无 Case ID，需 Dashboard 复核 |
| `failed` | 自动流程失败，且 Dashboard 未确认成功/Draft |
| `partial` | 不确定状态，页面需人工复核 |
| `error` | 脚本/环境异常 |
| `already_approved` | Add Product/Description 显示已有权限 |

## Case 详情回复状态

| 状态 | 含义 |
|---|---|
| `approved` | 最新 Amazon 回复明确表示申请通过，且 Manage Brand 与 Add Product 二次验证均通过 |
| `false_approved` | Case 明确通过，但 Manage Brand 找不到品牌或 Add Product 明确仍无法进入 Description（假过） |
| `verification_pending` | Case 明确通过，但二次验证遇到未知页面或技术异常，等待重试/人工复核 |
| `declined` | 最新 Amazon 回复明确表示申请被拒 |
| `action_required` | Amazon 要求补材料、回复或执行其他动作 |
| `pending` | Case 详情尚无 Amazon 回复 |
| `answered_unknown` | Amazon 已回复，但规则无法可靠判断通过/拒绝 |
| `blocked` | 登录、CAPTCHA 或 2FA 阻止只读检查，需要人工处理 |

`Case Summary: Answered` 只表示 Amazon 已回复，不能映射为 `approved`。
Case 回复正文的明确批准也只触发二次验证，不能单独映射为最终 `approved`。

---

## 已新增模块

`src/status_sync.py`

提供：

- `normalize_submit_status(result)`
- `build_status_summary(account_id, marketplace, brand_name, result)`
- `apply_dashboard_precedence(result)`

当前模块只做规范化和 summary，不直接写 Excel/SQLite。后续可以让 `scripts/check_case_log.py` 和批量主流程统一调用。

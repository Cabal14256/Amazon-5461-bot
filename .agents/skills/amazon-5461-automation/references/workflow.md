# Workflow Reference

> 2026-06-01 status note: Several Teach/UAT scripts referenced here are historical and are not present in the active tree. Current active entry points are submit_5461.py, scripts/run_full_5461_batch.py, scripts/run_full_5461_workflow.py, scripts/check_case_log.py, scripts/diagnose_no_submit_add_product.py, and scripts/audit_account_config.py.

> 位置: `projects/amazon-5461-bot/`
> 最后更新: 2026-04-28

## 当前推荐工作流

**实际生产中使用的是直接执行脚本，而非 Teach/UAT 框架。**

### 单品牌 5461 提交
```bash
python submit_5461.py <account_id> <brand_name>
# Example: python submit_5461.py us_store_549 OUNNE
```

### 批量 5461 提交
```bash
python scripts/run_full_5461_batch.py --accounts <acct> --brands <brand1>,<brand2> [--site US|UK|DE|MX]
```

### 品牌验证 / 安全诊断

旧 `verify_brand.py` 不在当前项目树。当前使用 no-submit 诊断：

```bash
.\.venv\Scripts\python.exe scripts/diagnose_no_submit_add_product.py --account <account_id> --brand <brand> --site <SITE>
```

---

## Teach → UAT → Approve → Run（历史设计，当前项目树不存在这些脚本）

> ⚠️ **以下流程是历史设计/归档思路，当前项目树不存在这些脚本，不可直接执行。**
> 保留仅供参考。

### 流程生命周期

```
draft → uat_pending → approved → disabled
```

| 状态 | 说明 | 可执行 |
|------|------|--------|
| `draft` | 刚示教完成，待 UAT | UAT only |
| `uat_pending` | UAT 运行过，待人工批准 | Approve only |
| `approved` | 已批准，可正式执行 | Run only |
| `disabled` | 已禁用 | None |

### 核心命令（历史示例，不可直接执行）

```bash
# 历史示例，不可直接执行：当前项目树不存在以下 Teach/UAT 脚本。
# 1. Teach (示教)
python scripts/teach_verify_add_product.py <account_id> <brand_name>  # 历史示例，不可直接执行：当前不存在
python scripts/teach_submit_5461.py <account_id> <brand_name>  # 历史示例，不可直接执行：当前不存在
python scripts/teach_submit_gtin_exemption.py <account_id> <brand_name>  # 历史示例，不可直接执行：当前不存在

# 2. UAT (验收)
python scripts/replay_flow.py <account_id> <brand_name> <flow_id> uat  # 历史示例，不可直接执行：当前不存在

# 3. Approve (批准)
python scripts/approve_flow.py <flow_id> <brand_name>  # 历史示例，不可直接执行：当前不存在

# 4. Run (正式执行)
python scripts/replay_flow.py <account_id> <brand_name> <flow_id> run  # 历史示例，不可直接执行：当前不存在
```

---

## 未知情况处理（历史示例，不可直接执行）

```bash
# 历史示例，不可直接执行：当前项目树不存在 scripts/resolve_unknown_case.py。
# 列出待处理
python scripts/resolve_unknown_case.py list  # 历史示例，不可直接执行：当前不存在

# 转换为规则
python scripts/resolve_unknown_case.py add <unknown_case_id>  # 历史示例，不可直接执行：当前不存在
```

建议固化的全局规则:
1. 验证码 → `pause_for_human`
2. 二步验证 → `pause_for_human`
3. 某类确认页 → `click Continue`

---

## 证据留存

自动保存到 `evidence/<account_id>/<brand_name>/`:
- 运行截图
- 调试现场 HTML
- 页面文本

---

## 安全边界

- 验证码 / 2FA 必须人工处理
- 最终提交按钮默认需人工确认
- 只允许白名单动作 (`action_executor.py`)
- 未知页面必须暂停，不允许猜测


# 项目文档导航

本目录只保存可共享、不含账号级数据的文档。运行证据、日志、数据库、账号配置和业务工作簿必须留在 Git 忽略的 `runtime/`、`data/`或 `brand_packs/` 中。

## 当前状态与规则

- [`CURRENT_STATE.md`](CURRENT_STATE.md)：当前实现、迁移语境和已验证边界。
- [`reference/status-sync-rules.md`](reference/status-sync-rules.md)：业务状态优先级与同步规则。
- [`security/secret-handling.md`](security/secret-handling.md)：私密配置与证据处理规则。

## 运维手册

- [`runbooks/runbook-submit-5461.md`](runbooks/runbook-submit-5461.md)
- [`runbooks/runbook-dashboard-check.md`](runbooks/runbook-dashboard-check.md)
- [`runbooks/runbook-case-followup.md`](runbooks/runbook-case-followup.md)
- [`runbooks/runbook-reapplication-campaigns.md`](runbooks/runbook-reapplication-campaigns.md)
- [`runbooks/runbook-web-console.md`](runbooks/runbook-web-console.md)

## 设计与历史

- `plans/`：分阶段方案、蓝图和已完成的实施计划。这些文档用于追溯，不代替 `CURRENT_STATE.md`。
- `migration/`：迁移记录、历史归档计划和旧脚本索引。
- `examples/`：只包含可公开的形状示例，不存放真实账号页面或业务证据。

运行决策以根目录的 `AGENTS.md`、`PROJECT.md` 和本目录的 `CURRENT_STATE.md` 为准。

# 旧脚本能力迁移索引

更新时间：2026-05-27

目标：把旧脚本 / scratch 中已验证有价值的能力迁移进 `src/` 或稳定 `scripts/`，避免同一能力散落多套实现。

---

## 已迁移 / 已产品化

| 来源 | 能力 | 目标位置 | 状态 |
|---|---|---|---|
| `submit_5461.py:get_autofill_email()` | 从账号配置、AdsPower profile、页面 autofill/storage/global 获取邮箱 | `src/email_resolver.py` | ✅ 已迁移 |
| `_submit_mp_mall_draft.py` | `/sq/approvalrequest` Draft 字段填写、上传、提交等待 Case ID | `src/draft_resume.py` | ✅ 已迁移为模块，默认不提交 |
| `_submit_vporyadku_draft.py` | Draft submit + DB 记录经验 | `src/draft_resume.py` + 后续状态同步模块 | ⚠️ 提交流程已迁移，DB/Excel 统一待状态同步项处理 |
| `_scratch/diagnose_no_submit_pid_idempotent_589_jzg.py` | no-submit 安全诊断 | `scripts/diagnose_no_submit_add_product.py` | ✅ 泛化为账号/品牌/站点参数 |
| 多处手写 hover/click/type | 真实交互 pacing | `src/human_interaction.py` | ✅ 已抽象基础 helper |
| 批量日志中 429 判断经验 | 失败/健康分类标签 | `src/failure_classifier.py` | ✅ 已新增 |

---

## 待迁移 / 待收口

| 来源 | 能力 | 建议目标 | 优先级 |
|---|---|---|---|
| `check_case_dashboard.py` / `check_case_dashboard_v2.py` / `check_case_dashboard_v3.py` | Dashboard/Selling Applications 多版本探测经验 | `src/case_dashboard_checker.py` | 高 |
| `scripts/check_case_log.py` | Case ID 补录 + Excel/SQLite 更新 | 状态同步模块 + CLI | 高 |
| `_scratch/diagnose_587_new_ui.py` 等 | 新 UI DOM 探测样本 | `knowledge/pages/add-product/examples/` + 文档 | 中 |
| `_scratch/try_itemtype_methods.py` | ItemType KAT dropdown 设置方法 | `src/form_filler.py` / knowledge selector notes | 中 |
| `_scratch/try_pid_toggle_variants.py` | No Product ID toggle 变体 | `src/form_filler.py` 单元测试 | 中 |
| `retry_578_draft.py` / `retry_578_now.py` | 特定账号 retry 经验 | 不迁移代码，只归档经验 | 低 |

---

## 迁移规则

1. 只迁移可复用能力，不迁移写死账号 / Case ID / CDP URL 的脚本。
2. 默认不做外部提交动作；提交必须由主流程或显式 `submit=True` 控制。
3. 迁移进 `src/` 后必须通过 `py_compile`，后续补单元测试。
4. 原 `_scratch` 脚本暂不删除，后续统一归档到 `_archive/experiments/YYYY-MM/`。
5. 状态写入 Excel/SQLite/batch_state/ledger 的逻辑要走统一状态同步模块，不在草稿脚本里各写各的。

---

## 2026-06-01 实测状态

- 已存在并可作为当前核心模块：`src/email_resolver.py`、`src/draft_resume.py`、`src/failure_classifier.py`、`src/human_interaction.py`、`src/status_sync.py`、`src/case_dashboard_checker.py`。
- 当前缺失/历史入口：`verify_brand.py`、`scripts/scan_adspower_accounts.py`、`scripts/auto_discover_accounts.py`、`scripts/smoke_verify_add_product.py`、`scripts/run_verify_add_product.py`。
- 当前安全诊断入口：`scripts/diagnose_no_submit_add_product.py --account <id> --brand <brand> --site <SITE>`。
- 当前 Case 补录入口：`scripts/check_case_log.py <account_id> <brand_name> --site <SITE>`。
- 批量主入口：`scripts/run_full_5461_batch.py`；`--use-state-loop` 仅显式启用，默认仍走旧 flow。

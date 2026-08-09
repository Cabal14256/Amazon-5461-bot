# 2026-05-27 计划模式执行记录

用户指定只执行以下事项，其他事项暂不执行：

1. 刚改完代码后的安全验证 —— 591US JZG
2. Draft 续接流程产品化
3. declined-old-application 流程继续收口
4. StateLoopExecutor 暂不适合默认生产启用
5. 429 健康状态分类细化
7. Case ID 提取增强
8. 真实交互层继续收口
9. 账号自动补全体检命令
10. 旧脚本能力系统性合并到主流程
11. Evidence / ledger / batch_state 同步统一
13. 清理 scratch / batch_state / evidence 索引
14. 文档和 skill 更新

---

## 执行结果

### 1. 591US / JZG no-submit 安全验证

状态：✅ 完成

命令：

```bash
python scripts/diagnose_no_submit_add_product.py --account 591 --brand JZG --site US
```

结果：

- `item_type_ok: true`
- `no_product_id_on: true`
- `external_pid_errors: 0`
- `email_resolved: true`
- `no_submit_clicked: true`
- 注意：`action_button_disabled: "true"`，正式提交前建议复核按钮选择器是否抓到隐藏/非目标按钮。

证据：

```text
evidence/2026-05-27/us_store_591/JZG/no_submit_safety/
```

新增脚本：

```text
scripts/diagnose_no_submit_add_product.py
```

---

### 2. Draft 续接流程产品化

状态：✅ 完成基础模块，默认不提交

新增模块：

```text
src/draft_resume.py
```

能力：

- 打开 `reEvaluateApplicationEndpoint` / `/sq/approvalrequest`
- 填写 SQ 字段：
  - `level-0:question-0:text` Product ID，可留空
  - `level-0:question-1:text` Product title
  - `level-0:question-2:text` Manufacturer
  - `level-0:question-3:text` Product Description
  - `contact_info_email_input` Email
- 上传图片
- 默认 `submit=False`，不会点击 Submit
- 显式 `submit=True` 时才提交并等待 Case ID

主流程增强：

- Dashboard 发现 Draft 时记录：
  - `draft_application_id`
  - `draft_resume.available`

---

### 3. declined-old-application 流程收口

状态：✅ 完成状态语义修正

修改：

```text
src/flow_submit_5461.py
```

修正：

- `handle_declined_case_application()` 的结果写入 `result["declined_recovery"]`
- 如果旧 Case declined，但新申请恢复失败原因是：
  - `half_loaded_panel`
  - `retry_failed`
  - `no_new_form`
- 则标记为 `failed`，且不把旧 declined Case ID 当作本次最终 Case ID。

目的：避免旧 declined Case ID 污染当前提交结果。

---

### 4. StateLoopExecutor 非默认生产启用

状态：✅ 完成

修改：

```text
scripts/run_full_5461_batch.py
```

结果：

- `auto_state_loop_fallback` 默认从 `True` 改为 `False`
- StateLoop fallback 必须显式配置：
  - `auto_state_loop_fallback=true`
  - 或 `--use-state-loop`

目的：避免真实生产运行中旧流程失败后自动升级到实验 StateLoop。

---

### 5. 429 健康状态分类细化

状态：✅ 完成基础分类并接入批量结果

新增模块：

```text
src/failure_classifier.py
```

支持标签：

- `restriction_endpoint_429`
- `cloudfront_chunk_failed`
- `panel_shell_only`
- `form_fields_missing`
- `upload_input_missing`
- `submit_no_case_id`
- `dashboard_navigation_failed`
- `dashboard_not_found`
- `draft_detected`
- `account_rate_limited`
- `amazon_410001`
- `login_or_permission_issue`

接入：

- 批量结果追加 `health_classification`

---

### 7. Case ID 提取增强

状态：✅ 完成

修改：

```text
src/flow_submit_5461.py
```

增强：

- `extract_case_id()` 改为候选打分
- 排除 `declined/rejected/denied` 附近旧 Case
- 支持：
  - `Case ID`
  - `Case Number`
  - `Case #`
  - 2025/2026 开头 Case
- 新增 `extract_case_ids_with_context()` 便于测试/诊断

---

### 8. 真实交互层继续收口

状态：✅ 完成第一轮抽象

新增模块：

```text
src/human_interaction.py
```

提供：

- `human_delay()`
- `human_click()`
- `human_type()`
- `safe_checkbox_click()`

接入：

- `src/form_filler.py`
- `src/flow_submit_5461.py`

目的：减少散落的直接 DOM click/value 设置，统一真实交互优先、JS fallback 的模式。

---

### 9. 账号自动补全体检命令

状态：✅ 完成并对 591US/JZG 通过

新增脚本：

```text
scripts/audit_account_config.py
```

检查项：

- account 是否存在 / 自动创建
- AdsPower profile
- profile name / remark / email
- email 是否可解析
- marketplace_configs
- entry_url / item_type_keyword
- brand pack 文案同步
- statement 文件
- upload 图片
- cdp_url

591US/JZG 结果：

```text
ok=11, fail=0
```

---

### 10. 旧脚本能力系统性合并到主流程

状态：✅ 完成第一版迁移索引

新增文档：

```text
docs/legacy-script-migration-index.md
```

已标记迁移：

- `get_autofill_email()` → `src/email_resolver.py`
- Draft 脚本经验 → `src/draft_resume.py`
- no-submit 诊断 → `scripts/diagnose_no_submit_add_product.py`
- 真实交互 → `src/human_interaction.py`
- 429 分类 → `src/failure_classifier.py`

---

### 11. Evidence / ledger / batch_state 同步统一

状态：✅ 完成第一版规范化模块和规则文档

新增模块：

```text
src/status_sync.py
```

提供：

- `normalize_submit_status(result)`
- `build_status_summary(account_id, marketplace, brand_name, result)`
- `apply_dashboard_precedence(result)`

新增文档：

```text
docs/status-sync-rules.md
```

规则：

- Dashboard 当天实测优先
- Draft ≠ failed
- no Case ID ≠ success
- 旧 declined Case ID 不能当本次 Case

---

### 13. 清理 scratch / batch_state / evidence 索引

状态：✅ 完成计划，不移动/删除文件

新增：

```text
docs/archive-plan-2026-05.json
docs/archive-plan-2026-05.md
```

扫描结果：

- 75 个候选归档项

说明：

- 只生成索引；
- 没有删除；
- 没有移动；
- 正式归档前需人工确认。

---

### 14. 文档和 skill 更新

状态：✅ 本执行记录已创建

新增：

```text
docs/plan-execution-2026-05-27.md
```

后续还应同步更新：

- `skills/amazon-5461-automation/SKILL.md`
- `memory/capabilities.md`
- `memory/troubleshooting.md`
- `memory/checklist.md`

---

## 编译 / smoke

已通过的检查包括：

```bash
python -m py_compile src/draft_resume.py
python -m py_compile src/flow_submit_5461.py
python -m py_compile scripts/run_full_5461_batch.py
python -m py_compile src/failure_classifier.py
python -m py_compile src/human_interaction.py
python -m py_compile src/status_sync.py
python -m py_compile scripts/audit_account_config.py
python -m py_compile scripts/diagnose_no_submit_add_product.py
```

额外 smoke：

- `failure_classifier` 429/CloudFront/Dashboard 标签测试通过
- `status_sync` 状态规范化测试通过
- `extract_case_ids_with_context` 旧 declined 排除测试通过
- `audit_account_config.py 591 --brand JZG --site US` 通过

---

## 剩余注意项

1. 591US/JZG no-submit 中 `action_button_disabled: "true"`，正式提交前建议复核按钮选择器是否抓到了隐藏/非目标按钮。
2. Draft resume 模块默认不提交，真实 Draft 续接仍需要单独安全验证。
3. Dashboard resilient navigation 已改过，但跨站点真实验证还没全覆盖。
4. 归档计划只生成索引，未执行移动。

# 2026-05 归档清理计划

更新时间：2026-05-27

> 本计划只生成索引，不直接移动/删除文件。正式归档前需确认，避免误移仍在使用的诊断脚本或状态文件。

索引文件：`docs/archive-plan-2026-05.json`

---

## 归档范围

### 1. `_scratch/` 临时诊断脚本

目标目录：

```text
_archive/experiments/2026-05/_scratch/
```

说明：

- 保留作为历史实验；
- 已产品化能力应迁移到 `src/` 或稳定 `scripts/`；
- 不建议直接删除。

### 2. `data/batch_state_*.json`

目标目录：

```text
_archive/batch_states/2026-05/
```

说明：

- 这些是历史批次状态；
- 当前执行默认状态文件仍保留在 `data/batch_state.json`；
- 归档后若要追溯历史，按日期和账号从 archive 查。

### 3. `check_case_dashboard*.py`

目标目录：

```text
_archive/experiments/2026-05/dashboard_check/
```

说明：

- Dashboard 探测能力应合并到 `src/case_dashboard_checker.py`；
- 多版本实验脚本归档。

### 4. `retry_*.py`

目标目录：

```text
_archive/experiments/2026-05/retry/
```

说明：

- 写死账号/品牌/Case ID 的 retry 脚本不应作为稳定入口；
- 仅保留历史参考。

---

## 当前计划数量

本次扫描生成 75 个候选归档项。

查看明细：

```bash
cat docs/archive-plan-2026-05.json
```

---

## 正式执行前检查

1. 确认没有正在运行的批量任务引用这些文件；
2. 确认最新诊断脚本已经迁移到稳定路径：
   - `scripts/diagnose_no_submit_add_product.py`
   - `scripts/audit_account_config.py`
3. 确认 Dashboard 能力已经在 `src/case_dashboard_checker.py`；
4. 确认 Draft 能力已经在 `src/draft_resume.py`；
5. 使用移动/归档，不使用删除。

---

## 建议执行命令（待确认后再用）

```powershell
# 示例：按 docs/archive-plan-2026-05.json 移动文件
# 注意：执行前应先人工确认 JSON 内容
```

---

## 2026-06-01 清理补充

- 旧 Amazon evidence/artifacts（2026-05-20 前）已压缩归档到：`../../archives/amazon-evidence-artifacts-20260601-1439/`。
- 保留 `evidence-pre-2026-05-20.zip`、`artifacts-pre-2026-05-20.zip`、manifest、summary、SHA256 和 `deleted-originals-20260601-1447.json`。
- 项目内 `artifacts/` 原旧件已删除；`evidence/` 保留 2026-05-20 及之后证据。

# 阶段 5–9 实施计划（异常检测 → Codex 修复闭环 → 加固试运行）

> 上位蓝图：`docs/plan-internal-console-codex-repair-2026-08-04.md`（§9 数据库、§14 检测设计、§15 证据包、§16 Codex 集成、§17 Worktree 流程、§18 验证门禁、§19 发布流程、§20 阶段清单）。
> 本文把阶段 5–9 落到本仓库现有代码。每个阶段开工时仍按惯例先出该阶段的详细计划再实施（同阶段 4 的做法）。
> 已完成基线：阶段 0–4（只读控制台、SQLite 数据基础、diagnose/dry-run 队列、两阶段真实提交审批，193 测试全绿）。

## 全局前置决策（开工前需拍板）

| 决策项 | 现状 | 建议 |
|---|---|---|
| Codex CLI 可用性 | 已全局安装 codex-cli 0.147.0（2026-08-11），`codex exec` 冒烟通过 | 阶段 6 按完整方案实施；测试仍走 mock 可执行文件 |
| Codex 身份与配额 | 已拍板（2026-08-12）：使用桌面客户端与 CLI 共享的 ChatGPT 登录态 | 共享桌面 ChatGPT 会话是正式运行方式；每日调用限额继续由本地配置兜底 |
| canary 账号/站点/品牌 | 未指定 | 建议在本地私有配置中固定一个已批准、Amazon 侧无操作的组合做诊断/dry-run canary |
| 截图是否给 Codex | 蓝图待决策项 | 第一版**默认 withheld**（只给脱敏文本/DOM 摘要），OCR 遮挡成熟前不放行原图 |
| 生产补丁合并方式 | 蓝图待决策项 | 第一版本地合并（不 push、不开外部 PR） |

## 阶段 5：持久化异常检测（蓝图估 4–6 工作日）

目标：把失败信号从一次性 `runtime/codex_signal.json` 升级为持久化 incident 队列，带分类、聚合、置信度和脱敏证据包；人工处理与代码修复从入口就分流。

### 5.1 数据库（src/db.py，沿用迁移模式）

- 新表 `repair_incidents`（蓝图 §9.5 字段全量：signature/scope_type/flow_type/account_id/marketplace/brand_name/detector_type/confidence/classification/status/first_seen_at/last_seen_at/occurrence_count/evidence_bundle_path/codex_thread_id/resolution_note）。
- status 状态机：`open → triaged → patch_queued → patching → validated → released / rejected / closed_human / closed_duplicate`。
- 助手：按 signature+scope 查 open incident、创建或累加 occurrence（同一事务）、追加证据路径、状态迁移。

### 5.2 Detector（新文件 src/incidents/detector.py + signature.py）

- 入口一（兼容迁移）：`write_pending_signal`（src/codex_signal.py）改为同时写入 incident 队列；`scripts/check_codex_signal.py` 改读队列头部。保留文件信号一个版本周期后下线。
- 入口二（主路径）：批次/执行器在确定性恢复耗尽时调用 `record_incident(...)`（先在 `run_full_5461_batch.py` 失败收口处和 `src/executor/state_loop.py` 的 `flow_loop_exhausted` 处接线）。
- signature（蓝图 §14.4）：flow_type + page_family + normalized_url_pattern + missing_landmarks + component_types + state_loop_reason + normalized_error_class；账号/品牌/Case ID/长数字一律不进 signature。
- 排除规则（蓝图 §14.2 硬编码 + 配置可扩展）：CAPTCHA、2FA、登录过期、账号风险、429/410001/CloudFront、brand hard block、配置缺失 → 进 `待人工处理` 分类，**永不**进 patch 队列。
- 置信度（蓝图 §14.3，阈值入 `config/settings.yaml` `incidents:` 段）：首现恢复耗尽建 incident；同 signature 同 scope 复发 +occurrence；跨账号/站点同 signature 提置信度；命中已知 Amazon 错误文本降置信度。

### 5.3 证据包（新文件 src/incidents/evidence_bundle.py，复用 src/capture/redact.py）

- 蓝图 §15.1 八件套（manifest / page-summary.redacted / dom-contract.redacted / visible-text.redacted / screenshot 或 withheld / previous-success-contract / relevant-selectors / run-context.redacted），落 `runtime/evidence/incidents/<incident_id>/`。
- 第一版截图一律 `screenshot_withheld.json`（见前置决策）；HTML 只保留标签/role/data-cy/id/name/有限文本，页面文本包裹为不可信数据标记。

### 5.4 API + 前端

- `GET /api/incidents`（筛选：status/classification/scope）、`GET /api/incidents/{id}`（含证据包清单与下载，走现有 evidence allowlist）。
- 前端：`PendingPage.vue` 扩展分组（蓝图 §12.5：CAPTCHA/2FA、429/410001、Draft/无 Case ID、疑似改版…），新增"疑似页面改版"分组来自 incidents 表；`RepairCenterPage.vue` 先放空壳占位（阶段 6/7 填充）。

### 5.5 测试与验收

- 单测：signature 稳定性（同因同 signature、含账号信息不泄漏）、排除规则全覆盖、置信度升降、occurrence 聚合、证据包八件套生成且无明文敏感串（对真实证据目录抽样扫描）。
- 回放：用 `evidence/` 下历史失败证据（429、CAPTCHA、selector 缺失各若干）离线回放 detector，验证已知环境异常零误判进 patch 队列。
- 验收（对齐蓝图）：已知 429/410001/CAPTCHA/2FA 不会进入代码修复队列。

## 阶段 6：Codex 只读判因（蓝图估 3–5 工作日）

目标：incident 创建后自动（或人工触发）做只读判因，输出结构化 classification，决定能否进入补丁生成。

### 6.1 可用性与子进程（新文件 src/codex_client/）

- `availability.py`：检测 Codex CLI（PATH/配置路径）、版本、配额；不可用时全链路降级（incident 停留 `open`，UI 显示"Codex 不可用"），**绝不影响主自动化**。
- `triage.py`：`codex exec --json --output-schema triage-schema.json -` 子进程（参数以实际安装的 CLI 文档为准），超时（默认 120s 可配）、JSONL 事件落 `runtime/logs/repair/<repair_job_id>/codex-events.jsonl`、结果落 `runtime/state/repair/<repair_job_id>/result.json`。
- prompt 注入防护：证据文本一律以不可信数据块包裹；prompt 只含证据包路径与安全约束（蓝图 §16.4 相关条目），不给任何写权限（只读 sandbox）。
- triage JSON Schema（蓝图 §16.1 七类 classification + confidence + safe_to_generate_patch + requires_human_review + missing_evidence），Schema 校验失败按 `insufficient_evidence` 处理并重试一次。

### 6.2 数据与 API

- 新表 `codex_repair_jobs`（蓝图 §9.6：stage=triage/patch/review、status、jsonl_log_path、result_json_path 等）。
- `POST /api/incidents/{id}/triage`（operator+，人工触发/重试）；自动触发规则：confidence ≥ 0.60 且非排除分类（阈值配置化）。
- 所有判因结果（含失败/超时/配额不足）写审计。

### 6.3 前端

- incident 详情页展示判因结果、置信度、缺失证据清单；"生成补丁"按钮按 `safe_to_generate_patch` 与阈值启停（阶段 7 接线）。

### 6.4 测试与验收

- 单测：JSONL 解析（正常/截断/非 JSON 行）、超时与子进程异常退出、Schema 校验失败路径、不可用降级。
- 集成：mock `codex` 可执行文件（PATH 前置一个脚本）跑通 triage 全流程。
- 验收（对齐蓝图）：Codex 不可用不影响主自动化；incident 保持排队可重试。

## 阶段 7：隔离生成补丁（蓝图估 5–8 工作日）

目标：在隔离 worktree 中由 Codex 生成修复补丁，生产工作树零直接变化。

### 7.1 Worktree 管理（新文件 src/repair/worktree.py）

- `git worktree add runtime/worktrees/repair-<repair_job_id>/ -b codex/repair-<incident_id>-<short_sig>`（worktree 根目录配置化，可放仓库外）。
- 输入只含 tracked 源码 + 脱敏证据副本；`.env`/`runtime/private`/`data`/`brand_packs`/原始截图一律不进。
- 并发去重（蓝图 §17.4）：同 signature 同时仅一个 active repair（DB 唯一约束 + 事务）；新证据追加到既有 incident。
- 保留与清理策略：released/rejected 后保留 N 天（配置）再 `git worktree remove`，保留期用于回滚取证。

### 7.2 补丁生成（src/codex_client/patch.py）

- 启动前提（蓝图 §16.1B 全量）：判因为代码/selector 类、证据充分、incident 未关闭、无同 signature active repair、worktree 创建成功。
- `codex exec --sandbox workspace-write --ask-for-approval never --json --output-schema repair-result-schema.json -`，cwd 为 repair worktree。
- 修复 prompt 约束（蓝图 §16.4 全量入 prompt 模板）：安全边界、允许目录（`config/selectors/`、`src/` 指定白名单、`tests/`）、禁止私有目录、优先共享 selector/状态识别、禁止账号专用 one-off、必须新增最小回归测试并跑测试、不得启动 AdsPower/真实提交、业务语义变化必须 `requires_human_review=true`。
- 修改级别（蓝图 §17.3）：R0/R1 可自动生成；R2 需人工在 UI 明确允许；R3 只生成分析。级别判定入 repair job 记录。

### 7.3 diff 私密扫描（src/repair/diff_scan.py）

- 生成后立即扫描 diff：邮箱、长数字串、AdsPower profile、私有路径、凭据模式（复用 redact.py 模式集）；命中即 `validation_failed` 并隔离该 worktree。
- 必须拒绝的 diff 规则（蓝图 §18.1 全量实现为静态检查：移除 --submit 门禁、绕过 CAPTCHA/限流、删除证据保存、测试中调真实 Seller Central 等）。

### 7.4 前端修复中心（RepairCenterPage.vue 正式版）

- incident 范围/次数/置信度、判因结果、worktree 与分支、修改文件与完整 diff（`GET /api/repair-jobs/{id}/diff`）、测试结果、剩余风险；批准验证/批准发布/拒绝按钮（阶段 8 接权限）。

### 7.5 测试与验收

- 单测：worktree 创建/去重/清理、diff 扫描全规则、允许范围检查、R0–R3 级别判定。
- 集成：mock codex 生成一个真实 selector 修改，验证隔离性（生产树 `git status` 无变化）。
- 验收（对齐蓝图）：Codex 只能在隔离 worktree 修改；生产工作树无直接变化。

## 阶段 8：验证、审批、发布与回滚（蓝图估 5–7 工作日）

目标：补丁从 worktree 到生产必须经过固定验证门禁与两级人工审批，全程可回滚。

### 8.1 验证门禁状态机（src/repair/validation.py）

- 蓝图 §18 固定 12 步，1–8 自动化（允许范围检查 → diff 私密扫描 → compileall → pytest → ruff → 新增 contract 测试 → 离线 fixture 回放 → Codex read-only diff review），9–12 人工+canary（Reviewer 批准验证 → canary diagnose → canary dry-run → 人工审截图）。
- 状态机：`patch_ready → validating → validation_failed / awaiting_validation_approval → canary → awaiting_release_approval → released / rejected`，任何一步失败不可跳过，结果全量落 `codex_repair_jobs` + 审计。

### 8.2 审批（新表 `repair_approvals`，蓝图 §9.7）

- `POST /api/repair-jobs/{id}/approve-validation`（reviewer+）、`POST /api/repair-jobs/{id}/approve-release`（**仅 admin**）、`POST /api/repair-jobs/{id}/reject`（reviewer+，必填 note）。
- 复用阶段 4 的审计与角色体系；发布审批页展示蓝图 §19.1 全部内容（incident 摘要、判因、diff、测试、canary 证据、风险与回滚方式）。

### 8.3 发布与回滚（src/repair/release.py）

- 发布 = 本地合并已审核 repair 分支（先记录当前 SHA 到 repair job），不 push 不开 PR。
- 发布后自动：只读 health check + canary diagnose，结果落审计；失败提示回滚。
- 回滚 = `git revert` 或重置到发布前 SHA + worktree 清理，runbook 写明手工兜底步骤。
- 原任务恢复（蓝图 §19.3）：不自动续跑可能处于提交中间态的页面；先查 Dashboard，按 Under Review/Approved/Draft/Not Found 分支处理（UI 给操作指引）。

### 8.4 测试与验收

- 单测：状态机全迁移与非法迁移拒绝、审批权限矩阵、发布前 SHA 记录、回滚路径。
- 端到端（mock codex + 真 git）：R0 selector 补丁从 incident 到 released 全链路，验证生产树仅在 approve-release 后才变化。
- 验收（对齐蓝图）：没有人工发布批准，任何 Codex 补丁都不能进入生产。

## 阶段 9：加固与试运行（蓝图估 5–10 工作日）

目标：上线前最后的攻防验证 + 至少一周真实环境观察。

### 9.1 安全与韧性测试（tests/web/test_hardening.py 等）

- 权限矩阵全量（viewer/operator/reviewer/admin × 全部端点）、CSRF/CORS、路径穿越（evidence/log/截图下载全端点）、日志泄露（SSE/日志 API 抽样扫描无明文敏感串）。
- 长任务断电/重启恢复（阶段 3 已有基础，补 submit 任务中间态场景）、SQLite 并发与锁回收、磁盘满、Codex 超时/不可用、AdsPower 不可用。

### 9.2 试运行（至少一周）

- 只读 + dry-run 模式跑真实业务；submit 默认开启。当前保持双人审批，后续按已定策略取消不同用户限制。
- 监控指标（蓝图 §22）：diagnose/dry-run/submit 成功率、incident 误报率、Codex 触发率、修复成功率；每周评审一次，调 incident 阈值。
- 试运行达标后再正式开放受控提交与 repair 发布。

### 9.3 收尾

- 更换临时凭据（admin/reviewer1）、确认 LAN 防火墙规则、备份 runbook（含 WAL）、运维/回滚/incident 处理 runbook 补齐。
- 验收（对齐蓝图）：运行指标和审计记录满足公司内部使用要求。

## 顺序、依赖与里程碑

```text
阶段 5（检测与 incident 队列）   ← 地基，先做
  └─ 阶段 6（只读判因）          ← 依赖 5 的 incident；依赖 Codex CLI 前置决策
       └─ 阶段 7（隔离补丁）      ← 依赖 6 的判因结果；可与 6 部分并行（UI/管线先行）
            └─ 阶段 8（验证发布） ← 依赖 7 的补丁产物
                 └─ 阶段 9（加固试运行）← 收口
```

- 里程碑 M1（阶段 5 完）：历史失败可回放分类，人工处理队列上线。
- 里程碑 M2（阶段 6–7 完）：第一个 mock/真实 selector incident 走完"判因→隔离补丁"。
- 里程碑 M3（阶段 8 完）：第一个补丁走完全部门禁并发布、可回滚。
- 里程碑 M4（阶段 9 完）：试运行达标，蓝图完成定义（§28）全部满足。

## 全程不变的红线

- Codex 永不接触：`runtime/private/`、`.env`、`data/`、`brand_packs`、真实浏览器/AdsPower、真实提交与登记写入。
- 任何阶段都不自动发布补丁；发布仅 admin 人工批准。
- 排除清单（CAPTCHA/2FA/429/410001/登录过期/账号风险）永不进 patch 队列。
- 每阶段交付保持现有节奏：详细计划 → 实施 → 全量 pytest/ruff/vue-tsc → 文档 → 受控 UAT → CURRENT_STATE 更新。

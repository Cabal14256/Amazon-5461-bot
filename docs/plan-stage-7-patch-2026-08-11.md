# 阶段 7 详细计划：隔离生成补丁（2026-08-11）

> 上位文档：`docs/plan-stages-5-9-implementation-2026-08-11.md`（阶段 7 节）、
> `docs/plan-internal-console-codex-repair-2026-08-04.md`（§16.1B 启动前提、§16.4 修复提示词、§17 Worktree 流程、§18.1 必须拒绝的 diff）。
> 基线已核实（2026-08-11）：阶段 6 已落地并真实联调通过（`src/codex_client/` 子进程管线、
> `codex_repair_jobs` 表、`POST /api/incidents/{id}/triage`、前端判因卡片、incident `triaged` 状态、
> 真实 codex exec 判因 incident 98 成功）。`.env`/`data/`/`runtime/`（除 .gitkeep）/`brand_packs/`
> 均未被 git 跟踪，worktree 检出天然不含私有目录。

## 目标

对判因通过（`safe_to_generate_patch=true` 且证据充分）的 incident，在**隔离 git worktree** 中由
Codex 生成修复补丁：生产工作树零直接变化，补丁经允许范围检查与私密扫描后以前端完整 diff
呈现，等待阶段 8 的验证门禁与人工审批。本阶段**不做**任何发布/合并动作。

## 开工前置（Day 0）

1. **真实联调输入**：当前 183 条 incident 均无证据包（回放路径不生成 bundle），判因只能
   返回 `insufficient_evidence`，不会有 `safe_to_generate_patch=true` 的真实输入。
   开工前用 `src/incidents/evidence_bundle.py` 为至少 1 个 selector 类 incident 回填证据包
   （数据源 `evidence/` 历史目录），或对存量数据确认无法回填后改用"下一次真实失败的现场
   采集"作为联调输入。单元/集成测试不依赖此项（全走 mock codex）。
2. **worktree 基线策略**（设计决策，本计划拍板）：阶段 5/6 代码尚未提交，worktree 从
   HEAD 检出会缺失这些代码。定为：创建 worktree 时把生产树的**未提交 tracked diff**
   （`git diff` + `git diff --cached`）作为基线补丁 apply 进 worktree 并作为分支首个
   commit（`baseline: uncommitted production state at <sha>`），Codex 在其上修改。
   生产树本身不提交、不变动。该基线 commit 的 SHA 记入 repair job。
3. canary 账号/站点/品牌决策仍挂起（阶段 8 才需要，本阶段不阻塞）。

## 7.1 配置（`config/settings.yaml` `codex:` 段扩展）

```yaml
codex:
  # …阶段 6 已有字段不变…
  patch_timeout_sec: 600          # 补丁生成比判因慢，独立超时
  worktree_root: "./runtime/worktrees"
  worktree_retention_days: 14     # released/rejected 后保留期，供回滚取证
  patch_allowed_paths:            # R0/R1 允许修改范围（相对仓库根）
    - "config/selectors/"
    - "src/executor/"
    - "src/capture/"
    - "tests/"
```

## 7.2 Worktree 管理（新文件 `src/repair/worktree.py`）

- 创建：`git worktree add <worktree_root>/repair-<repair_job_id>/ -b
  codex/repair-<incident_id>-<short_signature>`（short_signature = signature 哈希前 8 位）。
  worktree 根目录配置化，可放仓库外。
- 基线 overlay：见 Day 0 决策 2；基线 commit 后 `git status` 必须干净，否则建 job 失败。
- 并发去重（蓝图 §17.4）：`codex_repair_jobs` 加**部分唯一索引**
  `UNIQUE(incident_id) WHERE stage='patch' AND status IN ('running','patch_ready','validating')`
  （SQLite 支持部分索引）；同事务内"查 active + 插行"，冲突即 409。新证据只追加到既有
  incident（阶段 5 已有 occurrence 聚合），不创建竞争补丁。
- 清理：job 终态（released/rejected/closed）后超过 `worktree_retention_days` 由
  `scripts/cleanup_repair_worktrees.py`（或 Web 启动时惰性扫描）`git worktree remove`；
  保留期内不动。`git worktree prune` 兜底清理残留元数据。
- 红线落实：worktree 只含 tracked 文件（已核实私有目录均未跟踪）；脱敏证据副本放
  worktree 内未跟踪目录 `.repair-evidence/`（prompt 引用相对路径）；worktree 与生产树
  不共享任何可写 runtime 路径。

## 7.3 补丁生成（`src/codex_client/patch.py`，复用 triage 的子进程管线）

- 启动前提（蓝图 §16.1B 全量校验，任一不满足即拒绝并审计）：
  最近判因为代码/selector 类且 `safe_to_generate_patch=true`；incident 未关闭；
  无同 incident active patch job（7.2 唯一索引兜底）；worktree 创建成功。
- 调用形参（沿用阶段 6 实测口径）：
  `codex exec --json --output-schema repair-result-schema.json -o <file> -s workspace-write -C <worktree_path> -`，
  prompt 走 stdin，超时 `patch_timeout_sec`；JSONL 与 result.json 落
  `runtime/logs|state/repair/<repair_job_id>/`（与 triage 同目录规则）。
  注意：`--add-dir` 不用——可写范围仅限 worktree 本身。
- `src/codex_client/schemas/repair-result-schema.json`（严格模式：required 含全部字段）：
  `summary / changed_files[] / risk_level(R0|R1|R2|R3) / requires_human_review /
  tests_added[] / tests_ran / tests_passed / notes`。
- prompt 模板 `knowledge/prompts/codex_patch_prompt.md`（蓝图 §16.4 全量入模板）：
  安全边界；证据在 `.repair-evidence/`；允许目录 = `patch_allowed_paths`；禁止私有目录；
  优先共享 selector/状态识别；禁止账号专用 one-off；必须新增最小回归测试并在 worktree
  内跑测试；不得启动 AdsPower/真实浏览器/真实提交；业务语义变化必须停止并
  `requires_human_review=true`。证据文本同样不可信数据块包裹。
- 修改级别（蓝图 §17.3）：Codex 自评 `risk_level` + 后端按 changed_files 独立复核
  （改 `patch_allowed_paths` 内 → R0/R1；碰导航/表单步骤语义 → R2；碰 submit/法律声明/
  授权语义 → R3），取两者较严者记 job。R2 需前端人工明确允许后才启动（本阶段 API 加
  `allow_r2` 参数，默认 false）；R3 不启动补丁，只保留判因分析。

## 7.4 diff 私密扫描（新文件 `src/repair/diff_scan.py`）

- 生成后立即对 `git diff`（基线 commit → 工作区）扫描：
  - 私密串：邮箱、长数字串、AdsPower profile、私有路径、凭据模式——复用
    `src/capture/redact.py` 的 `SENSITIVE_PATTERNS`，命中即 job `validation_failed`。
  - 允许范围：changed_files 必须全部在 `patch_allowed_paths` 内（R2 经人工允许时按
    单独白名单放宽，本阶段先不支持范围外文件）。
  - 蓝图 §18.1 必须拒绝规则实现为静态检查：移除 `--submit` 门禁、绕过 CAPTCHA/2FA/
    限流、`Draft`/无 Case ID 直接映射 success/failed、删除证据保存/Dashboard 检查/
    人工复核逻辑、无限重试或缩短冷却、测试中调真实 Seller Central——以 diff 关键词/
    文件对规则表实现（可扩展配置），命中即拒绝。
- 扫描通过的 diff 存 `runtime/state/repair/<job_id>/patch.diff`，job → `patch_ready`；
  Codex 改动 commit 为分支第二 commit（`codex: repair incident <id>`），SHA 记 job。

## 7.5 数据与 API

- `codex_repair_jobs` 填充 `stage='patch'` 行：worktree_path/branch_name/codex_session_id/
  changed_files_json/risk_level/tests_passed（result.json 解析）。job status 增补
  `patch_ready`（其余沿用阶段 6 词表）。
- incident 状态机开放 `triaged → patching → patch_ready`（生成中/补丁就绪）；
  失败回 `triaged` 可重试。
- 端点：
  - `POST /api/incidents/{id}/generate-patch`（operator+；`allow_r2` 默认 false；
    前提不满足/并发冲突 409；全部路径审计 action=`patch_generate`）。
  - `GET /api/repair-jobs/{id}`（viewer+：job 全字段 + result.json）。
  - `GET /api/repair-jobs/{id}/diff`（viewer+：patch.diff 原文，仅在 codex_state_root
    内读取，与阶段 6 result.json 同一 allowlist 机制）。
- 本阶段**不实现** approve/release 端点（阶段 8）。

## 7.6 前端修复中心（`RepairCenterPage.vue` 正式化）

- incident 详情"Codex 判因"卡片上启用"生成补丁"按钮：`safe_to_generate_patch=true`
  且非 active 时可用（R2 弹确认框带 `allow_r2`）；进行中 loading + 超时提示（最长约
  10 分钟）；结果写回 store。
- 新增"补丁"区：worktree 路径、分支名、risk_level、changed_files 列表、完整 diff 视图
  （`GET /api/repair-jobs/{id}/diff`，等宽字体滚动区）、Codex 自评 summary/tests 结果。
- "批准验证/批准发布/拒绝"按钮仅展示禁用占位（tooltip 说明阶段 8 开放）。
- mock 层补 patch job 样例（成功 R0、diff 扫描拒绝各一）；types/statusColors 同步。

## 7.7 测试与验收

- 单测（`tests/test_repair_worktree.py`、`tests/test_diff_scan.py`、
  `tests/test_codex_patch.py`）：
  - worktree 创建/基线 overlay/唯一索引去重/清理（用临时 git 仓库夹具）；
  - diff 扫描全规则（§18.1 每条至少一个命中用例 + 干净 diff 不误报）；
  - 允许范围检查；R0–R3 级别判定（含前后端取较严者）；
  - 前提校验全分支（未判因/不安全/已关闭/并发冲突）；超时与 schema 重试沿用 triage 模式。
- 集成（`tests/web/test_repair_patch_api.py`）：mock codex 在隔离 worktree 生成一个真实
  selector 修改，断言**生产树 `git status` 全程无变化**、diff/审计/incident 状态正确。
- 真实联调（Day 0 前置 1 完成后）：一个真实 incident 走完 判因→生成补丁→扫描→
  `patch_ready`，人工核对 diff 合理性（不合并）。
- 验收（对齐蓝图）：Codex 只能在隔离 worktree 修改；生产工作树无直接变化。

## 工作量与顺序

1. Day 0 前置（证据包回填）（0.5–1 天）
2. worktree 管理 + 去重 + 清理（1 天）
3. patch.py + schema + prompt 模板（1 天）
4. diff 扫描（0.5–1 天）
5. API + incident 状态机（0.5 天）
6. 前端（1 天）
7. 测试 + 真实联调 + 文档（1 天）

合计约 5–6.5 工作日，落在蓝图 5–8 天区间。交付节奏不变：实施 → 全量
pytest/ruff/vue-tsc → 更新 `docs/CURRENT_STATE.md` → 受控 UAT。

## 红线（本阶段不变）

- Codex 补丁只在隔离 worktree；生产树零直接变化；本阶段无任何合并/发布动作。
- Codex 永不接触 `runtime/private/`、`.env`、`data/`、`brand_packs`、真实浏览器/AdsPower。
- 排除清单（CAPTCHA/2FA/429/410001/登录过期/账号风险）永不进 patch 队列。
- R3 不生成补丁；R2 未经人工明确允许不启动；§18.1 命中一律拒绝。

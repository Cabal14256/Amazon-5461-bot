import type {
  DiffScanViolation,
  GeneratePatchOutcome,
  Incident,
  IncidentBundleFile,
  LatestTriage,
  PatchResult,
  RepairIncident,
  IncidentTestResult,
  RepairJob,
} from '@/types'

function minsAgo(min: number): string {
  return new Date(Date.now() - min * 60_000).toISOString()
}

/** 验证门禁 12 步（与修复流水线对齐） */
function gate12(passedUpTo: number, failedAt: number | null = null): IncidentTestResult[] {
  const steps = [
    '语法检查（ruff）',
    '类型检查（mypy 关键路径）',
    '单元测试（pytest）',
    '选择器静态校验',
    'dry-run 冒烟（诊断模式）',
    'dry-run 冒烟（单品牌）',
    '证据落盘检查',
    '状态机一致性检查',
    '回滚可用性检查',
    '金丝雀任务（单账号单品牌）',
    '金丝雀结果复核',
    '全量回归（历史证据重放）',
  ]
  return steps.map((step, i) => {
    const idx = i + 1
    if (failedAt !== null && idx === failedAt) return { step, status: 'failed', detail: '未通过，已自动回滚候选补丁' }
    if (idx <= passedUpTo) return { step, status: 'passed', detail: null }
    return { step, status: 'pending', detail: null }
  })
}

const sampleDiff = `--- a/config/selectors/sellercentral_us.yaml
+++ b/config/selectors/sellercentral_us.yaml
@@ -14,7 +14,8 @@ apply_5461:
   qualification_next: "button#sc-content button[type='submit']"
-  submit_button: "button[data-testid='submit-application']"
+  submit_button: "button[data-testid='submit-application'], button[aria-label*='Submit application']"
+  submit_button_fallback: "form[action*='sell-application'] button[type='submit']"
   success_banner: "div[data-testid='case-created-confirmation']"
@@ -22,3 +23,5 @@ apply_5461:
   case_id_pattern: "Case ID[:：]\\\\s*(\\\\d{8,})"
+  captcha_marker: "form[action*='validateCaptcha'], img[src*='captcha']"
+  captcha_marker_note: "2026-08-09 改版后新增的显式标记，命中即转 waiting_human"
`

/** Codex 修复流水线演示数据（阶段 6-7 语义，仅 Mock 模式的修复中心演示使用） */
export const mockRepairIncidents: RepairIncident[] = [
  {
    id: 'inc-demo-ui',
    signature: 'selector_miss:submit_button@apply_5461/US',
    scope: '5461 申请 · US 站 · 全部账号',
    occurrences: 7,
    confidence: 0.86,
    status: 'awaiting_review',
    classification: 'selector_drift（疑似页面改版：提交按钮 testid 变更且新增 aria-label）',
    missingEvidence: ['UK/DE 站是否同步改版尚无证据', '改版前后完整 DOM 对比仅覆盖 US'],
    worktree: 'runtime/worktrees/inc-demo-ui',
    branch: 'codex/fix-submit-button-20260809',
    changedFiles: ['config/selectors/sellercentral_us.yaml'],
    diff: sampleDiff,
    tests: gate12(9),
    remainingRisks: [
      'fallback 选择器较宽，理论上有误命中其他提交按钮的可能（已在 dry-run 冒烟中排除当前页面）',
      '若亚马逊 A/B 测试两种页面并存，可能出现部分会话仍命中旧结构',
    ],
    detectedAt: minsAgo(60 * 26),
    updatedAt: minsAgo(60 * 3),
  },
  {
    id: 'inc-demo-config',
    signature: 'precheck_gap:brand_pack_missing@apply_5461',
    scope: '预检逻辑 · 全部站点',
    occurrences: 1,
    confidence: 0.93,
    status: 'validation_failed',
    classification: 'code_defect（预检未校验声明文件存在性，导致 DEMO_TECH 进入流程后才失败）',
    missingEvidence: [],
    worktree: 'runtime/worktrees/inc-demo-config',
    branch: 'codex/precheck-brand-pack-20260809',
    changedFiles: ['src/precheck.py', 'tests/test_brand_selection.py'],
    diff: `--- a/src/precheck.py
+++ b/src/precheck.py
@@ -31,6 +31,9 @@ def check_brand_pack(brand: str) -> PrecheckResult:
     if not pack_dir.exists():
         return PrecheckResult.failed(f"品牌包目录缺失: {pack_dir}")
+    statement = pack_dir / "statement.txt"
+    if not statement.exists():
+        return PrecheckResult.failed(f"品牌 {brand} 缺少声明文件 statement.txt")
     return PrecheckResult.ok()
`,
    tests: gate12(4, 5),
    remainingRisks: ['验证门禁第 5 步 dry-run 冒烟失败：新增校验把 2 个在途品牌误拦，需调整例外逻辑后重跑'],
    detectedAt: minsAgo(60 * 25),
    updatedAt: minsAgo(60 * 20),
  },
  {
    id: 'inc-2026-0808-03',
    signature: 'page_structure:catalog_auth_table@catalog_auth/UK',
    scope: '目录授权 · UK 站',
    occurrences: 3,
    confidence: 0.71,
    status: 'repairing',
    classification: 'selector_drift（授权结果表格由 table 改为 div grid 布局）',
    missingEvidence: ['仅有 3 次失败样本，置信度中等'],
    worktree: 'runtime/worktrees/inc-2026-0808-03',
    branch: 'codex/catalog-auth-grid-20260808',
    changedFiles: ['config/selectors/sellercentral_uk.yaml'],
    diff: null,
    tests: gate12(0),
    remainingRisks: [],
    detectedAt: minsAgo(60 * 50),
    updatedAt: minsAgo(60 * 6),
  },
  {
    id: 'inc-2026-0806-04',
    signature: 'timeout:dashboard_poll@case_followup',
    scope: 'Case follow-up · 全部站点',
    occurrences: 5,
    confidence: 0.78,
    status: 'canary_failed',
    classification: 'code_defect（轮询间隔过短触发 429，已改为指数退避）',
    missingEvidence: [],
    worktree: 'runtime/worktrees/inc-2026-0806-04',
    branch: 'codex/case-followup-backoff-20260806',
    changedFiles: ['src/case_followup.py'],
    diff: `--- a/src/case_followup.py
+++ b/src/case_followup.py
@@ -88,7 +88,7 @@ async def poll_case(case_id: str) -> CaseStatus:
-    await asyncio.sleep(30)
+    await asyncio.sleep(backoff.next())  # 指数退避，429 时冷却
`,
    tests: gate12(10, 11),
    remainingRisks: ['金丝雀复核发现退避上限 300s 仍偏短，已拒绝发布并退回修复'],
    detectedAt: minsAgo(60 * 96),
    updatedAt: minsAgo(60 * 12),
  },
  {
    id: 'inc-2026-0805-05',
    signature: 'selector_miss:brand_search_input@brand_verify/MX',
    scope: '品牌验证 · MX 站',
    occurrences: 4,
    confidence: 0.9,
    status: 'released',
    classification: 'selector_drift（搜索框 placeholder 文案变更导致定位失败）',
    missingEvidence: [],
    worktree: 'runtime/worktrees/inc-2026-0805-05',
    branch: 'codex/brand-search-mx-20260805',
    changedFiles: ['config/selectors/sellercentral_mx.yaml'],
    diff: null,
    tests: gate12(12),
    remainingRisks: [],
    detectedAt: minsAgo(60 * 120),
    updatedAt: minsAgo(60 * 100),
  },
  {
    id: 'inc-2026-0810-06',
    signature: 'unknown_page:after_login@672',
    scope: '登录后流程 · MX 站',
    occurrences: 1,
    confidence: 0.42,
    status: 'detected',
    classification: 'pending_triage（登录后出现未知插页，尚未判因）',
    missingEvidence: ['页面截图已保留但 DOM 快照缺失', '仅 1 次出现，无法判断是个例还是改版'],
    worktree: null,
    branch: null,
    changedFiles: [],
    diff: null,
    tests: gate12(0),
    remainingRisks: [],
    detectedAt: minsAgo(90),
    updatedAt: minsAgo(90),
  },
]


/* ---------- 阶段 5：持久化异常检测 incident 队列（GET /api/incidents 契约形状） ---------- */

function hoursAgo(h: number): string {
  return new Date(Date.now() - h * 3_600_000).toISOString()
}

function hexSig(seed: number): string {
  return seed.toString(16).padStart(16, '0').slice(0, 16)
}

/** Mock incident：3 条人工处理类 open + 4 条修复候选类 + 1 条 closed_human */
export const mockIncidents: Incident[] = [
  {
    id: 101,
    signature: hexSig(0xa1c9e04b7d23f108),
    scope_type: 'account_site',
    flow_type: 'apply_5461',
    account_id: '672',
    marketplace: 'mx',
    brand_name: 'DEMO_FLEX',
    detector_type: 'captcha_detector',
    classification: 'captcha',
    confidence: 0.98,
    status: 'open',
    occurrence_count: 2,
    first_seen_at: hoursAgo(30),
    last_seen_at: hoursAgo(3),
    evidence_bundle_path: 'evidence/2026-08-10/inc_101',
    resolution_note: null,
    codex_thread_id: null,
  },
  {
    id: 102,
    signature: hexSig(0x5b3d77aa01c9e246),
    scope_type: 'account_site',
    flow_type: null,
    account_id: '670',
    marketplace: 'uk',
    brand_name: null,
    detector_type: 'http_error_detector',
    classification: 'rate_limit',
    confidence: 0.99,
    status: 'open',
    occurrence_count: 5,
    first_seen_at: hoursAgo(52),
    last_seen_at: hoursAgo(6),
    evidence_bundle_path: 'evidence/2026-08-09/inc_102',
    resolution_note: null,
    codex_thread_id: null,
  },
  {
    id: 103,
    signature: hexSig(0x88f1c2d49b05e637),
    scope_type: 'brand',
    flow_type: 'apply_5461',
    account_id: '668',
    marketplace: 'de',
    brand_name: 'DEMO_TECH',
    detector_type: 'result_contract_detector',
    classification: 'business_uncertain',
    confidence: 0.64,
    status: 'open',
    occurrence_count: 1,
    first_seen_at: hoursAgo(20),
    last_seen_at: hoursAgo(20),
    evidence_bundle_path: 'evidence/2026-08-10/inc_103',
    resolution_note: null,
    codex_thread_id: null,
  },
  {
    id: 104,
    signature: hexSig(0x1de04ac6b7928355),
    scope_type: 'site',
    flow_type: 'apply_5461',
    account_id: null,
    marketplace: 'us',
    brand_name: 'DEMO_ALPHA',
    detector_type: 'selector_detector',
    classification: 'selector_missing',
    confidence: 0.87,
    status: 'patch_ready',
    occurrence_count: 7,
    first_seen_at: hoursAgo(49),
    last_seen_at: hoursAgo(4),
    evidence_bundle_path: 'evidence/2026-08-09/inc_104',
    resolution_note: null,
    codex_thread_id: null,
  },
  {
    id: 105,
    signature: hexSig(0x33ab90cd12e5f784),
    scope_type: 'site',
    flow_type: 'catalog_auth',
    account_id: null,
    marketplace: 'uk',
    brand_name: null,
    detector_type: 'dom_contract_detector',
    classification: 'dom_contract_changed',
    confidence: 0.72,
    status: 'open',
    occurrence_count: 3,
    first_seen_at: hoursAgo(28),
    last_seen_at: hoursAgo(9),
    evidence_bundle_path: 'evidence/2026-08-10/inc_105',
    resolution_note: null,
    codex_thread_id: null,
  },
  {
    id: 106,
    signature: hexSig(0x7c04d1a85f96b320),
    scope_type: 'account_site',
    flow_type: null,
    account_id: '672',
    marketplace: 'mx',
    brand_name: null,
    detector_type: 'page_state_detector',
    classification: 'state_unknown',
    confidence: 0.41,
    status: 'closed_human',
    occurrence_count: 1,
    first_seen_at: hoursAgo(80),
    last_seen_at: hoursAgo(80),
    evidence_bundle_path: 'evidence/2026-08-08/inc_106',
    resolution_note: '人工确认为一次性网络抖动导致的白屏，非页面改版，无需修复。',
    codex_thread_id: null,
  },
  // 107：判因通过但补丁 diff 扫描拒绝（validation_failed，演示"补丁"区的失败态与 violations 列表）
  {
    id: 107,
    signature: hexSig(0x4fa92c10de83b756),
    scope_type: 'site',
    flow_type: 'apply_5461',
    account_id: null,
    marketplace: 'de',
    brand_name: 'DEMO_TECH',
    detector_type: 'selector_detector',
    classification: 'selector_missing',
    confidence: 0.81,
    status: 'triaged',
    occurrence_count: 3,
    first_seen_at: hoursAgo(26),
    last_seen_at: hoursAgo(11),
    evidence_bundle_path: 'evidence/2026-08-10/inc_107',
    resolution_note: null,
    codex_thread_id: null,
  },
  // 108：判因通过，后端复核为 R2（演示 allow_r2 确认框：首次 r2_not_allowed，确认后 patch_ready）
  {
    id: 108,
    signature: hexSig(0x92bd04c3a718e6f5),
    scope_type: 'site',
    flow_type: 'catalog_auth',
    account_id: null,
    marketplace: 'uk',
    brand_name: null,
    detector_type: 'navigation_detector',
    classification: 'navigation_changed',
    confidence: 0.77,
    status: 'triaged',
    occurrence_count: 4,
    first_seen_at: hoursAgo(18),
    last_seen_at: hoursAgo(5),
    evidence_bundle_path: 'evidence/2026-08-11/inc_108',
    resolution_note: null,
    codex_thread_id: null,
  },
]

/** Mock 详情：按 evidence_bundle_path 拼出固定的证据包文件清单 */
export function mockIncidentBundleFiles(incident: Incident): IncidentBundleFile[] {
  if (!incident.evidence_bundle_path) return []
  const dir = incident.evidence_bundle_path
  return [
    { name: 'screenshot.png', path: `${dir}/screenshot.png`, size: 182_340, kind: 'screenshot' },
    { name: 'page_dump.html', path: `${dir}/page_dump.html`, size: 96_120, kind: 'page_dump' },
    { name: 'detector_context.json', path: `${dir}/detector_context.json`, size: 2_048, kind: 'log' },
  ]
}

/* ---------- 阶段 6：Codex 只读判因 Mock（覆盖成功判因 / 证据不足 / Codex 不可用三种） ---------- */

/** 按 incident id 提供 latest_triage 样例；未列出的 incident 表示尚无判因（null） */
export const mockLatestTriage: Record<number, LatestTriage | null> = {
  // 104：成功判因（selector_change，safe_to_generate_patch=true → 演示"生成补丁"禁用占位按钮）
  104: {
    job: {
      id: 9001,
      incident_id: 104,
      stage: 'triage',
      status: 'succeeded',
      jsonl_log_path: 'runtime/logs/repair/9001/codex-events.jsonl',
      result_json_path: 'runtime/state/repair/9001/result.json',
      created_at: hoursAgo(4),
      finished_at: hoursAgo(3.9),
    },
    result: {
      classification: 'selector_change',
      confidence: 0.91,
      reason:
        '证据包 page_dump 显示提交按钮 data-testid 由 submit-application 变更为 submit-application-v2，' +
        '且新增 aria-label；其余流程步骤选择器均正常命中，判断为局部选择器变更而非整页改版。',
      affected_components: ['config/selectors/sellercentral_us.yaml', 'apply_5461 提交步骤'],
      recommended_scope: '仅更新 US 站 5461 提交按钮选择器并补充 fallback，其他站点与流程不受影响',
      safe_to_generate_patch: true,
      requires_human_review: true,
      missing_evidence: [],
    },
  },
  // 105：证据不足（insufficient_evidence + missing_evidence 清单）
  105: {
    job: {
      id: 9002,
      incident_id: 105,
      stage: 'triage',
      status: 'succeeded',
      jsonl_log_path: 'runtime/logs/repair/9002/codex-events.jsonl',
      result_json_path: 'runtime/state/repair/9002/result.json',
      created_at: hoursAgo(9),
      finished_at: hoursAgo(8.9),
    },
    result: {
      classification: 'insufficient_evidence',
      confidence: 0.35,
      reason:
        'dom_contract_changed 命中但证据包缺少改版前的基准 DOM，无法区分是授权结果表格结构变更' +
        '还是账号侧内容为空导致的渲染差异，暂不判因。',
      affected_components: [],
      recommended_scope: '补齐基准证据后重新判因',
      safe_to_generate_patch: false,
      requires_human_review: true,
      missing_evidence: ['授权结果页的基准（正常态）DOM 快照', '同一账号在 UK 站的手工页面截图'],
    },
  },
  // 106：Codex 不可用（job 结清为 unavailable，无 result → 演示顶部降级提示条）
  106: {
    job: {
      id: 9003,
      incident_id: 106,
      stage: 'triage',
      status: 'unavailable',
      jsonl_log_path: null,
      result_json_path: null,
      created_at: hoursAgo(78),
      finished_at: hoursAgo(78),
    },
    result: null,
  },
  // 107：成功判因（safe_to_generate_patch=true → 生成补丁后 diff 扫描拒绝，见 mockPatchJobs）
  107: {
    job: {
      id: 9004,
      incident_id: 107,
      stage: 'triage',
      status: 'succeeded',
      jsonl_log_path: 'runtime/logs/repair/9004/codex-events.jsonl',
      result_json_path: 'runtime/state/repair/9004/result.json',
      created_at: hoursAgo(12),
      finished_at: hoursAgo(11.9),
    },
    result: {
      classification: 'selector_change',
      confidence: 0.84,
      reason:
        '证据包显示 DE 站 5461 资格页"下一步"按钮由 button[type=submit] 改为 role=button 的 div，' +
        '其余步骤选择器正常，判断为局部控件重写。',
      affected_components: ['config/selectors/sellercentral_de.yaml', 'apply_5461 资格页步骤'],
      recommended_scope: '仅更新 DE 站资格页下一步按钮选择器',
      safe_to_generate_patch: true,
      requires_human_review: false,
      missing_evidence: [],
    },
  },
  // 108：成功判因（safe_to_generate_patch=true → 后端复核 R2，演示 allow_r2 确认流程）
  108: {
    job: {
      id: 9005,
      incident_id: 108,
      stage: 'triage',
      status: 'succeeded',
      jsonl_log_path: 'runtime/logs/repair/9005/codex-events.jsonl',
      result_json_path: 'runtime/state/repair/9005/result.json',
      created_at: hoursAgo(6),
      finished_at: hoursAgo(5.9),
    },
    result: {
      classification: 'page_state_change',
      confidence: 0.79,
      reason:
        'UK 站目录授权流程在提交前新增了一个确认插页，导航步骤多一跳；证据包含改版前后两份 page_dump，' +
        '可确认新增插页的"Continue"按钮定位。涉及导航步骤语义，后端复核将定为 R2。',
      affected_components: ['src/executor/catalog_auth.py', 'config/selectors/sellercentral_uk.yaml'],
      recommended_scope: '在授权流程导航中插入确认插页处理步骤',
      safe_to_generate_patch: true,
      requires_human_review: true,
      missing_evidence: [],
    },
  },
}

/* ---------- 阶段 7：隔离生成补丁 Mock（成功 R0 / diff 扫描拒绝 / R2 需人工允许 三种） ---------- */

/** 单个 incident 的 patch job 样例：job 行 + result.json 内容 + patch.diff 原文 + 扫描命中项 */
export interface MockPatchSample {
  /** POST generate-patch 的 200 outcome */
  outcome: GeneratePatchOutcome
  job: RepairJob
  result: PatchResult | null
  /** patch.diff 原文；validation_failed 时扫描未通过、不落盘，为 null */
  diff: string | null
  violations: DiffScanViolation[]
  /** true 时 allow_r2=false 返回 r2_not_allowed，allow_r2=true 才返回 outcome 并把 job 落为 patch_ready */
  r2Gate?: boolean
}

/** 104 的 patch.diff 原文（R0，仅改允许范围内的 selector 与测试） */
const mockDiff104 = `diff --git a/config/selectors/sellercentral_us.yaml b/config/selectors/sellercentral_us.yaml
index 3f8a1c2..9d4e7b0 100644
--- a/config/selectors/sellercentral_us.yaml
+++ b/config/selectors/sellercentral_us.yaml
@@ -14,7 +14,8 @@ apply_5461:
   qualification_next: "button#sc-content button[type='submit']"
-  submit_button: "button[data-testid='submit-application']"
+  submit_button: "button[data-testid='submit-application-v2'], button[data-testid='submit-application']"
+  submit_button_fallback: "button[aria-label*='Submit application']"
   success_banner: "div[data-testid='case-created-confirmation']"
diff --git a/tests/test_selector_contract_us.py b/tests/test_selector_contract_us.py
new file mode 100644
index 0000000..b2c5d11
--- /dev/null
+++ b/tests/test_selector_contract_us.py
@@ -0,0 +1,18 @@
+"""US 站 5461 提交按钮选择器回归（incident #104）。
+
+证据包 page_dump 显示 data-testid 由 submit-application 变更为
+submit-application-v2；断言新选择器与 fallback 均能命中改版后 DOM。
+"""
+
+from pathlib import Path
+
+from src.capture.dom import load_dump
+from src.executor.selector_contract import resolve_selector
+
+
+def test_submit_button_matches_new_testid() -> None:
+    dump = load_dump(Path("tests/fixtures/us_apply_5461_submit_v2.html"))
+    assert resolve_selector(dump, "apply_5461.submit_button", site="us")
+    assert resolve_selector(dump, "apply_5461.submit_button_fallback", site="us")
`

/** 108 允许 R2 后的 patch.diff 原文（授权流程导航插入确认插页步骤） */
const mockDiff108 = `diff --git a/config/selectors/sellercentral_uk.yaml b/config/selectors/sellercentral_uk.yaml
index 71c2a90..c8f3e4d 100644
--- a/config/selectors/sellercentral_uk.yaml
+++ b/config/selectors/sellercentral_uk.yaml
@@ -30,4 +30,6 @@ catalog_auth:
   brands_table_row: "div[role='row'][data-brand-name]"
+  confirm_interstitial: "div[data-testid='catalog-auth-confirm-interstitial']"
+  confirm_interstitial_continue: "div[data-testid='catalog-auth-confirm-interstitial'] button[aria-label='Continue']"
   result_banner: "div[data-testid='catalog-auth-result']"
diff --git a/src/executor/catalog_auth.py b/src/executor/catalog_auth.py
index a0e4f12..d77c9ab 100644
--- a/src/executor/catalog_auth.py
+++ b/src/executor/catalog_auth.py
@@ -142,6 +142,11 @@ async def navigate_to_submit(self) -> None:
         await self._open_brand_picker()
         await self._select_brand()
+        # 2026-08-11 改版：提交前新增确认插页，命中则多点一次 Continue
+        if await self.page.is_visible(sel("catalog_auth.confirm_interstitial")):
+            await self.page.click(sel("catalog_auth.confirm_interstitial_continue"))
+            await self.page.wait_for_load_state("networkidle")
         await self._expect_submit_form()
diff --git a/tests/test_catalog_auth_interstitial.py b/tests/test_catalog_auth_interstitial.py
new file mode 100644
index 0000000..f4a9c32
--- /dev/null
+++ b/tests/test_catalog_auth_interstitial.py
@@ -0,0 +1,14 @@
+"""UK 站目录授权确认插页回归（incident #108）。"""
+
+from pathlib import Path
+
+from src.capture.dom import load_dump
+from src.executor.selector_contract import resolve_selector
+
+
+def test_interstitial_selectors_resolve() -> None:
+    dump = load_dump(Path("tests/fixtures/uk_catalog_auth_interstitial.html"))
+    assert resolve_selector(dump, "catalog_auth.confirm_interstitial", site="uk")
+    assert resolve_selector(dump, "catalog_auth.confirm_interstitial_continue", site="uk")
`

/** 按 incident id 提供 patch job 样例；未列出的 incident 表示尚无补丁任务 */
export const mockPatchJobs: Record<number, MockPatchSample> = {
  // 104：成功 R0（扫描通过，patch_ready；diff 有原文）
  104: {
    outcome: 'patch_ready',
    job: {
      id: 9101,
      incident_id: 104,
      stage: 'patch',
      status: 'patch_ready',
      worktree_path: 'runtime/worktrees/repair-9101',
      branch_name: 'codex/repair-104-1de04ac6',
      codex_session_id: 'sess-9101-mock',
      jsonl_log_path: 'runtime/logs/repair/9101/codex-events.jsonl',
      result_json_path: 'runtime/state/repair/9101/result.json',
      changed_files: ['config/selectors/sellercentral_us.yaml', 'tests/test_selector_contract_us.py'],
      risk_level: 'R0',
      tests_passed: 1,
      baseline_sha: 'a41f9c02e6b847d0c1a25f6b90d3e7c48f1a2b6d',
      patch_sha: '7d2e9b41c0a35f68b1d4c2e7a90f53d8c6e41b09',
      created_at: hoursAgo(3.8),
      finished_at: hoursAgo(3.2),
    },
    result: {
      summary:
        '按判因结论仅更新 US 站 5461 提交按钮选择器：主选择器改为新 testid（保留旧值兼容 A/B 并存），' +
        '并新增 aria-label fallback；同步补充一条基于证据包 DOM 的选择器契约回归测试。',
      changed_files: ['config/selectors/sellercentral_us.yaml', 'tests/test_selector_contract_us.py'],
      risk_level: 'R0',
      requires_human_review: true,
      tests_added: ['tests/test_selector_contract_us.py'],
      tests_ran: true,
      tests_passed: true,
      notes: '未触碰提交语义与结果解析；worktree 内 pytest 该用例通过。fallback 选择器较宽，建议人工复核 diff。',
    },
    diff: mockDiff104,
    violations: [],
  },
  // 107：diff 扫描拒绝（命中蓝图 §18.1 必须拒绝规则；job 结清 validation_failed，无 diff 落盘）
  107: {
    outcome: 'validation_failed',
    job: {
      id: 9102,
      incident_id: 107,
      stage: 'patch',
      status: 'validation_failed',
      worktree_path: 'runtime/worktrees/repair-9102',
      branch_name: 'codex/repair-107-4fa92c10',
      codex_session_id: 'sess-9102-mock',
      jsonl_log_path: 'runtime/logs/repair/9102/codex-events.jsonl',
      result_json_path: 'runtime/state/repair/9102/result.json',
      changed_files: ['src/executor/apply_5461.py'],
      risk_level: 'R1',
      tests_passed: 0,
      baseline_sha: 'a41f9c02e6b847d0c1a25f6b90d3e7c48f1a2b6d',
      patch_sha: null,
      created_at: hoursAgo(11),
      finished_at: hoursAgo(10.4),
    },
    result: {
      summary:
        '更新 DE 站资格页下一步按钮选择器；同时在执行器中把该步骤失败时的处理由"重试并继续"改为直接' +
        '跳过后续步骤，以规避按钮定位偶发失败。',
      changed_files: ['src/executor/apply_5461.py'],
      risk_level: 'R1',
      requires_human_review: true,
      tests_added: [],
      tests_ran: false,
      tests_passed: false,
      notes: '选择器修改本身合理，但附带的流程跳过逻辑改变了失败处理语义。',
    },
    diff: null,
    violations: [
      {
        rule_id: 'no_evidence_removal',
        detail: 'diff 删除了失败路径上的证据保存调用（capture_evidence），命中必须拒绝规则',
        file: 'src/executor/apply_5461.py',
      },
      {
        rule_id: 'no_semantic_skip',
        detail: 'diff 在步骤失败时直接 return 跳过剩余流程，属于绕过状态机的语义变更',
        file: 'src/executor/apply_5461.py',
      },
    ],
  },
  // 108：R2 门槛（首次 allow_r2=false 返回 r2_not_allowed；确认后 allow_r2=true 才生成并落 patch_ready）
  108: {
    outcome: 'patch_ready',
    r2Gate: true,
    job: {
      id: 9103,
      incident_id: 108,
      stage: 'patch',
      status: 'validation_failed',
      worktree_path: 'runtime/worktrees/repair-9103',
      branch_name: 'codex/repair-108-92bd04c3',
      codex_session_id: 'sess-9103-mock',
      jsonl_log_path: 'runtime/logs/repair/9103/codex-events.jsonl',
      result_json_path: 'runtime/state/repair/9103/result.json',
      changed_files: ['config/selectors/sellercentral_uk.yaml', 'src/executor/catalog_auth.py', 'tests/test_catalog_auth_interstitial.py'],
      risk_level: 'R2',
      tests_passed: 1,
      baseline_sha: 'a41f9c02e6b847d0c1a25f6b90d3e7c48f1a2b6d',
      patch_sha: null,
      created_at: hoursAgo(5.5),
      finished_at: hoursAgo(5),
    },
    result: {
      summary:
        '在 UK 站目录授权导航中插入确认插页处理：命中插页时点击 Continue 并等待网络空闲后再断言提交表单；' +
        '选择器与插页步骤均补充了回归测试。改动触及导航步骤语义，后端复核为 R2。',
      changed_files: ['config/selectors/sellercentral_uk.yaml', 'src/executor/catalog_auth.py', 'tests/test_catalog_auth_interstitial.py'],
      risk_level: 'R2',
      requires_human_review: true,
      tests_added: ['tests/test_catalog_auth_interstitial.py'],
      tests_ran: true,
      tests_passed: true,
      notes: '仅在插页存在时多一跳；未改动任何 submit / 法律声明 / 授权语义。',
    },
    diff: null,
    violations: [],
  },
}

/** Mock 用：R2 门槛确认后把 108 的样例落为 patch_ready（模拟后端第二次生成的新 job） */
export function mockApproveR2Sample(incidentId: number): void {
  const sample = mockPatchJobs[incidentId]
  if (!sample?.r2Gate) return
  sample.r2Gate = false
  sample.job = {
    ...sample.job,
    id: sample.job.id + 1,
    status: 'patch_ready',
    patch_sha: 'c3e58d20a7b41f96d0c2e5a8b3f71d46c9a08e15',
    created_at: hoursAgo(0.2),
    finished_at: hoursAgo(0.05),
  }
  sample.diff = mockDiff108
}

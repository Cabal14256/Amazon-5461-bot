// 与 docs/plan-internal-console-codex-repair-2026-08-04.md §8 状态模型 / §11 API 规划对齐。
// 任务运行态与业务结果态始终分开建模、分开展示，绝不合并。

/** 任务运行态（JobRun.status）——描述自动化执行本身处于什么阶段 */
export type JobRunStatus =
  | 'queued'
  | 'starting'
  | 'running'
  | 'stop_requested'
  | 'waiting_human'
  | 'completed'
  | 'failed'
  | 'cancelled_before_start'
  | 'terminated_unknown_state'

/** 业务结果态（每品牌维度）——描述这一条品牌申请在亚马逊侧的结果 */
export type BusinessStatus =
  | 'draft'
  | 'submitted_no_case_id_pending_dashboard'
  | 'submitted'
  | 'pending'
  | 'under_review'
  | 'approved'
  | 'false_approved'
  | 'declined'
  | 'already_approved'
  | 'not_found'
  | 'partial'
  | 'failed'
  | 'error'

/** Codex 修复 incident 状态机（13 态） */
export type IncidentStatus =
  | 'detected'
  | 'triage_queued'
  | 'triaging'
  | 'not_code_issue'
  | 'repair_candidate'
  | 'repair_queued'
  | 'repairing'
  | 'validation_failed'
  | 'awaiting_review'
  | 'approved'
  | 'rejected'
  | 'released'
  | 'canary_failed'
  | 'closed'

/** 'submit' = 阶段 4 真实提交（后端 job_type 原值）；'real' 仅 Mock 遗留演示数据 */
export type JobMode = 'diagnose' | 'dry_run' | 'submit' | 'real' | 'unknown'

export type JobType = 'apply_5461' | 'catalog_auth' | 'brand_verify' | 'gtin_exemption' | 'unknown'

export interface Account {
  id: string
  /** 账号内部编号（如 670），可公开显示；绝不出现真实邮箱/密码 */
  label: string
  alias: string
  marketplaceIds: string[]
}

export interface Site {
  /** 大写站点码；真实后端站点不止 US/UK/DE/MX（还有 FR/IT/ES/NL/SE/BE/EU 等） */
  code: string
  marketplaceId: string
  name: string
}

export interface Brand {
  name: string
  /** 品牌包是否齐备（品牌百科/声明文件/素材） */
  packReady: boolean
  lastUsedAt: string | null
}

/** 任务内每个品牌的执行结果：运行态 + 业务态两个独立字段 */
export interface BrandJobResult {
  brand: string
  runStatus: JobRunStatus
  businessStatus: BusinessStatus
  caseId: string | null
  dashboardStatus: string | null
  durationSec: number | null
  note: string | null
}

export interface JobSummary {
  id: string
  type: JobType
  mode: JobMode
  accountId: string
  accountLabel: string
  site: Site['code']
  status: JobRunStatus
  /** 0-1 */
  progress: number
  brandTotal: number
  brandSucceeded: number
  brandFailed: number
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  stopReason: string | null
  /** queued 时的排队原因说明（真实模式由后端注解；Mock 演示数据缺省） */
  queueReason?: string | null
  results: BrandJobResult[]
}

export interface LogLine {
  ts: string
  level: 'DEBUG' | 'INFO' | 'WARN' | 'ERROR'
  message: string
}

export interface EvidenceItem {
  ts: string
  kind: 'screenshot' | 'page_dump' | 'dashboard' | 'case_reply' | 'log_excerpt'
  title: string
  path: string
  note: string | null
}

export interface ApplicationRecord {
  id: string
  accountLabel: string
  site: Site['code']
  brand: string
  type: JobType
  /** 本地运行结果（业务态） */
  localStatus: BusinessStatus
  /** 控制面板 / 销售申请侧状态，独立一列 */
  dashboardStatus: string | null
  /** Case 最新回复摘要，独立一列 */
  caseLastReply: string | null
  caseId: string | null
  /** 状态来源优先级说明 */
  statusSource: string
  /** read model 返回的原始 source 值（case_reply / dashboard_today / submission_case_id / batch_state / none），仅真实模式有 */
  statusSourceRaw?: string | null
  updatedAt: string
  evidence: EvidenceItem[]
}

export type PendingCategory =
  | 'captcha_2fa_login'
  | 'account_risk_unknown_ui'
  | 'rate_limit_429'
  | 'draft_no_case_conflict'
  | 'case_action_required'
  | 'ui_change_suspected'

export interface PendingItem {
  id: string
  category: PendingCategory
  title: string
  detail: string
  accountLabel: string
  brand: string | null
  jobId: string | null
  incidentId: string | null
  detectedAt: string
}

export interface IncidentTestResult {
  step: string
  status: 'passed' | 'failed' | 'skipped' | 'pending'
  detail: string | null
}

/** Codex 修复流水线 incident（阶段 6-7 语义；当前仅 Mock 演示数据使用） */
export interface RepairIncident {
  id: string
  signature: string
  scope: string
  occurrences: number
  /** 0-1 判因置信度 */
  confidence: number
  status: IncidentStatus
  classification: string
  missingEvidence: string[]
  worktree: string | null
  branch: string | null
  changedFiles: string[]
  diff: string | null
  tests: IncidentTestResult[]
  remainingRisks: string[]
  detectedAt: string
  updatedAt: string
}

/* ---------- 阶段 5：持久化异常检测 incident 队列（GET /api/incidents 契约镜像，snake_case） ---------- */

/** 修复候选类 classification（疑似页面改版，进修复中心） */
export const REPAIR_CLASSIFICATIONS = [
  'selector_missing',
  'state_unknown',
  'dom_contract_changed',
  'navigation_changed',
  'semantic_control_missing',
  'flow_loop_exhausted',
  'result_contract_changed',
] as const

/** 人工处理类 classification（待人工处理页分组用） */
export const HUMAN_CLASSIFICATIONS = [
  'captcha',
  'two_fa',
  'login_expired',
  'account_risk',
  'rate_limit',
  'brand_block',
  'config_missing',
  'amazon_platform_error',
  'business_uncertain',
] as const

/** 阶段 5/6/7 incident 状态：open / triaged（阶段 6 Codex 判因成功）/ patching / patch_ready（阶段 7）/ closed_human / closed_duplicate */
export type IncidentQueueStatus =
  | 'open'
  | 'waiting_evidence'
  | 'triaged'
  | 'patching'
  | 'patch_ready'
  | 'closed_human'
  | 'closed_duplicate'

export interface Incident {
  id: number
  /** 16 位 hex 去重签名 */
  signature: string
  scope_type: string
  flow_type: string | null
  account_id: string | null
  marketplace: string | null
  brand_name: string | null
  detector_type: string
  classification: string
  /** 0-1 判因置信度 */
  confidence: number
  status: IncidentQueueStatus
  occurrence_count: number
  first_seen_at: string
  last_seen_at: string
  evidence_bundle_path: string | null
  evidence_status: 'ready' | 'incomplete'
  missing_evidence: string[]
  evidence_checked_at: string | null
  resolution_note: string | null
  codex_thread_id: string | null
}

/* ---------- 阶段 6：Codex 只读判因（codex_repair_jobs / triage result 契约镜像，snake_case） ---------- */

/** Codex repair job 状态（codex_repair_jobs.status；阶段 6 triage 用前 7 个，阶段 7 patch 增补 patch_ready / validation_failed） */
export type RepairJobStatus =
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timeout'
  | 'unavailable'
  | 'quota_exceeded'
  | 'schema_invalid'
  | 'patch_ready'
  | 'validation_failed'
  | 'validating'
  | 'awaiting_validation_approval'
  | 'canary'
  | 'awaiting_release_approval'
  | 'releasing'
  | 'release_reconciliation_required'
  | 'release_pending_restart'
  | 'post_release_check'
  | 'release_check_failed'
  | 'released'
  | 'rejected'
  | 'rolled_back'
  | 'rolling_back'
  | 'rollback_reconciliation_required'

/** 阶段 7 修改级别（蓝图 §17.3；Codex 自评与后端文件复核取较严者） */
export type RiskLevel = 'R0' | 'R1' | 'R2' | 'R3'

/** Codex 判因七分类（triage-schema.json） */
export type TriageClassification =
  | 'selector_change'
  | 'shadow_dom_change'
  | 'page_state_change'
  | 'workflow_semantic_change'
  | 'amazon_platform_error'
  | 'account_specific_issue'
  | 'insufficient_evidence'

/** codex_repair_jobs 表镜像（RepairJobOut）；阶段 7 起 patch job 填充 worktree/branch/changed_files/risk_level/sha 字段 */
export interface RepairJob {
  id: number
  incident_id: number
  stage: string
  status: RepairJobStatus
  jsonl_log_path: string | null
  result_json_path: string | null
  codex_session_id?: string | null
  worktree_path?: string | null
  branch_name?: string | null
  /** git diff 得出的真实改动文件清单（非模型自报） */
  changed_files?: string[]
  risk_level?: RiskLevel | null
  /** result.json 的 tests_passed 转 0/1 */
  tests_passed?: number | null
  baseline_sha?: string | null
  patch_sha?: string | null
  pre_release_sha?: string | null
  release_sha?: string | null
  rollback_sha?: string | null
  validation_json_path?: string | null
  validation_pid?: number | null
  validation_started_at?: string | null
  validation_heartbeat_at?: string | null
  created_at: string | null
  finished_at: string | null
}

/** Codex 只读判因结构化结果（result.json 内容；schema 校验失败时按 insufficient_evidence 处理） */
export interface TriageResult {
  classification: TriageClassification
  /** 0-1 */
  confidence: number
  reason: string
  affected_components: string[]
  recommended_scope: string
  safe_to_generate_patch: boolean
  requires_human_review: boolean
  missing_evidence: string[]
}

/** GET /api/incidents/{id} 的 latest_triage / POST /triage 返回：最近一次 triage job + 其结果 */
export interface LatestTriage {
  job: RepairJob
  result: TriageResult | null
}

/** GET /api/incidents/{id} 返回的证据包文件条目 */
export interface IncidentBundleFile {
  name: string
  path: string
  size: number
  kind: string
}

/* ---------- 阶段 7：隔离生成补丁（generate-patch / repair-jobs 契约镜像，snake_case） ---------- */

/** Codex 补丁结构化结果（repair-result-schema.json 的 result.json 内容） */
export interface PatchResult {
  summary: string
  changed_files: string[]
  risk_level: RiskLevel
  requires_human_review: boolean
  tests_added: string[]
  offline_replay_tests: string[]
  offline_fixture_paths: string[]
  tests_ran: boolean
  tests_passed: boolean
  notes: string
}

/** POST /api/incidents/{id}/generate-patch 200 响应的 outcome（拒绝类走 409/422/503 HTTP 错误，不在此列） */
export type GeneratePatchOutcome =
  | 'patch_ready'
  | 'validation_failed'
  | 'r2_not_allowed'
  | 'timeout'
  | 'unavailable'
  | 'schema_invalid'
  | 'error'
  | 'empty_patch'
  | 'worktree_failed'

/** diff 私密扫描命中条目（蓝图 §18.1 必须拒绝规则） */
export interface DiffScanViolation {
  rule_id: string
  detail: string
  file: string | null
}

/** POST /api/incidents/{id}/generate-patch 200 响应 */
export interface GeneratePatchResponse {
  outcome: GeneratePatchOutcome
  job: RepairJob | null
  result: PatchResult | null
  incident: Incident | null
  violations: DiffScanViolation[]
}

/** GET /api/repair-jobs/{id} 响应：job 全字段 + result.json 内容 */
export interface RepairJobDetail {
  job: RepairJob
  result: PatchResult | null
  validation: RepairValidation | null
  approvals: RepairApproval[]
  canary: RepairCanaryState
  canary_jobs: RepairCanaryJob[]
  restart_required: boolean
  release_enabled: boolean
  git_operation: RepairGitOperation | null
}

export interface RepairGitOperation {
  id: number
  repair_job_id: number
  operation: 'release' | 'rollback'
  state: 'prepared' | 'git_applied' | 'completed' | 'failed' | 'manual_review'
  from_status: string
  expected_head_sha: string
  target_sha: string
  result_sha: string | null
  error_code: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface RepairValidationStep {
  name: string
  status: 'passed' | 'failed' | 'running' | 'skipped'
  duration_sec: number
  exit_code: number | null
  log_path: string | null
  failure_reason: string
  detail: Record<string, unknown>
}

export interface RepairValidation {
  version: number
  job_id: number
  status: 'running' | 'pass' | 'failed'
  failure_reason: string
  started_at?: string | null
  finished_at?: string | null
  duration_sec?: number
  steps: RepairValidationStep[]
}

export interface RepairApproval {
  id: number
  repair_job_id: number
  decision: 'approve_validation' | 'confirm_canary' | 'approve_release' | 'reject' | 'rollback'
  actor_id: number
  note: string | null
  created_at: string
}

export interface RepairCanaryState {
  diagnose?: string
  dry_run?: string
  post_release?: string
  ready_for_review?: boolean
  failure_reason?: string
  post_release_result?: string
  post_release_reason?: string
  preflight?: { name: string; ok: boolean }[]
}

export interface RepairCanaryJob {
  kind: 'diagnose' | 'dry_run' | 'post_release'
  job: AutomationJob
  items: AutomationJobItem[]
  artifacts: RepairCanaryArtifact[]
}

export interface RepairCanaryArtifact {
  root: 'evidence' | 'logs'
  path: string
  type: 'text' | 'image' | 'binary'
  size: number
}

export type HealthLevel = 'ok' | 'warn' | 'down'

export interface HealthItem {
  key: string
  name: string
  level: HealthLevel
  detail: string
  checkedAt: string
}

export interface OverviewData {
  health: HealthItem[]
  /** 业务状态分布（donut 图）；真实后端的状态值不限于 BusinessStatus 枚举 */
  businessDistribution: { status: string; count: number }[]
  runningCount: number
  queuedCount: number
  waitingHumanCount: number
  codexSignalPending: number
  caseFollowUpPending: number
  uiChangeSuspected: number
  patchesAwaitingReview: number
  /** 阶段 5 incident 队列中 open 的数量（/api/overview 的 incidents_open） */
  incidentsOpen: number
  /** 目录中的账号总数（真实模式来自 /api/catalog/accounts） */
  accountTotal?: number
}

export interface PrecheckItem {
  key: string
  label: string
  status: 'passed' | 'failed' | 'warning'
  detail: string
}

export interface NewJobPayload {
  mode: JobMode
  type: JobType
  accountId: string
  site: Site['code']
  brands: string[]
}

/* ---------- 后端契约镜像（对齐 src/web/schemas.py，保持 snake_case） ---------- */

export interface WebUser {
  id: number
  username: string
  role: string
  display_name: string | null
  last_login_at?: string | null
}

export interface BackendHealthCheck {
  ok: boolean
  detail: Record<string, unknown>
}

export interface BackendHealth {
  ok: boolean
  checks: Record<string, BackendHealthCheck>
  time: string
}

export interface BackendOverview {
  brand_status_counts: Record<string, number>
  pending_case_followups: number
  manual_review_followups: number
  pending_case_id_recoveries: number
  active_reapplication_campaigns: number
  reapplication_campaigns_by_status: Record<string, number>
  running_jobs: number
  codex_signal_pending: boolean
  /** 阶段 5 新增：incident 队列 open 数量 */
  incidents_open: number
}

export interface CatalogAccount {
  account_id: string
  marketplace: string | null
  status: string | null
  note: string | null
  domain: string | null
  item_type_keyword: string | null
}

export interface StatusResolution {
  status: string
  source: string
  detail: string | null
  checked_at: string | null
}

export interface BackendApplication {
  account_id: string
  marketplace: string
  brand_name: string
  submitted_at: string | null
  case_id: string | null
  submit_result: string | null
  authoritative: StatusResolution
}

/** 后端 AutomationJobOut 镜像（automation_jobs 表主源，snake_case） */
export interface AutomationJob {
  id: string
  /** "diagnose" | "dry_run" | "submit"（阶段 4 新增真实提交） */
  job_type: string
  run_status: JobRunStatus
  created_by: string
  account_id: string
  marketplace: string | null
  brands: string[]
  pid: number | null
  exit_code: number | null
  error_class: string | null
  /** 后端读取时注解：queued 任务的排队原因（waiting_case_followup / waiting_profile_lock / waiting_serial_queue） */
  queue_reason: string | null
  stop_requested_at: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
  updated_at: string | null
}

/** 后端 AutomationJobItemOut 镜像（automation_job_items，snake_case） */
export interface AutomationJobItem {
  id: number
  job_id: string
  account_id: string | null
  marketplace: string | null
  brand_name: string | null
  run_status: string | null
  business_status: string | null
  case_id: string | null
  dashboard_status: string | null
  started_at: string | null
  finished_at: string | null
  note: string | null
}

/**
 * 任务详情：页面消费的 JobSummary 形状 + DB 主源原始行。
 * raw/items 保留后端字段原貌（snake_case），summary/results 供既有组件复用。
 */
export interface JobDetail extends JobSummary {
  raw: AutomationJob
  items: AutomationJobItem[]
}

/** GET /api/jobs/{id}/events 的 SSE event:job 负载（AutomationJobOut） */
export type JobStatusEvent = AutomationJob

/** SSE event:log 负载；事件 id 为 1-based 行号，断线重连用 Last-Event-ID 续传 */
export interface JobLogEvent {
  line: string
}

export interface CaseFollowUp {
  id: number
  account_id: string
  marketplace: string
  brand_name: string
  case_id: string
  submitted_at: string | null
  scheduled_at: string | null
  status: string
  attempt_count: number
  last_checked_at: string | null
  completed_at: string | null
  case_status: string | null
  final_result: string | null
  decision_reason: string | null
  evidence_path: string | null
  error: string | null
}

export interface CaseIdRecovery {
  id: number
  account_id: string
  marketplace: string
  brand_name: string
  sku: string | null
  submitted_at: string | null
  scheduled_at: string | null
  status: string
  attempt_count: number
  last_checked_at: string | null
  completed_at: string | null
  case_id: string | null
  dashboard_status: string | null
  decision_reason: string | null
  evidence_path: string | null
  error: string | null
}

export interface ReapplicationAttempt {
  id: number
  campaign_id: number
  route_index: number
  site: string
  status: string
  scheduled_at: string | null
  started_at: string | null
  submitted_at: string | null
  completed_at: string | null
  case_id: string | null
  final_result: string | null
  decision_reason: string | null
  error: string | null
}

export interface ReapplicationCampaign {
  id: number
  account_id: string
  brand_name: string
  region: string
  route: string[]
  current_route_index: number
  status: string
  source_case_followup_id: number | null
  source_marketplace: string | null
  stop_reason: string | null
  created_at: string | null
  updated_at: string | null
  completed_at: string | null
  attempts: ReapplicationAttempt[]
}

export interface EligibleDeclinedCase {
  id: number
  account_id: string
  brand_name: string
  marketplace: string
  case_id: string
  completed_at: string | null
  region: string
  route: string[]
  source_route_index: number
  remaining_route: string[]
  next_site: string
}

export interface ReapplicationAuthorizationResult {
  reapplication: ReapplicationCampaign
  created: boolean
  preflight: Array<{ code: string; ok: boolean; level: string; message: string }>
  worker: { started: boolean; reason: string | null }
}

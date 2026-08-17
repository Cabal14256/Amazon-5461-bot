/**
 * 真实后端适配层：把 src/web/schemas.py 的响应结构（snake_case）转换为页面使用的类型。
 * 后端契约不变；所有出入都在这一层消化。
 */

import { apiFetch, apiFetchText, ApiError } from './http'
import { parseServerTime } from '@/utils/datetime'
import { dashboardStatusLabel } from '@/utils/statusLabels'
import type {
  Account,
  ApplicationRecord,
  AutomationJob,
  AutomationJobItem,
  BackendApplication,
  BackendHealth,
  BackendOverview,
  Brand,
  BusinessStatus,
  CaseFollowUp,
  CaseIdRecovery,
  EligibleDeclinedCase,
  CatalogAccount,
  GeneratePatchResponse,
  Incident,
  IncidentBundleFile,
  JobDetail,
  JobLogEvent,
  JobRunStatus,
  JobSummary,
  LatestTriage,
  LogLine,
  OverviewData,
  ReapplicationCampaign,
  ReapplicationAuthorizationResult,
  RepairJob,
  RepairJobDetail,
  Site,
  WebUser,
} from '@/types'

/* ---------- 认证 ---------- */

export async function fetchMe(): Promise<WebUser> {
  const r = await apiFetch<{ user: WebUser }>('/auth/me', { skipAuthRedirect: true })
  return r.user
}

export async function login(username: string, password: string): Promise<WebUser> {
  const r = await apiFetch<{ user: WebUser }>('/auth/login', {
    method: 'POST',
    body: { username, password },
    skipAuthRedirect: true,
  })
  return r.user
}

export async function logout(): Promise<void> {
  await apiFetch('/auth/logout', { method: 'POST', skipAuthRedirect: true })
}

/* ---------- 总览 ---------- */

const HEALTH_NAME: Record<string, string> = {
  db: 'SQLite 存储',
  disk: '磁盘空间',
  evidence_dir: '证据目录',
  logs_dir: '日志目录',
  adspower: 'AdsPower',
  codex_signal: 'Codex 信号',
}

function healthDetail(detail: Record<string, unknown>): string {
  if (detail.free_gb !== undefined) return `剩余 ${detail.free_gb} GB`
  if (detail.pending !== undefined) return detail.pending ? '有待处理失败信号' : '无待处理信号'
  if (detail.reachable !== undefined) {
    return detail.reachable ? '本地 API 可达' : `不可达（${String(detail.error ?? '连接失败')}）`
  }
  if (detail.journal_mode) return `journal_mode=${String(detail.journal_mode)}`
  if (detail.error) return String(detail.error)
  if (detail.path) return String(detail.path)
  return '—'
}

export async function fetchOverview(): Promise<OverviewData> {
  const [ov, health, accounts, queued] = await Promise.all([
    apiFetch<BackendOverview>('/overview'),
    apiFetch<BackendHealth>('/health'),
    apiFetch<{ total: number }>('/catalog/accounts'),
    apiFetch<{ total: number }>('/jobs', { query: { run_status: 'queued', limit: 1 } }),
  ])
  return {
    health: Object.entries(health.checks).map(([key, check]) => ({
      key,
      name: HEALTH_NAME[key] ?? key,
      level: check.ok ? 'ok' : 'down',
      detail: healthDetail(check.detail),
      checkedAt: health.time,
    })),
    businessDistribution: Object.entries(ov.brand_status_counts).map(([status, count]) => ({ status, count })),
    // running_jobs 统计 automation_jobs 全部活跃态（queued/starting/running/stop_requested/waiting_human）
    runningCount: Math.max(0, ov.running_jobs - queued.total),
    queuedCount: queued.total,
    waitingHumanCount: ov.manual_review_followups,
    codexSignalPending: ov.codex_signal_pending ? 1 : 0,
    caseFollowUpPending: ov.pending_case_followups,
    uiChangeSuspected: 0,
    patchesAwaitingReview: 0,
    incidentsOpen: ov.incidents_open ?? 0,
    accountTotal: accounts.total,
  }
}

/* ---------- 目录 ---------- */

const SITE_NAME: Record<string, string> = {
  US: '美国站',
  UK: '英国站',
  DE: '德国站',
  FR: '法国站',
  IT: '意大利站',
  ES: '西班牙站',
  NL: '荷兰站',
  SE: '瑞典站',
  BE: '比利时站',
  MX: '墨西哥站',
  EU: '欧洲站',
}

export async function fetchAccounts(): Promise<Account[]> {
  const r = await apiFetch<{ accounts: CatalogAccount[]; total: number }>('/catalog/accounts')
  return r.accounts.map((a) => ({
    id: a.account_id,
    label: a.account_id,
    alias: a.note ?? '',
    marketplaceIds: a.marketplace ? [a.marketplace] : [],
  }))
}

export interface AccountSyncResult {
  profiles_scanned: number
  enrolled: { account_id: string; marketplace: string; status: string }[]
  failed: { account_id: string; error: string }[]
  ambiguous: { account_num?: string; reason: string }[]
  no_number_count: number
  total: number
}

/** 扫描 AdsPower 新环境并登记到本地账号目录（operator 及以上） */
export async function syncAccounts(): Promise<AccountSyncResult> {
  return apiFetch<AccountSyncResult>('/catalog/accounts/sync', { method: 'POST' })
}

export async function fetchSites(): Promise<Site[]> {
  const r = await apiFetch<{ sites: { code: string; marketplace: string | null }[] }>('/catalog/sites')
  return r.sites.map((s) => {
    const code = s.code.toUpperCase()
    return { code, marketplaceId: s.marketplace ?? '', name: SITE_NAME[code] ?? `${code} 站` }
  })
}

export async function fetchBrands(): Promise<Brand[]> {
  const r = await apiFetch<{ brands: { name: string }[] }>('/catalog/brands')
  return r.brands.map((b) => ({ name: b.name, packReady: true, lastUsedAt: null }))
}

/* ---------- 申请记录 ---------- */

/** read model source 值 -> 中文说明（状态来源标签） */
export const SOURCE_LABEL: Record<string, string> = {
  case_reply: 'Case 最新回复',
  dashboard_today: '控制面板当天检查',
  submission_case_id: '提交记录（含 Case ID）',
  batch_state: '本地批次状态',
  none: '暂无数据',
}

export async function fetchApplications(): Promise<ApplicationRecord[]> {
  const r = await apiFetch<{ applications: BackendApplication[] }>('/applications', { query: { limit: 500 } })
  return r.applications.map((a) => {
    const auth = a.authoritative
    const sourceLabel = SOURCE_LABEL[auth.source] ?? auth.source
    return {
      id: `${a.account_id}|${a.marketplace}|${a.brand_name}|${a.submitted_at ?? ''}`,
      accountLabel: a.account_id,
      site: a.marketplace.toUpperCase(),
      brand: a.brand_name,
      type: 'unknown',
      localStatus: auth.status as BusinessStatus,
      dashboardStatus: auth.source === 'dashboard_today' ? dashboardStatusLabel(auth.status) : null,
      caseLastReply: auth.source === 'case_reply' ? auth.detail ?? '有回复' : null,
      caseId: a.case_id,
      statusSource: `${sourceLabel}${auth.detail ? `（${auth.detail}）` : ''}`,
      statusSourceRaw: auth.source,
      updatedAt: auth.checked_at ?? a.submitted_at ?? '',
      evidence: [],
    }
  })
}

/* ---------- 任务队列（阶段 3：automation_jobs 表主源） ---------- */

const TERMINAL_RUN_STATUSES: readonly string[] = [
  'completed',
  'failed',
  'cancelled_before_start',
  'terminated_unknown_state',
]

export const STOP_REQUESTED_HINT = '已请求停止，将在当前品牌完成后安全停下'

const ITEM_RUN_STATUS: Record<string, JobRunStatus> = {
  queued: 'queued',
  pending: 'queued',
  starting: 'starting',
  running: 'running',
  stop_requested: 'stop_requested',
  waiting_human: 'waiting_human',
  completed: 'completed',
  success: 'completed',
  failed: 'failed',
  error: 'failed',
  skipped: 'cancelled_before_start',
  cancelled_before_start: 'cancelled_before_start',
  terminated_unknown_state: 'terminated_unknown_state',
}

function itemBusinessStatus(raw: string, caseId: string | null): BusinessStatus {
  switch (raw) {
    case 'approved':
      return 'approved'
    case 'already_approved':
      return 'already_approved'
    case 'false_approved':
      return 'false_approved'
    case 'declined':
      return 'declined'
    case 'pending':
      return 'pending'
    case 'submitted':
      return 'submitted'
    case 'under_review':
      return 'under_review'
    case 'not_found':
      return 'not_found'
    case 'completed':
    case 'success':
      return caseId ? 'under_review' : 'submitted_no_case_id_pending_dashboard'
    case 'running':
      return 'under_review'
    case 'failed':
      return 'failed'
    case 'error':
      return 'error'
    case 'draft':
    case '':
      return 'draft'
    default:
      // DB 中出现未知业务态时原样展示（StatusTag 有回退），不臆造语义
      return raw as BusinessStatus
  }
}

function mapJobItem(it: AutomationJobItem): JobSummary['results'][number] {
  const rawRun = String(it.run_status ?? '')
  const caseId = it.case_id ?? null
  const rawBusiness = String(it.business_status ?? '')
  let durationSec: number | null = null
  if (it.started_at && it.finished_at) {
    const started = parseServerTime(it.started_at)
    const finished = parseServerTime(it.finished_at)
    if (started && finished) {
      const sec = Math.round((finished.getTime() - started.getTime()) / 1000)
      if (Number.isFinite(sec) && sec >= 0) durationSec = sec
    }
  }
  return {
    brand: it.brand_name ?? '—',
    runStatus: ITEM_RUN_STATUS[rawRun] ?? (rawRun as JobRunStatus) ?? 'queued',
    businessStatus: itemBusinessStatus(rawBusiness, caseId),
    caseId,
    dashboardStatus: it.dashboard_status ? dashboardStatusLabel(it.dashboard_status) : null,
    durationSec,
    note: it.note ?? null,
  }
}

export function mapAutomationJob(j: AutomationJob, items?: AutomationJobItem[]): JobSummary {
  const results = (items ?? []).map(mapJobItem)
  const brandTotal = results.length > 0 ? results.length : j.brands.length
  const brandSucceeded = results.filter((r) => r.runStatus === 'completed').length
  const brandFailed = results.filter(
    (r) => r.runStatus === 'failed' || r.runStatus === 'terminated_unknown_state',
  ).length
  const finishedItems = brandSucceeded + brandFailed + results.filter((r) => r.runStatus === 'cancelled_before_start').length

  let progress: number
  if (results.length > 0) {
    progress = finishedItems / results.length
  } else {
    progress = j.run_status === 'completed' ? 1 : 0
  }

  const mode = j.job_type === 'diagnose' || j.job_type === 'dry_run' || j.job_type === 'submit' ? j.job_type : 'unknown'

  return {
    id: j.id,
    type: 'unknown',
    mode,
    accountId: j.account_id,
    accountLabel: j.account_id,
    site: (j.marketplace ?? '—').toUpperCase(),
    status: j.run_status,
    progress,
    brandTotal,
    brandSucceeded,
    brandFailed,
    createdAt: j.created_at ?? '',
    startedAt: j.started_at,
    finishedAt: j.finished_at,
    stopReason: j.stop_requested_at ? STOP_REQUESTED_HINT : null,
    queueReason: j.queue_reason ?? null,
    results,
  }
}

export interface JobListFilters {
  run_status?: string | null
  job_type?: string | null
  account_id?: string | null
  limit?: number
  offset?: number
}

export async function fetchAutomationJobs(filters: JobListFilters = {}): Promise<{ jobs: AutomationJob[]; total: number }> {
  const r = await apiFetch<{ jobs: AutomationJob[]; total: number; limit: number; offset: number }>('/jobs', {
    query: {
      run_status: filters.run_status ?? null,
      job_type: filters.job_type ?? null,
      account_id: filters.account_id ?? null,
      limit: filters.limit ?? 100,
      offset: filters.offset ?? 0,
    },
  })
  return { jobs: r.jobs, total: r.total }
}

export async function fetchJobs(): Promise<JobSummary[]> {
  const { jobs } = await fetchAutomationJobs()
  // 列表接口不含 items；为非排队任务取一次详情以算出真实进度（本地 SQLite 读，开销小）
  const details = await Promise.all(
    jobs.map((j) => (j.run_status === 'queued' ? Promise.resolve(null) : fetchJobDetail(j.id).catch(() => null))),
  )
  return jobs.map((j, i) => {
    const d = details[i]
    return d ? mapAutomationJob(j, d.items) : mapAutomationJob(j)
  })
}

export async function fetchJobDetail(id: string): Promise<{ job: AutomationJob; items: AutomationJobItem[] }> {
  return apiFetch<{ job: AutomationJob; items: AutomationJobItem[] }>(`/jobs/${encodeURIComponent(id)}`)
}

export async function fetchJob(id: string): Promise<JobDetail | null> {
  try {
    const r = await fetchJobDetail(id)
    return { ...mapAutomationJob(r.job, r.items), raw: r.job, items: r.items }
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null
    throw e
  }
}

/* ---------- 任务创建 / 停止（阶段 3 白名单：仅 diagnose / dry_run） ---------- */

export interface JobCreatePayload {
  account: string
  brands: string[]
  site?: string | null
}

export async function createDiagnoseJob(payload: JobCreatePayload): Promise<AutomationJob> {
  const r = await apiFetch<{ job: AutomationJob }>('/jobs/diagnose', {
    method: 'POST',
    body: { account: payload.account, brands: payload.brands, site: payload.site ?? null },
  })
  return r.job
}

export async function createDryRunJob(payload: JobCreatePayload): Promise<AutomationJob> {
  const r = await apiFetch<{ job: AutomationJob }>('/jobs/dry-run', {
    method: 'POST',
    body: { account: payload.account, brands: payload.brands, site: payload.site ?? null },
  })
  return r.job
}

/* ---------- 阶段 4：真实提交（单接口，创建后直接入队，无审批/token 环节） ---------- */

/** /jobs/submit 的单条预检结果（level=blocker 未通过时后端直接 422 preflight_blocked） */
export interface SubmitPreflightCheck {
  name: string
  ok: boolean
  level: 'blocker' | 'warning'
  detail: string
}

/** POST /api/jobs/submit 200 响应；任务创建后直接为 queued，preflight 为服务端预检结果 */
export interface SubmitJobResponse {
  job: AutomationJob
  preflight: SubmitPreflightCheck[]
}

/**
 * 创建真实提交任务（reviewer/admin）：创建后直接 queued 入队，之后走既有 SSE 流。
 * 错误约定：403 submit_disabled / forbidden；422 preflight_blocked（detail 为 {error, checks}，见 ApiError.rawDetail）
 * 及 too_many_brands / unknown_account / unknown_brand:xxx / unknown_site / no_brands。
 */
export async function createSubmitJob(payload: JobCreatePayload): Promise<SubmitJobResponse> {
  return apiFetch<SubmitJobResponse>('/jobs/submit', {
    method: 'POST',
    body: { account: payload.account, brands: payload.brands, site: payload.site ?? null },
  })
}

/** 请求安全停止（operator+）：仅置停止标记，批次在当前品牌完成后退出 */
export async function requestJobStop(id: string): Promise<AutomationJob> {
  const r = await apiFetch<{ job: AutomationJob }>(`/jobs/${encodeURIComponent(id)}/request-stop`, { method: 'POST' })
  return r.job
}

/** 强制终止（admin，高危）：杀子进程并记 terminated_unknown_state，需先人工查控制面板 */
export async function forceTerminateJob(id: string): Promise<AutomationJob> {
  const r = await apiFetch<{ job: AutomationJob }>(`/jobs/${encodeURIComponent(id)}/force-terminate`, { method: 'POST' })
  return r.job
}

/* ---------- 任务日志 ---------- */

/** 后端日志行为纯文本（已脱敏、无结构化 ts），尽力识别级别供 LogViewer 着色 */
export function parseLogLine(line: string): LogLine {
  const m = line.match(/\b(ERROR|WARNING|WARN|INFO|DEBUG)\b/)
  const raw = m?.[1]
  const level: LogLine['level'] = raw === 'ERROR' ? 'ERROR' : raw === 'WARN' || raw === 'WARNING' ? 'WARN' : raw === 'DEBUG' ? 'DEBUG' : 'INFO'
  return { ts: '', level, message: line }
}

export interface JobLogTail {
  job_id: string
  stream: 'stdout' | 'stderr'
  total_lines: number
  lines: string[]
}

export async function fetchJobLogTail(id: string, stream: 'stdout' | 'stderr' = 'stdout', lines = 200): Promise<JobLogTail> {
  return apiFetch<JobLogTail>(`/jobs/${encodeURIComponent(id)}/logs`, { query: { stream, lines } })
}

/* ---------- SSE：任务状态 + 日志增量（Cookie 会话天然携带认证） ---------- */

export interface JobEventHandlers {
  /** event:job — 任务行有变化时推送（AutomationJobOut） */
  onJob: (job: AutomationJob) => void
  /** event:log — 脱敏 stdout 增量行；lineNo 为 1-based 行号（事件 id） */
  onLog: (line: string, lineNo: number) => void
  /** 连接出错/断线时触发一次（封装已关闭 EventSource，调用方应降级为轮询） */
  onError: () => void
}

export interface JobEventSubscription {
  close: () => void
}

/**
 * 订阅 GET /api/jobs/{id}/events（SSE）。
 * 服务端在任务终态自动关流；客户端收到终态 event:job 后也会主动 close，
 * 避免 EventSource 对已关闭端点无限重连。出错时不依赖浏览器自动重连，
 * 统一交回调用方降级为 5s 轮询（组件卸载时调 close()）。
 */
export function subscribeJobEvents(id: string, handlers: JobEventHandlers): JobEventSubscription {
  const source = new EventSource(`/api/jobs/${encodeURIComponent(id)}/events`)
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    source.close()
  }

  source.addEventListener('job', (ev) => {
    try {
      const job = JSON.parse((ev as MessageEvent).data as string) as AutomationJob
      handlers.onJob(job)
      if (TERMINAL_RUN_STATUSES.includes(job.run_status)) close()
    } catch (err) {
      console.warn('[jobs] 无法解析 SSE event:job 负载', err)
    }
  })

  source.addEventListener('log', (ev) => {
    try {
      const payload = JSON.parse((ev as MessageEvent).data as string) as JobLogEvent
      handlers.onLog(payload.line, Number((ev as MessageEvent).lastEventId) || 0)
    } catch (err) {
      console.warn('[jobs] 无法解析 SSE event:log 负载', err)
    }
  })

  source.onerror = () => {
    if (closed) return
    console.warn(`[jobs] SSE 连接中断（任务 ${id}），降级为 5s 轮询`)
    close()
    handlers.onError()
  }

  return { close }
}

/* ---------- Case 跟进 / Case ID 找回 / 重新申请 ---------- */

export async function fetchCaseFollowUps(status?: string): Promise<CaseFollowUp[]> {
  const r = await apiFetch<{ case_followups: CaseFollowUp[] }>('/case-followups', {
    query: { status: status ?? null },
  })
  return r.case_followups
}

export async function fetchCaseIdRecoveries(status?: string): Promise<CaseIdRecovery[]> {
  const r = await apiFetch<{ case_id_recoveries: CaseIdRecovery[] }>('/case-id-recoveries', {
    query: { status: status ?? null },
  })
  return r.case_id_recoveries
}

export async function fetchReapplications(status?: string): Promise<ReapplicationCampaign[]> {
  const r = await apiFetch<{ reapplications: ReapplicationCampaign[] }>('/reapplications', {
    query: { status: status ?? null },
  })
  return r.reapplications
}

export async function fetchReapplication(id: number): Promise<ReapplicationCampaign> {
  const r = await apiFetch<{ reapplication: ReapplicationCampaign }>(`/reapplications/${id}`)
  return r.reapplication
}

export async function fetchEligibleDeclinedCases(): Promise<EligibleDeclinedCase[]> {
  const response = await apiFetch<{ eligible_declines: EligibleDeclinedCase[] }>('/reapplications/eligible-declines')
  return response.eligible_declines
}

export async function authorizeDeclinedCaseReapplication(
  sourceCaseFollowupId: number,
  confirmedRemainingRoute: string[],
): Promise<ReapplicationAuthorizationResult> {
  return apiFetch<ReapplicationAuthorizationResult>('/reapplications', {
    method: 'POST',
    body: {
      source_case_followup_id: sourceCaseFollowupId,
      confirmed_remaining_route: confirmedRemainingRoute,
      authorize_submit: true,
    },
  })
}

/* ---------- 阶段 5：持久化异常检测 incident 队列 ---------- */

export interface IncidentListFilters {
  status?: string | null
  classification?: string | null
  account_id?: string | null
  marketplace?: string | null
  limit?: number
  offset?: number
}

/** GET /api/incidents（所有筛选参数可选；classification 支持逗号分隔多值） */
export async function fetchIncidents(
  filters: IncidentListFilters = {},
): Promise<{ incidents: Incident[]; total: number }> {
  return apiFetch<{ incidents: Incident[]; total: number }>('/incidents', {
    query: {
      status: filters.status ?? null,
      classification: filters.classification ?? null,
      account_id: filters.account_id ?? null,
      marketplace: filters.marketplace ?? null,
      limit: filters.limit ?? 50,
      offset: filters.offset ?? 0,
    },
  })
}

/** GET /api/incidents/{id}：incident 详情 + 证据包文件清单 + 最近一次 Codex 判因（阶段 6，无判因时 latest_triage 为 null）；404 unknown_incident */
export async function fetchIncident(
  id: number,
): Promise<{ incident: Incident; bundle_files: IncidentBundleFile[]; latest_triage: LatestTriage | null }> {
  return apiFetch<{ incident: Incident; bundle_files: IncidentBundleFile[]; latest_triage: LatestTriage | null }>(
    `/incidents/${id}`,
  )
}

/** GET /api/incidents/{id}/repair-jobs（viewer+）：Codex repair job 历史列表；404 unknown_incident */
export async function fetchRepairJobs(id: number): Promise<RepairJob[]> {
  const r = await apiFetch<{ repair_jobs: RepairJob[] }>(`/incidents/${id}/repair-jobs`)
  return r.repair_jobs
}

/**
 * POST /api/incidents/{id}/triage（operator+）：同步触发/重试 Codex 只读判因（默认 ≤120s）。
 * 错误约定：404 unknown_incident；403 forbidden；503 codex_disabled（Codex 不可用）。
 */
export async function postTriage(id: number): Promise<LatestTriage> {
  return apiFetch<LatestTriage>(`/incidents/${id}/triage`, { method: 'POST' })
}

/**
 * POST /api/incidents/{id}/close（operator+）：人工关闭 incident。
 * 错误约定：404 unknown_incident；409 incident_already_closed；403 forbidden。
 */
export async function closeIncident(id: number, note: string): Promise<Incident> {
  const r = await apiFetch<{ incident: Incident }>(`/incidents/${id}/close`, {
    method: 'POST',
    body: { note },
  })
  return r.incident
}

/* ---------- 阶段 7：隔离生成补丁 ---------- */

/**
 * POST /api/incidents/{id}/generate-patch（operator+）：在隔离 worktree 中由 Codex 生成补丁（同步等待 ≤ patch_timeout_sec，默认约 10 分钟）。
 * allowR2 默认 false；后端复核为 R2 时返回 outcome=r2_not_allowed，需人工确认后带 allow_r2=true 重发。
 * 错误约定：404 unknown_incident；409 incident_not_triaged / incident_closed / active_patch_conflict；
 * 422 unsafe_to_patch / r3_refused；503 codex_disabled。
 */
export async function postGeneratePatch(id: number, allowR2 = false): Promise<GeneratePatchResponse> {
  return apiFetch<GeneratePatchResponse>(`/incidents/${id}/generate-patch`, {
    method: 'POST',
    body: { allow_r2: allowR2 },
  })
}

/** GET /api/repair-jobs/{id}（viewer+）：job 全字段 + result.json 内容；404 unknown_job */
export async function fetchRepairJobDetail(jobId: number): Promise<RepairJobDetail> {
  return apiFetch<RepairJobDetail>(`/repair-jobs/${jobId}`)
}

/** GET /api/repair-jobs/{id}/diff（viewer+）：patch.diff 原文；404 unknown_job / diff_not_found */
export async function fetchRepairJobDiff(jobId: number): Promise<string> {
  return apiFetchText(`/repair-jobs/${jobId}/diff`)
}

/* ---------- 阶段 8：验证审批、Canary、发布与回滚 ---------- */

export async function approveRepairValidation(jobId: number, note = ''): Promise<RepairJob> {
  const r = await apiFetch<{ job: RepairJob }>(`/repair-jobs/${jobId}/approve-validation`, {
    method: 'POST',
    body: { note },
  })
  return r.job
}

export async function confirmRepairCanary(
  jobId: number,
  note: string,
  evidenceReviewed: boolean,
): Promise<RepairJob> {
  const r = await apiFetch<{ job: RepairJob }>(`/repair-jobs/${jobId}/confirm-canary`, {
    method: 'POST',
    body: { note, evidence_reviewed: evidenceReviewed },
  })
  return r.job
}

export async function approveRepairRelease(jobId: number, patchSha: string, note = ''): Promise<RepairJob> {
  const r = await apiFetch<{ job: RepairJob }>(`/repair-jobs/${jobId}/approve-release`, {
    method: 'POST',
    body: { patch_sha: patchSha, note },
  })
  return r.job
}

export async function rejectRepair(jobId: number, note: string): Promise<RepairJob> {
  const r = await apiFetch<{ job: RepairJob }>(`/repair-jobs/${jobId}/reject`, {
    method: 'POST',
    body: { note },
  })
  return r.job
}

export async function startPostReleaseCheck(jobId: number): Promise<RepairJob> {
  const r = await apiFetch<{ job: RepairJob }>(`/repair-jobs/${jobId}/post-release-check`, {
    method: 'POST',
  })
  return r.job
}

export async function rollbackRepair(jobId: number, note: string): Promise<RepairJob> {
  const r = await apiFetch<{ job: RepairJob }>(`/repair-jobs/${jobId}/rollback`, {
    method: 'POST',
    body: { note },
  })
  return r.job
}

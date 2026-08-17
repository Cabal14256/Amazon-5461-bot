import { resolve, USE_MOCK } from './client'
import { ApiError } from './http'
import {
  closeIncident as realCloseIncident,
  approveRepairRelease,
  approveRepairValidation,
  confirmRepairCanary,
  createDiagnoseJob,
  createDryRunJob,
  createSubmitJob,
  fetchAccounts,
  fetchApplications,
  fetchBrands,
  fetchCaseFollowUps,
  fetchCaseIdRecoveries,
  fetchIncident,
  fetchIncidents,
  fetchJob,
  fetchJobLogTail,
  fetchJobs,
  fetchMe,
  fetchOverview,
  fetchReapplication,
  fetchReapplications,
  fetchRepairJobDetail,
  fetchRepairJobDiff,
  fetchRepairJobs,
  fetchSites,
  forceTerminateJob,
  login as realLogin,
  logout as realLogout,
  parseLogLine,
  postGeneratePatch,
  postTriage,
  requestJobStop,
  rejectRepair,
  rollbackRepair,
  startPostReleaseCheck,
  subscribeJobEvents,
  syncAccounts as realSyncAccounts,
  type AccountSyncResult,
  type IncidentListFilters,
  type JobCreatePayload,
  type JobEventHandlers,
  type JobEventSubscription,
  type SubmitJobResponse,
  type SubmitPreflightCheck,
} from './real'
import { mockOverview } from './mock/overview'
import { mockApplications } from './mock/applications'
import { mockCaseFollowUps, mockCaseFollowUpTasks, mockPendingItems } from './mock/cases'
import { mockAccounts, mockBrands, mockSites } from './mock/catalog'
import { mockApproveR2Sample, mockIncidentBundleFiles, mockIncidents, mockLatestTriage, mockPatchJobs, mockRepairIncidents } from './mock/incidents'
import { mockJobEvidence, mockJobLogs, mockJobs } from './mock/jobs'
import type {
  Account,
  ApplicationRecord,
  AutomationJob,
  Brand,
  CaseFollowUp,
  CaseIdRecovery,
  EvidenceItem,
  GeneratePatchResponse,
  Incident,
  IncidentBundleFile,
  JobDetail,
  JobSummary,
  LatestTriage,
  LogLine,
  NewJobPayload,
  OverviewData,
  PendingItem,
  PrecheckItem,
  ReapplicationCampaign,
  RepairIncident,
  RepairJob,
  RepairJobDetail,
  Site,
  WebUser,
} from '@/types'
import type { CaseFollowUpItem } from './mock/cases'

const mockAdmin: WebUser = { id: 0, username: 'mock-admin', role: 'admin', display_name: 'Admin' }

export function getOverview(): Promise<OverviewData> {
  return USE_MOCK ? resolve(mockOverview) : fetchOverview()
}

export function getJobs(): Promise<JobSummary[]> {
  return USE_MOCK ? resolve(mockJobs) : fetchJobs()
}

export function getJob(id: string): Promise<JobDetail | null> {
  if (USE_MOCK) {
    const mock = mockJobs.find((j) => j.id === id) ?? null
    if (!mock) return resolve(null)
    // Mock 模式补齐 JobDetail 的 DB 镜像字段（仅形状对齐，无后端语义）
    const raw: AutomationJob = {
      id: mock.id,
      job_type: mock.mode === 'diagnose' || mock.mode === 'dry_run' || mock.mode === 'submit' ? mock.mode : 'dry_run',
      run_status: mock.status,
      created_by: 'mock-admin',
      account_id: mock.accountLabel,
      marketplace: mock.site,
      brands: mock.results.map((r) => r.brand),
      pid: null,
      exit_code: null,
      error_class: null,
      queue_reason: null,
      stop_requested_at: null,
      started_at: mock.startedAt,
      finished_at: mock.finishedAt,
      created_at: mock.createdAt,
      updated_at: mock.createdAt,
    }
    return resolve({ ...mock, raw, items: [] })
  }
  return fetchJob(id)
}

/** 任务日志尾部（真实模式读 /api/jobs/{id}/logs，已脱敏） */
export async function getJobLogs(id: string): Promise<LogLine[]> {
  if (USE_MOCK) return resolve(mockJobLogs[id] ?? [])
  const tail = await fetchJobLogTail(id)
  return tail.lines.map(parseLogLine)
}

/** 阶段 2 证据按日期/账号检索，尚无按任务聚合接口，真实模式返回空 */
export function getJobEvidence(id: string): Promise<EvidenceItem[]> {
  return USE_MOCK ? resolve(mockJobEvidence[id] ?? []) : Promise.resolve([])
}

export function getApplications(): Promise<ApplicationRecord[]> {
  return USE_MOCK ? resolve(mockApplications) : fetchApplications()
}

/** Mock 分组数据；真实模式 PendingPage 改用 Case ID 找回 / 重新申请人工审核两个真实分组 */
export function getPendingItems(): Promise<PendingItem[]> {
  return USE_MOCK ? resolve(mockPendingItems) : Promise.resolve([])
}

/** Codex 修复流水线演示数据（阶段 6-7 语义；仅 Mock 模式修复中心使用） */
export function getRepairIncidents(): Promise<RepairIncident[]> {
  return resolve(mockRepairIncidents)
}

/* ---------- 阶段 5：持久化异常检测 incident 队列 ---------- */

/** incident 列表（真实模式 GET /api/incidents；Mock 按同一契约本地筛选） */
export function getIncidents(
  filters: IncidentListFilters = {},
): Promise<{ incidents: Incident[]; total: number }> {
  if (!USE_MOCK) return fetchIncidents(filters)
  let list = mockIncidents
  if (filters.status) list = list.filter((i) => i.status === filters.status)
  if (filters.classification) {
    const wanted = filters.classification.split(',').map((s) => s.trim())
    list = list.filter((i) => wanted.includes(i.classification))
  }
  if (filters.account_id) list = list.filter((i) => i.account_id === filters.account_id)
  if (filters.marketplace) list = list.filter((i) => i.marketplace === filters.marketplace)
  const total = list.length
  const offset = filters.offset ?? 0
  const limit = filters.limit ?? 50
  return resolve({ incidents: list.slice(offset, offset + limit), total })
}

/** incident 详情 + 证据包文件清单 + 最近一次 Codex 判因（Mock 按 mockLatestTriage 样例返回） */
export function getIncident(
  id: number,
): Promise<{ incident: Incident; bundle_files: IncidentBundleFile[]; latest_triage: LatestTriage | null }> {
  if (!USE_MOCK) return fetchIncident(id)
  const incident = mockIncidents.find((i) => i.id === id)
  if (!incident) return Promise.reject(new Error('unknown_incident'))
  return resolve({ incident, bundle_files: mockIncidentBundleFiles(incident), latest_triage: mockLatestTriage[id] ?? null })
}

/**
 * 触发/重试 Codex 只读判因（operator+，同步等待 ≤120s）。
 * Mock：无样例或样例 job 为 unavailable 时模拟 503 codex_disabled；成功样例同步把 incident 置 triaged。
 */
export function triageIncident(id: number): Promise<LatestTriage> {
  if (!USE_MOCK) return postTriage(id)
  const t = mockLatestTriage[id]
  if (!t || t.job.status === 'unavailable') {
    return new Promise((_, reject) =>
      setTimeout(() => reject(new ApiError(503, 'codex_disabled', 'codex_disabled')), 600),
    )
  }
  if (t.job.status === 'succeeded') {
    const incident = mockIncidents.find((i) => i.id === id)
    if (incident && incident.status === 'open') incident.status = 'triaged'
  }
  return resolve(t, 900)
}

/** Codex repair job 历史列表（viewer+）；Mock 返回该 incident 的 triage + patch 样例 job */
export function getRepairJobs(id: number): Promise<RepairJob[]> {
  if (!USE_MOCK) return fetchRepairJobs(id)
  const jobs: RepairJob[] = []
  const t = mockLatestTriage[id]
  if (t) jobs.push(t.job)
  const p = mockPatchJobs[id]
  if (p) jobs.push(p.job)
  return resolve(jobs)
}

/* ---------- 阶段 7：隔离生成补丁 ---------- */

/**
 * 生成补丁（operator+，同步等待最长约 10 分钟）。
 * Mock 按 mockPatchJobs 样例模拟：非法状态走 409/422；R2 门槛样例在 allowR2=false 时返回 r2_not_allowed，
 * allowR2=true 时落为 patch_ready 并把 incident 置为 patch_ready。
 */
export function generatePatch(id: number, allowR2 = false): Promise<GeneratePatchResponse> {
  if (!USE_MOCK) return postGeneratePatch(id, allowR2)
  const incident = mockIncidents.find((i) => i.id === id)
  if (!incident) return Promise.reject(new ApiError(404, 'unknown_incident', 'unknown_incident'))
  if (incident.status === 'closed_human' || incident.status === 'closed_duplicate') {
    return Promise.reject(new ApiError(409, 'incident_closed', 'incident_closed'))
  }
  if (incident.status === 'patching') {
    return Promise.reject(new ApiError(409, 'active_patch_conflict', 'active_patch_conflict'))
  }
  if (incident.status !== 'triaged') {
    return Promise.reject(new ApiError(409, 'incident_not_triaged', 'incident_not_triaged'))
  }
  const triage = mockLatestTriage[id]
  if (!triage?.result?.safe_to_generate_patch) {
    return Promise.reject(new ApiError(422, 'unsafe_to_patch', 'unsafe_to_patch'))
  }
  const sample = mockPatchJobs[id]
  if (!sample) return Promise.reject(new ApiError(422, 'unsafe_to_patch', 'unsafe_to_patch'))
  if (sample.r2Gate && !allowR2) {
    return resolve({ outcome: 'r2_not_allowed', job: sample.job, result: sample.result, incident, violations: [] }, 900)
  }
  if (sample.r2Gate && allowR2) mockApproveR2Sample(id)
  if (sample.outcome === 'patch_ready') incident.status = 'patch_ready'
  return resolve(
    { outcome: sample.outcome, job: sample.job, result: sample.result, incident, violations: sample.violations },
    1500,
  )
}

/** 补丁 job 详情（viewer+）：job 全字段 + result.json 内容；Mock 从样例查找，未知 id 404 */
export function getRepairJobDetail(jobId: number): Promise<RepairJobDetail> {
  if (!USE_MOCK) return fetchRepairJobDetail(jobId)
  const sample = Object.values(mockPatchJobs).find((s) => s.job.id === jobId)
  if (!sample) return Promise.reject(new ApiError(404, 'unknown_job', 'unknown_job'))
  return resolve({
    job: sample.job,
    result: sample.result,
    validation: null,
    approvals: [],
    canary: {},
    canary_jobs: [],
    restart_required: false,
    release_enabled: false,
  })
}

/** 补丁 diff 原文（viewer+）；Mock 无 diff 落盘的样例返回 404 diff_not_found（与后端一致） */
export function getRepairJobDiff(jobId: number): Promise<string> {
  if (!USE_MOCK) return fetchRepairJobDiff(jobId)
  const sample = Object.values(mockPatchJobs).find((s) => s.job.id === jobId)
  if (!sample) return Promise.reject(new ApiError(404, 'unknown_job', 'unknown_job'))
  if (sample.diff == null) return Promise.reject(new ApiError(404, 'diff_not_found', 'diff_not_found'))
  return resolve(sample.diff, 200)
}

/** Stage-8 actions are real-mode only; Mock keeps its separate demo state machine. */
export function approveValidation(jobId: number, note = ''): Promise<RepairJob> {
  return approveRepairValidation(jobId, note)
}

export function confirmCanary(jobId: number, note: string, evidenceReviewed: boolean): Promise<RepairJob> {
  return confirmRepairCanary(jobId, note, evidenceReviewed)
}

export function approveRelease(jobId: number, patchSha: string, note = ''): Promise<RepairJob> {
  return approveRepairRelease(jobId, patchSha, note)
}

export function rejectRepairJob(jobId: number, note: string): Promise<RepairJob> {
  return rejectRepair(jobId, note)
}

export function postReleaseCheck(jobId: number): Promise<RepairJob> {
  return startPostReleaseCheck(jobId)
}

export function rollbackRepairJob(jobId: number, note: string): Promise<RepairJob> {
  return rollbackRepair(jobId, note)
}

/** 关闭 incident（operator+）；Mock 把状态置 closed_human 并记录 resolution_note */
export function closeIncident(id: number, note: string): Promise<Incident> {
  if (!USE_MOCK) return realCloseIncident(id, note)
  const incident = mockIncidents.find((i) => i.id === id)
  if (!incident) return Promise.reject(new Error('unknown_incident'))
  if (incident.status !== 'open') return Promise.reject(new Error('incident_already_closed'))
  incident.status = 'closed_human'
  incident.resolution_note = note
  return resolve(incident)
}

/** Case 跟进队列（真实模式返回后端结构） */
export function getCaseFollowUps(status?: string): Promise<CaseFollowUpItem[] | CaseFollowUp[]> {
  return USE_MOCK ? resolve(mockCaseFollowUps) : fetchCaseFollowUps(status)
}

/** Case 跟进任务队列（/api/case-followups，结构在 Mock/真实模式下一致） */
export function getCaseFollowupTasks(status?: string): Promise<CaseFollowUp[]> {
  return USE_MOCK ? resolve(mockCaseFollowUpTasks) : fetchCaseFollowUps(status)
}

export function getCaseIdRecoveries(status?: string): Promise<CaseIdRecovery[]> {
  return fetchCaseIdRecoveries(status)
}

export function getReapplications(status?: string): Promise<ReapplicationCampaign[]> {
  return fetchReapplications(status)
}

export function getReapplication(id: number): Promise<ReapplicationCampaign> {
  return fetchReapplication(id)
}

export function getAccounts(): Promise<Account[]> {
  return USE_MOCK ? resolve(mockAccounts) : fetchAccounts()
}

/** 从 AdsPower 同步新环境到账号目录（Mock 模式返回空结果桩） */
export function syncAccounts(): Promise<AccountSyncResult> {
  if (USE_MOCK) {
    return resolve(
      { profiles_scanned: 0, enrolled: [], failed: [], ambiguous: [], no_number_count: 0, total: mockAccounts.length },
      300,
    )
  }
  return realSyncAccounts()
}

export function getSites(): Promise<Site[]> {
  return USE_MOCK ? resolve(mockSites) : fetchSites()
}

export function getBrands(): Promise<Brand[]> {
  return USE_MOCK ? resolve(mockBrands) : fetchBrands()
}

/* ---------- 认证 ---------- */

export function getMe(): Promise<WebUser> {
  return USE_MOCK ? resolve(mockAdmin) : fetchMe()
}

export function login(username: string, password: string): Promise<WebUser> {
  return USE_MOCK ? resolve(mockAdmin) : realLogin(username, password)
}

export function logout(): Promise<void> {
  return USE_MOCK ? resolve(undefined) : realLogout()
}

/* ---------- 阶段 3 任务队列写接口（真实模式接后端；Mock 模式保持演示桩） ---------- */

/** 创建任务（真实模式：diagnose/dry_run POST 后端白名单接口；Mock：返回伪造 ID） */
export function createJob(payload: NewJobPayload): Promise<{ jobId: string }> {
  if (USE_MOCK) {
    void payload
    return resolve({ jobId: `job-mock-${Date.now()}` }, 300)
  }
  const body: JobCreatePayload = { account: payload.accountId, brands: payload.brands, site: payload.site }
  const call = payload.mode === 'diagnose' ? createDiagnoseJob(body) : createDryRunJob(body)
  return call.then((job) => ({ jobId: job.id }))
}

/** 请求安全停止（operator+）：真实模式调后端；Mock 仅由 store 本地改状态 */
export async function stopJob(id: string): Promise<AutomationJob | null> {
  if (USE_MOCK) return resolve(null)
  return requestJobStop(id)
}

/* ---------- 阶段 4：真实提交（真实模式接后端单接口；Mock 提供可开发桩） ---------- */

/** 把 JobSummary 形状拼成 AutomationJob 镜像（仅 Mock 用，字段对齐 DB 主源） */
function mockRawJob(jobId: string, payload: NewJobPayload, runStatus: AutomationJob['run_status']): AutomationJob {
  const now = new Date().toISOString()
  return {
    id: jobId,
    job_type: 'submit',
    run_status: runStatus,
    created_by: 'mock-admin',
    account_id: payload.accountId,
    marketplace: payload.site,
    brands: payload.brands,
    pid: null,
    exit_code: null,
    error_class: null,
    queue_reason: null,
    stop_requested_at: null,
    started_at: null,
    finished_at: null,
    created_at: now,
    updated_at: now,
  }
}

/**
 * 创建真实提交任务（真实模式 POST /jobs/submit，创建后直接 queued）。
 * Mock：返回 queued 状态任务与全 ok 的 preflight，并把任务插进 mockJobs，供详情页查看。
 */
export function createSubmit(payload: NewJobPayload): Promise<SubmitJobResponse> {
  if (!USE_MOCK) {
    const body: JobCreatePayload = { account: payload.accountId, brands: payload.brands, site: payload.site }
    return createSubmitJob(body)
  }
  const jobId = `job-mock-submit-${Date.now()}`
  const now = new Date().toISOString()
  mockJobs.unshift({
    id: jobId,
    type: payload.type,
    mode: 'submit',
    accountId: payload.accountId,
    accountLabel: payload.accountId,
    site: payload.site,
    status: 'queued',
    progress: 0,
    brandTotal: payload.brands.length,
    brandSucceeded: 0,
    brandFailed: 0,
    createdAt: now,
    startedAt: null,
    finishedAt: null,
    stopReason: null,
    results: payload.brands.map((b) => ({
      brand: b,
      runStatus: 'queued' as const,
      businessStatus: 'draft' as const,
      caseId: null,
      dashboardStatus: null,
      durationSec: null,
      note: null,
    })),
  })
  const preflight: SubmitPreflightCheck[] = [
    { name: 'seller_session', ok: true, level: 'blocker', detail: 'AdsPower profile 在线，Seller Central 会话有效' },
    { name: 'brand_packs', ok: true, level: 'blocker', detail: `${payload.brands.length} 个品牌包均已齐备` },
    { name: 'site_config', ok: true, level: 'blocker', detail: '站点在 marketplaces 配置中' },
    { name: 'rate_limit', ok: true, level: 'warning', detail: '该站点近期无 429 / 410001 限流记录' },
  ]
  return resolve({ job: mockRawJob(jobId, payload, 'queued'), preflight }, 400)
}

/** 强制终止（admin，高危）：真实模式调后端；Mock 不可用 */
export async function terminateJob(id: string): Promise<AutomationJob | null> {
  if (USE_MOCK) return resolve(null)
  return forceTerminateJob(id)
}

const noopSubscription: JobEventSubscription = { close: () => undefined }

/** 订阅任务 SSE（Mock 模式无后端流，返回 no-op；Mock 日志流由 store 的定时器模拟） */
export function subscribeJob(id: string, handlers: JobEventHandlers): JobEventSubscription {
  if (USE_MOCK) {
    void id
    void handlers
    return noopSubscription
  }
  return subscribeJobEvents(id, handlers)
}

/* ---------- 以下为阶段 3+ 写接口的 Mock 桩（只读控制台无真实后端） ---------- */

/** 前置条件检查（Mock：按品牌包齐备情况生成结果） */
export function runPrecheck(payload: NewJobPayload): Promise<PrecheckItem[]> {
  const items: PrecheckItem[] = [
    { key: 'session', label: 'Seller Central 会话有效', status: 'passed', detail: 'AdsPower profile 在线，登录态未过期' },
    { key: 'disk', label: '磁盘空间充足', status: 'warning', detail: 'C 盘剩余不足 5GB，建议清理后再跑长批次' },
    {
      key: 'packs',
      label: '品牌包齐备',
      status: payload.brands.every((b) => mockBrands.find((mb) => mb.name === b)?.packReady) ? 'passed' : 'failed',
      detail: payload.brands.every((b) => mockBrands.find((mb) => mb.name === b)?.packReady)
        ? `${payload.brands.length} 个品牌包均已齐备`
        : '存在未齐备品牌包（缺声明文件或素材），将被拦截',
    },
    {
      key: 'rate_limit',
      label: '无限流冷却中',
      status: payload.site === 'UK' ? 'warning' : 'passed',
      detail: payload.site === 'UK' ? 'UK 站 3 天前触发过 429 熔断，建议降低并发' : '该站点近期无限流记录',
    },
  ]
  return resolve(items, 400)
}

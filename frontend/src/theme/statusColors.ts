import type { BusinessStatus, HealthLevel, IncidentStatus, JobRunStatus, RepairJobStatus, RiskLevel } from '@/types'

export interface StatusMeta {
  /** 中文文案 */
  label: string
  /** 圆点/主色 hex，供 Tag 与图表统一使用 */
  color: string
  /** 是否脉冲动画（running 等进行中状态） */
  pulse?: boolean
  /** 虚线圆点（not_found 等"查无此项"语义） */
  dashed?: boolean
}

/** 任务运行态 9 色 */
export const runStatusMeta: Record<JobRunStatus, StatusMeta> = {
  queued: { label: '排队中', color: '#8a919e' },
  starting: { label: '启动中', color: '#3b82f6', pulse: true },
  running: { label: '运行中', color: '#2080f0', pulse: true },
  stop_requested: { label: '已请求停止', color: '#f0a020' },
  waiting_human: { label: '待人工介入', color: '#8b5cf6' },
  completed: { label: '已完成', color: '#18a058' },
  failed: { label: '失败', color: '#d03050' },
  cancelled_before_start: { label: '开始前取消', color: '#8a919e' },
  terminated_unknown_state: { label: '异常终止·状态未知', color: '#7f1d1d' },
}

/** 业务结果态 10 色 */
export const businessStatusMeta: Record<BusinessStatus, StatusMeta> = {
  draft: { label: '草稿', color: '#8a919e' },
  submitted_no_case_id_pending_dashboard: { label: '已提交·无Case ID·待查控制面板', color: '#f0a020' },
  submitted: { label: '已提交', color: '#3b82f6' },
  pending: { label: '待处理', color: '#f0a020' },
  under_review: { label: '审核中', color: '#3b82f6' },
  approved: { label: '已通过', color: '#18a058' },
  false_approved: { label: '假过', color: '#d03050' },
  declined: { label: '已拒绝', color: '#d03050' },
  already_approved: { label: '本已通过', color: '#0e7490' },
  not_found: { label: '未找到', color: '#8a919e', dashed: true },
  partial: { label: '部分完成', color: '#eab308' },
  failed: { label: '失败', color: '#d03050' },
  error: { label: '异常', color: '#d03050' },
}

/** incident 状态机 13 态 */
export const incidentStatusMeta: Record<IncidentStatus, StatusMeta> = {
  detected: { label: '已发现', color: '#8a919e' },
  triage_queued: { label: '判因排队', color: '#8a919e' },
  triaging: { label: '判因中', color: '#3b82f6', pulse: true },
  not_code_issue: { label: '非代码问题', color: '#8a919e' },
  repair_candidate: { label: '待修复候选', color: '#f0a020' },
  repair_queued: { label: '修复排队', color: '#f0a020' },
  repairing: { label: '修复中', color: '#3b82f6', pulse: true },
  validation_failed: { label: '验证失败', color: '#d03050' },
  awaiting_review: { label: '待审核', color: '#8b5cf6' },
  approved: { label: '已批准', color: '#18a058' },
  rejected: { label: '已拒绝', color: '#d03050' },
  released: { label: '已发布', color: '#0e7490' },
  canary_failed: { label: '金丝雀失败', color: '#7f1d1d' },
  closed: { label: '已关闭', color: '#8a919e' },
}

export const healthLevelMeta: Record<HealthLevel, { label: string; color: string }> = {
  ok: { label: '正常', color: '#18a058' },
  warn: { label: '告警', color: '#f0a020' },
  down: { label: '不可用', color: '#d03050' },
}

/** 阶段 5/6/7 incident 队列状态（open / triaged / patching / patch_ready / closed_human / closed_duplicate） */
export const incidentQueueStatusMeta: Record<string, StatusMeta> = {
  open: { label: '待处理', color: '#f0a020' },
  triaged: { label: '已判因', color: '#3b82f6' },
  patching: { label: '补丁生成中', color: '#2080f0', pulse: true },
  patch_ready: { label: '补丁就绪', color: '#8b5cf6' },
  validating: { label: '验证中', color: '#2080f0', pulse: true },
  validated: { label: '验证通过', color: '#18a058' },
  released: { label: '已发布', color: '#18a058' },
  rejected: { label: '已拒绝', color: '#d03050' },
  closed_human: { label: '人工关闭', color: '#8a919e' },
  closed_duplicate: { label: '重复关闭', color: '#8a919e', dashed: true },
}

/** 阶段 6/7 Codex repair job 状态（triage 与 patch 共用词表；running/succeeded 的文案按 triage 语义） */
export const repairJobStatusMeta: Record<RepairJobStatus, StatusMeta> = {
  running: { label: '判因中', color: '#2080f0', pulse: true },
  succeeded: { label: '判因成功', color: '#18a058' },
  failed: { label: '判因失败', color: '#d03050' },
  timeout: { label: '判因超时', color: '#d03050' },
  unavailable: { label: 'Codex 不可用', color: '#f0a020' },
  quota_exceeded: { label: '超出配额', color: '#f0a020' },
  schema_invalid: { label: '结果格式无效', color: '#d03050' },
  patch_ready: { label: '补丁就绪', color: '#18a058' },
  validation_failed: { label: '校验失败', color: '#d03050' },
  validating: { label: '自动验证中', color: '#2080f0', pulse: true },
  awaiting_validation_approval: { label: '等待验证批准', color: '#f0a020' },
  canary: { label: 'Canary', color: '#8b5cf6', pulse: true },
  awaiting_release_approval: { label: '等待发布批准', color: '#f0a020' },
  release_pending_restart: { label: '等待重启', color: '#f0a020', pulse: true },
  post_release_check: { label: '发布后检查', color: '#2080f0', pulse: true },
  release_check_failed: { label: '发布检查失败', color: '#d03050' },
  released: { label: '已发布', color: '#18a058' },
  rejected: { label: '已拒绝', color: '#d03050' },
  rolled_back: { label: '已回滚', color: '#8a919e' },
}

/** 阶段 7 修改级别 R0–R3（蓝图 §17.3；R2 需人工允许，R3 不生成补丁） */
export const riskLevelMeta: Record<RiskLevel, StatusMeta> = {
  R0: { label: 'R0 · 允许范围内', color: '#18a058' },
  R1: { label: 'R1 · 允许范围内', color: '#3b82f6' },
  R2: { label: 'R2 · 导航/表单语义', color: '#f0a020' },
  R3: { label: 'R3 · 提交/授权语义', color: '#d03050' },
}

export type StatusKind = 'run' | 'business' | 'incident' | 'incident_queue' | 'repair_job'

export function getStatusMeta(kind: StatusKind, status: string): StatusMeta {
  const table =
    kind === 'run'
      ? runStatusMeta
      : kind === 'business'
        ? businessStatusMeta
        : kind === 'incident'
          ? incidentStatusMeta
          : kind === 'incident_queue'
            ? incidentQueueStatusMeta
            : repairJobStatusMeta
  const meta = (table as Record<string, StatusMeta>)[status]
  return meta ?? { label: status, color: '#8a919e' }
}

export interface StatusTagSpec {
  kind: StatusKind
  status: string
}

/** 重新申请 campaign 状态 -> run/incident 色系语义映射（复用 StatusTag） */
export function campaignTag(status: string): StatusTagSpec {
  switch (status) {
    case 'scheduled':
    case 'next_scheduled':
      return { kind: 'run', status: 'queued' }
    case 'running':
      return { kind: 'run', status: 'running' }
    case 'waiting_case_id':
    case 'waiting_case':
      return { kind: 'run', status: 'waiting_human' }
    case 'paused':
      return { kind: 'run', status: 'stop_requested' }
    case 'blocked':
    case 'manual_review':
      return { kind: 'incident', status: 'awaiting_review' }
    case 'completed':
      return { kind: 'run', status: 'completed' }
    case 'failed':
    case 'stopped':
      return { kind: 'run', status: 'failed' }
    default:
      return { kind: 'run', status }
  }
}

/** 重新申请 attempt / Case ID 找回队列状态 -> run/incident 色系语义映射 */
export function attemptTag(status: string): StatusTagSpec {
  switch (status) {
    case 'scheduled':
    case 'pending':
      return { kind: 'run', status: 'queued' }
    case 'retry':
      return { kind: 'run', status: 'stop_requested' }
    case 'running':
      return { kind: 'run', status: 'running' }
    case 'submitted':
      return { kind: 'business', status: 'under_review' }
    case 'completed':
      return { kind: 'run', status: 'completed' }
    case 'skipped':
    case 'cancelled':
      return { kind: 'run', status: 'cancelled_before_start' }
    case 'manual_review':
    case 'blocked':
      return { kind: 'incident', status: 'awaiting_review' }
    case 'failed':
    case 'error':
      return { kind: 'run', status: 'failed' }
    default:
      return { kind: 'run', status }
  }
}

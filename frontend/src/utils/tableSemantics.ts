import type { CaseFollowUp, JobSummary } from '@/types'
import { parseServerTime } from '@/utils/datetime'

export function serverTimeMillis(value: string | null | undefined): number {
  return parseServerTime(value ?? '')?.getTime() ?? 0
}

export function jobExecutionSummary(row: Pick<JobSummary, 'brandSucceeded' | 'brandFailed' | 'brandTotal'>): string {
  return `执行完成 ${row.brandSucceeded} / 执行失败 ${row.brandFailed} / 总数 ${row.brandTotal}`
}

export function jobDetailPath(id: string): string {
  return `/jobs/${id}`
}

export function caseExpandedDetails(row: CaseFollowUp) {
  const timeline = [
    row.submitted_at ? `提交：${row.submitted_at}` : null,
    row.scheduled_at ? `计划：${row.scheduled_at}` : null,
    row.last_checked_at ? `上次检查：${row.last_checked_at}` : null,
    row.completed_at ? `完成：${row.completed_at}` : null,
  ].filter((item): item is string => Boolean(item))
  return {
    decision: row.decision_reason || null,
    error: row.error || null,
    evidence: row.evidence_path || null,
    timeline,
  }
}

export function reapplicationAuthorizationMessage(created: boolean): string {
  return created ? '已授权并安排下一站重新申请' : '该 Case 已有关联的重新申请活动'
}

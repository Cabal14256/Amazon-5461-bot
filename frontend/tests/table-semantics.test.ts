import { describe, expect, it } from 'vitest'

import type { CaseFollowUp } from '@/types'
import {
  caseExpandedDetails,
  jobDetailPath,
  jobExecutionSummary,
  reapplicationAuthorizationMessage,
  serverTimeMillis,
} from '@/utils/tableSemantics'

describe('table semantics', () => {
  it('sorts server-local timestamps deterministically', () => {
    expect(serverTimeMillis('2026-08-17 09:00:00')).toBeLessThan(
      serverTimeMillis('2026-08-17 10:00:00'),
    )
    expect(serverTimeMillis('not-a-time')).toBe(0)
  })

  it('labels execution counts without implying business approval', () => {
    expect(jobExecutionSummary({ brandSucceeded: 2, brandFailed: 1, brandTotal: 4 })).toBe(
      '执行完成 2 / 执行失败 1 / 总数 4',
    )
  })

  it('uses the row id for job navigation', () => {
    expect(jobDetailPath('job-5461-001')).toBe('/jobs/job-5461-001')
  })

  it('keeps Case decision, error, evidence and full timeline in expanded content', () => {
    const row = {
      decision_reason: '明确拒绝',
      error: 'fixture error',
      evidence_path: 'fixture/evidence.json',
      submitted_at: '2026-08-17 08:00:00',
      scheduled_at: '2026-08-17 09:00:00',
      last_checked_at: '2026-08-17 10:00:00',
      completed_at: '2026-08-17 11:00:00',
    } as CaseFollowUp
    const expanded = caseExpandedDetails(row)
    expect(expanded).toMatchObject({
      decision: '明确拒绝',
      error: 'fixture error',
      evidence: 'fixture/evidence.json',
    })
    expect(expanded.timeline).toEqual([
      '提交：2026-08-17 08:00:00',
      '计划：2026-08-17 09:00:00',
      '上次检查：2026-08-17 10:00:00',
      '完成：2026-08-17 11:00:00',
    ])
  })

  it('distinguishes a newly created campaign from an idempotent response', () => {
    expect(reapplicationAuthorizationMessage(true)).toContain('已授权')
    expect(reapplicationAuthorizationMessage(false)).toContain('已有关联')
  })
})

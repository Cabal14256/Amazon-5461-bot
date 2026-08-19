import { describe, expect, it } from 'vitest'

import { mapAutomationJob } from '@/api/real'
import type { AutomationJob, AutomationJobItem } from '@/types'


describe('job detail authoritative business status', () => {
  it('keeps execution state separate from later Dashboard truth', () => {
    const job: AutomationJob = {
      id: 'job-fixture',
      job_type: 'submit',
      run_status: 'waiting_human',
      created_by: 'fixture',
      account_id: 'fixture-account',
      marketplace: 'UK',
      brands: ['FIXTURE'],
      pid: null,
      exit_code: null,
      error_class: 'waiting_reconciliation',
      queue_reason: null,
      stop_requested_at: null,
      started_at: '2026-08-18 10:00:00',
      finished_at: null,
      created_at: '2026-08-18 09:59:00',
      updated_at: '2026-08-18 10:05:00',
    }
    const item: AutomationJobItem = {
      id: 1,
      job_id: job.id,
      account_id: job.account_id,
      marketplace: 'UK',
      brand_name: 'FIXTURE',
      run_status: 'waiting_human',
      business_status: 'waiting_reconciliation',
      case_id: null,
      dashboard_status: null,
      started_at: null,
      finished_at: null,
      note: null,
      authoritative: {
        status: 'approved',
        source: 'dashboard_today',
        detail: 'dashboard',
        checked_at: '2026-08-18 10:04:00',
      },
    }

    const mapped = mapAutomationJob(job, [item])

    expect(mapped.results[0].runStatus).toBe('waiting_human')
    expect(mapped.results[0].businessStatus).toBe('approved')
    expect(mapped.results[0].businessSource).toBe('控制面板当天检查')
  })
})

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ApplicationRecordDetails from '@/components/ApplicationRecordDetails.vue'
import type { ApplicationRecord } from '@/types'


function reconciliationRecord(): ApplicationRecord {
  return {
    id: 'fixture',
    accountLabel: 'us_store_001',
    site: 'UK',
    brand: 'TESTBRAND',
    type: 'apply_5461',
    localStatus: 'waiting_reconciliation',
    dashboardStatus: null,
    caseLastReply: null,
    caseId: null,
    statusSource: '自动处理检查点',
    statusSourceRaw: 'automation_checkpoint',
    updatedAt: '2026-08-18 10:00:00',
    evidence: [],
    automation: {
      checkpoint_id: 1,
      owner_type: 'reapplication_attempt',
      owner_id: '9',
      status: 'waiting_reconciliation',
      phase: 'reconciliation',
      current_site: 'UK',
      submit_intent_at: '2026-08-18 09:59:59',
      submit_click_fenced_at: '2026-08-18 10:00:00',
      resumed_at: null,
      submit_fenced: true,
      auth_block_id: 3,
      block_type: 'login_required',
      detected_at: '2026-08-18 10:00:01',
      last_checked_at: null,
      next_check_at: '2026-08-18 10:05:01',
      evidence_path: null,
      next_action: '不会重复提交；核对 Selling Applications/Case',
      campaign_id: 2,
      timeline: [],
      updated_at: '2026-08-18 10:00:01',
      detail: 'login required',
    },
  }
}


describe('authentication recovery application detail', () => {
  it('shows the no-reclick fence and reconciliation action', () => {
    const wrapper = mount(ApplicationRecordDetails, {
      props: { record: reconciliationRecord() },
    })
    expect(wrapper.text()).toContain('系统不会重复提交')
    expect(wrapper.text()).toContain('已落库（禁止再次点击）')
    expect(wrapper.text()).toContain('核对 Selling Applications/Case')
  })

  it('hides login interruption fields for an ordinary automation checkpoint', () => {
    const record = reconciliationRecord()
    record.automation = {
      ...record.automation!,
      status: 'active',
      auth_block_id: null,
      block_type: null,
      detected_at: null,
      last_checked_at: null,
    }

    const wrapper = mount(ApplicationRecordDetails, { props: { record } })

    expect(wrapper.text()).not.toContain('登录中断阶段')
    expect(wrapper.text()).not.toContain('最近登录检测')
    expect(wrapper.text()).toContain('提交点击安全边界')
  })

  it('shows a source label once and renders its detail separately', () => {
    const record = reconciliationRecord()
    record.statusSource = '控制面板当天检查（Selling Applications 显示 Approved）'
    record.statusSourceRaw = 'dashboard_today'
    record.statusSourceDetail = 'Selling Applications 显示 Approved'

    const wrapper = mount(ApplicationRecordDetails, { props: { record } })
    const text = wrapper.text()

    expect(text.match(/控制面板当天检查/g)).toHaveLength(1)
    expect(text).toContain('Selling Applications 显示 Approved')
  })
})

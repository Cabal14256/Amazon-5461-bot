import { describe, expect, it } from 'vitest'

import { attemptTag, campaignTag, getStatusMeta } from '@/theme/statusColors'


describe('reapplication automatic waiting labels', () => {
  it.each([
    ['waiting_case_id', '正在找回 Case ID'],
    ['waiting_case', '等待 Case 最终回复'],
  ])('keeps campaign %s as an automatic business state', (status, label) => {
    const tag = campaignTag(status)

    expect(tag).toEqual({ kind: 'business', status })
    expect(getStatusMeta(tag.kind, tag.status).label).toBe(label)
    expect(getStatusMeta(tag.kind, tag.status).label).not.toContain('人工')
  })

  it.each(['waiting_case_id', 'waiting_case'])(
    'uses the same automatic state for attempt %s',
    (status) => {
      expect(attemptTag(status)).toEqual({ kind: 'business', status })
    },
  )

  it('reserves the human label for genuinely blocked campaigns', () => {
    const tag = campaignTag('manual_review')

    expect(tag).toEqual({ kind: 'incident', status: 'awaiting_review' })
    expect(getStatusMeta(tag.kind, tag.status).label).toContain('审核')
  })

  it('renders a confirmed Draft as business state instead of execution failure', () => {
    expect(campaignTag('draft')).toEqual({ kind: 'business', status: 'draft' })
    expect(attemptTag('draft')).toEqual({ kind: 'business', status: 'draft' })
    expect(getStatusMeta('business', 'draft').label).toBe('草稿')
  })
})

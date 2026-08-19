import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { mockEffectiveSettings, patchMockEffectiveSettings } from '@/api/mock/settings'


describe('settings console contract', () => {
  it('updates the mock snapshot and dynamic submit limit together', () => {
    const before = mockEffectiveSettings.revision
    const result = patchMockEffectiveSettings(before, { 'web.submit_max_brands': 8 })

    expect(result.snapshot.limits.submit).toBe(8)
    expect(result.changed['web.submit_max_brands']).toEqual({ old: 5, new: 8 })
    expect(result.snapshot.revision).not.toBe(before)
  })

  it('renders apply modes, protected settings and discard/save actions', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/pages/SettingsPage.vue'), 'utf8')

    expect(source).toContain('立即生效')
    expect(source).toContain('新启动任务生效')
    expect(source).toContain('重启服务生效')
    expect(source).toContain('受保护与未接线配置（只读）')
    expect(source).toContain('放弃修改')
    expect(source).toContain('确认保存系统设置')
  })

  it('uses backend limits and submit-only immutable overrides in the wizard', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/pages/NewJobWizard.vue'), 'utf8')

    expect(source).toContain('jobLimits.value = effective.limits')
    expect(source).not.toContain('const MAX_BRANDS = 20')
    expect(source).toContain('case_followup_delay_hours')
    expect(source).toContain('case_followup_enabled')
    expect(source).toContain('任务级覆盖')
  })

  it('guards the settings route for administrators', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/router/index.ts'), 'utf8')

    expect(source).toContain("path: '/settings'")
    expect(source).toContain('requiresAdmin: true')
    expect(source).toContain('to.meta.requiresAdmin && !auth.isAdmin')
  })
})

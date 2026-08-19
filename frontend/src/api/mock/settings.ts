import type { EffectiveSettings, SettingField, SettingsPatchResult } from '@/types'

function field(
  key: string,
  group: string,
  label: string,
  value: number | boolean,
  unit: string,
  editable = true,
): SettingField {
  return {
    key,
    group,
    label,
    value_type: typeof value === 'boolean' ? 'boolean' : Number.isInteger(value) ? 'integer' : 'number',
    value,
    default: value,
    unit,
    minimum: editable && typeof value === 'number' ? 0 : null,
    maximum: editable && typeof value === 'number' ? 3600 : null,
    step: editable && typeof value === 'number' ? 1 : null,
    editable,
    apply_mode: key === 'web.submit_max_brands' ? 'immediate' : 'restart_required',
    status: editable ? 'active' : 'protected',
    description: editable ? 'Mock 环境中的配置示例。' : '安全开关仅供查看。',
  }
}

export const mockEffectiveSettings: EffectiveSettings = {
  revision: 'mock-1',
  fields: [
    field('browser.action_timeout_ms', '浏览器与 AdsPower', '页面操作超时', 20000, '毫秒'),
    field('browser.page_timeout_ms', '浏览器与 AdsPower', '页面导航超时', 45000, '毫秒'),
    field('case_followup.delay_hours', 'Case 跟进', '首次跟进延迟', 2, '小时'),
    field('case_followup.retry_interval_hours', 'Case 跟进', '未决结果重试间隔', 1, '小时'),
    field('case_id_recovery.retry_interval_minutes', 'Case ID 恢复', '恢复重试间隔', 30, '分钟'),
    field('reapplication.decline_delay_hours', '重新申请', '拒绝后延迟', 2, '小时'),
    field('web.session_ttl_hours', '认证与 Web', '登录会话有效期', 12, '小时'),
    field('web.submit_max_brands', '认证与 Web', '真实提交品牌上限', 5, '个'),
    field('codex.timeout_sec', 'Codex 修复', '判因调用超时', 300, '秒'),
    field('web.submit_enabled', '安全开关（只读）', '真实提交总开关', true, '', false),
    field('run.pause_on_captcha_or_2fa', '安全开关（只读）', '验证码/2FA 暂停', true, '', false),
  ],
  limits: { diagnose: 20, dry_run: 20, submit: 5 },
  job_defaults: { case_followup_delay_hours: 2, case_followup_enabled: true },
}

export function patchMockEffectiveSettings(
  revision: string,
  changes: Record<string, number>,
): SettingsPatchResult {
  if (revision !== mockEffectiveSettings.revision) throw new Error('settings_revision_conflict')
  const changed: SettingsPatchResult['changed'] = {}
  for (const [key, value] of Object.entries(changes)) {
    const target = mockEffectiveSettings.fields.find((item) => item.key === key && item.editable)
    if (!target || typeof target.value !== 'number') continue
    changed[key] = { old: target.value, new: value }
    target.value = value
  }
  const next = Number(mockEffectiveSettings.revision.split('-')[1] ?? '1') + 1
  mockEffectiveSettings.revision = `mock-${next}`
  const submitLimit = mockEffectiveSettings.fields.find((item) => item.key === 'web.submit_max_brands')
  if (submitLimit && typeof submitLimit.value === 'number') mockEffectiveSettings.limits.submit = submitLimit.value
  return {
    snapshot: structuredClone(mockEffectiveSettings),
    changed,
    restart_required: Object.keys(changed).filter((key) => {
      return mockEffectiveSettings.fields.find((item) => item.key === key)?.apply_mode === 'restart_required'
    }),
  }
}

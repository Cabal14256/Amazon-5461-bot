const RESULT_LABELS: Record<string, string> = {
  pending: '待处理',
  submitted: '已提交',
  false_approved: '假过',
  approved: '已通过',
  already_approved: '本已通过',
  declined: '已拒绝',
  under_review: '审核中',
  verification_pending: '待验证',
  draft: '草稿',
  completed: '已完成',
  failed: '失败',
  error: '异常',
  unknown: '未知',
  not_found: '未找到',
}

const DASHBOARD_LABELS: Record<string, string> = {
  dashboard: '控制面板',
  approved: '已通过',
  already_approved: '已通过',
  false_approved: '假过',
  declined: '未通过',
  'not approved': '未通过',
  under_review: '审核中',
  'under review': '审核中',
  pending: '待处理',
  submitted: '已提交',
  'pending check': '待核查',
  draft: '草稿',
  unknown: '未知',
  not_found: '未找到',
  'not found': '未找到',
}

/** Translate persisted business result codes without changing their stored values. */
export function businessResultLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return RESULT_LABELS[value.trim().toLowerCase()] ?? value
}

/** Translate Amazon control-panel status values, including historical English labels. */
export function dashboardStatusLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return DASHBOARD_LABELS[value.trim().toLowerCase()] ?? value
}

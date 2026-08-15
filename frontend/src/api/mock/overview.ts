import type { OverviewData } from '@/types'

function minsAgo(min: number): string {
  return new Date(Date.now() - min * 60_000).toISOString()
}

export const mockOverview: OverviewData = {
  health: [
    { key: 'adspower', name: 'AdsPower', level: 'ok', detail: '本地 API 正常，3 个 profile 在线', checkedAt: minsAgo(1) },
    { key: 'sqlite', name: 'SQLite 存储', level: 'ok', detail: 'WAL 正常，最近写入 2 分钟前', checkedAt: minsAgo(1) },
    { key: 'worker', name: '自动化 worker', level: 'ok', detail: '1 个任务运行中，1 个排队', checkedAt: minsAgo(1) },
    { key: 'case_followup', name: 'Case follow-up', level: 'warn', detail: '2 条 Case 超过 24h 未跟进', checkedAt: minsAgo(3) },
    { key: 'disk', name: '磁盘空间', level: 'warn', detail: 'C 盘剩余不足 5GB，建议清理', checkedAt: minsAgo(5) },
    { key: 'codex', name: 'Codex 可用性', level: 'ok', detail: '修复通道就绪，1 个补丁待审核', checkedAt: minsAgo(2) },
  ],
  businessDistribution: [
    { status: 'approved', count: 11 },
    { status: 'under_review', count: 6 },
    { status: 'already_approved', count: 4 },
    { status: 'submitted_no_case_id_pending_dashboard', count: 2 },
    { status: 'declined', count: 1 },
    { status: 'not_found', count: 3 },
    { status: 'partial', count: 1 },
    { status: 'draft', count: 8 },
    { status: 'error', count: 2 },
  ],
  runningCount: 1,
  queuedCount: 2,
  waitingHumanCount: 1,
  codexSignalPending: 1,
  caseFollowUpPending: 2,
  uiChangeSuspected: 1,
  patchesAwaitingReview: 1,
  incidentsOpen: 5,
}

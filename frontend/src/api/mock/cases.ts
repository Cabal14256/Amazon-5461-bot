import type { CaseFollowUp, PendingItem } from '@/types'

function minsAgo(min: number): string {
  return new Date(Date.now() - min * 60_000).toISOString()
}

/** Mock：Case 跟进任务队列（与 GET /api/case-followups 真实结构一致） */
export const mockCaseFollowUpTasks: CaseFollowUp[] = [
  {
    id: 101,
    account_id: 'us_store_005',
    marketplace: 'UK',
    brand_name: 'DEMO_HOME',
    case_id: 'case-demo-0001',
    submitted_at: minsAgo(60 * 26),
    scheduled_at: minsAgo(-60 * 3),
    status: 'retry',
    attempt_count: 2,
    last_checked_at: minsAgo(60 * 2),
    completed_at: null,
    case_status: 'under_review',
    final_result: null,
    decision_reason: 'Case 仍在审核中，3 小时后重查',
    evidence_path: null,
    error: null,
  },
  {
    id: 102,
    account_id: 'us_store_005',
    marketplace: 'UK',
    brand_name: 'DEMO_JADE',
    case_id: 'case-demo-0002',
    submitted_at: minsAgo(60 * 30),
    scheduled_at: minsAgo(60 * 1),
    status: 'completed',
    attempt_count: 3,
    last_checked_at: minsAgo(60 * 1),
    completed_at: minsAgo(60 * 1),
    case_status: 'approved',
    final_result: 'approved',
    decision_reason: '控制面板显示已通过',
    evidence_path: 'evidence/2026-08-12/us_store_005/DEMO_JADE/final.png',
    error: null,
  },
  {
    id: 103,
    account_id: 'us_store_006',
    marketplace: 'MX',
    brand_name: 'DEMO_IOTA',
    case_id: 'case-demo-0003',
    submitted_at: minsAgo(60 * 50),
    scheduled_at: minsAgo(60 * 20),
    status: 'failed',
    attempt_count: 5,
    last_checked_at: minsAgo(60 * 20),
    completed_at: minsAgo(60 * 20),
    case_status: null,
    final_result: null,
    decision_reason: null,
    evidence_path: null,
    error: 'AdsPower profile 启动超时',
  },
]

export interface CaseFollowUpItem {
  caseId: string
  brand: string
  site: string
  lastReplyAt: string
  hoursSinceReply: number
  needsFollowUp: boolean
  summary: string
}

export const mockCaseFollowUps: CaseFollowUpItem[] = [
  {
    caseId: 'case-demo-0002',
    brand: 'DEMO_HOME',
    site: 'US',
    lastReplyAt: minsAgo(60 * 20),
    hoursSinceReply: 20,
    needsFollowUp: false,
    summary: '已确认收件，等待审核结果',
  },
  {
    caseId: 'case-demo-0003',
    brand: 'DEMO_IOTA',
    site: 'MX',
    lastReplyAt: minsAgo(60 * 47),
    hoursSinceReply: 47,
    needsFollowUp: true,
    summary: '超过 24h 无回复，应发跟进消息',
  },
  {
    caseId: 'case-demo-0001',
    brand: 'DEMO_ALPHA',
    site: 'DE',
    lastReplyAt: minsAgo(60 * 30),
    hoursSinceReply: 30,
    needsFollowUp: true,
    summary: '超过 24h 无回复，应发跟进消息',
  },
]

/** 待人工处理分组数据 */
export const mockPendingItems: PendingItem[] = [
  {
    id: 'pend-001',
    category: 'captcha_2fa_login',
    title: 'DEMO_CASE 遇到图形验证码',
    detail: 'job-demo-captcha 在 DE 站提交前出现 CAPTCHA，浏览器保持打开，需要人工完成验证后点"继续"。',
    accountLabel: 'demo-eu',
    brand: 'DEMO_CASE',
    jobId: 'job-demo-captcha',
    incidentId: null,
    detectedAt: minsAgo(41),
  },
  {
    id: 'pend-002',
    category: 'account_risk_unknown_ui',
    title: '账号风险提示页',
    detail: '演示账号登录后出现"账户状况"黄色警示横幅，自动化未继续，需要人工确认是否影响提交。',
    accountLabel: 'demo-mx',
    brand: null,
    jobId: null,
    incidentId: null,
    detectedAt: minsAgo(60 * 3),
  },
  {
    id: 'pend-003',
    category: 'rate_limit_429',
    title: 'UK 站连续 429 / 410001',
    detail: 'job-demo-rate-limit 已熔断冷却。按安全策略需冷却 4 小时以上，重试前请人工确认账号状态。',
    accountLabel: 'demo-eu',
    brand: 'DEMO_CASE',
    jobId: 'job-demo-rate-limit',
    incidentId: null,
    detectedAt: minsAgo(60 * 69),
  },
  {
    id: 'pend-004',
    category: 'draft_no_case_conflict',
    title: 'DEMO_FLEX 提交后无 Case ID',
    detail: '本地显示已提交但未回显 Case ID，控制面板状态为待核查。需要人工登录 Seller Central 核对销售申请。',
    accountLabel: 'demo-na',
    brand: 'DEMO_FLEX',
    jobId: 'job-demo-complete',
    incidentId: null,
    detectedAt: minsAgo(60 * 25),
  },
  {
    id: 'pend-005',
    category: 'draft_no_case_conflict',
    title: 'DEMO_HOME(MX) 崩溃瞬间状态未知',
    detail: '异常终止·状态未知：提交请求已发出但浏览器崩溃，是否生效未知。禁止盲目重提，必须先查控制面板。',
    accountLabel: 'demo-mx',
    brand: 'DEMO_HOME',
    jobId: 'job-demo-unknown',
    incidentId: null,
    detectedAt: minsAgo(60 * 47),
  },
  {
    id: 'pend-006',
    category: 'case_action_required',
    title: 'DEMO_BEAM Case 要求补发票',
    detail: 'case-demo-0006 被拒，要求提供近 180 天内含品牌名的发票。补齐材料后可从申请记录重新发起。',
    accountLabel: 'demo-eu',
    brand: 'DEMO_BEAM',
    jobId: null,
    incidentId: null,
    detectedAt: minsAgo(60 * 30),
  },
  {
    id: 'pend-007',
    category: 'ui_change_suspected',
    title: '疑似 5461 页面改版',
    detail: '选择器 submit_button 连续 7 次未命中，页面结构与历史快照差异显著。已生成 incident，等待修复审核。',
    accountLabel: 'demo-na',
    brand: null,
    jobId: null,
    incidentId: 'inc-demo-ui',
    detectedAt: minsAgo(60 * 26),
  },
]

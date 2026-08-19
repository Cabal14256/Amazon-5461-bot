<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NBadge, NButton, NCard, NEmpty, NInput, NSpin, NTag, useDialog, useMessage } from 'naive-ui'
import {
  closeIncident,
  getAuthBlocks,
  getCaseIdRecoveries,
  getIncidents,
  getPendingItems,
  getReapplications,
  openAuthProfile,
  verifyAndResumeAuth,
} from '@/api'
import { USE_MOCK } from '@/api/client'
import { ApiError } from '@/api/http'
import {
  REPAIR_CLASSIFICATIONS,
  type AuthBlock,
  type CaseIdRecovery,
  type Incident,
  type PendingCategory,
  type PendingItem,
  type ReapplicationCampaign,
} from '@/types'
import { useAuthStore } from '@/stores/auth'
import { attemptTag, campaignTag } from '@/theme/statusColors'
import StatusTag from '@/components/StatusTag.vue'
import RelativeTime from '@/components/RelativeTime.vue'
import { dashboardStatusLabel } from '@/utils/statusLabels'

const router = useRouter()
const dialog = useDialog()
const message = useMessage()
const auth = useAuthStore()
const items = ref<PendingItem[]>([])
const recoveries = ref<CaseIdRecovery[]>([])
const manualCampaigns = ref<ReapplicationCampaign[]>([])
const incidents = ref<Incident[]>([])
const authBlocks = ref<AuthBlock[]>([])
const loading = ref(true)
const loadError = ref('')
const actionState = ref<Record<number, 'opening' | 'verifying' | undefined>>({})
let refreshTimer: ReturnType<typeof setInterval> | null = null

async function loadPage(showLoading = false) {
  if (showLoading) loading.value = true
  try {
    if (USE_MOCK) {
      items.value = await getPendingItems()
    } else {
      const [blocks, rec, camps, inc] = await Promise.all([
        getAuthBlocks(),
        getCaseIdRecoveries(),
        getReapplications(),
        // incident 接口（阶段 5）不可用时降级为空，不影响既有两张卡片
        getIncidents({ status: 'open' }).catch(() => ({ incidents: [] as Incident[], total: 0 })),
      ])
      authBlocks.value = blocks
      // 未完成或需人工核对的找回项算待处理；已完成/已取消的历史不展示
      recoveries.value = rec.filter((r) => !['completed', 'cancelled'].includes(r.status))
      // campaign 本身 manual_review/blocked，或任一 attempt 需人工审核
      manualCampaigns.value = camps.filter(
        (c) =>
          ['manual_review', 'blocked'].includes(c.status) ||
          c.attempts.some((a) => ['manual_review', 'blocked'].includes(a.status)),
      )
      incidents.value = inc.incidents
    }
    loadError.value = ''
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '状态读取失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadPage(true)
  refreshTimer = setInterval(() => void loadPage(false), 15_000)
})
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

const BLOCK_LABEL: Record<string, string> = {
  login_required: '登录过期',
  captcha_required: 'CAPTCHA',
  two_factor_required: '2FA',
  account_risk: '账户风控',
  unknown_auth_state: '认证页面待确认',
}

function phaseLabel(block: AuthBlock): string {
  if (block.source_type === 'case_followup') return 'Case 跟进'
  if (block.submit_fenced) return '提交后结果未知'
  return '提交前'
}

function evidenceUrl(path: string): string {
  return `/api/evidence/file?path=${encodeURIComponent(path)}`
}

async function openProfile(block: AuthBlock) {
  if (actionState.value[block.id]) return
  actionState.value[block.id] = 'opening'
  try {
    await openAuthProfile(block.id)
    message.success('已打开准确的 AdsPower profile，请在保留的页面中完成登录')
  } catch (error) {
    message.error(error instanceof ApiError ? `打开失败：${error.detail}` : '打开 AdsPower 失败')
  } finally {
    actionState.value[block.id] = undefined
  }
}

async function verifyResume(block: AuthBlock) {
  if (actionState.value[block.id]) return
  actionState.value[block.id] = 'verifying'
  try {
    const result = await verifyAndResumeAuth(block.id)
    if (result.status === 'resolved' || result.status === 'already_resolved') {
      message.success(
        block.submit_fenced
          ? '登录已恢复，已开始结果确认；不会重复提交'
          : '登录已恢复，已安排从当前站点安全继续',
      )
      await loadPage(false)
    } else if (result.status === 'still_blocked') {
      message.warning(`仍需登录或人工验证：${BLOCK_LABEL[result.block_type ?? ''] ?? result.reason ?? '认证未通过'}`)
    } else {
      message.warning(`验证结果：${result.status}`)
    }
  } catch (error) {
    message.error(error instanceof ApiError ? `验证失败：${error.detail}` : '验证失败，请稍后重试')
  } finally {
    actionState.value[block.id] = undefined
  }
}

const groups: { key: PendingCategory; title: string; desc: string; color: string }[] = [
  { key: 'captcha_2fa_login', title: 'CAPTCHA / 2FA / 登录过期', desc: '浏览器保持打开，人工完成后任务继续', color: '#8b5cf6' },
  { key: 'account_risk_unknown_ui', title: '账号风险 / 未知页面', desc: '自动化已主动停下，等待人工确认', color: '#d03050' },
  { key: 'rate_limit_429', title: '429 / 410001 限流', desc: '已熔断冷却，重试前需人工确认', color: '#f0a020' },
  { key: 'draft_no_case_conflict', title: '草稿 / 无 Case ID / 状态冲突', desc: '先查控制面板再决定，禁止盲目重提', color: '#eab308' },
  { key: 'case_action_required', title: 'Case 要求补充材料', desc: '补齐材料后可重新发起', color: '#3b82f6' },
  { key: 'ui_change_suspected', title: '疑似页面改版', desc: '已生成 incident，去修复中心处理', color: '#0e7490' },
]

const grouped = computed(() =>
  groups.map((g) => ({
    ...g,
    items: items.value.filter((it) => it.category === g.key),
  })),
)

const realEmpty = computed(
  () => authBlocks.value.length === 0 && recoveries.value.length === 0 && manualCampaigns.value.length === 0 && incidents.value.length === 0,
)

/** 真实模式 incident 分组（按 classification 首匹配归组，state_unknown 只进"账号风险"组不重复进改版组） */
const incidentGroups: { key: string; title: string; desc: string; color: string; classifications: readonly string[]; toRepair: boolean }[] = [
  { key: 'captcha', title: 'CAPTCHA / 2FA / 登录过期', desc: '浏览器保持打开，人工完成后任务继续', color: '#8b5cf6', classifications: ['captcha', 'two_fa', 'login_expired'], toRepair: false },
  { key: 'risk', title: '账号风险 / 未知页面', desc: '自动化已主动停下，等待人工确认', color: '#d03050', classifications: ['account_risk', 'state_unknown'], toRepair: false },
  { key: 'rate_limit', title: '429 / 410001 限流', desc: '已熔断冷却，重试前需人工确认', color: '#f0a020', classifications: ['rate_limit'], toRepair: false },
  { key: 'business', title: '业务未决', desc: '先查控制面板再决定，禁止盲目重提', color: '#eab308', classifications: ['business_uncertain', 'amazon_platform_error'], toRepair: false },
  { key: 'config', title: '配置缺失 / 品牌阻断', desc: '补齐配置或品牌材料后可重新发起', color: '#3b82f6', classifications: ['config_missing', 'brand_block'], toRepair: false },
  { key: 'ui_change', title: '疑似页面改版', desc: '已生成 incident，去修复中心处理', color: '#0e7490', classifications: REPAIR_CLASSIFICATIONS, toRepair: true },
]

const incidentGrouped = computed(() => {
  const assigned = new Set<number>()
  return incidentGroups.map((g) => {
    const list = incidents.value.filter(
      (it) => !assigned.has(it.id) && g.classifications.includes(it.classification),
    )
    list.forEach((it) => assigned.add(it.id))
    return { ...g, items: list }
  })
})

function incidentTitle(it: Incident): string {
  return it.brand_name ?? it.signature.slice(0, 8)
}

/** 关闭 incident（operator+）：弹输入 note 的确认框，成功后从列表移除 */
function closeIncidentItem(it: Incident) {
  const note = ref('')
  dialog.warning({
    title: '关闭 incident',
    content: () =>
      h(NInput, {
        'value': note.value,
        'onUpdate:value': (v: string) => {
          note.value = v
        },
        'type': 'textarea',
        'rows': 3,
        'placeholder': '关闭说明（必填）：记录人工处理结论，随 incident 存档',
      }),
    positiveText: '确认关闭',
    negativeText: '取消',
    onPositiveClick: async () => {
      const trimmed = note.value.trim()
      if (!trimmed) {
        message.warning('请填写关闭说明')
        return false
      }
      try {
        await closeIncident(it.id, trimmed)
        incidents.value = incidents.value.filter((x) => x.id !== it.id)
        message.success('已关闭')
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          // 已被他人关闭：直接从列表移除
          incidents.value = incidents.value.filter((x) => x.id !== it.id)
          message.info('该 incident 已被关闭')
          return
        }
        message.error(e instanceof ApiError ? `关闭失败：${e.detail}` : '关闭失败，请稍后重试')
        return false
      }
    },
  })
}

function go(item: PendingItem) {
  if (item.jobId) router.push(`/jobs/${item.jobId}`)
  else if (item.incidentId) router.push('/repair')
}
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">待人工处理</h1>
        <p class="page-subtitle">自动化主动停下来的事项都汇聚在这里，按类别分组</p>
      </div>
    </div>

    <n-alert v-if="loadError" type="error" :bordered="false" style="margin-bottom: 16px">
      状态读取失败：{{ loadError }}
      <n-button size="small" tertiary style="margin-left: 10px" @click="loadPage(true)">重试</n-button>
    </n-alert>

    <n-spin :show="loading">
      <!-- Mock 模式：原有六类演示分组 -->
      <template v-if="USE_MOCK">
        <n-empty
          v-if="!loading && items.length === 0"
          description="太棒了，当前没有需要人工处理的事项"
          style="margin-top: 80px"
        />
        <div class="group-grid">
          <n-card v-for="g in grouped" :key="g.key" class="group-card hoverable" size="small">
            <template #header>
              <div class="group-head">
                <span class="group-dot" :style="{ backgroundColor: g.color }" />
                <span>{{ g.title }}</span>
                <n-badge :value="g.items.length" :max="99" :type="g.items.length > 0 ? 'warning' : 'default'" />
              </div>
            </template>
            <template #header-extra>
              <span class="group-desc">{{ g.desc }}</span>
            </template>

            <n-empty v-if="g.items.length === 0" description="暂无事项" size="small" style="margin: 12px 0" />
            <div v-else class="pending-items">
              <div v-for="it in g.items" :key="it.id" class="pending-item">
                <div class="pending-title">{{ it.title }}</div>
                <div class="pending-detail">{{ it.detail }}</div>
                <div class="pending-meta">
                  <span>{{ it.accountLabel }}</span>
                  <span v-if="it.brand">· {{ it.brand }}</span>
                  <span>· <RelativeTime :time="it.detectedAt" /></span>
                  <n-button
                    v-if="it.jobId || it.incidentId"
                    size="tiny"
                    tertiary
                    type="primary"
                    @click="go(it)"
                  >
                    {{ it.jobId ? '看任务' : '去修复中心' }}
                  </n-button>
                </div>
              </div>
            </div>
          </n-card>
        </div>
      </template>

      <!-- 真实模式：Case ID 找回 + 重新申请人工审核 -->
      <template v-else>
        <n-empty
          v-if="!loading && realEmpty"
          description="太棒了，当前没有需要人工处理的事项"
          style="margin-top: 80px"
        />
        <div class="group-grid">
          <n-card v-if="authBlocks.length > 0" class="group-card auth-card" size="small">
            <template #header>
              <div class="group-head">
                <span class="group-dot" style="background-color: #8b5cf6" />
                <span>登录恢复</span>
                <n-badge :value="authBlocks.length" :max="99" type="error" />
              </div>
            </template>
            <template #header-extra>
              <span class="group-desc">同一账号已安全暂停，其他账号仍可运行；页面每 15 秒刷新</span>
            </template>

            <div class="auth-block-list">
              <div v-for="block in authBlocks" :key="block.id" class="auth-block-item">
                <div class="auth-block-head">
                  <div>
                    <strong>{{ block.brand_name || '账号级认证阻塞' }}</strong>
                    <span class="title-scope">
                      {{ block.account_id }} · {{ block.marketplace?.toUpperCase() || '—' }} · profile {{ block.profile_hint }}
                    </span>
                  </div>
                  <div class="auth-tags">
                    <n-tag size="small" type="error" :bordered="false">
                      {{ BLOCK_LABEL[block.block_type] ?? block.block_type }}
                    </n-tag>
                    <n-tag size="small" :type="block.submit_fenced ? 'warning' : 'info'" :bordered="false">
                      {{ phaseLabel(block) }}
                    </n-tag>
                  </div>
                </div>

                <n-alert :type="block.submit_fenced ? 'warning' : 'info'" :bordered="false" class="auth-safety">
                  <strong>已安全暂停。</strong>
                  <template v-if="block.submit_fenced">
                    提交点击安全边界已落库，系统不会重复提交；恢复后只核对 Selling Applications/Case。
                  </template>
                  <template v-else>
                    尚未越过提交点击安全边界；恢复后只继续当前站点，不会提前推进下一站。
                  </template>
                </n-alert>

                <div class="auth-meta-grid">
                  <span>发生阶段：{{ phaseLabel(block) }}</span>
                  <span>检测：<RelativeTime :time="block.detected_at" /></span>
                  <span v-if="block.last_checked_at">最后检查：<RelativeTime :time="block.last_checked_at" /></span>
                  <span v-if="block.next_check_at">下次自动检查：<RelativeTime :time="block.next_check_at" /></span>
                </div>
                <div v-if="block.detail" class="pending-detail">{{ block.detail }}</div>

                <div class="auth-actions">
                  <n-button
                    size="small"
                    type="primary"
                    secondary
                    :disabled="!block.can_open_profile || Boolean(actionState[block.id])"
                    :loading="actionState[block.id] === 'opening'"
                    @click="openProfile(block)"
                  >
                    打开 AdsPower 登录
                  </n-button>
                  <n-button
                    size="small"
                    type="warning"
                    :disabled="!block.can_verify_and_resume || Boolean(actionState[block.id])"
                    :loading="actionState[block.id] === 'verifying'"
                    @click="verifyResume(block)"
                  >
                    {{ block.submit_fenced ? '验证并开始结果确认' : '验证并继续' }}
                  </n-button>
                  <n-button
                    v-if="block.evidence_path"
                    size="small"
                    tertiary
                    tag="a"
                    target="_blank"
                    :href="evidenceUrl(block.evidence_path)"
                  >
                    查看登录页面证据
                  </n-button>
                  <span v-if="!block.can_verify_and_resume" class="permission-hint">viewer/operator 仅可查看，需 reviewer/admin 操作</span>
                </div>
              </div>
            </div>
          </n-card>

          <n-card class="group-card hoverable" size="small">
            <template #header>
              <div class="group-head">
                <span class="group-dot" style="background-color: #eab308" />
                <span>Case ID 找回</span>
                <n-badge :value="recoveries.length" :max="99" :type="recoveries.length > 0 ? 'warning' : 'default'" />
              </div>
            </template>
            <template #header-extra>
              <span class="group-desc">提交后未回显 Case ID，按计划自动找回；卡住的需要人工核对控制面板</span>
            </template>

            <n-empty v-if="recoveries.length === 0" description="暂无事项" size="small" style="margin: 12px 0" />
            <div v-else class="pending-items">
              <div v-for="r in recoveries" :key="r.id" class="pending-item">
                <div class="pending-title">
                  {{ r.brand_name }}
                  <span class="title-scope">{{ r.account_id }} · {{ r.marketplace.toUpperCase() }}</span>
                </div>
                <div class="pending-detail">
                  Case ID：<code>{{ r.case_id || '未找回' }}</code>
                  <template v-if="r.dashboard_status"> · 控制面板：{{ dashboardStatusLabel(r.dashboard_status) }}</template>
                  <template v-if="r.decision_reason"> · {{ r.decision_reason }}</template>
                  <template v-if="r.error"> · {{ r.error }}</template>
                </div>
                <div class="pending-meta">
                  <StatusTag :kind="attemptTag(r.status).kind" :status="attemptTag(r.status).status" size="small" />
                  <span>已尝试 {{ r.attempt_count }} 次</span>
                  <span v-if="r.scheduled_at && ['pending', 'retry', 'running'].includes(r.status)">
                    · 下次 <RelativeTime :time="r.scheduled_at" />
                  </span>
                </div>
              </div>
            </div>
          </n-card>

          <n-card class="group-card hoverable" size="small">
            <template #header>
              <div class="group-head">
                <span class="group-dot" style="background-color: #8b5cf6" />
                <span>重新申请人工审核</span>
                <n-badge
                  :value="manualCampaigns.length"
                  :max="99"
                  :type="manualCampaigns.length > 0 ? 'warning' : 'default'"
                />
              </div>
            </template>
            <template #header-extra>
              <span class="group-desc">跨站点重新申请活动中被标记为需人工确认 / 被阻塞的条目</span>
            </template>

            <n-empty v-if="manualCampaigns.length === 0" description="暂无事项" size="small" style="margin: 12px 0" />
            <div v-else class="pending-items">
              <div v-for="c in manualCampaigns" :key="c.id" class="pending-item">
                <div class="pending-title">
                  {{ c.brand_name }}
                  <span class="title-scope">{{ c.account_id }} · {{ c.region }}</span>
                </div>
                <div class="pending-detail">
                  路由：{{ c.route.map((s) => s.toUpperCase()).join(' → ') }}
                  <template v-if="c.stop_reason"> · {{ c.stop_reason }}</template>
                </div>
                <div class="pending-meta">
                  <StatusTag :kind="campaignTag(c.status).kind" :status="campaignTag(c.status).status" size="small" />
                  <n-button size="tiny" tertiary type="primary" @click="router.push('/reapplications')">
                    查看活动
                  </n-button>
                  <span v-if="c.updated_at">· <RelativeTime :time="c.updated_at" /></span>
                </div>
              </div>
            </div>
          </n-card>

          <!-- 阶段 5：incident 队列分组（open 状态，按 classification 首匹配归组） -->
          <n-card v-for="g in incidentGrouped" :key="g.key" class="group-card hoverable" size="small">
            <template #header>
              <div class="group-head">
                <span class="group-dot" :style="{ backgroundColor: g.color }" />
                <span>{{ g.title }}</span>
                <n-badge :value="g.items.length" :max="99" :type="g.items.length > 0 ? 'warning' : 'default'" />
              </div>
            </template>
            <template #header-extra>
              <span class="group-desc">{{ g.desc }}</span>
            </template>

            <n-empty v-if="g.items.length === 0" description="暂无事项" size="small" style="margin: 12px 0" />
            <div v-else class="pending-items">
              <div v-for="it in g.items" :key="it.id" class="pending-item">
                <div class="pending-title">
                  {{ incidentTitle(it) }}
                  <span class="title-scope">
                    <template v-if="it.account_id">{{ it.account_id }} · </template>
                    <template v-if="it.marketplace">{{ it.marketplace.toUpperCase() }}</template>
                  </span>
                </div>
                <div class="pending-detail">
                  {{ it.detector_type }} · 置信度 {{ Math.round(it.confidence * 100) }}%
                  <template v-if="it.flow_type"> · {{ it.flow_type }}</template>
                </div>
                <div class="pending-meta">
                  <span>×{{ it.occurrence_count }} 次</span>
                  <span>· 最近 <RelativeTime :time="it.last_seen_at" /></span>
                  <n-button
                    v-if="g.toRepair"
                    size="tiny"
                    tertiary
                    type="primary"
                    @click="router.push('/repair')"
                  >
                    去修复中心
                  </n-button>
                  <n-button
                    v-if="auth.isOperatorPlus"
                    size="tiny"
                    tertiary
                    @click="closeIncidentItem(it)"
                  >
                    关闭
                  </n-button>
                </div>
              </div>
            </div>
          </n-card>
        </div>
      </template>
    </n-spin>
  </div>
</template>

<style scoped>
.group-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 16px;
}

.auth-card {
  grid-column: 1 / -1;
  border-color: rgba(139, 92, 246, 0.35);
}

.auth-block-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.auth-block-item {
  padding: 14px;
  border: 1px solid rgba(139, 92, 246, 0.22);
  border-radius: 8px;
}

.auth-block-head,
.auth-tags,
.auth-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.auth-block-head {
  justify-content: space-between;
}

.auth-safety {
  margin-top: 12px;
}

.auth-meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 8px 18px;
  margin-top: 12px;
  font-size: 12px;
  opacity: 0.7;
}

.auth-actions {
  margin-top: 12px;
}

.permission-hint {
  font-size: 12px;
  opacity: 0.55;
}

.group-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}

.group-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}

.group-desc {
  font-size: 12px;
  opacity: 0.5;
  font-weight: 400;
}

.pending-items {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pending-item {
  border-left: 3px solid rgba(128, 128, 128, 0.25);
  padding-left: 12px;
}

.pending-title {
  font-weight: 600;
  font-size: 13px;
}

.title-scope {
  font-weight: 400;
  opacity: 0.6;
  margin-left: 6px;
}

.pending-detail {
  font-size: 12px;
  opacity: 0.75;
  line-height: 1.6;
  margin-top: 4px;
}

.pending-detail code {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
}

.pending-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  opacity: 0.6;
  margin-top: 6px;
  flex-wrap: wrap;
}
</style>

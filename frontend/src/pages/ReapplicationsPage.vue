<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NIcon, NModal, NSelect, NSpin, NStep, NSteps, NTag, NTooltip, useMessage } from 'naive-ui'
import { RefreshOutline } from '@vicons/ionicons5'
import { authorizeReapplication, getEligibleDeclinedCases, getReapplication, getReapplications } from '@/api'
import type { EligibleDeclinedCase, ReapplicationAttempt, ReapplicationCampaign } from '@/types'
import { attemptTag, campaignTag } from '@/theme/statusColors'
import StatusTag from '@/components/StatusTag.vue'
import RelativeTime from '@/components/RelativeTime.vue'
import { businessResultLabel } from '@/utils/statusLabels'
import { useAuthStore } from '@/stores/auth'
import { reapplicationAuthorizationMessage } from '@/utils/tableSemantics'

const campaigns = ref<ReapplicationCampaign[]>([])
const loading = ref(true)
const refreshingId = ref<number | null>(null)
const eligible = ref<EligibleDeclinedCase[]>([])
const selectedFollowupId = ref<number | null>(null)
const showAuthorize = ref(false)
const submitting = ref(false)
const auth = useAuthStore()
const message = useMessage()

onMounted(async () => {
  campaigns.value = await getReapplications()
  if (auth.isReviewerPlus) eligible.value = await getEligibleDeclinedCases()
  loading.value = false
})

const selectedCandidate = computed(() => eligible.value.find((item) => item.id === selectedFollowupId.value) ?? null)
const eligibleOptions = computed(() => eligible.value.map((item) => ({
  label: `${item.brand_name} · ${item.account_id} · ${item.marketplace} · Case ${item.case_id || '—'}`,
  value: item.id,
})))

async function submitAuthorization() {
  const candidate = selectedCandidate.value
  if (!candidate || submitting.value) return
  submitting.value = true
  try {
    const response = await authorizeReapplication(candidate.id, candidate.remaining_route)
    message.success(reapplicationAuthorizationMessage(response.created))
    campaigns.value = await getReapplications()
    eligible.value = await getEligibleDeclinedCases()
    selectedFollowupId.value = null
    showAuthorize.value = false
  } catch (error) {
    message.error(error instanceof Error ? error.message : '授权失败')
  } finally {
    submitting.value = false
  }
}

/** 单卡刷新：拉取 /api/reapplications/{id} 最新 attempts 进度 */
async function refreshCampaign(c: ReapplicationCampaign) {
  if (refreshingId.value !== null) return
  refreshingId.value = c.id
  try {
    const fresh = await getReapplication(c.id)
    const idx = campaigns.value.findIndex((it) => it.id === c.id)
    if (idx >= 0) campaigns.value[idx] = fresh
  } finally {
    refreshingId.value = null
  }
}

function attemptAt(c: ReapplicationCampaign, routeIndex: number): ReapplicationAttempt | undefined {
  return c.attempts.find((a) => a.route_index === routeIndex)
}

function stepStatus(c: ReapplicationCampaign, i: number): 'process' | 'finish' | 'wait' | 'error' {
  const attempt = attemptAt(c, i)
  if (attempt) {
    if (attempt.status === 'completed') return 'finish'
    if (['failed', 'error', 'manual_review', 'blocked'].includes(attempt.status)) return 'error'
    if (['running', 'submitted'].includes(attempt.status)) return 'process'
  }
  if (i < c.current_route_index) return 'finish'
  if (i === c.current_route_index && !['completed', 'failed', 'stopped'].includes(c.status)) return 'process'
  return 'wait'
}

const ACTIVE_STATUSES = ['scheduled', 'next_scheduled', 'running', 'waiting_case_id', 'waiting_case', 'paused', 'blocked']

function isManualReview(a: ReapplicationAttempt): boolean {
  return a.status === 'manual_review' || a.status === 'blocked'
}
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">重新申请</h1>
        <p class="page-subtitle">跨站点路由（如 US → MX）逐个站点重试申请，直到某站通过或全部失败</p>
      </div>
      <n-button v-if="auth.isReviewerPlus" type="error" style="margin-left: auto" @click="showAuthorize = true">
        从已拒绝 Case 发起
      </n-button>
    </div>

    <n-spin :show="loading">
      <n-empty
        v-if="!loading && campaigns.length === 0"
        description="当前没有重新申请活动"
        style="margin-top: 80px"
      />
      <div class="campaign-list">
        <n-card v-for="c in campaigns" :key="c.id" class="campaign-card hoverable" size="small">
          <template #header>
            <div class="c-head">
              <span class="c-brand">{{ c.brand_name }}</span>
              <n-tag size="tiny" :bordered="false">{{ c.region }}</n-tag>
              <span class="c-account">账号 {{ c.account_id }}</span>
            </div>
          </template>
          <template #header-extra>
            <div class="c-head-extra">
              <StatusTag :kind="campaignTag(c.status).kind" :status="campaignTag(c.status).status" size="small" />
              <n-tooltip trigger="hover">
                <template #trigger>
                  <n-button quaternary circle size="small" :loading="refreshingId === c.id" @click="refreshCampaign(c)">
                    <template #icon>
                      <n-icon :component="RefreshOutline" />
                    </template>
                  </n-button>
                </template>
                刷新该活动最新进度
              </n-tooltip>
            </div>
          </template>

          <n-alert v-if="c.stop_reason" type="warning" :bordered="false" style="margin-bottom: 12px">
            {{ c.stop_reason }}
          </n-alert>

          <n-steps size="small" :current="c.current_route_index + 1" class="route-steps">
            <n-step
              v-for="(site, i) in c.route"
              :key="i"
              :title="site.toUpperCase()"
              :status="stepStatus(c, i)"
            />
          </n-steps>

          <div v-if="c.attempts.length > 0" class="attempt-list">
            <div v-for="a in c.attempts" :key="a.id" class="attempt-row">
              <span class="a-site">{{ a.site.toUpperCase() }}</span>
              <StatusTag :kind="attemptTag(a.status).kind" :status="attemptTag(a.status).status" size="small" />
              <n-tag v-if="isManualReview(a)" size="tiny" type="warning" :bordered="false">需人工审核</n-tag>
              <span class="a-field">Case ID：<code>{{ a.case_id ?? '—' }}</code></span>
              <span v-if="a.final_result" class="a-field">结果：{{ businessResultLabel(a.final_result) }}</span>
              <span v-if="a.error" class="a-error">{{ a.error }}</span>
              <span v-if="a.scheduled_at" class="a-time">
                计划 <RelativeTime :time="a.scheduled_at" />
              </span>
            </div>
          </div>

          <div class="c-footer">
            <span v-if="c.created_at">创建 <RelativeTime :time="c.created_at" /></span>
            <span v-if="c.updated_at">· 更新 <RelativeTime :time="c.updated_at" /></span>
            <span v-if="c.completed_at">· 完成 <RelativeTime :time="c.completed_at" /></span>
            <span v-if="ACTIVE_STATUSES.includes(c.status)" class="c-active-hint">· 活动进行中</span>
          </div>
        </n-card>
      </div>
    </n-spin>

    <n-modal v-model:show="showAuthorize" preset="card" title="授权拒绝后的重新申请" style="width: min(620px, 92vw)">
      <n-alert type="warning" :bordered="false" style="margin-bottom: 14px">
        这是一次真实提交授权。确认后，当前明确拒绝记录会保留为来源，后续明确拒绝将自动按固定国家顺序推进。
      </n-alert>
      <n-select v-model:value="selectedFollowupId" :options="eligibleOptions" placeholder="选择已完成且明确拒绝的 Case" filterable />
      <div v-if="selectedCandidate" class="authorization-route">
        <div>来源：{{ selectedCandidate.marketplace }} / Case {{ selectedCandidate.case_id || '—' }}</div>
        <strong>剩余路线：{{ selectedCandidate.remaining_route.join(' → ') }}</strong>
        <small>下一站：{{ selectedCandidate.next_site }}；实际计划时间由后端按“拒绝时间 + 配置延迟”计算。</small>
      </div>
      <template #footer>
        <div class="modal-actions">
          <n-button @click="showAuthorize = false">取消</n-button>
          <n-button type="error" :disabled="!selectedCandidate" :loading="submitting" @click="submitAuthorization">
            确认授权按上述路线提交
          </n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.campaign-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.c-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.c-brand {
  font-weight: 700;
}

.c-account {
  font-size: 13px;
  opacity: 0.65;
}

.c-head-extra {
  display: flex;
  align-items: center;
  gap: 6px;
}

.route-steps {
  margin: 8px 0 16px;
}

.attempt-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-top: 1px dashed rgba(128, 128, 128, 0.25);
  padding-top: 12px;
}

.attempt-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}

.a-site {
  font-weight: 700;
  min-width: 32px;
}

.a-field {
  opacity: 0.75;
}

.a-field code {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
}

.a-error {
  color: #d03050;
  font-size: 12px;
}

.a-time {
  margin-left: auto;
  font-size: 12px;
  opacity: 0.55;
}

.c-footer {
  margin-top: 12px;
  font-size: 12px;
  opacity: 0.5;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.c-active-hint {
  color: #2080f0;
  opacity: 1;
}

.authorization-route { margin-top: 16px; padding: 14px; border-radius: 8px; background: rgba(240, 160, 32, 0.1); display: grid; gap: 8px; }
.authorization-route small { opacity: 0.65; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; }
</style>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NBreadcrumb, NBreadcrumbItem, NButton, NCard, NDescriptions, NDescriptionsItem, NEmpty, NGrid, NGridItem, NSpin, NTag, useDialog, useMessage } from 'naive-ui'
import { useJobsStore } from '@/stores/jobs'
import { useAuthStore } from '@/stores/auth'
import { USE_MOCK } from '@/api/client'
import { STOP_REQUESTED_HINT } from '@/api/real'
import type { JobMode, JobType } from '@/types'
import StatusTag from '@/components/StatusTag.vue'
import RelativeTime from '@/components/RelativeTime.vue'
import LogViewer from '@/components/LogViewer.vue'
import EvidenceTimeline from '@/components/EvidenceTimeline.vue'
import { dashboardStatusLabel } from '@/utils/statusLabels'

const route = useRoute()
const router = useRouter()
const dialog = useDialog()
const message = useMessage()
const jobsStore = useJobsStore()
const auth = useAuthStore()

const jobId = route.params.id as string

// 真实模式由 store 内的 SSE 驱动（断线自动降级 5s 轮询）；组件卸载时关闭
onMounted(() => {
  void jobsStore.openJob(jobId)
})

onUnmounted(() => {
  jobsStore.stopLogStream()
})

const job = computed(() => jobsStore.currentJob)

const typeLabel: Record<JobType, string> = {
  apply_5461: '5461 申请',
  catalog_auth: '目录授权',
  brand_verify: '品牌验证',
  gtin_exemption: 'GTIN 豁免',
  unknown: '任务',
}

const modeLabel: Record<JobMode, string> = { diagnose: '诊断', dry_run: 'Dry-run', submit: '真实提交', real: '真实提交', unknown: '—' }

/** queued 排队原因注解（后端 queue_reason）→ 中文说明 */
const queueReasonLabel: Record<string, string> = {
  waiting_case_followup: '等待该账号的 Case 跟进检查完成',
  waiting_profile_lock: '等待浏览器 profile 释放',
  waiting_serial_queue: '等待其他任务完成（串行队列）',
}

/** 运行模式徽章色：真实提交红、Dry-run 蓝、诊断灰 */
function modeTagType(m: JobMode): 'error' | 'info' | 'default' {
  if (m === 'submit' || m === 'real') return 'error'
  if (m === 'dry_run') return 'info'
  return 'default'
}

const isLive = computed(() => !!job.value && ['running', 'starting'].includes(job.value.status))

const TERMINAL: readonly string[] = ['completed', 'failed', 'cancelled_before_start', 'terminated_unknown_state']

// 请求安全停止：operator+ 可见；任务处于非终态即可请求（queued 也可在启动前取消）
const canStop = computed(
  () =>
    !!job.value &&
    !TERMINAL.includes(job.value.status) &&
    job.value.status !== 'stop_requested' &&
    (USE_MOCK || auth.isOperatorPlus),
)

// 强制终止（admin，高危）：不显眼入口 + 二次确认；仅真实模式
const canForceTerminate = computed(
  () => !USE_MOCK && auth.isAdmin && !!job.value && !TERMINAL.includes(job.value.status),
)

const liveHint = computed(() => {
  if (USE_MOCK) return jobsStore.streamMode === 'mock' ? '实时日志（模拟流）' : '日志'
  if (jobsStore.streamMode === 'sse') return '实时日志（SSE）'
  if (jobsStore.streamMode === 'polling') return '日志（SSE 已断线，5s 轮询降级中）'
  return '日志'
})

function fmtDuration(sec: number | null): string {
  if (sec === null) return '—'
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

function onRequestStop() {
  dialog.warning({
    title: '请求安全停止',
    content: '任务不会立刻被杀掉：会在当前品牌处理完成后退出浏览器并落盘状态，确保不留半截提交。',
    positiveText: '请求安全停止',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await jobsStore.requestStop()
        if (!USE_MOCK) message.success(STOP_REQUESTED_HINT)
      } catch (e) {
        message.error(`请求停止失败：${e instanceof Error ? e.message : String(e)}`)
      }
    },
  })
}

function onForceTerminate() {
  dialog.warning({
    title: '强制终止（高危）',
    content:
      '强制终止会立即杀掉浏览器子进程，任务将记为「异常终止·状态未知」。本地状态与亚马逊侧可能不一致，之后必须先人工查控制面板再决定下一步。确定继续？',
    positiveText: '继续',
    negativeText: '取消',
    onPositiveClick: () => {
      // 二次确认
      dialog.error({
        title: '最后确认',
        content: `确认强制终止任务 ${jobId}？此操作不可撤销。`,
        positiveText: '确认强制终止',
        negativeText: '取消',
        onPositiveClick: async () => {
          try {
            await jobsStore.forceTerminate()
            message.warning('已强制终止：状态未知，请先查控制面板再决定后续操作')
          } catch (e) {
            message.error(`强制终止失败：${e instanceof Error ? e.message : String(e)}`)
          }
        },
      })
    },
  })
}
</script>

<template>
  <div class="page-container">
    <n-breadcrumb style="margin-bottom: 12px">
      <n-breadcrumb-item @click="router.push('/jobs')">任务列表</n-breadcrumb-item>
      <n-breadcrumb-item>{{ jobId }}</n-breadcrumb-item>
    </n-breadcrumb>

    <n-spin :show="!job">
      <template v-if="job">
        <n-alert
          v-if="job.status === 'terminated_unknown_state'"
          type="error"
          title="异常终止·状态未知"
          style="margin-bottom: 16px"
        >
          状态未知：请先到 Seller Central 的“查看销售申请”/控制面板核查是否已提交，再决定是否重跑，<strong>切勿直接重复提交</strong>。
        </n-alert>
        <n-alert v-else-if="job.status === 'waiting_human'" type="warning" title="等待人工介入" style="margin-bottom: 16px">
          {{ job.stopReason }}。处理完成后请前往「待人工处理」页标记继续。
        </n-alert>
        <n-alert v-else-if="job.status === 'stop_requested'" type="warning" title="已请求停止" style="margin-bottom: 16px">
          {{ job.stopReason ?? '已请求停止，将在当前品牌完成后安全停下' }}。如需立即终止请联系管理员（强制终止会留下未知状态）。
        </n-alert>
        <n-alert v-else-if="job.status === 'queued' && job.queueReason" type="info" title="排队中" style="margin-bottom: 16px">
          {{ queueReasonLabel[job.queueReason] ?? job.queueReason }}
        </n-alert>

        <n-card style="margin-bottom: 16px">
          <div class="head-row">
            <div class="head-left">
              <span class="head-type">{{ typeLabel[job.type] }}</span>
              <n-tag size="small" :bordered="false" :type="modeTagType(job.mode)">
                {{ modeLabel[job.mode] }}
              </n-tag>
              <StatusTag kind="run" :status="job.status" />
            </div>
            <div class="head-actions">
              <n-button v-if="canStop" type="warning" secondary @click="onRequestStop">请求安全停止</n-button>
              <n-button v-if="canForceTerminate" size="tiny" tertiary type="error" @click="onForceTerminate">
                强制终止
              </n-button>
            </div>
          </div>
          <n-descriptions :column="3" label-placement="top" size="small" style="margin-top: 12px">
            <n-descriptions-item label="账号">{{ job.accountLabel }}</n-descriptions-item>
            <n-descriptions-item label="站点">{{ job.site }}</n-descriptions-item>
            <n-descriptions-item label="品牌数">{{ job.brandTotal }}</n-descriptions-item>
            <n-descriptions-item label="创建时间"><RelativeTime :time="job.createdAt" /></n-descriptions-item>
            <n-descriptions-item label="开始时间"><RelativeTime :time="job.startedAt" /></n-descriptions-item>
            <n-descriptions-item label="结束时间"><RelativeTime :time="job.finishedAt" /></n-descriptions-item>
            <n-descriptions-item v-if="job.raw.stop_requested_at" label="停止请求时间">
              <RelativeTime :time="job.raw.stop_requested_at" />
            </n-descriptions-item>
            <n-descriptions-item v-if="job.raw.exit_code !== null" label="退出码">{{ job.raw.exit_code }}</n-descriptions-item>
            <n-descriptions-item v-if="job.raw.error_class" label="错误分类">{{ job.raw.error_class }}</n-descriptions-item>
          </n-descriptions>
          <div v-if="job.stopReason && job.status !== 'stop_requested'" class="stop-reason">{{ job.stopReason }}</div>
        </n-card>

        <n-grid :x-gap="16" cols="1 l:5" responsive="screen" item-responsive>
          <n-grid-item span="1 l:3">
            <n-card title="品牌执行明细" size="small">
              <template #header-extra>
                <span class="hint">运行态与业务态始终分开展示</span>
              </template>
              <div class="brand-rows">
                <div v-for="r in job.results" :key="r.brand" class="brand-row">
                  <div class="brand-line1">
                    <span class="brand-name">{{ r.brand }}</span>
                    <StatusTag kind="run" :status="r.runStatus" size="small" />
                    <StatusTag kind="business" :status="r.businessStatus" size="small" />
                    <span class="brand-duration">{{ fmtDuration(r.durationSec) }}</span>
                  </div>
                  <div class="brand-line2">
                    <span>Case ID：<code>{{ r.caseId ?? '—' }}</code></span>
                    <span class="sep">·</span>
                    <span>控制面板：{{ dashboardStatusLabel(r.dashboardStatus) }}</span>
                  </div>
                  <div v-if="r.note" class="brand-note">{{ r.note }}</div>
                </div>
              </div>
            </n-card>

            <n-card title="证据时间线" size="small" style="margin-top: 16px">
              <EvidenceTimeline :items="jobsStore.currentEvidence" />
            </n-card>
          </n-grid-item>

          <n-grid-item span="1 l:2">
            <div class="log-wrap">
              <LogViewer :lines="jobsStore.currentLogs" :live="isLive || jobsStore.streamMode === 'sse'" :title="liveHint" />
            </div>
          </n-grid-item>
        </n-grid>
      </template>
      <n-empty v-else-if="!job" description="任务不存在或已被清理" style="margin-top: 120px" />
    </n-spin>
  </div>
</template>

<style scoped>
.head-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.head-type {
  font-size: 18px;
  font-weight: 700;
}

.hint {
  font-size: 12px;
  opacity: 0.5;
  font-weight: 400;
}

.stop-reason {
  margin-top: 10px;
  font-size: 13px;
  color: #f0a020;
}

.brand-rows {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.brand-row {
  border-bottom: 1px solid rgba(128, 128, 128, 0.15);
  padding-bottom: 12px;
}

.brand-row:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.brand-line1 {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.brand-name {
  font-weight: 700;
  min-width: 120px;
}

.brand-duration {
  margin-left: auto;
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  opacity: 0.6;
}

.brand-line2 {
  display: flex;
  gap: 8px;
  font-size: 12px;
  opacity: 0.7;
  margin-top: 6px;
  flex-wrap: wrap;
}

.brand-line2 code {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
}

.sep {
  opacity: 0.4;
}

.brand-note {
  font-size: 12px;
  color: #f0a020;
  margin-top: 4px;
}

.log-wrap {
  height: 520px;
  min-height: 0;
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NEmpty, NIcon, NProgress, NSelect, NSpin, NTag } from 'naive-ui'
import { AddCircleOutline } from '@vicons/ionicons5'
import { useJobsStore } from '@/stores/jobs'
import type { JobMode, JobRunStatus, JobType } from '@/types'
import StatusTag from '@/components/StatusTag.vue'
import RelativeTime from '@/components/RelativeTime.vue'

const jobsStore = useJobsStore()
const route = useRoute()
const router = useRouter()

const statusFilter = ref<JobRunStatus | null>((route.query.status as JobRunStatus) ?? null)
const modeFilter = ref<JobMode | null>(null)
const accountFilter = ref<string | null>(null)

onMounted(() => {
  void jobsStore.refreshList()
})

const statusOptions = [
  { label: '排队中', value: 'queued' },
  { label: '启动中', value: 'starting' },
  { label: '运行中', value: 'running' },
  { label: '已请求停止', value: 'stop_requested' },
  { label: '待人工介入', value: 'waiting_human' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
  { label: '开始前取消', value: 'cancelled_before_start' },
  { label: '异常终止·状态未知', value: 'terminated_unknown_state' },
]

// 任务类型筛选：DB 主源的任务类型即运行模式（diagnose/dry_run/submit；real 仅 Mock 遗留演示数据）
const modeOptions = [
  { label: '诊断', value: 'diagnose' },
  { label: 'Dry-run', value: 'dry_run' },
  { label: '真实提交', value: 'submit' },
]

const accountOptions = computed(() =>
  Array.from(new Set(jobsStore.jobs.map((j) => j.accountLabel))).map((a) => ({ label: a, value: a })),
)

const filtered = computed(() =>
  jobsStore.jobs.filter(
    (j) =>
      (!statusFilter.value || j.status === statusFilter.value) &&
      (!modeFilter.value || j.mode === modeFilter.value) &&
      (!accountFilter.value || j.accountLabel === accountFilter.value),
  ),
)

const typeLabel: Record<JobType, string> = {
  apply_5461: '5461 申请',
  catalog_auth: '目录授权',
  brand_verify: '品牌验证',
  gtin_exemption: 'GTIN 豁免',
  unknown: '任务',
}

const modeLabel = { diagnose: '诊断', dry_run: 'Dry-run', submit: '真实提交', real: '真实提交', unknown: '—' } as const

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
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">任务列表</h1>
        <p class="page-subtitle">运行态看执行进度，业务结果逐品牌见任务详情</p>
      </div>
      <n-button style="margin-left: auto" type="primary" @click="router.push('/jobs/new')">
        <template #icon>
          <n-icon :component="AddCircleOutline" />
        </template>
        新建任务
      </n-button>
    </div>

    <div class="filter-bar">
      <n-select v-model:value="statusFilter" :options="statusOptions" placeholder="全部运行态" clearable style="width: 200px" />
      <n-select v-model:value="modeFilter" :options="modeOptions" placeholder="全部类型" clearable style="width: 180px" />
      <n-select v-model:value="accountFilter" :options="accountOptions" placeholder="全部账号" clearable style="width: 200px" />
    </div>

    <n-spin :show="jobsStore.loading && jobsStore.jobs.length === 0">
      <n-empty
        v-if="filtered.length === 0 && !jobsStore.loading"
        description="没有符合条件的任务——调整筛选，或新建一个 dry-run 试试"
        style="margin-top: 80px"
      />
      <div class="job-list">
        <n-card
          v-for="job in filtered"
          :key="job.id"
          class="job-row hoverable"
          @click="router.push(`/jobs/${job.id}`)"
        >
          <div class="job-main">
            <div class="job-line1">
              <span class="job-type">{{ typeLabel[job.type] }}</span>
              <n-tag size="tiny" :bordered="false" :type="modeTagType(job.mode)">
                {{ modeLabel[job.mode] }}
              </n-tag>
              <StatusTag kind="run" :status="job.status" size="small" />
              <span v-if="job.status === 'queued' && job.queueReason" class="queue-reason">
                {{ queueReasonLabel[job.queueReason] ?? job.queueReason }}
              </span>
              <span class="job-id">{{ job.id }}</span>
            </div>
            <div class="job-line2">
              <span>{{ job.accountLabel }} · {{ job.site }} 站</span>
              <span class="sep">·</span>
              <span>品牌 {{ job.brandSucceeded }} 成 / {{ job.brandFailed }} 败 / 共 {{ job.brandTotal }}</span>
              <span class="sep">·</span>
              <RelativeTime :time="job.startedAt ?? job.createdAt" />
            </div>
            <n-progress
              type="line"
              :percentage="Math.round(job.progress * 100)"
              :status="job.status === 'failed' || job.status === 'terminated_unknown_state' ? 'error' : job.status === 'completed' ? 'success' : 'default'"
              :show-indicator="false"
              :height="6"
              border-radius="3px"
              style="margin-top: 10px"
            />
          </div>
        </n-card>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.filter-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.job-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.job-row {
  cursor: pointer;
}

.job-line1 {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.job-type {
  font-weight: 700;
  font-size: 15px;
}

.job-id {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  opacity: 0.5;
  margin-left: auto;
}

.job-line2 {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  opacity: 0.75;
  margin-top: 6px;
  flex-wrap: wrap;
}

.sep {
  opacity: 0.4;
}

.queue-reason {
  font-size: 12px;
  opacity: 0.65;
}
</style>

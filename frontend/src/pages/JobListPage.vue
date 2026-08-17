<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NDataTable, NIcon, NProgress, NSelect, NTag } from 'naive-ui'
import type { DataTableColumns, PaginationProps } from 'naive-ui'
import { AddCircleOutline } from '@vicons/ionicons5'
import { useJobsStore } from '@/stores/jobs'
import type { JobMode, JobRunStatus, JobSummary, JobType } from '@/types'
import StatusTag from '@/components/StatusTag.vue'
import RelativeTime from '@/components/RelativeTime.vue'

const jobsStore = useJobsStore()
const route = useRoute()
const router = useRouter()
const statusFilter = ref<JobRunStatus | null>((route.query.status as JobRunStatus) ?? null)
const modeFilter = ref<JobMode | null>(null)
const accountFilter = ref<string | null>(null)
onMounted(() => void jobsStore.refreshList())

const statusOptions = [
  ['排队中', 'queued'], ['启动中', 'starting'], ['运行中', 'running'],
  ['已请求停止', 'stop_requested'], ['待人工介入', 'waiting_human'], ['已完成', 'completed'],
  ['失败', 'failed'], ['开始前取消', 'cancelled_before_start'], ['异常终止·状态未知', 'terminated_unknown_state'],
].map(([label, value]) => ({ label, value }))
const modeOptions = [
  { label: '诊断', value: 'diagnose' }, { label: 'Dry-run', value: 'dry_run' }, { label: '真实提交', value: 'submit' },
]
const accountOptions = computed(() =>
  Array.from(new Set(jobsStore.jobs.map((job) => job.accountLabel))).map((value) => ({ label: value, value })),
)
const filtered = computed(() => jobsStore.jobs.filter((job) =>
  (!statusFilter.value || job.status === statusFilter.value) &&
  (!modeFilter.value || job.mode === modeFilter.value) &&
  (!accountFilter.value || job.accountLabel === accountFilter.value),
))

const typeLabel: Record<JobType, string> = {
  apply_5461: '5461 申请', catalog_auth: '目录授权', brand_verify: '品牌验证', gtin_exemption: 'GTIN 豁免', unknown: '任务',
}
const modeLabel = { diagnose: '诊断', dry_run: 'Dry-run', submit: '真实提交', real: '真实提交', unknown: '—' } as const
const queueReasonLabel: Record<string, string> = {
  waiting_case_followup: '等待 Case 跟进', waiting_profile_lock: '等待浏览器释放', waiting_serial_queue: '等待串行队列',
}
function modeTagType(mode: JobMode): 'error' | 'info' | 'default' {
  if (mode === 'submit' || mode === 'real') return 'error'
  return mode === 'dry_run' ? 'info' : 'default'
}

const columns: DataTableColumns<JobSummary> = [
  {
    title: '类型', key: 'mode', width: 170, fixed: 'left',
    render: (row) => h('div', { class: 'type-cell' }, [
      h('strong', typeLabel[row.type]),
      h(NTag, { size: 'tiny', bordered: false, type: modeTagType(row.mode) }, { default: () => modeLabel[row.mode] }),
    ]),
  },
  { title: '任务 ID', key: 'id', width: 185, ellipsis: { tooltip: true }, render: (row) => h('code', row.id) },
  { title: '账号', key: 'accountLabel', width: 110, sorter: (a, b) => a.accountLabel.localeCompare(b.accountLabel) },
  { title: '站点', key: 'site', width: 72 },
  { title: '品牌结果', key: 'brandTotal', width: 150, render: (row) => `${row.brandSucceeded} 成 / ${row.brandFailed} 败 / 共 ${row.brandTotal}` },
  {
    title: '运行状态', key: 'status', width: 205,
    render: (row) => h('div', { class: 'status-cell' }, [
      h(StatusTag, { kind: 'run', status: row.status, size: 'small' }),
      row.status === 'queued' && row.queueReason ? h('small', queueReasonLabel[row.queueReason] ?? row.queueReason) : null,
    ]),
  },
  {
    title: '进度', key: 'progress', width: 150,
    render: (row) => h(NProgress, {
      type: 'line', percentage: Math.round(row.progress * 100), height: 8,
      status: row.status === 'completed' ? 'success' : ['failed', 'terminated_unknown_state'].includes(row.status) ? 'error' : 'default',
    }),
  },
  {
    title: '更新时间', key: 'updated', width: 125,
    sorter: (a, b) => Date.parse(a.finishedAt ?? a.startedAt ?? a.createdAt) - Date.parse(b.finishedAt ?? b.startedAt ?? b.createdAt),
    render: (row) => h(RelativeTime, { time: row.finishedAt ?? row.startedAt ?? row.createdAt }),
  },
  {
    title: '操作', key: 'action', width: 80, fixed: 'right',
    render: (row) => h(NButton, { size: 'tiny', tertiary: true, type: 'primary', onClick: (event: Event) => {
      event.stopPropagation(); void router.push(`/jobs/${row.id}`)
    } }, { default: () => '详情' }),
  },
]

const pagination = reactive<PaginationProps>({
  page: 1, pageSize: 20, showSizePicker: true, pageSizes: [10, 20, 50], prefix: ({ itemCount }) => `共 ${itemCount} 条`,
  onUpdatePage: (page) => { pagination.page = page },
  onUpdatePageSize: (pageSize) => { pagination.pageSize = pageSize; pagination.page = 1 },
})
watch([statusFilter, modeFilter, accountFilter], () => { pagination.page = 1 })
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <div><h1 class="page-title">任务列表</h1><p class="page-subtitle">运行态看执行进度，业务结果逐品牌见任务详情</p></div>
      <n-button style="margin-left: auto" type="primary" @click="router.push('/jobs/new')">
        <template #icon><n-icon :component="AddCircleOutline" /></template>新建任务
      </n-button>
    </div>
    <n-alert type="info" :bordered="false" style="margin-bottom: 16px">
      命令行 diagnose/dry-run 在进程退出、任务进入终态并保存日志后可关闭；Web 创建的任务为隐藏分离进程，页面显示 completed/failed 等终态后可关闭普通启动窗口。
    </n-alert>
    <div class="filter-bar">
      <n-select v-model:value="statusFilter" :options="statusOptions" placeholder="全部运行态" clearable style="width: 200px" />
      <n-select v-model:value="modeFilter" :options="modeOptions" placeholder="全部类型" clearable style="width: 180px" />
      <n-select v-model:value="accountFilter" :options="accountOptions" placeholder="全部账号" clearable style="width: 200px" />
    </div>
    <n-data-table
      :columns="columns" :data="filtered" :loading="jobsStore.loading" :pagination="pagination"
      :row-key="(row: JobSummary) => row.id" striped :scroll-x="1220"
      :row-props="(row: JobSummary) => ({ class: 'clickable-row', onClick: () => router.push(`/jobs/${row.id}`) })"
    />
  </div>
</template>

<style scoped>
.filter-bar, .type-cell, .status-cell { display: flex; gap: 10px; align-items: center; }
.filter-bar { margin-bottom: 16px; flex-wrap: wrap; }
.status-cell { align-items: flex-start; flex-direction: column; gap: 3px; }
.status-cell small { opacity: 0.6; }
:deep(.clickable-row) { cursor: pointer; }
</style>

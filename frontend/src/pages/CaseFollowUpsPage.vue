<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NAlert, NButton, NDataTable, NIcon, NSelect, NTag } from 'naive-ui'
import type { DataTableColumns, PaginationProps } from 'naive-ui'
import { RefreshOutline } from '@vicons/ionicons5'
import { getCaseFollowupTasks } from '@/api'
import type { CaseFollowUp } from '@/types'
import { attemptTag } from '@/theme/statusColors'
import StatusTag from '@/components/StatusTag.vue'
import RelativeTime from '@/components/RelativeTime.vue'
import { businessResultLabel } from '@/utils/statusLabels'

const items = ref<CaseFollowUp[]>([])
const loading = ref(true)
const statusFilter = ref('active')
const statusOptions = [
  { label: '进行中（待执行 + 运行中）', value: 'active' }, { label: '待执行', value: 'pending,retry' },
  { label: '运行中', value: 'running' }, { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' }, { label: '已取消', value: 'cancelled' }, { label: '全部', value: 'all' },
]
function statusParam(): string | undefined {
  if (statusFilter.value === 'all') return undefined
  return statusFilter.value === 'active' ? 'pending,retry,running' : statusFilter.value
}
async function load() {
  loading.value = true
  try { items.value = await getCaseFollowupTasks(statusParam()) } finally { loading.value = false }
}
onMounted(load)

function detail(row: CaseFollowUp) {
  const timeline = [
    row.submitted_at ? `提交：${row.submitted_at}` : null,
    row.scheduled_at ? `计划：${row.scheduled_at}` : null,
    row.last_checked_at ? `上次检查：${row.last_checked_at}` : null,
    row.completed_at ? `完成：${row.completed_at}` : null,
  ].filter(Boolean)
  return h('div', { class: 'case-detail' }, [
    row.decision_reason ? h('p', [h('strong', '判定：'), row.decision_reason]) : null,
    row.error ? h(NAlert, { type: 'error', bordered: false }, { default: () => row.error }) : null,
    row.evidence_path ? h('p', [h('strong', '证据：'), h('code', row.evidence_path)]) : null,
    h('p', { class: 'timeline' }, timeline.join('　·　') || '暂无时间线'),
  ])
}

const columns: DataTableColumns<CaseFollowUp> = [
  { type: 'expand', fixed: 'left', width: 46, renderExpand: detail },
  { title: '品牌', key: 'brand_name', fixed: 'left', width: 170, ellipsis: { tooltip: true }, sorter: (a, b) => a.brand_name.localeCompare(b.brand_name) },
  { title: '账号', key: 'account_id', width: 110, ellipsis: { tooltip: true } },
  { title: '站点', key: 'marketplace', width: 70, render: (row) => h(NTag, { size: 'tiny', bordered: false }, { default: () => row.marketplace }) },
  { title: 'Case ID', key: 'case_id', width: 155, ellipsis: { tooltip: true }, render: (row) => h('code', row.case_id || '—') },
  {
    title: '队列状态', key: 'status', width: 125,
    render: (row) => { const tag = attemptTag(row.status); return h(StatusTag, { kind: tag.kind, status: tag.status, size: 'small' }) },
  },
  { title: '检查次数', key: 'attempt_count', width: 90, sorter: (a, b) => a.attempt_count - b.attempt_count },
  { title: 'Case 状态', key: 'case_status', width: 125, render: (row) => businessResultLabel(row.case_status || '') || '—' },
  { title: '最终结果', key: 'final_result', width: 125, render: (row) => businessResultLabel(row.final_result || '') || '—' },
  {
    title: '计划 / 最近检查', key: 'scheduled_at', width: 145,
    sorter: (a, b) => Date.parse(a.scheduled_at || '') - Date.parse(b.scheduled_at || ''),
    render: (row) => h(RelativeTime, { time: row.last_checked_at ?? row.scheduled_at ?? row.submitted_at ?? '' }),
  },
]
const pagination = reactive<PaginationProps>({
  page: 1, pageSize: 20, showSizePicker: true, pageSizes: [10, 20, 50], prefix: ({ itemCount }) => `共 ${itemCount} 条`,
  onUpdatePage: (page) => { pagination.page = page },
  onUpdatePageSize: (pageSize) => { pagination.pageSize = pageSize; pagination.page = 1 },
})
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">Case 跟进</h1>
        <p class="page-subtitle">延迟 Case 检查队列；展开行查看判定、错误、证据和时间线</p>
      </div>
      <div class="header-actions">
        <n-select v-model:value="statusFilter" :options="statusOptions" style="width: 220px" @update:value="load" />
        <n-button tertiary :loading="loading" @click="load"><template #icon><n-icon :component="RefreshOutline" /></template>刷新</n-button>
      </div>
    </div>
    <n-data-table
      :columns="columns" :data="items" :loading="loading" :pagination="pagination"
      :row-key="(row: CaseFollowUp) => row.id" striped :scroll-x="1280"
    />
  </div>
</template>

<style scoped>
.header-actions { display: flex; gap: 12px; align-items: center; }
:deep(.case-detail) { padding: 4px 18px 10px 48px; }
:deep(.case-detail p) { margin: 7px 0; }
:deep(.case-detail code) { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
:deep(.case-detail .timeline) { font-size: 12px; opacity: 0.65; }
</style>

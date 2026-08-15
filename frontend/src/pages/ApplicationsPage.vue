<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import { NDataTable, NDatePicker, NEmpty, NSelect, NTag } from 'naive-ui'
import type { DataTableColumns, PaginationProps } from 'naive-ui'
import { getApplications } from '@/api'
import type { ApplicationRecord, BusinessStatus, Site } from '@/types'
import ApplicationRecordDetails from '@/components/ApplicationRecordDetails.vue'
import StatusTag from '@/components/StatusTag.vue'
import RelativeTime from '@/components/RelativeTime.vue'
import { parseServerTime } from '@/utils/datetime'
import { dashboardStatusLabel } from '@/utils/statusLabels'

const records = ref<ApplicationRecord[]>([])
const loading = ref(true)

const accountFilter = ref<string | null>(null)
const siteFilter = ref<Site['code'] | null>(null)
const brandFilter = ref<string | null>(null)
const statusFilter = ref<BusinessStatus | null>(null)
const dateRange = ref<[number, number] | null>(null)

onMounted(async () => {
  records.value = await getApplications()
  loading.value = false
})

const accountOptions = computed(() =>
  Array.from(new Set(records.value.map((r) => r.accountLabel))).map((a) => ({ label: a, value: a })),
)

const siteOptions = [
  { label: 'US', value: 'US' },
  { label: 'UK', value: 'UK' },
  { label: 'DE', value: 'DE' },
  { label: 'MX', value: 'MX' },
]

const brandOptions = computed(() =>
  Array.from(new Set(records.value.map((r) => r.brand))).map((b) => ({ label: b, value: b })),
)

const statusOptions = [
  { label: '草稿', value: 'draft' },
  { label: '已提交·无Case ID·待查控制面板', value: 'submitted_no_case_id_pending_dashboard' },
  { label: '审核中', value: 'under_review' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'declined' },
  { label: '本已通过', value: 'already_approved' },
  { label: '未找到', value: 'not_found' },
  { label: '部分完成', value: 'partial' },
  { label: '失败', value: 'failed' },
  { label: '异常', value: 'error' },
]

const filtered = computed(() =>
  records.value.filter((r) => {
    if (accountFilter.value && r.accountLabel !== accountFilter.value) return false
    if (siteFilter.value && r.site !== siteFilter.value) return false
    if (brandFilter.value && r.brand !== brandFilter.value) return false
    if (statusFilter.value && r.localStatus !== statusFilter.value) return false
    if (dateRange.value) {
      const d = parseServerTime(r.updatedAt)
      if (!d) return false
      const t = d.getTime()
      if (t < dateRange.value[0] || t > dateRange.value[1]) return false
    }
    return true
  }),
)

const typeLabel: Record<ApplicationRecord['type'], string> = {
  apply_5461: '5461',
  catalog_auth: '目录授权',
  brand_verify: '品牌验证',
  gtin_exemption: 'GTIN 豁免',
  unknown: '通用',
}

/** 控制面板状态用独立 tag 着色，与本地业务态区分 */
function dashboardTagType(s: string | null): 'success' | 'info' | 'warning' | 'error' | 'default' {
  if (!s) return 'default'
  const label = dashboardStatusLabel(s)
  if (label === '已通过') return 'success'
  if (label === '已提交' || label === '审核中') return 'info'
  if (label === '待处理' || label === '待核查' || label === '未知') return 'warning'
  if (label === '未通过' || label === '假过') return 'error'
  return 'default'
}

const columns: DataTableColumns<ApplicationRecord> = [
  {
    type: 'expand',
    fixed: 'left',
    width: 46,
    renderExpand: (row) => h(ApplicationRecordDetails, { record: row }),
  },
  {
    title: '品牌',
    key: 'brand',
    fixed: 'left',
    width: 170,
    ellipsis: { tooltip: true },
    sorter: (a, b) => a.brand.localeCompare(b.brand, 'zh-CN'),
    render: (row) => h('span', { class: 'brand-cell' }, row.brand),
  },
  {
    title: '申请类型',
    key: 'type',
    width: 104,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false }, { default: () => typeLabel[row.type] }),
  },
  {
    title: '账号',
    key: 'accountLabel',
    width: 92,
    sorter: (a, b) => a.accountLabel.localeCompare(b.accountLabel, 'zh-CN'),
  },
  {
    title: '站点',
    key: 'site',
    width: 78,
    sorter: (a, b) => a.site.localeCompare(b.site),
  },
  {
    title: '本地业务状态',
    key: 'localStatus',
    width: 210,
    sorter: (a, b) => a.localStatus.localeCompare(b.localStatus),
    render: (row) => h(StatusTag, { kind: 'business', status: row.localStatus, size: 'small' }),
  },
  {
    title: '控制面板',
    key: 'dashboardStatus',
    width: 132,
    sorter: (a, b) => dashboardStatusLabel(a.dashboardStatus).localeCompare(dashboardStatusLabel(b.dashboardStatus), 'zh-CN'),
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: dashboardTagType(row.dashboardStatus) },
        { default: () => dashboardStatusLabel(row.dashboardStatus) },
      ),
  },
  {
    title: 'Case 回复',
    key: 'caseLastReply',
    width: 104,
    sorter: (a, b) => Number(Boolean(a.caseLastReply)) - Number(Boolean(b.caseLastReply)),
    render: (row) =>
      row.caseLastReply
        ? h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => '有回复' })
        : h('span', { class: 'muted-cell' }, '暂无回复'),
  },
  {
    title: '更新时间',
    key: 'updatedAt',
    width: 132,
    sorter: (a, b) =>
      (parseServerTime(a.updatedAt)?.getTime() ?? 0) - (parseServerTime(b.updatedAt)?.getTime() ?? 0),
    render: (row) => h(RelativeTime, { time: row.updatedAt }),
  },
]

const pagination = reactive<PaginationProps>({
  page: 1,
  pageSize: 20,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  prefix: ({ itemCount }) => `共 ${itemCount} 条`,
  onUpdatePage: (page) => {
    pagination.page = page
  },
  onUpdatePageSize: (pageSize) => {
    pagination.pageSize = pageSize
    pagination.page = 1
  },
})

watch([accountFilter, siteFilter, brandFilter, statusFilter, dateRange], () => {
  pagination.page = 1
})

const rowKey = (row: ApplicationRecord) => row.id
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">申请记录</h1>
        <p class="page-subtitle">本地运行结果、控制面板状态、Case 最新回复三列独立展示；展开行可查看完整详情与证据</p>
      </div>
    </div>

    <div class="filter-bar">
      <n-select v-model:value="accountFilter" :options="accountOptions" placeholder="全部账号" clearable style="width: 170px" />
      <n-select v-model:value="siteFilter" :options="siteOptions" placeholder="全部站点" clearable style="width: 130px" />
      <n-select v-model:value="brandFilter" :options="brandOptions" placeholder="全部品牌" clearable style="width: 190px" />
      <n-select v-model:value="statusFilter" :options="statusOptions" placeholder="全部业务状态" clearable style="width: 260px" />
      <n-date-picker v-model:value="dateRange" type="daterange" clearable style="width: 260px" />
    </div>

    <n-data-table
      class="applications-table"
      :columns="columns"
      :data="filtered"
      :loading="loading"
      :row-key="rowKey"
      :pagination="pagination"
      :scroll-x="1168"
      :single-line="false"
      striped
      size="small"
    >
      <template #empty>
        <n-empty description="没有符合条件的申请记录——调整筛选条件试试" />
      </template>
    </n-data-table>
  </div>
</template>

<style scoped>
.filter-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.applications-table {
  border-radius: 8px;
  overflow: hidden;
}

.applications-table :deep(.n-data-table-th) {
  white-space: nowrap;
}

.applications-table :deep(.brand-cell) {
  font-weight: 700;
}

.applications-table :deep(.muted-cell) {
  opacity: 0.65;
}

.applications-table :deep(.n-data-table-expand-trigger) {
  transition: background-color 0.16s ease;
}

.applications-table :deep(.n-data-table-expand-trigger:hover) {
  background: rgba(24, 160, 88, 0.12);
}

@media (max-width: 720px) {
  .filter-bar > * {
    width: 100% !important;
  }
}
</style>

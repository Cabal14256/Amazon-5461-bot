<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NIcon, NSelect, NSpin, NTag } from 'naive-ui'
import { RefreshOutline } from '@vicons/ionicons5'
import { getCaseFollowupTasks } from '@/api'
import type { CaseFollowUp } from '@/types'
import { attemptTag } from '@/theme/statusColors'
import StatusTag from '@/components/StatusTag.vue'
import RelativeTime from '@/components/RelativeTime.vue'
import { businessResultLabel } from '@/utils/statusLabels'

const items = ref<CaseFollowUp[]>([])
const loading = ref(true)
const statusFilter = ref<string>('active')

/** 与后端 /api/case-followups?status= 的逗号分隔参数对齐 */
const statusOptions = [
  { label: '进行中（待执行 + 运行中）', value: 'active' },
  { label: '待执行', value: 'pending,retry' },
  { label: '运行中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
  { label: '全部', value: 'all' },
]

function statusParam(): string | undefined {
  if (statusFilter.value === 'all') return undefined
  if (statusFilter.value === 'active') return 'pending,retry,running'
  return statusFilter.value
}

async function load() {
  loading.value = true
  try {
    items.value = await getCaseFollowupTasks(statusParam())
  } finally {
    loading.value = false
  }
}

onMounted(load)

function onFilterChange() {
  void load()
}
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">Case 跟进</h1>
        <p class="page-subtitle">
          延迟 Case 检查队列，由独立后台 worker 执行；本地批处理提交的申请也在此跟进，不会出现在任务列表（automation_jobs）中
        </p>
      </div>
      <div class="header-actions">
        <n-select
          v-model:value="statusFilter"
          :options="statusOptions"
          style="width: 220px"
          @update:value="onFilterChange"
        />
        <n-button tertiary :loading="loading" @click="load">
          <template #icon><n-icon :component="RefreshOutline" /></template>
          刷新
        </n-button>
      </div>
    </div>

    <n-spin :show="loading">
      <n-empty
        v-if="!loading && items.length === 0"
        description="当前筛选条件下没有 Case 跟进任务"
        style="margin-top: 80px"
      />
      <div class="followup-list">
        <n-card v-for="it in items" :key="it.id" class="followup-card" size="small">
          <template #header>
            <div class="f-head">
              <span class="f-brand">{{ it.brand_name }}</span>
              <n-tag size="tiny" :bordered="false">{{ it.marketplace }}</n-tag>
              <span class="f-account">账号 {{ it.account_id }}</span>
              <span class="f-case">Case ID：<code>{{ it.case_id || '—' }}</code></span>
            </div>
          </template>
          <template #header-extra>
            <StatusTag :kind="attemptTag(it.status).kind" :status="attemptTag(it.status).status" size="small" />
          </template>

          <div class="f-body">
            <span class="f-field">已尝试 {{ it.attempt_count }} 次</span>
            <span v-if="it.case_status" class="f-field">Case 状态：{{ businessResultLabel(it.case_status) }}</span>
            <span v-if="it.final_result" class="f-field">最终结果：{{ businessResultLabel(it.final_result) }}</span>
            <span v-if="it.decision_reason" class="f-field">{{ it.decision_reason }}</span>
          </div>

          <n-alert v-if="it.error" type="error" :bordered="false" style="margin-top: 10px">
            {{ it.error }}
          </n-alert>

          <div v-if="it.evidence_path" class="f-evidence">
            证据：<code>{{ it.evidence_path }}</code>
          </div>

          <div class="f-footer">
            <span v-if="it.submitted_at">提交 <RelativeTime :time="it.submitted_at" /></span>
            <span v-if="it.scheduled_at">· 计划 <RelativeTime :time="it.scheduled_at" /></span>
            <span v-if="it.last_checked_at">· 上次检查 <RelativeTime :time="it.last_checked_at" /></span>
            <span v-if="it.completed_at">· 完成 <RelativeTime :time="it.completed_at" /></span>
          </div>
        </n-card>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.followup-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.f-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.f-brand {
  font-weight: 700;
}

.f-account {
  font-size: 13px;
  opacity: 0.65;
}

.f-case {
  font-size: 13px;
  opacity: 0.75;
}

.f-case code,
.f-evidence code {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
}

.f-body {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 13px;
}

.f-field {
  opacity: 0.75;
}

.f-evidence {
  margin-top: 10px;
  font-size: 12px;
  opacity: 0.6;
}

.f-footer {
  margin-top: 12px;
  font-size: 12px;
  opacity: 0.5;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
</style>

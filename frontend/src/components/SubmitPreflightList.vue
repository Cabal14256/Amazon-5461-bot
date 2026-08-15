<script setup lang="ts">
import { CheckmarkOutline, CloseOutline, WarningOutline } from '@vicons/ionicons5'
import { NIcon, NTag } from 'naive-ui'
import type { SubmitPreflightCheck } from '@/api/real'

/** 阶段 4 真实提交的服务端 preflight 列表：blocker 未过红 ✗、warning 未过黄 ⚠、通过绿 ✓ */
defineProps<{ checks: SubmitPreflightCheck[] }>()

function iconOf(c: SubmitPreflightCheck) {
  if (c.ok) return CheckmarkOutline
  return c.level === 'blocker' ? CloseOutline : WarningOutline
}

function colorOf(c: SubmitPreflightCheck): string {
  if (c.ok) return '#18a058'
  return c.level === 'blocker' ? '#d03050' : '#f0a020'
}
</script>

<template>
  <div class="preflight-list">
    <div v-for="c in checks" :key="c.name" class="preflight-row">
      <n-icon :component="iconOf(c)" :color="colorOf(c)" size="18" class="preflight-icon" />
      <div class="preflight-body">
        <div class="preflight-name">
          {{ c.name }}
          <n-tag size="tiny" :bordered="false" :type="c.level === 'blocker' ? 'error' : 'warning'">
            {{ c.level === 'blocker' ? '阻断项' : '警告项' }}
          </n-tag>
        </div>
        <div class="preflight-detail">{{ c.detail }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.preflight-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.preflight-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.preflight-icon {
  margin-top: 2px;
  flex-shrink: 0;
}

.preflight-name {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}

.preflight-detail {
  font-size: 13px;
  opacity: 0.7;
  margin-top: 2px;
}
</style>

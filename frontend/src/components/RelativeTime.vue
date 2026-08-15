<script setup lang="ts">
import { computed } from 'vue'
import { NTooltip } from 'naive-ui'
import { parseServerTime } from '@/utils/datetime'

const props = defineProps<{ time: string | null }>()

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

const parseTime = parseServerTime

const absolute = computed(() => {
  if (!props.time) return '—'
  const d = parseTime(props.time)
  if (!d) return props.time
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
})

const relative = computed(() => {
  if (!props.time) return '—'
  const d = parseTime(props.time)
  if (!d) return props.time
  const diffSec = Math.round((Date.now() - d.getTime()) / 1000)
  if (diffSec < 0) return '刚刚'
  if (diffSec < 60) return `${diffSec} 秒前`
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`
  if (diffSec < 86400 * 30) return `${Math.floor(diffSec / 86400)} 天前`
  return absolute.value
})
</script>

<template>
  <n-tooltip trigger="hover">
    <template #trigger>
      <span class="relative-time">{{ relative }}</span>
    </template>
    {{ absolute }}
  </n-tooltip>
</template>

<style scoped>
.relative-time {
  cursor: default;
}
</style>

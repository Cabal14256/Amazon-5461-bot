<script setup lang="ts">
import { computed } from 'vue'
import { NCard } from 'naive-ui'
import { healthLevelMeta } from '@/theme/statusColors'
import type { HealthLevel } from '@/types'
import RelativeTime from './RelativeTime.vue'

const props = defineProps<{
  name: string
  level: HealthLevel
  detail: string
  checkedAt: string
}>()

const meta = computed(() => healthLevelMeta[props.level])
</script>

<template>
  <n-card class="health-card hoverable" size="small">
    <div class="health-row">
      <span class="breath-dot" :class="level" :style="{ '--dot-color': meta.color }" />
      <span class="health-name">{{ name }}</span>
      <span class="health-level" :style="{ color: meta.color }">{{ meta.label }}</span>
    </div>
    <div class="health-detail">{{ detail }}</div>
    <div class="health-time">
      检查于 <RelativeTime :time="checkedAt" />
    </div>
  </n-card>
</template>

<style scoped>
.health-card {
  min-width: 0;
}

.health-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.breath-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background-color: var(--dot-color);
  flex-shrink: 0;
  animation: breathe 2.2s ease-in-out infinite;
}

.breath-dot.down {
  animation-duration: 0.9s;
}

@keyframes breathe {
  0%,
  100% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--dot-color) 40%, transparent);
  }
  50% {
    box-shadow: 0 0 0 6px transparent;
  }
}

.health-name {
  font-weight: 600;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.health-level {
  font-size: 12px;
  font-weight: 600;
}

.health-detail {
  font-size: 12px;
  opacity: 0.75;
  line-height: 1.5;
  min-height: 36px;
}

.health-time {
  font-size: 12px;
  opacity: 0.5;
  margin-top: 4px;
}
</style>

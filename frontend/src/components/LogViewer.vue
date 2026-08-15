<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { NEmpty } from 'naive-ui'
import type { LogLine } from '@/types'
import { parseServerTime } from '@/utils/datetime'

const props = defineProps<{
  lines: LogLine[]
  /** 是否显示为"实时流"（标题栏带脉冲点） */
  live?: boolean
  /** 标题栏文案（区分 SSE / 轮询降级 / 模拟流） */
  title?: string
}>()

const bodyRef = ref<HTMLElement | null>(null)

watch(
  () => props.lines.length,
  async () => {
    await nextTick()
    const el = bodyRef.value
    if (el) el.scrollTop = el.scrollHeight
  },
)

const levelColor: Record<LogLine['level'], string> = {
  DEBUG: '#8a919e',
  INFO: '#18a058',
  WARN: '#f0a020',
  ERROR: '#d03050',
}

function fmt(ts: string): string {
  const d = parseServerTime(ts)
  if (!d) return ts
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
</script>

<template>
  <div class="log-viewer">
    <div class="log-header">
      <span v-if="live" class="live-dot" />
      <span>{{ props.title ?? (live ? '实时日志' : '日志') }}</span>
      <span class="log-count">{{ lines.length }} 行</span>
    </div>
    <div ref="bodyRef" class="log-body">
      <n-empty v-if="lines.length === 0" description="暂无日志输出" size="small" style="margin-top: 48px" />
      <div v-for="(line, i) in lines" :key="i" class="log-line">
        <span class="log-ts">{{ fmt(line.ts) }}</span>
        <span class="log-level" :style="{ color: levelColor[line.level] }">{{ line.level.padEnd(5) }}</span>
        <span class="log-msg">{{ line.message }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.log-viewer {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-radius: 10px;
  overflow: hidden;
  background: #101418;
  color: #d7dde5;
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
}

.log-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #181d24;
  font-size: 12px;
  color: #9aa4b2;
  flex-shrink: 0;
}

.log-count {
  margin-left: auto;
}

.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #18a058;
  animation: live-pulse 1.6s ease-in-out infinite;
}

@keyframes live-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(24, 160, 88, 0.5);
  }
  50% {
    box-shadow: 0 0 0 5px transparent;
  }
}

.log-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
  min-height: 0;
}

.log-line {
  display: flex;
  gap: 8px;
  line-height: 1.7;
}

.log-ts {
  color: #5f6b7a;
  flex-shrink: 0;
}

.log-level {
  flex-shrink: 0;
  font-weight: 600;
}

.log-msg {
  word-break: break-all;
  white-space: pre-wrap;
}
</style>

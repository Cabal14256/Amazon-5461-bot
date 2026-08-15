<script setup lang="ts">
import { computed } from 'vue'
import { NEmpty } from 'naive-ui'

const props = defineProps<{ diff: string | null }>()

interface DiffLine {
  text: string
  cls: 'add' | 'del' | 'hunk' | 'meta' | 'ctx'
}

const parsed = computed<DiffLine[]>(() => {
  if (!props.diff) return []
  return props.diff.split('\n').map((text) => {
    let cls: DiffLine['cls'] = 'ctx'
    if (text.startsWith('+++') || text.startsWith('---')) cls = 'meta'
    else if (text.startsWith('@@')) cls = 'hunk'
    else if (text.startsWith('+')) cls = 'add'
    else if (text.startsWith('-')) cls = 'del'
    return { text, cls }
  })
})
</script>

<template>
  <n-empty v-if="!diff" description="尚无补丁 diff（修复未完成或已被拒绝）" size="small" />
  <div v-else class="diff-viewer">
    <div v-for="(line, i) in parsed" :key="i" class="diff-line" :class="line.cls">
      <span class="diff-sign">{{ line.text[0] === '+' ? '+' : line.text[0] === '-' ? '-' : ' ' }}</span>
      <span class="diff-text">{{ line.text.replace(/^[+-]/, '') || ' ' }}</span>
    </div>
  </div>
</template>

<style scoped>
.diff-viewer {
  border-radius: 10px;
  overflow: auto;
  max-height: 420px;
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  background: #101418;
  padding: 8px 0;
}

.diff-line {
  display: flex;
  white-space: pre;
  padding: 0 12px;
  color: #c9d1d9;
}

.diff-line.add {
  background: rgba(24, 160, 88, 0.14);
  color: #7ee2a8;
}

.diff-line.del {
  background: rgba(208, 48, 80, 0.14);
  color: #f09aa9;
}

.diff-line.hunk {
  color: #79b8ff;
}

.diff-line.meta {
  color: #8a919e;
}

.diff-sign {
  width: 16px;
  flex-shrink: 0;
  user-select: none;
}

.diff-text {
  word-break: break-all;
  white-space: pre-wrap;
}
</style>

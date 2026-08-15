<script setup lang="ts">
import { CameraOutline, CodeSlashOutline, DocumentTextOutline, FolderOpenOutline, MailOutline } from '@vicons/ionicons5'
import { NEmpty, NIcon, NTag, NTimeline, NTimelineItem } from 'naive-ui'
import type { Component } from 'vue'
import type { EvidenceItem } from '@/types'
import RelativeTime from './RelativeTime.vue'

defineProps<{ items: EvidenceItem[] }>()

const kindIcon: Record<EvidenceItem['kind'], Component> = {
  screenshot: CameraOutline,
  page_dump: CodeSlashOutline,
  dashboard: FolderOpenOutline,
  case_reply: MailOutline,
  log_excerpt: DocumentTextOutline,
}

const kindLabel: Record<EvidenceItem['kind'], string> = {
  screenshot: '截图',
  page_dump: '页面快照',
  dashboard: '控制面板',
  case_reply: 'Case 回复',
  log_excerpt: '日志摘录',
}
</script>

<template>
  <n-empty v-if="items.length === 0" description="暂无证据留存" size="small" />
  <n-timeline v-else>
    <n-timeline-item v-for="(item, i) in items" :key="i" type="info">
      <template #icon>
        <n-icon :component="kindIcon[item.kind]" />
      </template>
      <template #header>
        <span class="ev-title">{{ item.title }}</span>
        <n-tag size="tiny" :bordered="false" class="ev-kind">{{ kindLabel[item.kind] }}</n-tag>
      </template>
      <template #footer>
        <RelativeTime :time="item.ts" />
      </template>
      <div class="ev-path">{{ item.path }}</div>
      <div v-if="item.note" class="ev-note">{{ item.note }}</div>
    </n-timeline-item>
  </n-timeline>
</template>

<style scoped>
.ev-title {
  font-weight: 500;
  margin-right: 8px;
}

.ev-kind {
  vertical-align: 1px;
}

.ev-path {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  opacity: 0.6;
  word-break: break-all;
}

.ev-note {
  font-size: 12px;
  opacity: 0.8;
  margin-top: 2px;
}
</style>

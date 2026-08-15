<script setup lang="ts">
import { NTag } from 'naive-ui'
import { SOURCE_LABEL } from '@/api/real'
import type { ApplicationRecord } from '@/types'
import EvidenceTimeline from '@/components/EvidenceTimeline.vue'

defineProps<{
  record: ApplicationRecord
}>()
</script>

<template>
  <div class="application-record-detail">
    <div class="detail-grid">
      <div class="detail-item">
        <div class="detail-label">Case ID</div>
        <code>{{ record.caseId ?? '—' }}</code>
      </div>
      <div class="detail-item">
        <div class="detail-label">Case 最新回复</div>
        <div class="case-reply">{{ record.caseLastReply ?? '暂无回复' }}</div>
      </div>
      <div class="detail-item full">
        <div class="detail-label">状态来源</div>
        <div class="source-line">
          <n-tag
            v-if="record.statusSourceRaw"
            size="small"
            :bordered="false"
            type="warning"
            class="source-tag"
          >
            {{ SOURCE_LABEL[record.statusSourceRaw] ?? record.statusSourceRaw }}
          </n-tag>
          <span>{{ record.statusSource }}</span>
        </div>
      </div>
    </div>

    <div class="detail-evidence">
      <div class="detail-label evidence-title">证据</div>
      <EvidenceTimeline :items="record.evidence" />
    </div>
  </div>
</template>

<style scoped>
.application-record-detail {
  padding: 16px 18px 18px;
  background: rgba(128, 128, 128, 0.055);
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px 28px;
}

.detail-item.full {
  grid-column: 1 / -1;
}

.detail-label {
  margin-bottom: 5px;
  font-size: 12px;
  font-weight: 600;
  opacity: 0.55;
}

.detail-item code {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
}

.case-reply {
  line-height: 1.65;
  white-space: pre-wrap;
}

.source-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: #f0a020;
  font-size: 13px;
  line-height: 1.6;
}

.source-tag {
  flex: 0 0 auto;
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
}

.detail-evidence {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px dashed rgba(128, 128, 128, 0.25);
}

.evidence-title {
  margin-bottom: 9px;
}

@media (max-width: 720px) {
  .application-record-detail {
    padding: 14px;
  }

  .detail-grid {
    grid-template-columns: 1fr;
  }

  .detail-item.full {
    grid-column: auto;
  }
}
</style>

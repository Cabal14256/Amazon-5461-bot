<script setup lang="ts">
import { NAlert, NTag } from 'naive-ui'
import { computed } from 'vue'
import { SOURCE_LABEL } from '@/api/real'
import type { ApplicationRecord } from '@/types'
import EvidenceTimeline from '@/components/EvidenceTimeline.vue'
import RelativeTime from '@/components/RelativeTime.vue'
import StatusTag from '@/components/StatusTag.vue'
import { attemptTag } from '@/theme/statusColors'

const props = defineProps<{
  record: ApplicationRecord
}>()

const hasAuthInterruption = computed(() => {
  const automation = props.record.automation
  return Boolean(
    automation
      && (automation.auth_block_id
        || automation.block_type
        || ['waiting_login', 'waiting_reconciliation'].includes(automation.status)),
  )
})
</script>

<template>
  <div class="application-record-detail">
    <n-alert
      v-if="record.automation?.status === 'waiting_reconciliation'"
      type="warning"
      :bordered="false"
      class="reconciliation-alert"
    >
      <strong>系统不会重复提交。</strong>正在核对 Selling Applications/Case，只有明确拒绝才会推进下一站。
    </n-alert>

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
          <span v-if="record.statusSourceDetail">{{ record.statusSourceDetail }}</span>
          <span v-else-if="!record.statusSourceRaw">{{ record.statusSource }}</span>
        </div>
      </div>
      <template v-if="record.automation">
        <div class="detail-item">
          <div class="detail-label">当前重新申请站点</div>
          <strong>{{ record.automation.current_site || record.site }}</strong>
        </div>
        <template v-if="hasAuthInterruption">
          <div class="detail-item">
            <div class="detail-label">登录中断阶段</div>
            <span>{{ record.automation.submit_fenced ? '提交后结果未知' : '提交前' }}</span>
          </div>
        </template>
        <div class="detail-item">
          <div class="detail-label">提交点击安全边界</div>
          <n-tag size="small" :type="record.automation.submit_fenced ? 'warning' : 'info'" :bordered="false">
            {{ record.automation.submit_fenced ? '已落库（禁止再次点击）' : '尚未越过' }}
          </n-tag>
        </div>
        <template v-if="hasAuthInterruption">
          <div class="detail-item">
            <div class="detail-label">最近登录检测</div>
            <RelativeTime v-if="record.automation.last_checked_at || record.automation.detected_at" :time="record.automation.last_checked_at || record.automation.detected_at || ''" />
            <span v-else>—</span>
          </div>
        </template>
        <div class="detail-item full">
          <div class="detail-label">下一步动作</div>
          <strong>{{ record.automation.next_action }}</strong>
        </div>
        <div v-if="record.automation.timeline.length" class="detail-item full">
          <div class="detail-label">当前 campaign / attempt 时间线</div>
          <div class="automation-timeline">
            <div v-for="item in record.automation.timeline" :key="`${item.route_index}-${item.site}`" class="timeline-row">
              <strong>{{ item.site.toUpperCase() }}</strong>
              <StatusTag :kind="attemptTag(item.status).kind" :status="attemptTag(item.status).status" size="small" />
              <span v-if="item.scheduled_at">计划 <RelativeTime :time="item.scheduled_at" /></span>
              <span v-if="item.case_id">Case {{ item.case_id }}</span>
              <span v-if="item.decision_reason">{{ item.decision_reason }}</span>
            </div>
          </div>
        </div>
      </template>
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

.reconciliation-alert {
  margin-bottom: 16px;
}

.automation-timeline {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.timeline-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
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

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NButton, NCard, NGrid, NGridItem, NIcon, NSpin } from 'naive-ui'
import { ArrowForwardOutline, PulseOutline } from '@vicons/ionicons5'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ComposeOption } from 'echarts/core'
import type { PieSeriesOption } from 'echarts/charts'
import type { LegendComponentOption, TooltipComponentOption } from 'echarts/components'
import { useOverviewStore } from '@/stores/overview'
import { useThemeStore } from '@/stores/theme'
import { getStatusMeta } from '@/theme/statusColors'
import { USE_MOCK } from '@/api/client'
import HealthBadge from '@/components/HealthBadge.vue'

use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer])
type DonutOption = ComposeOption<TooltipComponentOption | LegendComponentOption | PieSeriesOption>

const overview = useOverviewStore()
const themeStore = useThemeStore()
const router = useRouter()

// 真实模式每 15s 轮询刷新（阶段 2 无 SSE）；组件卸载时清除
let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  void overview.refresh()
  if (!USE_MOCK) {
    pollTimer = setInterval(() => void overview.refresh(), 15_000)
  }
})

onUnmounted(() => {
  if (pollTimer !== null) clearInterval(pollTimer)
})

const donutOption = computed<DonutOption>(() => {
  const items = overview.data?.businessDistribution ?? []
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} 条（{d}%）' },
    legend: { show: false },
    series: [
      {
        type: 'pie',
        radius: ['58%', '80%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 6, borderColor: themeStore.isDark ? '#18181c' : '#fff', borderWidth: 2 },
        label: { show: false },
        emphasis: { label: { show: false }, scaleSize: 6 },
        data: items.map((it) => ({
          name: getStatusMeta('business', it.status).label,
          value: it.count,
          itemStyle: { color: getStatusMeta('business', it.status).color },
        })),
      },
    ],
  }
})

const banners = computed(() => {
  const d = overview.data
  if (!d) return []
  return [
    {
      key: 'codex_signal',
      show: d.codexSignalPending > 0,
      type: 'warning' as const,
      text: `有 ${d.codexSignalPending} 个 codex_signal 待处理：失败信号需要诊断并标记 handled`,
      btn: '去修复中心',
      to: '/repair',
    },
    {
      key: 'incidents_open',
      show: d.incidentsOpen > 0,
      type: 'warning' as const,
      text: `有 ${d.incidentsOpen} 个待处理 incident（异常检测队列），需要人工确认或关闭`,
      btn: '去处理',
      to: '/pending',
    },
    {
      key: 'followup',
      show: d.caseFollowUpPending > 0,
      type: 'info' as const,
      text: `${d.caseFollowUpPending} 条 Case 超过 24h 未跟进，需要发跟进消息`,
      btn: '查看申请记录',
      to: '/applications',
    },
    {
      key: 'ui_change',
      show: d.uiChangeSuspected > 0,
      type: 'error' as const,
      text: `检测到 ${d.uiChangeSuspected} 起疑似页面改版 incident，审核前不要跑新批次`,
      btn: '查看 incident',
      to: '/repair',
    },
    {
      key: 'patch',
      show: d.patchesAwaitingReview > 0,
      type: 'warning' as const,
      text: `${d.patchesAwaitingReview} 个修复补丁待人工审核（验证门禁已通过前 9 步）`,
      btn: '去审核',
      to: '/repair',
    },
    {
      key: 'waiting',
      show: d.waitingHumanCount > 0,
      type: 'warning' as const,
      text: `${d.waitingHumanCount} 个任务正等待人工介入（CAPTCHA / 2FA / 登录态）`,
      btn: '去处理',
      to: '/pending',
    },
  ].filter((b) => b.show)
})
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">系统总览</h1>
        <p class="page-subtitle">一眼看清系统能不能用、有没有事要我处理</p>
      </div>
      <n-button style="margin-left: auto" type="primary" @click="router.push('/jobs/new')">
        <template #icon>
          <n-icon :component="PulseOutline" />
        </template>
        新建任务
      </n-button>
    </div>

    <n-spin :show="overview.loading && !overview.data">
      <!-- 健康检查一排 -->
      <div class="health-grid">
        <HealthBadge
          v-for="h in overview.data?.health ?? []"
          :key="h.key"
          :name="h.name"
          :level="h.level"
          :detail="h.detail"
          :checked-at="h.checkedAt"
        />
      </div>

      <n-grid :x-gap="16" :y-gap="16" cols="1 m:5" responsive="screen" style="margin-top: 16px">
        <!-- 左：业务状态分布 -->
        <n-grid-item span="1 m:3">
          <n-card title="业务状态分布" class="hoverable">
            <div class="dist-wrap">
              <VChart class="donut" :option="donutOption" autoresize />
              <ul class="dist-list">
                <li v-for="it in overview.data?.businessDistribution ?? []" :key="it.status">
                  <span class="dist-dot" :style="{ backgroundColor: getStatusMeta('business', it.status).color }" />
                  <span class="dist-label">{{ getStatusMeta('business', it.status).label }}</span>
                  <span class="dist-count">{{ it.count }}</span>
                </li>
              </ul>
            </div>
          </n-card>
        </n-grid-item>

        <!-- 右：运行计数 -->
        <n-grid-item span="1 m:2">
          <div class="stat-col">
            <n-card class="stat-card hoverable" @click="router.push({ path: '/jobs', query: { status: 'running' } })">
              <div class="stat-label">运行中任务</div>
              <div class="stat-number" style="color: #2080f0">{{ overview.data?.runningCount ?? 0 }}</div>
              <div class="stat-hint">点击查看 <n-icon :component="ArrowForwardOutline" /></div>
            </n-card>
            <n-card class="stat-card hoverable" @click="router.push({ path: '/jobs', query: { status: 'queued' } })">
              <div class="stat-label">排队中</div>
              <div class="stat-number" style="color: #8a919e">{{ overview.data?.queuedCount ?? 0 }}</div>
              <div class="stat-hint">点击查看 <n-icon :component="ArrowForwardOutline" /></div>
            </n-card>
            <n-card class="stat-card hoverable" @click="router.push({ path: '/jobs', query: { status: 'waiting_human' } })">
              <div class="stat-label">待人工介入</div>
              <div class="stat-number" style="color: #8b5cf6">{{ overview.data?.waitingHumanCount ?? 0 }}</div>
              <div class="stat-hint">点击处理 <n-icon :component="ArrowForwardOutline" /></div>
            </n-card>
            <n-card v-if="overview.data?.accountTotal != null" class="stat-card hoverable" @click="router.push('/applications')">
              <div class="stat-label">目录账号</div>
              <div class="stat-number" style="color: #18a058">{{ overview.data.accountTotal }}</div>
              <div class="stat-hint">查看申请记录 <n-icon :component="ArrowForwardOutline" /></div>
            </n-card>
          </div>
        </n-grid-item>
      </n-grid>

      <!-- 待处理横幅区 -->
      <div v-if="banners.length > 0" class="banner-area">
        <n-alert v-for="b in banners" :key="b.key" :type="b.type" class="banner-item">
          <div class="banner-line">
            <span>{{ b.text }}</span>
            <n-button size="small" tertiary :type="b.type" @click="router.push(b.to)">{{ b.btn }}</n-button>
          </div>
        </n-alert>
      </div>
      <n-alert v-else-if="overview.data" type="success" style="margin-top: 16px">
        当前没有待处理事项，系统状态良好。
      </n-alert>
    </n-spin>
  </div>
</template>

<style scoped>
.health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

.dist-wrap {
  display: flex;
  gap: 20px;
  align-items: center;
  flex-wrap: wrap;
}

.donut {
  width: 260px;
  height: 260px;
  flex-shrink: 0;
}

.dist-list {
  list-style: none;
  margin: 0;
  padding: 0;
  flex: 1;
  min-width: 220px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dist-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.dist-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dist-label {
  flex: 1;
}

.dist-count {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-weight: 700;
}

.stat-col {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
}

.stat-card {
  cursor: pointer;
  flex: 1;
}

.stat-label {
  font-size: 13px;
  opacity: 0.65;
}

.stat-hint {
  font-size: 12px;
  opacity: 0.5;
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.banner-area {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.banner-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
</style>

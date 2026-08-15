import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getJob,
  getJobEvidence,
  getJobLogs,
  getJobs,
  stopJob,
  subscribeJob,
  terminateJob,
} from '@/api'
import { USE_MOCK } from '@/api/client'
import { mockLogStreamTemplates } from '@/api/mock/jobs'
import { mapAutomationJob, parseLogLine, STOP_REQUESTED_HINT } from '@/api/real'
import type { AutomationJob, EvidenceItem, JobDetail, JobSummary, LogLine } from '@/types'

const TERMINAL: readonly string[] = ['completed', 'failed', 'cancelled_before_start', 'terminated_unknown_state']

export const useJobsStore = defineStore('jobs', () => {
  const jobs = ref<JobSummary[]>([])
  const loading = ref(false)
  const currentJob = ref<JobDetail | null>(null)
  const currentLogs = ref<LogLine[]>([])
  const currentEvidence = ref<EvidenceItem[]>([])
  /** 实时通道状态：sse / polling（降级）/ mock / null（终态或未连接） */
  const streamMode = ref<'sse' | 'polling' | 'mock' | null>(null)

  let streamTimer: ReturnType<typeof setInterval> | null = null
  let subscription: { close: () => void } | null = null
  let openedId: string | null = null

  async function refreshList() {
    loading.value = true
    try {
      jobs.value = await getJobs()
    } finally {
      loading.value = false
    }
  }

  /** SSE event:job —— 用最新任务行刷新详情（保留既有 items，状态变化靠下一次详情拉取补齐） */
  function applyJobEvent(job: AutomationJob) {
    if (!currentJob.value) return
    const summary = mapAutomationJob(job, currentJob.value.items)
    currentJob.value = { ...currentJob.value, ...summary, raw: job }
    if (TERMINAL.includes(job.run_status)) {
      // 终态：关流前拉一次最终详情，把 items 收尾状态带回来
      void refreshCurrentDetail()
    }
  }

  async function refreshCurrentDetail() {
    if (!openedId) return
    const detail = await getJob(openedId)
    if (detail && openedId === detail.id) currentJob.value = detail
  }

  function startSse(id: string) {
    streamMode.value = 'sse'
    subscription = subscribeJob(id, {
      onJob: (job) => {
        applyJobEvent(job)
        if (TERMINAL.includes(job.run_status)) {
          stopLogStream()
        }
      },
      onLog: (line) => {
        currentLogs.value = [...currentLogs.value, parseLogLine(line)]
      },
      onError: () => {
        // SSE 断线：降级为 5s 轮询（状态 + 日志尾部），直到组件卸载
        if (streamMode.value !== 'sse') return
        streamMode.value = 'polling'
        console.warn(`[jobs] 任务 ${id} 已降级为 5s 轮询`)
        let lastLogCount = currentLogs.value.length
        streamTimer = setInterval(() => {
          void (async () => {
            await refreshCurrentDetail()
            const logs = await getJobLogs(id)
            // 日志尾部可能与已收行重叠，行数变多时才整体替换
            if (logs.length !== lastLogCount) {
              currentLogs.value = logs
              lastLogCount = logs.length
            }
            if (currentJob.value && TERMINAL.includes(currentJob.value.status)) stopLogStream()
          })()
        }, 5_000)
      },
    })
  }

  async function openJob(id: string) {
    stopLogStream()
    openedId = id
    currentJob.value = await getJob(id)
    currentLogs.value = await getJobLogs(id)
    currentEvidence.value = await getJobEvidence(id)
    if (!currentJob.value) return

    if (USE_MOCK) {
      // 仅 Mock 模式：运行中的任务模拟 SSE，每 2.5s 追加一条日志
      if (['running', 'starting'].includes(currentJob.value.status)) {
        streamMode.value = 'mock'
        let i = 0
        streamTimer = setInterval(() => {
          const tpl = mockLogStreamTemplates[i % mockLogStreamTemplates.length]
          i += 1
          currentLogs.value = [
            ...currentLogs.value,
            { ts: new Date().toISOString(), level: 'INFO', message: `[stream] ${tpl}` },
          ]
        }, 2500)
      }
      return
    }

    if (!TERMINAL.includes(currentJob.value.status)) startSse(id)
  }

  function stopLogStream() {
    if (streamTimer !== null) {
      clearInterval(streamTimer)
      streamTimer = null
    }
    if (subscription !== null) {
      subscription.close()
      subscription = null
    }
    streamMode.value = null
  }

  /** 请求安全停止（真实模式调后端；Mock：仅本地改状态，不触碰任何真实任务） */
  async function requestStop() {
    const job = currentJob.value
    if (!job) return
    if (!USE_MOCK) {
      const updated = await stopJob(job.id)
      if (updated) applyJobEvent(updated)
      return
    }
    if (['running', 'starting', 'waiting_human'].includes(job.status)) {
      currentJob.value = { ...job, status: 'stop_requested', stopReason: STOP_REQUESTED_HINT }
    }
  }

  /** 强制终止（admin，真实模式调后端；返回 null 表示未执行） */
  async function forceTerminate(): Promise<void> {
    const job = currentJob.value
    if (!job || USE_MOCK) return
    const updated = await terminateJob(job.id)
    if (updated) {
      applyJobEvent(updated)
      stopLogStream()
    }
  }

  return {
    jobs,
    loading,
    currentJob,
    currentLogs,
    currentEvidence,
    streamMode,
    refreshList,
    openJob,
    stopLogStream,
    requestStop,
    forceTerminate,
  }
})

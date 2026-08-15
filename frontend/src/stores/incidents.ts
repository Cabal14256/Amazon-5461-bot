import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  closeIncident,
  generatePatch,
  getIncident,
  getIncidents,
  getRepairIncidents,
  getRepairJobDetail,
  getRepairJobDiff,
  getRepairJobs,
  triageIncident,
} from '@/api'
import { ApiError } from '@/api/http'
import { USE_MOCK } from '@/api/client'
import {
  REPAIR_CLASSIFICATIONS,
  type GeneratePatchResponse,
  type Incident,
  type IncidentBundleFile,
  type LatestTriage,
  type RepairIncident,
  type RepairJobDetail,
} from '@/types'

export const useIncidentsStore = defineStore('incidents', () => {
  /* ---------- Mock 演示：Codex 修复流水线（阶段 6-7 语义） ---------- */
  const incidents = ref<RepairIncident[]>([])
  const loading = ref(false)
  const selectedId = ref<string | null>(null)

  const selected = computed<RepairIncident | null>(
    () => incidents.value.find((i) => i.id === selectedId.value) ?? null,
  )

  function select(id: string) {
    selectedId.value = id
  }

  /** 审核操作（Mock：仅本地流转状态，演示交互） */
  function applyReview(action: 'approve_validation' | 'approve_release' | 'reject') {
    if (!selected.value) return
    const statusMap = {
      approve_validation: 'approved',
      approve_release: 'released',
      reject: 'rejected',
    } as const
    const idx = incidents.value.findIndex((i) => i.id === selected.value!.id)
    if (idx >= 0) {
      incidents.value[idx] = {
        ...incidents.value[idx],
        status: statusMap[action],
        updatedAt: new Date().toISOString(),
      }
    }
  }

  /* ---------- 真实模式：阶段 5 incident 队列（修复候选类） ---------- */
  const realIncidents = ref<Incident[]>([])
  const realLoading = ref(false)
  const selectedRealId = ref<number | null>(null)
  const realDetail = ref<{
    incident: Incident
    bundleFiles: IncidentBundleFile[]
    latestTriage: LatestTriage | null
    /** 最近一次 stage='patch' 的 job 详情（无补丁任务时为 null） */
    patchJob: RepairJobDetail | null
    /** patch.diff 原文（未落盘或读取失败时为 null） */
    patchDiff: string | null
  } | null>(null)
  const detailLoading = ref(false)

  const selectedReal = computed<Incident | null>(
    () => realIncidents.value.find((i) => i.id === selectedRealId.value) ?? null,
  )

  /** 取最近一次 stage='patch' job 的详情与 diff 原文；无补丁任务返回空 */
  async function loadPatchArtifacts(id: number): Promise<{ patchJob: RepairJobDetail | null; patchDiff: string | null }> {
    const jobs = await getRepairJobs(id)
    const patchJobRow = jobs.filter((j) => j.stage === 'patch').sort((a, b) => b.id - a.id)[0]
    if (!patchJobRow) return { patchJob: null, patchDiff: null }
    const detail = await getRepairJobDetail(patchJobRow.id)
    let diff: string | null = null
    try {
      diff = await getRepairJobDiff(patchJobRow.id)
    } catch (e) {
      // validation_failed 的 job 无 patch.diff 落盘（404 diff_not_found）；其他读取失败也不阻塞详情展示
      if (!(e instanceof ApiError && e.status === 404)) console.warn('[incidents] patch diff 读取失败', e)
    }
    return { patchJob: detail, patchDiff: diff }
  }

  /** 详情懒加载：GET /api/incidents/{id} 取证据包文件清单、最近一次 Codex 判因与最近一次补丁 job（阶段 7） */
  async function loadRealDetail(id: number) {
    detailLoading.value = true
    try {
      const [r, patch] = await Promise.all([getIncident(id), loadPatchArtifacts(id)])
      // 加载期间用户可能已切换选中项，避免覆盖
      if (selectedRealId.value === id) {
        realDetail.value = {
          incident: r.incident,
          bundleFiles: r.bundle_files,
          latestTriage: r.latest_triage,
          patchJob: patch.patchJob,
          patchDiff: patch.patchDiff,
        }
      }
    } finally {
      detailLoading.value = false
    }
  }

  async function refreshReal() {
    realLoading.value = true
    try {
      const r = await getIncidents({ classification: REPAIR_CLASSIFICATIONS.join(',') })
      realIncidents.value = r.incidents
      if (selectedRealId.value == null || !realIncidents.value.some((i) => i.id === selectedRealId.value)) {
        selectedRealId.value = realIncidents.value[0]?.id ?? null
        realDetail.value = null
      }
      if (selectedRealId.value != null) await loadRealDetail(selectedRealId.value)
    } finally {
      realLoading.value = false
    }
  }

  async function selectReal(id: number) {
    if (selectedRealId.value === id) return
    selectedRealId.value = id
    realDetail.value = null
    await loadRealDetail(id)
  }

  /** 关闭当前选中 incident（operator+；成功后把列表行替换为后端返回的最新状态） */
  async function closeSelected(note: string): Promise<Incident> {
    const id = selectedRealId.value
    if (id == null) throw new Error('no_incident_selected')
    const updated = await closeIncident(id, note)
    realIncidents.value = realIncidents.value.map((i) => (i.id === id ? updated : i))
    if (realDetail.value?.incident.id === id) {
      realDetail.value = { ...realDetail.value, incident: updated }
    }
    return updated
  }

  /** 触发/重试 Codex 只读判因（operator+）：同步等待结果，成功后重拉详情并同步列表行状态（open→triaged） */
  async function triageSelected(): Promise<LatestTriage> {
    const id = selectedRealId.value
    if (id == null) throw new Error('no_incident_selected')
    const t = await triageIncident(id)
    await loadRealDetail(id)
    const fresh = realDetail.value?.incident
    if (fresh) {
      realIncidents.value = realIncidents.value.map((i) => (i.id === id ? fresh : i))
    }
    return t
  }

  /**
   * 生成补丁（operator+，阶段 7）：POST generate-patch 同步等待（最长约 10 分钟）。
   * 返回 200 后用响应里的 incident 同步列表行（triaged→patch_ready 或失败回 triaged），并重拉详情刷新补丁区。
   * outcome=r2_not_allowed 不视为错误，由页面弹确认框后以 allowR2=true 重发。
   */
  async function generatePatchSelected(allowR2 = false): Promise<GeneratePatchResponse> {
    const id = selectedRealId.value
    if (id == null) throw new Error('no_incident_selected')
    const resp = await generatePatch(id, allowR2)
    const updated = resp.incident
    if (updated) {
      realIncidents.value = realIncidents.value.map((i) => (i.id === id ? updated : i))
    }
    await loadRealDetail(id)
    return resp
  }

  /** 双模式入口：Mock 拉流水线演示数据，真实模式拉修复候选类 incident */
  async function refresh() {
    if (!USE_MOCK) {
      await refreshReal()
      return
    }
    loading.value = true
    try {
      incidents.value = await getRepairIncidents()
      if (!selectedId.value && incidents.value.length > 0) {
        selectedId.value = incidents.value[0].id
      }
    } finally {
      loading.value = false
    }
  }

  return {
    incidents,
    loading,
    selectedId,
    selected,
    refresh,
    select,
    applyReview,
    realIncidents,
    realLoading,
    selectedRealId,
    selectedReal,
    realDetail,
    detailLoading,
    selectReal,
    closeSelected,
    triageSelected,
    generatePatchSelected,
  }
})

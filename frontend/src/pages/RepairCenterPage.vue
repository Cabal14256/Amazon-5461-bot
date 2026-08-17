<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NInput,
  NList,
  NListItem,
  NProgress,
  NScrollbar,
  NSpin,
  NStep,
  NSteps,
  NTag,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import { useIncidentsStore } from '@/stores/incidents'
import { useAuthStore } from '@/stores/auth'
import { USE_MOCK } from '@/api/client'
import { ApiError } from '@/api/http'
import { riskLevelMeta } from '@/theme/statusColors'
import { parseServerTime } from '@/utils/datetime'
import type { GeneratePatchResponse, IncidentTestResult, RepairJob, RiskLevel } from '@/types'
import StatusTag from '@/components/StatusTag.vue'
import RelativeTime from '@/components/RelativeTime.vue'
import DiffViewer from '@/components/DiffViewer.vue'

const store = useIncidentsStore()
const dialog = useDialog()
const message = useMessage()
const auth = useAuthStore()

/** Mock 角色：仅用于演示"不同角色看到不同按钮"。前端隐藏按钮 ≠ 权限控制，真实权限必须由后端强制。 */
const mockRole = ref<'Reviewer' | 'Admin'>('Admin')
const reviewNote = ref('')

let refreshTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  void store.refresh()
  if (!USE_MOCK) {
    refreshTimer = setInterval(() => {
      if (!store.realLoading && !store.detailLoading) void store.refresh()
    }, 5000)
  }
})
onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

const incident = computed(() => store.selected)

/* ---------- 真实模式：阶段 5 incident 队列（修复候选类） ---------- */

/** 详情优先用懒加载的 getIncident 结果（含关闭后的最新行），列表行兜底 */
const realView = computed(() => store.realDetail?.incident ?? store.selectedReal)
const bundleFiles = computed(() => store.realDetail?.bundleFiles ?? [])

const CLASSIFICATION_LABEL: Record<string, string> = {
  selector_missing: '选择器缺失',
  state_unknown: '页面状态未知',
  dom_contract_changed: 'DOM 结构变化',
  navigation_changed: '导航变化',
  semantic_control_missing: '控件定位失败',
  flow_loop_exhausted: '恢复流程耗尽',
  result_contract_changed: '结果解析不兼容',
}

function classificationLabel(c: string): string {
  return CLASSIFICATION_LABEL[c] ?? c
}

function bundleUrl(path: string): string {
  return `/api/evidence/file?path=${encodeURIComponent(path)}`
}

/* ---------- 阶段 6：Codex 只读判因 ---------- */

/** 最近一次判因（无判因时为 null；job 结清但无 result 时 result 为 null） */
const latestTriage = computed(() => store.realDetail?.latestTriage ?? null)

const TRIAGE_CLASSIFICATION_LABEL: Record<string, string> = {
  selector_change: '选择器变更',
  shadow_dom_change: 'Shadow DOM 变更',
  page_state_change: '页面状态变更',
  workflow_semantic_change: '流程语义变更',
  amazon_platform_error: '亚马逊平台错误',
  account_specific_issue: '账号特定问题',
  insufficient_evidence: '证据不足',
}

function triageClassificationLabel(c: string): string {
  return TRIAGE_CLASSIFICATION_LABEL[c] ?? c
}

/** 判因进行中（POST 同步等待，后端默认 ≤120s） */
const triageRunning = ref(false)
/** POST 返回 503 codex_disabled 后置位；成功判因后复位 */
const codexDisabled = ref(false)

/** 顶部降级提示条：最近一次 job 结清为 unavailable/quota_exceeded，或 POST 明确 503；不阻塞其他操作 */
const codexDegraded = computed(
  () =>
    codexDisabled.value ||
    latestTriage.value?.job.status === 'unavailable' ||
    latestTriage.value?.job.status === 'quota_exceeded',
)

/** 判因/补丁 job 耗时（秒）；时间缺失或异常时返回 null */
function jobDurationSec(job: RepairJob): number | null {
  if (!job.created_at || !job.finished_at) return null
  const created = parseServerTime(job.created_at)
  const finished = parseServerTime(job.finished_at)
  if (!created || !finished) return null
  const ms = finished.getTime() - created.getTime()
  return Number.isFinite(ms) && ms >= 0 ? Math.round(ms / 1000) : null
}

/** 触发/重试 Codex 只读判因（operator+） */
async function runTriage() {
  if (triageRunning.value) return
  triageRunning.value = true
  try {
    await store.triageSelected()
    codexDisabled.value = false
    message.success('判因完成，结果已更新')
  } catch (e) {
    if (e instanceof ApiError && e.status === 503) {
      codexDisabled.value = true
      message.warning('Codex 当前不可用，判因已跳过；其他操作不受影响')
    } else {
      message.error(e instanceof ApiError ? `判因失败：${e.detail}` : '判因失败，请稍后重试')
    }
  } finally {
    triageRunning.value = false
  }
}

/* ---------- 阶段 7：隔离生成补丁 ---------- */

/** 最近一次 stage='patch' job 详情（无补丁任务为 null）与 patch.diff 原文 */
const patchJob = computed(() => store.realDetail?.patchJob ?? null)
const patchDiff = computed(() => store.realDetail?.patchDiff ?? null)
const validation = computed(() => patchJob.value?.validation ?? null)
const approvals = computed(() => patchJob.value?.approvals ?? [])
const canaryJobs = computed(() => patchJob.value?.canary_jobs ?? [])

/** 后端唯一索引语义：running / patch_ready 视为 active（就绪补丁在阶段 8 审批前不可重新生成） */
const activePatchJob = computed(() => {
  const j = patchJob.value?.job
  return j && !['failed', 'timeout', 'unavailable', 'schema_invalid', 'validation_failed', 'released', 'rejected', 'rolled_back'].includes(j.status)
    ? j
    : null
})

const canGeneratePatch = computed(
  () =>
    auth.isOperatorPlus &&
    realView.value?.status === 'triaged' &&
    realView.value?.evidence_status === 'ready' &&
    latestTriage.value?.result?.safe_to_generate_patch === true &&
    !activePatchJob.value,
)

/** 按钮禁用时的 tooltip 说明 */
const generatePatchDisabledHint = computed(() => {
  if (activePatchJob.value) {
    return activePatchJob.value.status === 'patch_ready'
      ? `已有就绪补丁（job #${activePatchJob.value.id}），待阶段 8 审批流转后才可重新生成`
      : '已有进行中的补丁任务'
  }
  if (realView.value?.status !== 'triaged') return 'incident 需处于「已判因」状态'
  if (realView.value?.evidence_status !== 'ready') return '证据不完整，需先补证'
  return ''
})

/** 补丁生成进行中（POST 同步等待，后端默认 ≤ patch_timeout_sec 约 10 分钟） */
const patchRunning = ref(false)

/** patch 阶段 job 状态文案（repairJobStatusMeta 的 running/succeeded 等按 triage 语义命名，此处用补丁语义覆盖） */
const PATCH_JOB_STATUS_LABEL: Record<string, string> = {
  running: '生成中',
  failed: '生成失败',
  timeout: '生成超时',
  unavailable: 'Codex 不可用',
  quota_exceeded: '超出配额',
  schema_invalid: '结果格式无效',
  validation_failed: '校验失败',
  patch_ready: '补丁就绪',
  validating: '自动验证中',
  awaiting_validation_approval: '等待验证批准',
  canary: 'Canary 验证',
  awaiting_release_approval: '等待发布批准',
  releasing: '发布事务中',
  release_reconciliation_required: '发布需人工对账',
  release_pending_restart: '已合并，等待重启',
  post_release_check: '发布后检查中',
  release_check_failed: '发布后检查失败',
  released: '已发布',
  rejected: '已拒绝',
  rolled_back: '已回滚',
  rolling_back: '回滚事务中',
  rollback_reconciliation_required: '回滚需人工对账',
}

function patchJobStatusLabel(status: string): string {
  return PATCH_JOB_STATUS_LABEL[status] ?? status
}

function riskMeta(level: RiskLevel | null | undefined) {
  return (level && riskLevelMeta[level]) || { label: level ?? '未定级', color: '#8a919e' }
}

function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : '—'
}

/** 200 outcome 的用户反馈（r2_not_allowed 单独走确认框，不在此列） */
function reportPatchOutcome(resp: GeneratePatchResponse) {
  switch (resp.outcome) {
    case 'patch_ready':
      message.success(`补丁已就绪（job #${resp.job?.id ?? '?'}），请核对 diff，验证与发布在阶段 8 开放`)
      break
    case 'validation_failed':
      message.warning(
        resp.violations.length > 0
          ? `补丁未通过 diff 扫描（${resp.violations.length} 条命中），已拒绝并保留判因`
          : '补丁未通过校验，已回退为「已判因」，可重新生成',
      )
      break
    case 'empty_patch':
      message.warning('Codex 未产生任何改动（空补丁），已回退为「已判因」')
      break
    case 'timeout':
      message.warning('补丁生成超时，已回退为「已判因」，可稍后重试')
      break
    case 'unavailable':
      message.warning('Codex 当前不可用，补丁生成已跳过')
      break
    case 'worktree_failed':
      message.error('隔离 worktree 创建失败，未生成补丁')
      break
    default:
      message.error(`补丁生成失败（${resp.outcome}），已回退为「已判因」`)
  }
}

/** HTTP 错误反馈：409 冲突 / 422 前提不满足 / 503 Codex 禁用 */
function handlePatchError(e: unknown) {
  if (e instanceof ApiError) {
    if (e.status === 409) {
      if (e.detail === 'active_patch_conflict') message.warning('已有进行中的补丁任务')
      else if (e.detail === 'incident_closed') message.info('该 incident 已关闭')
      else message.warning('该 incident 尚未判因通过，无法生成补丁')
      void store.refresh()
      return
    }
    if (e.status === 422) {
      message.warning(e.detail === 'r3_refused' ? 'R3 级修改不生成补丁，仅保留判因分析' : '判因结果不满足生成补丁条件')
      return
    }
    if (e.status === 503) {
      codexDisabled.value = true
      message.warning('Codex 当前不可用，补丁生成已跳过；其他操作不受影响')
      return
    }
    message.error(`生成补丁失败：${e.detail}`)
    return
  }
  message.error('生成补丁失败，请稍后重试')
}

/** 生成补丁入口；outcome=r2_not_allowed 时弹确认框，确认后带 allow_r2=true 重发 */
async function runGeneratePatch(allowR2 = false) {
  if (patchRunning.value) return
  patchRunning.value = true
  try {
    const resp = await store.generatePatchSelected(allowR2)
    codexDisabled.value = false
    if (resp.outcome === 'r2_not_allowed') {
      dialog.warning({
        title: 'R2 级修改确认',
        content:
          '后端复核本次补丁为 R2 级修改（涉及导航/表单步骤语义）。生成补丁需要人工明确允许；R3 级修改一律不生成。确认允许并重新生成？',
        positiveText: '允许 R2 并重新生成',
        negativeText: '取消',
        onPositiveClick: () => {
          void runGeneratePatch(true)
        },
      })
      return
    }
    reportPatchOutcome(resp)
  } catch (e) {
    handlePatchError(e)
  } finally {
    patchRunning.value = false
  }
}

/* ---------- 阶段 8：验证审批、Canary、发布、回滚 ---------- */

const stage8Running = ref(false)

function stage8Error(error: unknown) {
  if (error instanceof ApiError) {
    const labels: Record<string, string> = {
      release_disabled: '发布总开关关闭；当前只能完成 UAT，不能合并生产分支',
      stale_patch_sha: '补丁 SHA 已变化，请刷新后重新核对',
      dirty_worktree: '生产工作树不干净，已停止发布/回滚',
      baseline_head_mismatch: '生产 HEAD 已漂移，已停止发布',
      restart_required: '必须先按 runbook 显式重启 Web 服务',
      canary_not_ready: 'Canary diagnose / dry-run 尚未全部完成',
      post_release_check_active: '发布后 Canary 仍在运行，暂不能回滚',
    }
    message.error(labels[error.detail] ?? `操作失败：${error.detail}`)
  } else {
    message.error('操作失败，请稍后重试')
  }
}

async function runStage8(
  action: 'approve_validation' | 'confirm_canary' | 'approve_release' | 'reject' | 'post_release_check' | 'rollback',
  options: { note?: string; patchSha?: string; evidenceReviewed?: boolean } = {},
) {
  if (stage8Running.value) return
  stage8Running.value = true
  try {
    await store.runStage8Action(action, options)
    message.success('操作已记录，状态已更新')
  } catch (error) {
    stage8Error(error)
  } finally {
    stage8Running.value = false
  }
}

function approveRealValidation() {
  dialog.info({
    title: '批准自动验证结果',
    content: '确认已核对八步验证摘要与 diff。批准后将按顺序创建 diagnose、dry-run Canary。',
    positiveText: '批准并进入 Canary',
    negativeText: '取消',
    onPositiveClick: () => runStage8('approve_validation'),
  })
}

function confirmRealCanary() {
  const note = ref('')
  const evidenceReviewed = ref(false)
  dialog.info({
    title: '确认 Canary 证据',
    content: () =>
      h('div', { style: 'display:grid;gap:10px' }, [
        h(NInput, {
          value: note.value,
          'onUpdate:value': (value: string) => (note.value = value),
          type: 'textarea',
          rows: 3,
          placeholder: '必填：说明已核对的截图、日志和 dry-run 结果',
        }),
        h(NCheckbox, {
          checked: evidenceReviewed.value,
          'onUpdate:checked': (value: boolean) => (evidenceReviewed.value = value),
        }, { default: () => '我已核对 Canary 证据，确认未触发 submit 或业务副作用' }),
      ]),
    positiveText: '确认并进入发布审批',
    negativeText: '取消',
    onPositiveClick: async () => {
      if (!note.value.trim() || !evidenceReviewed.value) {
        message.warning('请填写证据复核说明并勾选确认')
        return false
      }
      await runStage8('confirm_canary', {
        note: note.value.trim(),
        evidenceReviewed: evidenceReviewed.value,
      })
    },
  })
}

function approveRealRelease() {
  const sha = patchJob.value?.job.patch_sha
  if (!sha) return
  dialog.error({
    title: '批准本地发布（Admin）',
    content: `将以 --no-ff 合并已审核 repair 分支，确认补丁 SHA：${sha}。合并后必须显式重启，不会 push 远端。`,
    positiveText: '确认合并',
    negativeText: '取消',
    onPositiveClick: () => runStage8('approve_release', { patchSha: sha }),
  })
}

function stage8NoteAction(action: 'reject' | 'rollback') {
  const note = ref('')
  dialog.warning({
    title: action === 'reject' ? '拒绝补丁' : '回滚发布',
    content: () => h(NInput, {
      value: note.value,
      'onUpdate:value': (value: string) => (note.value = value),
      type: 'textarea',
      rows: 3,
      placeholder: '说明（必填，写入审计历史）',
    }),
    positiveText: action === 'reject' ? '确认拒绝' : '创建 git revert 回滚提交',
    negativeText: '取消',
    onPositiveClick: async () => {
      if (!note.value.trim()) {
        message.warning('请填写说明')
        return false
      }
      await runStage8(action, { note: note.value.trim() })
    },
  })
}

function runPostReleaseCheck() {
  dialog.info({
    title: '启动发布后检查',
    content: '确认 Web 服务已按 runbook 显式重启。系统将校验 HEAD、服务健康并创建一次 diagnose Canary。',
    positiveText: '已重启，开始检查',
    negativeText: '取消',
    onPositiveClick: () => runStage8('post_release_check'),
  })
}

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

/** 关闭当前选中 incident（operator+）：弹输入 note 的确认框 */
function closeRealIncident() {
  const target = realView.value
  if (!target) return
  const note = ref('')
  dialog.warning({
    title: '关闭 incident',
    content: () =>
      h(NInput, {
        'value': note.value,
        'onUpdate:value': (v: string) => {
          note.value = v
        },
        'type': 'textarea',
        'rows': 3,
        'placeholder': '关闭说明（必填）：记录人工判因与处理结论，随 incident 存档',
      }),
    positiveText: '确认关闭',
    negativeText: '取消',
    onPositiveClick: async () => {
      const trimmed = note.value.trim()
      if (!trimmed) {
        message.warning('请填写关闭说明')
        return false
      }
      try {
        await store.closeSelected(trimmed)
        message.success('已关闭')
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          message.info('该 incident 已被关闭')
          void store.refresh()
          return
        }
        message.error(e instanceof ApiError ? `关闭失败：${e.detail}` : '关闭失败，请稍后重试')
        return false
      }
    },
  })
}

function stepStatus(t: IncidentTestResult): 'finish' | 'error' | 'wait' | 'process' {
  if (t.status === 'passed') return 'finish'
  if (t.status === 'failed') return 'error'
  if (t.status === 'pending') return 'wait'
  return 'process'
}

const failedStepIndex = computed(() => {
  const inc = incident.value
  if (!inc) return undefined
  const idx = inc.tests.findIndex((t) => t.status === 'failed')
  return idx >= 0 ? idx : undefined
})

const currentStep = computed(() => {
  const inc = incident.value
  if (!inc) return 0
  return inc.tests.filter((t) => t.status === 'passed').length + 1
})

const canReview = computed(() => incident.value?.status === 'awaiting_review')
const canRelease = computed(() => incident.value?.status === 'approved')

function approveValidation() {
  dialog.info({
    title: '批准验证',
    content: '确认补丁通过验证门禁，状态流转为「已批准」，等待发布。',
    positiveText: '批准验证',
    negativeText: '取消',
    onPositiveClick: () => store.applyReview('approve_validation'),
  })
}

function approveRelease() {
  dialog.error({
    title: '批准发布（高危）',
    content: '发布后补丁将进入金丝雀任务验证，金丝雀失败会自动回滚。确认发布？',
    positiveText: '确认发布',
    negativeText: '取消',
    onPositiveClick: () => store.applyReview('approve_release'),
  })
}

function reject() {
  dialog.warning({
    title: '拒绝补丁',
    content: '拒绝后 incident 状态流转为「已拒绝」，补丁分支保留供回溯。',
    positiveText: '确认拒绝',
    negativeText: '取消',
    onPositiveClick: () => store.applyReview('reject'),
  })
}
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">Codex 修复中心</h1>
        <p class="page-subtitle">incident 判因 → 修复 → 验证门禁 → 人工审核 → 金丝雀发布</p>
      </div>
      <div v-if="USE_MOCK" style="margin-left: auto; display: flex; align-items: center; gap: 8px">
        <span style="font-size: 12px; opacity: 0.6">Mock 角色</span>
        <n-tag
          size="small"
          :bordered="false"
          :type="mockRole === 'Admin' ? 'error' : 'info'"
          style="cursor: pointer"
          @click="mockRole = mockRole === 'Admin' ? 'Reviewer' : 'Admin'"
        >
          {{ mockRole }}（点击切换）
        </n-tag>
      </div>
    </div>

    <!-- 阶段 6：Codex 不可用/超配额降级提示条（不阻塞其他操作） -->
    <n-alert v-if="!USE_MOCK && codexDegraded" type="warning" :bordered="false" style="margin-bottom: 14px">
      Codex 判因服务不可用或已超出每日配额，自动判因已降级。列表、证据包与人工处理操作不受影响，可稍后重试判因。
    </n-alert>

    <n-spin :show="USE_MOCK ? store.loading && store.incidents.length === 0 : store.realLoading && store.realIncidents.length === 0">
      <div v-if="USE_MOCK" class="repair-layout">
        <!-- 左：incident 列表 -->
        <n-card size="small" title="Incident 列表" class="incident-list-card">
          <n-scrollbar style="max-height: calc(100vh - 220px)">
            <n-list hoverable clickable>
              <n-list-item
                v-for="inc in store.incidents"
                :key="inc.id"
                :class="{ selected: store.selectedId === inc.id }"
                @click="store.select(inc.id)"
              >
                <div class="inc-signature">{{ inc.signature }}</div>
                <div class="inc-scope">{{ inc.scope }} · {{ inc.occurrences }} 次</div>
                <div class="inc-row">
                  <StatusTag kind="incident" :status="inc.status" size="small" />
                </div>
                <div class="inc-conf">
                  <span>置信度</span>
                  <n-progress
                    type="line"
                    :percentage="Math.round(inc.confidence * 100)"
                    :show-indicator="false"
                    :height="5"
                    border-radius="3px"
                    :color="inc.confidence >= 0.8 ? '#18a058' : inc.confidence >= 0.6 ? '#f0a020' : '#8a919e'"
                    style="flex: 1"
                  />
                  <span class="inc-conf-num">{{ Math.round(inc.confidence * 100) }}%</span>
                </div>
              </n-list-item>
            </n-list>
          </n-scrollbar>
        </n-card>

        <!-- 右：详情面板 -->
        <div class="incident-detail">
          <n-empty v-if="!incident" description="选择左侧 incident 查看详情" style="margin-top: 120px" />
          <template v-else>
            <n-card size="small" style="margin-bottom: 14px">
              <div class="detail-head">
                <div>
                  <div class="detail-title">{{ incident.signature }}</div>
                  <div class="detail-sub">
                    {{ incident.id }} · 发现于 <RelativeTime :time="incident.detectedAt" /> · 更新于
                    <RelativeTime :time="incident.updatedAt" />
                  </div>
                </div>
                <StatusTag kind="incident" :status="incident.status" />
              </div>
              <n-descriptions :column="2" label-placement="top" size="small" style="margin-top: 12px">
                <n-descriptions-item label="判因结果">
                  <div>{{ incident.classification }}</div>
                  <n-progress
                    type="line"
                    :percentage="Math.round(incident.confidence * 100)"
                    :height="6"
                    border-radius="3px"
                    style="margin-top: 6px; max-width: 240px"
                  />
                </n-descriptions-item>
                <n-descriptions-item label="缺失证据">
                  <ul v-if="incident.missingEvidence.length > 0" class="plain-list">
                    <li v-for="(e, i) in incident.missingEvidence" :key="i">{{ e }}</li>
                  </ul>
                  <span v-else>无</span>
                </n-descriptions-item>
                <n-descriptions-item label="worktree / 分支">
                  <code>{{ incident.worktree ?? '—' }}</code>
                  <br />
                  <code>{{ incident.branch ?? '—' }}</code>
                </n-descriptions-item>
                <n-descriptions-item label="修改文件">
                  <ul v-if="incident.changedFiles.length > 0" class="plain-list">
                    <li v-for="(f, i) in incident.changedFiles" :key="i"><code>{{ f }}</code></li>
                  </ul>
                  <span v-else>尚无（未完成修复）</span>
                </n-descriptions-item>
              </n-descriptions>
            </n-card>

            <n-card size="small" title="补丁 Diff" style="margin-bottom: 14px">
              <DiffViewer :diff="incident.diff" />
            </n-card>

            <n-card size="small" title="验证门禁（12 步）" style="margin-bottom: 14px">
              <n-steps vertical size="small" :current="currentStep" :status="failedStepIndex !== undefined ? 'error' : undefined">
                <n-step
                  v-for="(t, i) in incident.tests"
                  :key="i"
                  :title="`${i + 1}. ${t.step}`"
                  :status="stepStatus(t)"
                  :description="t.detail ?? undefined"
                />
              </n-steps>
            </n-card>

            <n-card v-if="incident.remainingRisks.length > 0" size="small" title="剩余风险" style="margin-bottom: 14px">
              <n-alert type="warning" :bordered="false">
                <ul class="plain-list" style="margin: 0">
                  <li v-for="(r, i) in incident.remainingRisks" :key="i">{{ r }}</li>
                </ul>
              </n-alert>
            </n-card>

            <n-card size="small" title="审核操作">
              <n-input
                v-model:value="reviewNote"
                type="textarea"
                placeholder="审核备注（可选）：记录判断依据，随状态流转存档"
                :rows="2"
                style="margin-bottom: 12px"
              />
              <div class="action-row">
                <n-button v-if="canReview" type="primary" @click="approveValidation">批准验证</n-button>
                <n-button v-if="canRelease && mockRole === 'Admin'" type="error" @click="approveRelease">
                  批准发布（Admin）
                </n-button>
                <n-button v-if="canReview || canRelease" secondary @click="reject">拒绝</n-button>
                <span v-if="!canReview && !canRelease" class="action-hint">
                  当前状态「{{ incident.status }}」无可用审核操作
                </span>
                <span class="action-hint">按钮按 Mock 角色显示；前端隐藏 ≠ 权限控制，真实权限由后端强制</span>
              </div>
            </n-card>
          </template>
        </div>
      </div>

      <!-- 真实模式：阶段 5 incident 队列（修复候选类列表 + 证据包详情） -->
      <div v-else class="repair-layout">
        <!-- 左：incident 列表 -->
        <n-card size="small" title="Incident 列表（修复候选）" class="incident-list-card">
          <n-empty
            v-if="!store.realLoading && store.realIncidents.length === 0"
            description="当前没有修复候选 incident"
            style="margin: 40px 0"
          />
          <n-scrollbar v-else style="max-height: calc(100vh - 220px)">
            <n-list hoverable clickable>
              <n-list-item
                v-for="inc in store.realIncidents"
                :key="inc.id"
                :class="{ selected: store.selectedRealId === inc.id }"
                @click="store.selectReal(inc.id)"
              >
                <div class="inc-signature">{{ inc.brand_name ?? inc.signature.slice(0, 8) }}</div>
                <div class="inc-scope">
                  {{ classificationLabel(inc.classification) }} ·
                  <template v-if="inc.account_id">{{ inc.account_id }} · </template>
                  <template v-if="inc.marketplace">{{ inc.marketplace.toUpperCase() }} · </template>
                  ×{{ inc.occurrence_count }} 次
                </div>
                <div class="inc-row">
                  <StatusTag kind="incident_queue" :status="inc.status" size="small" />
                </div>
                <div class="inc-conf">
                  <span>置信度</span>
                  <n-progress
                    type="line"
                    :percentage="Math.round(inc.confidence * 100)"
                    :show-indicator="false"
                    :height="5"
                    border-radius="3px"
                    :color="inc.confidence >= 0.8 ? '#18a058' : inc.confidence >= 0.6 ? '#f0a020' : '#8a919e'"
                    style="flex: 1"
                  />
                  <span class="inc-conf-num">{{ Math.round(inc.confidence * 100) }}%</span>
                </div>
              </n-list-item>
            </n-list>
          </n-scrollbar>
        </n-card>

        <!-- 右：详情面板 -->
        <div class="incident-detail">
          <n-empty v-if="!realView" description="选择左侧 incident 查看详情" style="margin-top: 120px" />
          <n-spin v-else :show="store.detailLoading">
            <n-card size="small" style="margin-bottom: 14px">
              <div class="detail-head">
                <div>
                  <div class="detail-title">{{ realView.signature }}</div>
                  <div class="detail-sub">
                    #{{ realView.id }} · 首次 <RelativeTime :time="realView.first_seen_at" /> · 最近
                    <RelativeTime :time="realView.last_seen_at" /> · ×{{ realView.occurrence_count }} 次
                  </div>
                </div>
                <StatusTag kind="incident_queue" :status="realView.status" />
              </div>
              <n-descriptions :column="2" label-placement="top" size="small" style="margin-top: 12px">
                <n-descriptions-item label="规则分类（检测器）">
                  <div>{{ classificationLabel(realView.classification) }}</div>
                  <n-progress
                    type="line"
                    :percentage="Math.round(realView.confidence * 100)"
                    :height="6"
                    border-radius="3px"
                    style="margin-top: 6px; max-width: 240px"
                  />
                </n-descriptions-item>
                <n-descriptions-item label="检测器 / 范围">
                  <div>{{ realView.detector_type }}</div>
                  <div class="detail-sub">
                    {{ realView.scope_type }}
                    <template v-if="realView.flow_type"> · {{ realView.flow_type }}</template>
                  </div>
                </n-descriptions-item>
                <n-descriptions-item label="账号 / 站点 / 品牌">
                  {{ realView.account_id ?? '—' }} · {{ realView.marketplace?.toUpperCase() ?? '—' }} ·
                  {{ realView.brand_name ?? '—' }}
                </n-descriptions-item>
                <n-descriptions-item label="处理结论">
                  <span v-if="realView.resolution_note">{{ realView.resolution_note }}</span>
                  <span v-else>尚无</span>
                </n-descriptions-item>
              </n-descriptions>
            </n-card>

            <n-card size="small" title="证据包文件" style="margin-bottom: 14px">
              <n-empty v-if="bundleFiles.length === 0" description="无证据包文件" size="small" style="margin: 12px 0" />
              <ul v-else class="plain-list">
                <li v-for="f in bundleFiles" :key="f.path">
                  <a :href="bundleUrl(f.path)" target="_blank" rel="noopener">{{ f.name }}</a>
                  <span class="file-meta">{{ f.kind }} · {{ formatSize(f.size) }}</span>
                </li>
              </ul>
            </n-card>

            <!-- 阶段 6：Codex 只读判因（与上方"规则分类（检测器）"相互独立、并列展示） -->
            <n-card size="small" style="margin-bottom: 14px">
              <template #header>
                Codex 判因
                <span class="action-hint" style="margin-left: 8px; font-weight: normal">
                  只读判因，不修改任何文件；与规则分类（检测器）相互独立
                </span>
              </template>

              <n-alert v-if="triageRunning" type="info" :bordered="false" style="margin-bottom: 12px">
                Codex 判因进行中，最长约 120 秒，请稍候…
                <n-progress type="line" :percentage="100" processing :show-indicator="false" :height="5" style="margin-top: 8px" />
              </n-alert>
              <n-alert v-if="realView.evidence_status !== 'ready'" type="warning" :bordered="false" style="margin-bottom: 12px">
                当前为“等待补证”，不会调用 Codex。缺失：{{ realView.missing_evidence.join('、') || '证据门禁尚未重新评估' }}
              </n-alert>

              <n-empty v-if="!latestTriage" description="尚无 Codex 判因结果" size="small" style="margin: 12px 0" />
              <template v-else>
                <div class="triage-job-row">
                  <StatusTag kind="repair_job" :status="latestTriage.job.status" size="small" />
                  <span class="file-meta">
                    job #{{ latestTriage.job.id }}
                    <template v-if="jobDurationSec(latestTriage.job) !== null">
                      · 耗时 {{ jobDurationSec(latestTriage.job) }}s
                    </template>
                    <template v-if="latestTriage.job.finished_at">
                      · 完成于 <RelativeTime :time="latestTriage.job.finished_at" />
                    </template>
                  </span>
                  <a
                    v-if="latestTriage.job.jsonl_log_path"
                    :href="bundleUrl(latestTriage.job.jsonl_log_path)"
                    target="_blank"
                    rel="noopener"
                    style="font-size: 12px"
                  >
                    下载 JSONL 日志
                  </a>
                </div>

                <n-alert
                  v-if="!latestTriage.result && latestTriage.job.status !== 'succeeded'"
                  type="warning"
                  :bordered="false"
                  style="margin-top: 12px"
                >
                  本次判因未产出结果（{{ latestTriage.job.status }}）。incident 保持可重试，可点击下方「重新判因」。
                </n-alert>

                <template v-if="latestTriage.result">
                  <n-descriptions :column="2" label-placement="top" size="small" style="margin-top: 12px">
                    <n-descriptions-item label="Codex 判因分类">
                      <div>{{ triageClassificationLabel(latestTriage.result.classification) }}</div>
                      <n-progress
                        type="line"
                        :percentage="Math.round(latestTriage.result.confidence * 100)"
                        :height="6"
                        border-radius="3px"
                        style="margin-top: 6px; max-width: 240px"
                      />
                    </n-descriptions-item>
                    <n-descriptions-item label="建议修复范围">
                      {{ latestTriage.result.recommended_scope }}
                    </n-descriptions-item>
                    <n-descriptions-item label="判因理由" :span="2">
                      {{ latestTriage.result.reason }}
                    </n-descriptions-item>
                    <n-descriptions-item label="受影响组件">
                      <ul v-if="latestTriage.result.affected_components.length > 0" class="plain-list">
                        <li v-for="(c, i) in latestTriage.result.affected_components" :key="i"><code>{{ c }}</code></li>
                      </ul>
                      <span v-else>无</span>
                    </n-descriptions-item>
                    <n-descriptions-item label="缺失证据">
                      <ul v-if="latestTriage.result.missing_evidence.length > 0" class="plain-list">
                        <li v-for="(e, i) in latestTriage.result.missing_evidence" :key="i">{{ e }}</li>
                      </ul>
                      <span v-else>无</span>
                    </n-descriptions-item>
                  </n-descriptions>
                  <div class="triage-flags">
                    <n-tag size="small" :bordered="false" :type="latestTriage.result.safe_to_generate_patch ? 'success' : 'default'">
                      {{ latestTriage.result.safe_to_generate_patch ? '可生成补丁' : '暂不可生成补丁' }}
                    </n-tag>
                    <n-tag v-if="latestTriage.result.requires_human_review" size="small" :bordered="false" type="warning">
                      需人工复核
                    </n-tag>
                  </div>
                </template>
              </template>

              <div class="action-row" style="margin-top: 14px">
                <n-button
                  v-if="auth.isOperatorPlus"
                  type="primary"
                  secondary
                  :loading="triageRunning"
                  :disabled="realView.evidence_status !== 'ready'"
                  @click="runTriage"
                >
                  {{ latestTriage ? '重新判因' : '触发判因' }}
                </n-button>
                <n-tooltip v-if="latestTriage?.result?.safe_to_generate_patch" trigger="hover" :disabled="canGeneratePatch">
                  <template #trigger>
                    <n-button
                      type="primary"
                      :disabled="!canGeneratePatch"
                      :loading="patchRunning"
                      @click="runGeneratePatch(false)"
                    >
                      生成补丁
                    </n-button>
                  </template>
                  {{ generatePatchDisabledHint }}
                </n-tooltip>
                <span class="action-hint">判因/生成补丁需 operator 及以上角色；前端隐藏 ≠ 权限控制，真实权限由后端强制</span>
              </div>

              <n-alert v-if="patchRunning" type="info" :bordered="false" style="margin-top: 12px">
                补丁生成进行中（隔离 worktree 内由 Codex 修改，生产工作树不变），最长约 10 分钟，请稍候…
                <n-progress type="line" :percentage="100" processing :show-indicator="false" :height="5" style="margin-top: 8px" />
              </n-alert>
            </n-card>

            <!-- 阶段 7：隔离生成补丁（有 stage='patch' job 时展示；验证/发布/拒绝按钮阶段 8 才开放） -->
            <n-card v-if="patchJob" size="small" style="margin-bottom: 14px">
              <template #header>
                补丁
                <span class="action-hint" style="margin-left: 8px; font-weight: normal">
                  Codex 仅在隔离 worktree 中修改，生产工作树无直接变化
                </span>
              </template>

              <div class="triage-job-row">
                <n-tag
                  size="small"
                  :bordered="false"
                  :type="patchJob.job.status === 'patch_ready' ? 'success' : patchJob.job.status === 'validation_failed' || patchJob.job.status === 'failed' ? 'error' : 'info'"
                >
                  {{ patchJobStatusLabel(patchJob.job.status) }}
                </n-tag>
                <n-tag
                  size="small"
                  :bordered="false"
                  :style="{ color: riskMeta(patchJob.job.risk_level).color, backgroundColor: riskMeta(patchJob.job.risk_level).color + '1a' }"
                >
                  {{ riskMeta(patchJob.job.risk_level).label }}
                </n-tag>
                <n-tag v-if="patchJob.result?.requires_human_review" size="small" :bordered="false" type="warning">
                  需人工复核
                </n-tag>
                <span class="file-meta">
                  job #{{ patchJob.job.id }}
                  <template v-if="jobDurationSec(patchJob.job) !== null"> · 耗时 {{ jobDurationSec(patchJob.job) }}s</template>
                  <template v-if="patchJob.job.finished_at"> · 完成于 <RelativeTime :time="patchJob.job.finished_at" /></template>
                </span>
                <a
                  v-if="patchJob.job.jsonl_log_path"
                  :href="bundleUrl(patchJob.job.jsonl_log_path)"
                  target="_blank"
                  rel="noopener"
                  style="font-size: 12px"
                >
                  下载 JSONL 日志
                </a>
              </div>

              <n-alert
                v-if="patchJob.job.status === 'validation_failed'"
                type="error"
                :bordered="false"
                style="margin-top: 12px"
              >
                补丁未通过校验，incident 已回退为「已判因」，可在判因卡片重新生成。
              </n-alert>

              <n-descriptions :column="2" label-placement="top" size="small" style="margin-top: 12px">
                <n-descriptions-item label="分支 / worktree">
                  <code>{{ patchJob.job.branch_name ?? '—' }}</code>
                  <br />
                  <code>{{ patchJob.job.worktree_path ?? '—' }}</code>
                </n-descriptions-item>
                <n-descriptions-item label="基线 / 补丁 commit">
                  <code>{{ shortSha(patchJob.job.baseline_sha) }}</code>
                  <span class="file-meta">baseline</span>
                  <br />
                  <code>{{ shortSha(patchJob.job.patch_sha) }}</code>
                  <span class="file-meta">patch</span>
                </n-descriptions-item>
                <n-descriptions-item v-if="patchJob.result" label="Codex 总结" :span="2">
                  {{ patchJob.result.summary }}
                </n-descriptions-item>
                <n-descriptions-item v-if="patchJob.result?.notes" label="备注" :span="2">
                  {{ patchJob.result.notes }}
                </n-descriptions-item>
                <n-descriptions-item label="修改文件">
                  <ul v-if="(patchJob.job.changed_files?.length ?? 0) > 0" class="plain-list">
                    <li v-for="(f, i) in patchJob.job.changed_files" :key="i"><code>{{ f }}</code></li>
                  </ul>
                  <span v-else>无</span>
                </n-descriptions-item>
                <n-descriptions-item label="测试">
                  <template v-if="patchJob.result">
                    <div>
                      {{ patchJob.result.tests_ran ? (patchJob.result.tests_passed ? '已运行 · 通过' : '已运行 · 未通过') : '未运行' }}
                    </div>
                    <ul v-if="patchJob.result.tests_added.length > 0" class="plain-list">
                      <li v-for="(t, i) in patchJob.result.tests_added" :key="i"><code>{{ t }}</code></li>
                    </ul>
                  </template>
                  <span v-else>—</span>
                </n-descriptions-item>
              </n-descriptions>

              <div style="margin-top: 14px">
                <div class="action-hint" style="margin-bottom: 6px">完整 diff（patch.diff 原文）</div>
                <DiffViewer :diff="patchDiff" />
              </div>

              <div v-if="validation" style="margin-top: 14px">
                <div class="action-hint" style="margin-bottom: 8px">八步验证门禁</div>
                <n-alert
                  :type="validation.status === 'pass' ? 'success' : validation.status === 'failed' ? 'error' : 'info'"
                  :bordered="false"
                  style="margin-bottom: 8px"
                >
                  {{ validation.status === 'pass' ? '全部验证通过，等待 Reviewer 批准' : validation.status === 'failed' ? `验证失败：${validation.failure_reason}` : '验证执行中' }}
                </n-alert>
                <n-list bordered size="small">
                  <n-list-item v-for="step in validation.steps" :key="step.name">
                    <div style="display:flex;justify-content:space-between;gap:12px;width:100%">
                      <span><code>{{ step.name }}</code></span>
                      <span class="file-meta">
                        {{ step.status }} · {{ step.duration_sec }}s
                        <template v-if="step.exit_code !== null"> · exit {{ step.exit_code }}</template>
                      </span>
                    </div>
                    <div v-if="step.failure_reason" class="action-hint">{{ step.failure_reason }}</div>
                  </n-list-item>
                </n-list>
              </div>

              <div v-if="canaryJobs.length > 0" style="margin-top: 14px">
                <div class="action-hint" style="margin-bottom: 8px">Canary 任务与证据</div>
                <n-list bordered size="small">
                  <n-list-item v-for="row in canaryJobs" :key="row.kind">
                    <div style="display:flex;justify-content:space-between;gap:12px;width:100%">
                      <span>{{ row.kind }} · <code>{{ row.job.id }}</code></span>
                      <StatusTag kind="run" :status="row.job.run_status" size="small" />
                    </div>
                    <ul v-if="row.items.length > 0" class="plain-list">
                      <li v-for="item in row.items" :key="item.id">
                        {{ item.brand_name }} · {{ item.business_status ?? item.run_status ?? '—' }}
                      </li>
                    </ul>
                    <ul v-if="row.artifacts.length > 0" class="plain-list">
                      <li v-for="artifact in row.artifacts" :key="`${artifact.root}:${artifact.path}`">
                        <a :href="bundleUrl(artifact.path)" target="_blank" rel="noopener noreferrer">
                          {{ artifact.root }} · {{ artifact.path }}
                        </a>
                        <span class="file-meta">{{ artifact.type }} · {{ artifact.size }} B</span>
                      </li>
                    </ul>
                    <div v-else class="action-hint">尚无可复核的截图或日志文件</div>
                  </n-list-item>
                </n-list>
              </div>

              <n-descriptions :column="2" label-placement="top" size="small" style="margin-top: 14px">
                <n-descriptions-item label="发布 SHA">
                  pre <code>{{ shortSha(patchJob.job.pre_release_sha) }}</code> ·
                  release <code>{{ shortSha(patchJob.job.release_sha) }}</code> ·
                  rollback <code>{{ shortSha(patchJob.job.rollback_sha) }}</code>
                </n-descriptions-item>
                <n-descriptions-item label="回滚方式">
                  仅允许 Admin 在干净工作树、HEAD 匹配时创建 <code>git revert</code> 提交；不使用 reset。
                </n-descriptions-item>
              </n-descriptions>

              <div v-if="approvals.length > 0" style="margin-top: 14px">
                <div class="action-hint" style="margin-bottom: 6px">审批历史</div>
                <ul class="plain-list">
                  <li v-for="approval in approvals" :key="approval.id">
                    {{ approval.decision }} · actor #{{ approval.actor_id }} · {{ approval.created_at }}
                    <template v-if="approval.note"> · {{ approval.note }}</template>
                  </li>
                </ul>
              </div>

              <n-alert v-if="patchJob.restart_required" type="warning" :bordered="false" style="margin-top: 14px">
                repair 分支已合并，但尚未发布完成。请按 runbook 显式重启 Web 服务，再由 Admin 启动发布后检查。
              </n-alert>
              <n-alert
                v-if="['release_reconciliation_required', 'rollback_reconciliation_required'].includes(patchJob.job.status)"
                type="error" :bordered="false" style="margin-top: 14px"
              >
                Git HEAD 与持久化意图无法严格对应，已停止自动操作。请人工核对，不要重复 merge、revert 或 reset。
              </n-alert>
              <n-alert v-if="patchJob.git_operation" type="info" :bordered="false" style="margin-top: 14px">
                Git 操作 #{{ patchJob.git_operation.id }}：{{ patchJob.git_operation.operation }} / {{ patchJob.git_operation.state }}；
                expected <code>{{ shortSha(patchJob.git_operation.expected_head_sha) }}</code>，target <code>{{ shortSha(patchJob.git_operation.target_sha) }}</code>
              </n-alert>
              <n-alert v-if="patchJob.job.status === 'awaiting_release_approval' && !patchJob.release_enabled" type="info" :bordered="false" style="margin-top: 14px">
                当前 release_enabled=false：UAT 已到安全终点，不会合并生产分支。
              </n-alert>

              <div class="action-row" style="margin-top: 14px">
                <n-button
                  v-if="auth.isReviewerPlus && patchJob.job.status === 'awaiting_validation_approval'"
                  type="primary"
                  :loading="stage8Running"
                  @click="approveRealValidation"
                >批准验证</n-button>
                <n-button
                  v-if="auth.isReviewerPlus && patchJob.job.status === 'canary'"
                  type="primary"
                  :disabled="!patchJob.canary.ready_for_review"
                  :loading="stage8Running"
                  @click="confirmRealCanary"
                >确认 Canary</n-button>
                <n-button
                  v-if="auth.isAdmin && patchJob.job.status === 'awaiting_release_approval'"
                  type="error"
                  :disabled="!patchJob.release_enabled"
                  :loading="stage8Running"
                  @click="approveRealRelease"
                >批准发布</n-button>
                <n-button
                  v-if="auth.isAdmin && patchJob.job.status === 'release_pending_restart'"
                  type="primary"
                  :loading="stage8Running"
                  @click="runPostReleaseCheck"
                >发布后检查</n-button>
                <n-button
                  v-if="auth.isReviewerPlus && ['awaiting_validation_approval', 'canary', 'awaiting_release_approval'].includes(patchJob.job.status)"
                  secondary
                  :loading="stage8Running"
                  @click="stage8NoteAction('reject')"
                >拒绝</n-button>
                <n-button
                  v-if="auth.isAdmin && ['release_pending_restart', 'post_release_check', 'release_check_failed', 'released'].includes(patchJob.job.status)"
                  type="error"
                  secondary
                  :loading="stage8Running"
                  @click="stage8NoteAction('rollback')"
                >回滚</n-button>
                <span class="action-hint">Reviewer 管验证/Canary，Admin 管发布/回滚；所有操作由后端再次校验角色与状态。</span>
              </div>
            </n-card>

            <n-card size="small" title="处理操作">
              <div class="action-row">
                <n-button
                  v-if="auth.isOperatorPlus && (realView.status === 'open' || realView.status === 'triaged')"
                  type="primary"
                  @click="closeRealIncident"
                >
                  关闭 incident
                </n-button>
                <span v-if="realView.status === 'closed_human' || realView.status === 'closed_duplicate'" class="action-hint">
                  该 incident 已关闭
                </span>
                <span class="action-hint">关闭需 operator 及以上角色；前端隐藏 ≠ 权限控制，真实权限由后端强制</span>
              </div>
            </n-card>
          </n-spin>
        </div>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.repair-layout {
  display: grid;
  grid-template-columns: 360px 1fr;
  gap: 16px;
  align-items: start;
}

@media (max-width: 1000px) {
  .repair-layout {
    grid-template-columns: 1fr;
  }
}

.incident-list-card :deep(.n-list-item) {
  border-radius: 8px;
  margin-bottom: 4px;
  padding: 10px 12px;
}

.incident-list-card :deep(.n-list-item.selected) {
  background: rgba(32, 128, 240, 0.1);
}

.inc-signature {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
  word-break: break-all;
}

.inc-scope {
  font-size: 12px;
  opacity: 0.65;
  margin-top: 2px;
}

.inc-row {
  margin-top: 6px;
}

.inc-conf {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  opacity: 0.7;
  margin-top: 6px;
}

.inc-conf-num {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  flex-shrink: 0;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.detail-title {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-weight: 700;
  font-size: 15px;
  word-break: break-all;
}

.detail-sub {
  font-size: 12px;
  opacity: 0.6;
  margin-top: 4px;
}

.plain-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.8;
}

.file-meta {
  font-size: 12px;
  opacity: 0.55;
  margin-left: 8px;
}

.triage-job-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.triage-job-row .file-meta {
  margin-left: 0;
}

.triage-flags {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.action-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.action-hint {
  font-size: 12px;
  opacity: 0.55;
}

code {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
}
</style>

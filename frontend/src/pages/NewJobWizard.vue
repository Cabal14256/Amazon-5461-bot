<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NCheckboxGroup,
  NEmpty,
  NIcon,
  NInput,
  NSelect,
  NSpin,
  NStep,
  NSteps,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import { FlaskOutline, RefreshOutline, SearchOutline, WarningOutline } from '@vicons/ionicons5'
import { createJob, createSubmit, getAccounts, getBrands, getSites, runPrecheck, syncAccounts } from '@/api'
import { USE_MOCK } from '@/api/client'
import { ApiError } from '@/api/http'
import type { SubmitPreflightCheck } from '@/api/real'
import type { Account, Brand, JobMode, JobType, PrecheckItem, Site } from '@/types'
import SubmitPreflightList from '@/components/SubmitPreflightList.vue'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const currentStep = ref(1)

const mode = ref<JobMode>('dry_run')
const jobType = ref<JobType>('apply_5461')
const accountId = ref<string | null>(null)
const site = ref<Site['code'] | null>(null)
const selectedBrands = ref<string[]>([])
const brandSearch = ref('')

const accounts = ref<Account[]>([])
const sites = ref<Site[]>([])
const brands = ref<Brand[]>([])
const catalogLoading = ref(true)

const precheckItems = ref<PrecheckItem[]>([])
const precheckRunning = ref(false)
const precheckDone = ref(false)

const submitting = ref(false)

/** 真实提交：风险确认勾选（未勾选不能创建真实提交任务） */
const riskAccepted = ref(false)
/** submit 被 422 preflight_blocked 阻断时后端返回的检查表 */
const blockedChecks = ref<SubmitPreflightCheck[] | null>(null)

onMounted(async () => {
  const [a, s, b] = await Promise.all([getAccounts(), getSites(), getBrands()])
  accounts.value = a
  sites.value = s
  brands.value = b
  catalogLoading.value = false
})

const modeCards = computed(() => [
  {
    key: 'diagnose' as JobMode,
    title: '诊断',
    desc: '只读取页面与状态，不填表不提交。用于确认账号/站点/品牌现状。',
    icon: SearchOutline,
    cls: 'mode-diagnose',
  },
  {
    key: 'dry_run' as JobMode,
    title: 'Dry-run',
    desc: '真实打开浏览器走完整流程到提交前一步，截图留证但不点击最终提交，耗时数分钟。推荐先用它验证。',
    icon: FlaskOutline,
    cls: 'mode-dryrun',
  },
  {
    key: 'submit' as JobMode,
    title: '真实提交',
    desc: '将真实向 Amazon 提交 5461 申请，不可撤销。确认后任务直接入队执行，需 reviewer 及以上权限。',
    icon: WarningOutline,
    cls: 'mode-real',
  },
])

function pickMode(m: (typeof modeCards.value)[number]) {
  mode.value = m.key
}

const typeOptions = [
  { label: '5461 申请', value: 'apply_5461' },
  { label: '目录授权', value: 'catalog_auth' },
  { label: '品牌验证', value: 'brand_verify' },
  { label: 'GTIN 豁免', value: 'gtin_exemption' },
]

const accountOptions = computed(() =>
  accounts.value.map((a) => ({ label: `${a.label}（${a.alias}）`, value: a.id })),
)

const siteOptions = computed(() => sites.value.map((s) => ({ label: `${s.name}（${s.code}）`, value: s.code })))

const filteredBrands = computed(() => {
  const q = brandSearch.value.trim().toLowerCase()
  if (!q) return brands.value
  return brands.value.filter((b) => b.name.toLowerCase().includes(q))
})

const accountLabel = computed(
  () => accounts.value.find((a) => a.id === accountId.value)?.label ?? '—',
)

/** 单次任务品牌数上限（与后端 MAX_BRANDS_PER_JOB=20 对齐） */
const MAX_BRANDS = 20
const brandCapReached = computed(() => selectedBrands.value.length >= MAX_BRANDS)

const precheckHasFailure = computed(() => precheckItems.value.some((i) => i.status === 'failed'))

/** 步骤条：最后一步为预检结果与提交确认 */
const stepTitles = ['运行模式', '账号', '站点', '品牌', '前置条件检查', '预检结果']

function canGoNext(): boolean {
  switch (currentStep.value) {
    case 1:
      return !!mode.value && !!jobType.value
    case 2:
      return !!accountId.value
    case 3:
      return !!site.value
    case 4:
      return selectedBrands.value.length > 0
    case 5:
      return precheckDone.value && !precheckRunning.value
    default:
      return false
  }
}

async function next() {
  if (currentStep.value === 4) {
    currentStep.value = 5
    await doPrecheck()
    return
  }
  if (currentStep.value === 5) {
    currentStep.value = 6
    return
  }
  currentStep.value += 1
}

function back() {
  currentStep.value -= 1
}

/** 从 AdsPower 同步新环境并刷新账号列表 */
const accountSyncing = ref(false)

async function onSyncAccounts() {
  if (accountSyncing.value) return
  accountSyncing.value = true
  try {
    const r = await syncAccounts()
    accounts.value = await getAccounts()
    if (r.enrolled.length > 0) {
      message.success(`已新登记 ${r.enrolled.length} 个账号：${r.enrolled.map((e) => e.account_id).join('、')}（当前共 ${r.total} 个）`)
    } else {
      message.info(`账号已是最新：扫描 ${r.profiles_scanned} 个 AdsPower 环境，共 ${r.total} 个账号`)
    }
    if (r.ambiguous.length > 0 || r.failed.length > 0) {
      message.warning(`${r.ambiguous.length} 个环境需人工确认、${r.failed.length} 个登记失败，未写入，请用 scripts/adspower_auto_enroll.py 查看详情`)
    }
  } catch (e) {
    message.error(e instanceof ApiError ? `同步失败：${e.message}` : '同步失败：AdsPower 不可用或权限不足')
  } finally {
    accountSyncing.value = false
  }
}

/** 真实模式：后端无独立预检接口，基于已加载的真实 catalog 做本地前置条件展示 */
function localPrecheck(): PrecheckItem[] {
  const acc = accounts.value.find((a) => a.id === accountId.value)
  const siteKnown = sites.value.some((s) => s.code === site.value)
  const allPacksReady = selectedBrands.value.every((b) => brands.value.some((x) => x.name === b && x.packReady))
  return [
    {
      key: 'account',
      label: '账号在真实目录中',
      status: acc ? 'passed' : 'failed',
      detail: acc ? '会话由 AdsPower profile 承载；调度时会检查同 profile 锁' : '所选账号不在 catalog，后端将拒绝创建',
    },
    {
      key: 'site',
      label: '站点配置存在',
      status: siteKnown ? 'passed' : 'failed',
      detail: siteKnown ? 'marketplaces 配置中包含该站点' : '站点不在 marketplaces 配置中，后端将拒绝创建',
    },
    {
      key: 'packs',
      label: '品牌包齐备',
      status: allPacksReady ? 'passed' : 'failed',
      detail: allPacksReady
        ? `${selectedBrands.value.length} 个品牌均有品牌包目录`
        : '存在无品牌包目录的品牌，后端将拒绝创建',
    },
    {
      key: 'queue',
      label: '串行调度说明',
      status: 'warning',
      detail: '任务全局串行执行：同 profile 不并发；Web 重启后排队任务自动恢复',
    },
  ]
}

async function doPrecheck() {
  if (!accountId.value || !site.value) return
  precheckRunning.value = true
  precheckDone.value = false
  precheckItems.value = USE_MOCK
    ? await runPrecheck({
        mode: mode.value,
        type: jobType.value,
        accountId: accountId.value,
        site: site.value,
        brands: selectedBrands.value,
      })
    : localPrecheck()
  precheckRunning.value = false
  precheckDone.value = true
}

function wizardPayload() {
  return {
    mode: mode.value,
    type: jobType.value,
    accountId: accountId.value ?? '',
    site: site.value ?? '',
    brands: selectedBrands.value,
  }
}

async function submit() {
  if (mode.value === 'submit') {
    confirmRealSubmit()
    return
  }
  await doCreate()
}

/** 真实提交：最终确认弹窗（防误触），确认后调 /jobs/submit 直接入队 */
function confirmRealSubmit() {
  dialog.error({
    title: '确认真实提交',
    content: `将对账号「${accountLabel.value}」（${site.value}）的 ${selectedBrands.value.length} 个品牌真实提交 Amazon 申请。任务创建后立即入队执行，不可撤销。确定继续？`,
    positiveText: '确认真实提交',
    negativeText: '取消',
    onPositiveClick: () => {
      void doRealSubmit()
    },
  })
}

/** 创建真实提交任务：POST /jobs/submit，成功后跳任务详情页 */
async function doRealSubmit() {
  if (!accountId.value || !site.value) return
  submitting.value = true
  blockedChecks.value = null
  try {
    const res = await createSubmit(wizardPayload())
    message.success(`真实提交任务已创建并入队：${res.job.id}`)
    router.push(`/jobs/${res.job.id}`)
  } catch (e) {
    const blocked = preflightBlockedChecks(e)
    if (blocked) {
      blockedChecks.value = blocked
      message.error('服务端预检存在阻断项，已拒绝提交')
    } else {
      message.error(submitErrorMessage(e))
    }
  } finally {
    submitting.value = false
  }
}

/** 422 preflight_blocked：detail 为结构化 {error, checks}，从 ApiError.rawDetail 取回 */
function preflightBlockedChecks(e: unknown): SubmitPreflightCheck[] | null {
  if (!(e instanceof ApiError) || e.status !== 422) return null
  const raw = e.rawDetail as { error?: unknown; checks?: unknown } | null
  if (raw && raw.error === 'preflight_blocked' && Array.isArray(raw.checks)) {
    return raw.checks as SubmitPreflightCheck[]
  }
  return null
}

function submitErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403 && e.detail === 'submit_disabled') {
      return '真实提交功能未开启（submit_disabled），请联系管理员在服务端开启后再试'
    }
    if (e.status === 403) return '提交失败：权限不足，需 reviewer 及以上角色'
    if (e.status === 422) {
      const raw = e.rawDetail as { error?: unknown; max_brands?: unknown; brand_count?: unknown } | null
      if (raw && raw.error === 'too_many_brands' && typeof raw.max_brands === 'number') {
        return `提交失败：已选 ${raw.brand_count} 个品牌，超过真实提交单次上限 ${raw.max_brands} 个，请分批创建`
      }
      if (e.detail === 'too_many_brands') return '提交失败：品牌数超过真实提交单次上限，请分批创建'
      if (e.detail === 'no_brands') return '提交失败：未选择品牌'
      if (e.detail === 'unknown_account') return '提交失败：账号不在白名单中'
      if (e.detail === 'unknown_site') return '提交失败：站点不在白名单中'
      if (e.detail.startsWith('unknown_brand')) {
        return `提交失败：品牌不在白名单中（${e.detail.split(':')[1] ?? '未知'}）`
      }
    }
    return `提交失败（HTTP ${e.status}）：${e.detail}`
  }
  return `提交失败：${e instanceof Error ? e.message : String(e)}`
}

async function doCreate() {
  if (!accountId.value || !site.value) return
  submitting.value = true
  try {
    const res = await createJob(wizardPayload())
    if (USE_MOCK) {
      message.success(`任务已创建（Mock）：${res.jobId}`)
      router.push('/jobs')
    } else {
      message.success(`任务已创建：${res.jobId}，已加入串行队列`)
      router.push(`/jobs/${res.jobId}`)
    }
  } catch (e) {
    message.error(`创建失败：${e instanceof Error ? e.message : String(e)}`)
  } finally {
    submitting.value = false
  }
}

const precheckStatusType = { passed: 'success', warning: 'warning', failed: 'error' } as const
</script>

<template>
  <div class="page-container wizard-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">新建任务</h1>
        <p class="page-subtitle">向导式创建；真实提交确认后直接入队执行，不可撤销</p>
      </div>
    </div>

    <n-alert v-if="!USE_MOCK" type="info" :bordered="false" style="margin-bottom: 16px">
      诊断与 Dry-run 创建后直接入队（全局串行执行，同 profile 不并发）；「真实提交」需 reviewer
      及以上权限，确认后任务立即入队执行，不可撤销。
    </n-alert>

    <n-card>
      <n-steps :current="currentStep" size="small" style="margin-bottom: 28px">
        <n-step v-for="(t, i) in stepTitles" :key="i" :title="t" />
      </n-steps>

      <n-spin :show="catalogLoading">
        <!-- 1. 运行模式 -->
        <div v-if="currentStep === 1" class="step-body">
          <div class="mode-cards">
            <div
              v-for="m in modeCards"
              :key="m.key"
              class="mode-card hoverable"
              :class="[m.cls, { active: mode === m.key }]"
              @click="pickMode(m)"
            >
              <n-icon :component="m.icon" size="26" class="mode-icon" />
              <div class="mode-title">{{ m.title }}</div>
              <div class="mode-desc">{{ m.desc }}</div>
            </div>
          </div>
          <div v-if="USE_MOCK" class="field-block">
            <div class="field-label">任务类型</div>
            <n-select v-model:value="jobType" :options="typeOptions" style="width: 240px" />
          </div>
        </div>

        <!-- 2. 账号 -->
        <div v-else-if="currentStep === 2" class="step-body">
          <div class="field-label">选择账号（脱敏标识，共 {{ accounts.length }} 个）</div>
          <div style="display: flex; gap: 12px; align-items: center">
            <n-select
              v-model:value="accountId"
              :options="accountOptions"
              placeholder="选择账号（可搜索）"
              filterable
              style="max-width: 420px"
            />
            <n-button tertiary :loading="accountSyncing" @click="onSyncAccounts">
              <template #icon><n-icon :component="RefreshOutline" /></template>
              更新账号
            </n-button>
          </div>
          <n-alert type="info" :bordered="false" style="margin-top: 16px; max-width: 560px">
            账号仅以脱敏标识展示；会话由 AdsPower profile 承载，本系统不存储任何密码。
          </n-alert>
        </div>

        <!-- 3. 站点 -->
        <div v-else-if="currentStep === 3" class="step-body">
          <div class="field-label">选择站点</div>
          <n-select v-model:value="site" :options="siteOptions" placeholder="选择站点" style="max-width: 420px" />
        </div>

        <!-- 4. 品牌 -->
        <div v-else-if="currentStep === 4" class="step-body">
          <div class="field-label">选择品牌（可多选，已选 {{ selectedBrands.length }} / {{ MAX_BRANDS }} 个）</div>
          <n-alert v-if="brandCapReached" type="warning" :bordered="false" style="margin-bottom: 12px; max-width: 560px">
            已达单次任务品牌数上限 {{ MAX_BRANDS }} 个，如需更多品牌请分多个任务创建。
          </n-alert>
          <n-input v-model:value="brandSearch" placeholder="搜索品牌名…" clearable style="max-width: 420px; margin-bottom: 12px" />
          <n-checkbox-group v-model:value="selectedBrands">
            <div class="brand-grid">
              <div
                v-for="b in filteredBrands"
                :key="b.name"
                class="brand-item hoverable"
                :class="{ disabled: !b.packReady || (brandCapReached && !selectedBrands.includes(b.name)) }"
              >
                <n-checkbox
                  :value="b.name"
                  :disabled="!b.packReady || (brandCapReached && !selectedBrands.includes(b.name))"
                >
                  <span class="brand-item-name">{{ b.name }}</span>
                </n-checkbox>
                <n-tag v-if="!b.packReady" size="tiny" type="warning" :bordered="false">品牌包未齐备</n-tag>
              </div>
            </div>
          </n-checkbox-group>
          <n-empty v-if="filteredBrands.length === 0" description="没有匹配的品牌" size="small" style="margin-top: 24px" />
        </div>

        <!-- 5. 前置条件检查 -->
        <div v-else-if="currentStep === 5" class="step-body">
          <n-spin :show="precheckRunning">
            <div v-if="precheckItems.length > 0" class="precheck-list">
              <div v-for="item in precheckItems" :key="item.key" class="precheck-row">
                <n-tag :type="precheckStatusType[item.status]" size="small" :bordered="false" style="width: 72px; justify-content: center">
                  {{ item.status === 'passed' ? '通过' : item.status === 'warning' ? '警告' : '未通过' }}
                </n-tag>
                <div>
                  <div class="precheck-label">{{ item.label }}</div>
                  <div class="precheck-detail">{{ item.detail }}</div>
                </div>
              </div>
            </div>
            <n-alert v-if="precheckDone && precheckHasFailure" type="error" style="margin-top: 16px">
              存在未通过项。仍可继续查看预检结果，但失败的品牌会在执行时被拦截。
            </n-alert>
          </n-spin>
        </div>

        <!-- 6. 预检结果 / 提交 -->
        <div v-else-if="currentStep === 6" class="step-body">
          <n-card size="small" title="任务摘要" style="max-width: 640px">
            <div class="summary-rows">
              <div class="summary-row"><span>运行模式</span>
                <n-tag size="small" :bordered="false" :type="mode === 'submit' ? 'error' : mode === 'dry_run' ? 'info' : 'default'">
                  {{ mode === 'submit' ? '真实提交' : mode === 'dry_run' ? 'Dry-run' : '诊断' }}
                </n-tag>
              </div>
              <div class="summary-row"><span>账号</span><b>{{ accountLabel }}</b></div>
              <div class="summary-row"><span>站点</span><b>{{ site }}</b></div>
              <div class="summary-row"><span>品牌</span><b>{{ selectedBrands.join('、') }}</b></div>
              <div class="summary-row"><span>预检</span>
                <b :style="{ color: precheckHasFailure ? '#d03050' : '#18a058' }">
                  {{ precheckHasFailure ? '存在未通过项' : '全部通过（警告不影响执行）' }}
                </b>
              </div>
            </div>
          </n-card>

          <template v-if="mode === 'submit'">
            <n-alert type="error" :bordered="false" style="margin-top: 16px; max-width: 640px">
              <template #icon><n-icon :component="WarningOutline" /></template>
              点击「确认真实提交」将直接创建真实提交任务并立即入队：自动化会真实登录 Seller Central
              提交申请，不可撤销。提交前会有一次最终确认弹窗。
            </n-alert>
            <n-checkbox v-model:checked="riskAccepted" style="margin-top: 14px">
              我已知晓这将真实提交 Amazon 5461 申请，且品牌包、发票与声明文件已人工核对
            </n-checkbox>
            <template v-if="blockedChecks">
              <n-alert type="error" title="服务端预检阻断" style="margin-top: 16px; max-width: 640px">
                以下阻断项未通过，服务端已拒绝提交。请处理后重新提交。
              </n-alert>
              <n-card size="small" style="margin-top: 12px; max-width: 640px">
                <SubmitPreflightList :checks="blockedChecks" />
              </n-card>
            </template>
          </template>
          <n-alert v-else type="success" :bordered="false" style="margin-top: 16px; max-width: 640px">
            {{ mode === 'dry_run' ? 'Dry-run 不会点击最终提交按钮，可放心执行。' : '诊断模式只读，不会修改任何页面状态。' }}
          </n-alert>
        </div>
      </n-spin>

      <div class="wizard-footer">
        <n-button v-if="currentStep > 1" @click="back">上一步</n-button>
        <div style="flex: 1" />
        <n-button v-if="currentStep < 6" type="primary" :disabled="!canGoNext()" @click="next">
          {{ currentStep === 4 ? '下一步（开始检查）' : '下一步' }}
        </n-button>
        <n-button
          v-else
          :type="mode === 'submit' ? 'error' : 'primary'"
          :loading="submitting"
          :disabled="mode === 'submit' && !riskAccepted"
          @click="submit"
        >
          {{ mode === 'submit' ? '确认真实提交' : '创建任务' }}
        </n-button>
      </div>
    </n-card>
  </div>
</template>

<style scoped>
.wizard-container {
  max-width: 980px;
}

.step-body {
  min-height: 300px;
}

.mode-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}

.mode-card {
  border: 2px solid rgba(128, 128, 128, 0.2);
  border-radius: 10px;
  padding: 18px;
  cursor: pointer;
}

.mode-card.active.mode-diagnose {
  border-color: #8a919e;
  background: rgba(138, 145, 158, 0.08);
}

.mode-card.active.mode-dryrun {
  border-color: #2080f0;
  background: rgba(32, 128, 240, 0.08);
}

.mode-card.active.mode-real {
  border-color: #d03050;
  background: rgba(208, 48, 80, 0.08);
}

.mode-icon {
  color: #8a919e;
}

.mode-card.mode-real .mode-icon {
  color: #d03050;
}

.mode-card.mode-dryrun .mode-icon {
  color: #2080f0;
}

.mode-title {
  font-weight: 700;
  font-size: 16px;
  margin-top: 8px;
}

.mode-desc {
  font-size: 13px;
  opacity: 0.7;
  margin-top: 6px;
  line-height: 1.6;
}

.field-block {
  margin-top: 22px;
}

.field-label {
  font-weight: 600;
  margin-bottom: 10px;
}

.brand-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
}

.brand-item {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid rgba(128, 128, 128, 0.2);
  border-radius: 8px;
  padding: 8px 12px;
}

.brand-item.disabled {
  opacity: 0.6;
}

.brand-item-name {
  font-weight: 500;
}

.precheck-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.precheck-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.precheck-label {
  font-weight: 600;
}

.precheck-detail {
  font-size: 13px;
  opacity: 0.7;
  margin-top: 2px;
}

.summary-rows {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.summary-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  font-size: 14px;
}

.summary-row > span {
  opacity: 0.6;
  flex-shrink: 0;
}

.summary-row > b {
  text-align: right;
  word-break: break-all;
}

.wizard-footer {
  display: flex;
  margin-top: 28px;
  padding-top: 16px;
  border-top: 1px solid rgba(128, 128, 128, 0.15);
}
</style>

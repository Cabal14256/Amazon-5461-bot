<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NInputNumber,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import { getEffectiveSettings, saveEffectiveSettings } from '@/api'
import { ApiError } from '@/api/http'
import type { EffectiveSettings, SettingApplyMode, SettingField } from '@/types'

const dialog = useDialog()
const message = useMessage()

const loading = ref(true)
const saving = ref(false)
const snapshot = ref<EffectiveSettings | null>(null)
const pending = ref<Record<string, number>>({})
const restartRequired = ref<string[]>([])

const applyModeLabel: Record<SettingApplyMode, string> = {
  immediate: '立即生效',
  new_job: '新启动任务生效',
  restart_required: '重启服务生效',
}

const applyModeType: Record<SettingApplyMode, 'success' | 'info' | 'warning'> = {
  immediate: 'success',
  new_job: 'info',
  restart_required: 'warning',
}

const editableGroups = computed(() => {
  const groups = new Map<string, SettingField[]>()
  for (const field of snapshot.value?.fields ?? []) {
    if (!field.editable) continue
    const items = groups.get(field.group) ?? []
    items.push(field)
    groups.set(field.group, items)
  }
  return Array.from(groups, ([name, fields]) => ({ name, fields }))
})

const readOnlyFields = computed(() => (snapshot.value?.fields ?? []).filter((field) => !field.editable))

const changedFields = computed(() => {
  return (snapshot.value?.fields ?? []).filter((field) => {
    return field.editable && typeof field.value === 'number' && pending.value[field.key] !== field.value
  })
})

function hydrate(next: EffectiveSettings) {
  snapshot.value = next
  pending.value = Object.fromEntries(
    next.fields
      .filter((field) => field.editable && typeof field.value === 'number')
      .map((field) => [field.key, field.value as number]),
  )
}

async function loadSettings() {
  loading.value = true
  try {
    hydrate(await getEffectiveSettings())
  } catch (error) {
    message.error(`读取设置失败：${error instanceof Error ? error.message : String(error)}`)
  } finally {
    loading.value = false
  }
}

function setValue(field: SettingField, value: number | null) {
  if (value !== null) pending.value[field.key] = value
}

function discardChanges() {
  if (snapshot.value) hydrate(snapshot.value)
  restartRequired.value = []
}

function confirmSave() {
  if (!snapshot.value || changedFields.value.length === 0) return
  const summary = changedFields.value
    .map((field) => `${field.label}：${field.value} → ${pending.value[field.key]}${field.unit}`)
    .join('；')
  dialog.warning({
    title: '确认保存系统设置',
    content: summary,
    positiveText: '确认保存',
    negativeText: '取消',
    onPositiveClick: () => saveChanges(),
  })
}

async function saveChanges() {
  if (!snapshot.value) return
  saving.value = true
  const changes = Object.fromEntries(
    changedFields.value.map((field) => [field.key, pending.value[field.key]]),
  )
  try {
    const result = await saveEffectiveSettings(snapshot.value.revision, changes)
    hydrate(result.snapshot)
    restartRequired.value = result.restart_required
    message.success(`已保存 ${Object.keys(result.changed).length} 项设置`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      message.warning('设置已被其他管理员更新，已重新载入，请核对后再保存')
      await loadSettings()
    } else {
      message.error(`保存失败：${error instanceof Error ? error.message : String(error)}`)
    }
  } finally {
    saving.value = false
  }
}

function booleanLabel(value: number | boolean): string {
  return value === true ? '已开启' : value === false ? '已关闭' : String(value)
}

onMounted(() => void loadSettings())
</script>

<template>
  <div class="page-container settings-page">
    <div class="page-header settings-header">
      <div>
        <h1 class="page-title">系统设置</h1>
        <p class="page-subtitle">仅开放安全的时间、频率、超时、次数和容量参数</p>
      </div>
      <div class="header-actions">
        <n-button :disabled="changedFields.length === 0 || saving" @click="discardChanges">放弃修改</n-button>
        <n-button type="primary" :disabled="changedFields.length === 0" :loading="saving" @click="confirmSave">
          保存 {{ changedFields.length > 0 ? `(${changedFields.length})` : '' }}
        </n-button>
      </div>
    </div>

    <n-alert type="info" :bordered="false" style="margin-bottom: 16px">
      保存不会改变正在运行的任务，也不会重算已有 Case 的计划时间。标记为“重启服务生效”的值将在相关后台服务重启后使用。
    </n-alert>
    <n-alert v-if="restartRequired.length > 0" type="warning" title="部分设置需要重启服务" style="margin-bottom: 16px">
      已保存 {{ restartRequired.length }} 项需重启配置；本页面不会自动重启服务。
    </n-alert>

    <n-spin :show="loading">
      <div class="settings-groups">
        <n-card v-for="group in editableGroups" :key="group.name" :title="group.name" size="small">
          <div class="settings-grid">
            <div v-for="field in group.fields" :key="field.key" class="setting-row">
              <div class="setting-copy">
                <div class="setting-title">
                  <span>{{ field.label }}</span>
                  <n-tag
                    size="small"
                    :bordered="false"
                    :type="applyModeType[field.apply_mode]"
                  >
                    {{ applyModeLabel[field.apply_mode] }}
                  </n-tag>
                  <n-tag v-if="changedFields.some((item) => item.key === field.key)" size="small" type="warning">
                    已修改
                  </n-tag>
                </div>
                <div class="setting-description">{{ field.description }}</div>
                <div class="setting-meta">
                  建议值 {{ field.default }}{{ field.unit }}；允许范围 {{ field.minimum }}–{{ field.maximum }}{{ field.unit }}
                </div>
              </div>
              <div class="setting-control">
                <n-input-number
                  :value="pending[field.key]"
                  :min="field.minimum ?? undefined"
                  :max="field.maximum ?? undefined"
                  :step="field.step ?? undefined"
                  :precision="field.value_type === 'integer' ? 0 : undefined"
                  @update:value="(value) => setValue(field, value)"
                />
                <span class="unit">{{ field.unit }}</span>
              </div>
            </div>
          </div>
        </n-card>

        <n-card title="受保护与未接线配置（只读）" size="small">
          <div class="readonly-grid">
            <div v-for="field in readOnlyFields" :key="field.key" class="readonly-row">
              <div>
                <div class="setting-title">
                  <span>{{ field.label }}</span>
                  <n-tag size="small" :type="field.status === 'unwired' ? 'warning' : 'default'">
                    {{ field.status === 'unwired' ? '当前未接线' : '受保护' }}
                  </n-tag>
                </div>
                <div class="setting-description">{{ field.description }}</div>
              </div>
              <n-tag :type="field.value === true ? 'success' : 'default'">{{ booleanLabel(field.value) }}</n-tag>
            </div>
          </div>
        </n-card>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.settings-page { max-width: 1120px; }
.settings-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.header-actions { display: flex; gap: 10px; }
.settings-groups { display: grid; gap: 16px; }
.settings-grid, .readonly-grid { display: grid; gap: 0; }
.setting-row, .readonly-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  align-items: center;
  padding: 15px 0;
  border-bottom: 1px solid rgba(128, 128, 128, 0.16);
}
.setting-row:last-child, .readonly-row:last-child { border-bottom: 0; }
.setting-title { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-weight: 600; }
.setting-description { margin-top: 5px; opacity: 0.72; font-size: 13px; }
.setting-meta { margin-top: 5px; opacity: 0.55; font-size: 12px; }
.setting-control { display: flex; align-items: center; gap: 8px; min-width: 230px; }
.setting-control :deep(.n-input-number) { width: 180px; }
.unit { min-width: 36px; opacity: 0.7; font-size: 13px; }
@media (max-width: 760px) {
  .settings-header, .setting-row, .readonly-row { grid-template-columns: 1fr; display: grid; }
  .header-actions { justify-content: flex-end; }
  .setting-control { min-width: 0; }
}
</style>

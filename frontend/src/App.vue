<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import {
  AddCircleOutline,
  ChatbubblesOutline,
  ConstructOutline,
  FileTrayFullOutline,
  GridOutline,
  ListOutline,
  LogOutOutline,
  MoonOutline,
  PersonCircleOutline,
  RepeatOutline,
  SettingsOutline,
  SunnyOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import type { MenuOption } from 'naive-ui'
import {
  NButton,
  NBadge,
  NConfigProvider,
  NDialogProvider,
  NIcon,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NMenu,
  NMessageProvider,
  NTag,
  NTooltip,
  dateZhCN,
  zhCN,
} from 'naive-ui'
import { themeOverrides } from '@/theme'
import { useThemeStore } from '@/stores/theme'
import { useAuthStore } from '@/stores/auth'
import { USE_MOCK } from '@/api/client'
import { getAuthBlocks } from '@/api'

const themeStore = useThemeStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const isLoginPage = computed(() => route.name === 'login')

function icon(comp: Parameters<typeof h>[0]) {
  return () => h(NIcon, null, { default: () => h(comp) })
}

const menuOptions = computed<MenuOption[]>(() => [
  { label: '系统总览', key: '/', icon: icon(GridOutline) },
  { label: '任务列表', key: '/jobs', icon: icon(ListOutline) },
  { label: '新建任务', key: '/jobs/new', icon: icon(AddCircleOutline) },
  { label: '申请记录', key: '/applications', icon: icon(FileTrayFullOutline) },
  { label: '重新申请', key: '/reapplications', icon: icon(RepeatOutline) },
  { label: 'Case 跟进', key: '/case-followups', icon: icon(ChatbubblesOutline) },
  { label: '待人工处理', key: '/pending', icon: icon(TimeOutline) },
  { label: '修复中心', key: '/repair', icon: icon(ConstructOutline) },
  ...(auth.isAdmin ? [{ label: '系统设置', key: '/settings', icon: icon(SettingsOutline) }] : []),
])

const activeKey = computed(() => {
  if (route.path.startsWith('/jobs/new')) return '/jobs/new'
  if (route.path.startsWith('/jobs')) return '/jobs'
  return route.path
})

function onMenu(key: string) {
  router.push(key)
}

const ROLE_LABEL: Record<string, string> = {
  viewer: '只读',
  operator: '操作员',
  reviewer: '审核员',
  admin: '管理员',
}

const userLabel = computed(() => auth.user?.display_name || auth.user?.username || '')
const roleLabel = computed(() => ROLE_LABEL[auth.user?.role ?? ''] ?? auth.user?.role ?? '')
const authBlockCount = ref(0)
let authPoll: ReturnType<typeof setInterval> | null = null

async function loadAuthBlockCount() {
  if (USE_MOCK || !auth.user || isLoginPage.value) {
    authBlockCount.value = 0
    return
  }
  try {
    authBlockCount.value = (await getAuthBlocks()).length
  } catch {
    // Keep the navigation usable; PendingPage shows its own loading/error state.
  }
}

watch(() => auth.user?.id, () => void loadAuthBlockCount())
onMounted(() => {
  void loadAuthBlockCount()
  authPoll = setInterval(() => void loadAuthBlockCount(), 15_000)
})
onUnmounted(() => {
  if (authPoll) clearInterval(authPoll)
})

async function onLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <n-config-provider :theme="themeStore.theme" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-dialog-provider>
        <!-- 登录页：裸布局，不渲染顶栏 -->
        <RouterView v-if="isLoginPage" />
        <n-layout v-else class="app-shell">
          <n-layout-header bordered class="top-bar">
            <div class="brand" @click="router.push('/')">
              <span class="brand-mark">5461</span>
              <span class="brand-name">内网控制台</span>
            </div>
            <n-menu
              mode="horizontal"
              :value="activeKey"
              :options="menuOptions"
              class="top-menu"
              @update:value="onMenu"
            />
            <div class="top-right">
              <n-badge v-if="authBlockCount > 0" :value="authBlockCount" :max="99" type="error">
                <n-button size="small" type="warning" secondary @click="router.push('/pending')">
                  账号需要重新登录
                </n-button>
              </n-badge>
              <n-tag v-if="USE_MOCK" size="small" :bordered="false" type="info" class="env-tag">Mock 环境</n-tag>
              <n-button quaternary circle @click="themeStore.toggle()">
                <template #icon>
                  <n-icon :component="themeStore.isDark ? SunnyOutline : MoonOutline" />
                </template>
              </n-button>
              <n-tooltip v-if="USE_MOCK" trigger="hover">
                <template #trigger>
                  <n-tag :bordered="false" size="small">
                    <template #icon>
                      <n-icon :component="PersonCircleOutline" />
                    </template>
                    Admin（Mock 角色）
                  </n-tag>
                </template>
                角色仅用于界面演示；前端隐藏按钮 ≠ 权限控制
              </n-tooltip>
              <template v-else>
                <n-tag :bordered="false" size="small">
                  <template #icon>
                    <n-icon :component="PersonCircleOutline" />
                  </template>
                  {{ userLabel }}
                </n-tag>
                <n-tag size="small" :bordered="false" type="warning">{{ roleLabel }}</n-tag>
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <n-button quaternary circle @click="onLogout">
                      <template #icon>
                        <n-icon :component="LogOutOutline" />
                      </template>
                    </n-button>
                  </template>
                  退出登录
                </n-tooltip>
              </template>
            </div>
          </n-layout-header>
          <n-layout-content class="app-content">
            <RouterView />
          </n-layout-content>
        </n-layout>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}

.top-bar {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 0 24px;
  height: 56px;
  position: sticky;
  top: 0;
  z-index: 10;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
  cursor: pointer;
  flex-shrink: 0;
}

.brand-mark {
  font-family: ui-monospace, SFMono-Regular, 'Cascadia Mono', Consolas, monospace;
  font-weight: 800;
  font-size: 18px;
  color: #2080f0;
}

.brand-name {
  font-size: 14px;
  font-weight: 600;
  opacity: 0.85;
}

.top-menu {
  flex: 1;
  min-width: 0;
}

.top-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.env-tag {
  font-weight: 600;
}

.app-content {
  min-height: calc(100vh - 56px);
}
</style>

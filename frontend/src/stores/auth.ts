import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getMe, login as apiLogin, logout as apiLogout } from '@/api'
import type { WebUser } from '@/types'

/** 与后端 src/web/deps.py ROLE_ORDER 对齐 */
const ROLE_ORDER: Record<string, number> = { viewer: 0, operator: 1, reviewer: 2, admin: 3 }

/** 登录态：初始化时通过 GET /api/auth/me 恢复会话；Mock 模式下不会被路由守卫触发 */
export const useAuthStore = defineStore('auth', () => {
  const user = ref<WebUser | null>(null)
  const initialized = ref(false)

  const role = computed(() => user.value?.role ?? '')
  /** operator / reviewer / admin：可创建任务、请求停止 */
  const isOperatorPlus = computed(() => (ROLE_ORDER[role.value] ?? -1) >= ROLE_ORDER.operator)
  /** reviewer / admin：可确认真实提交审批（阶段 4） */
  const isReviewerPlus = computed(() => (ROLE_ORDER[role.value] ?? -1) >= ROLE_ORDER.reviewer)
  /** admin：可强制终止 */
  const isAdmin = computed(() => (ROLE_ORDER[role.value] ?? -1) >= ROLE_ORDER.admin)

  async function fetchMe() {
    try {
      user.value = await getMe()
    } catch {
      user.value = null
    } finally {
      initialized.value = true
    }
  }

  async function login(username: string, password: string) {
    user.value = await apiLogin(username, password)
    initialized.value = true
  }

  async function logout() {
    try {
      await apiLogout()
    } catch {
      // 后端不可达也照常清理本地登录态
    }
    user.value = null
  }

  /** 会话过期（401）时由全局处理器调用，不请求后端 */
  function clear() {
    user.value = null
  }

  return { user, initialized, role, isOperatorPlus, isReviewerPlus, isAdmin, fetchMe, login, logout, clear }
})

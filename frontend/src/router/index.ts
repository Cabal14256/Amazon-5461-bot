import { createRouter, createWebHistory } from 'vue-router'
import { USE_MOCK } from '@/api/client'
import { setUnauthorizedHandler } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/pages/LoginPage.vue'), meta: { title: '登录' } },
    { path: '/', name: 'overview', component: () => import('@/pages/OverviewPage.vue'), meta: { title: '系统总览' } },
    { path: '/jobs', name: 'jobs', component: () => import('@/pages/JobListPage.vue'), meta: { title: '任务列表' } },
    { path: '/jobs/new', name: 'job-new', component: () => import('@/pages/NewJobWizard.vue'), meta: { title: '新建任务' } },
    { path: '/jobs/:id', name: 'job-detail', component: () => import('@/pages/JobDetailPage.vue'), meta: { title: '任务详情' } },
    { path: '/applications', name: 'applications', component: () => import('@/pages/ApplicationsPage.vue'), meta: { title: '申请记录' } },
    { path: '/reapplications', name: 'reapplications', component: () => import('@/pages/ReapplicationsPage.vue'), meta: { title: '重新申请' } },
    { path: '/case-followups', name: 'case-followups', component: () => import('@/pages/CaseFollowUpsPage.vue'), meta: { title: 'Case 跟进' } },
    { path: '/pending', name: 'pending', component: () => import('@/pages/PendingPage.vue'), meta: { title: '待人工处理' } },
    { path: '/repair', name: 'repair', component: () => import('@/pages/RepairCenterPage.vue'), meta: { title: '修复中心' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  // Mock 演示模式不做登录校验
  if (USE_MOCK) return true
  const auth = useAuthStore()
  if (!auth.initialized) await auth.fetchMe()
  if (to.name === 'login') return auth.user ? { path: '/' } : true
  if (!auth.user) return { name: 'login', query: { redirect: to.fullPath } }
  return true
})

router.afterEach((to) => {
  document.title = `${to.meta.title ?? '页面'} · Amazon 5461 内网控制台`
})

// API 任一请求 401（会话过期/被禁用）=> 清登录态并跳登录页，带 redirect query
setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  auth.clear()
  if (router.currentRoute.value.name !== 'login') {
    void router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})

export default router

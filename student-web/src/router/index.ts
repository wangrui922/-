/**
 * 学生端路由（T04 · F13 重构为嵌套布局）。
 *
 * 结构：
 *   /login              独立公开页
 *   /  (StudentLayout)  总布局：左导航 + 顶栏，children 四个分区
 *     /dashboard        学习画像（默认首页）
 *     /ai               AI 老师
 *     /practice         题库练习（题库消费层 · F1，需登录，**不加** meta.public）
 *     /mistakes         错题本
 *
 * 守卫口径：除 meta.public 的页面外，无 token 一律跳 /login（带 redirect 回跳）。
 * 页面组件一律懒加载，避免与 api/http.ts 形成模块循环。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { isLoggedIn } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/StudentLayout.vue'),
    redirect: { name: 'dashboard' },
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '学习画像' },
      },
      {
        path: 'ai',
        name: 'ai-teacher',
        component: () => import('@/views/AiTeacher.vue'),
        meta: { title: 'AI 老师' },
      },
      {
        path: 'practice',
        name: 'practice',
        component: () => import('@/views/Practice.vue'),
        meta: { title: '练习' },
      },
      {
        path: 'mistakes',
        name: 'mistakes',
        component: () => import('@/views/Mistakes.vue'),
        meta: { title: '错题本' },
      },
    ],
  },
  {
    path: '/r/:token',
    name: 'share-report',
    component: () => import('@/views/report/ShareReport.vue'),
    meta: { public: true, title: '家长周报' },
  },
  { path: '/:pathMatch(.*)*', redirect: { name: 'dashboard' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to) => {
  const isPublic = Boolean(to.meta.public)

  if (!isPublic && !isLoggedIn()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && isLoggedIn()) {
    return { name: 'dashboard' }
  }
  return true
})

router.afterEach((to) => {
  const title = to.meta.title as string | undefined
  document.title = title ? `${title} · AI 数学教练` : 'AI 数学教练'
})

export default router

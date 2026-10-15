/**
 * 学生端路由（T04 · F13 重构为嵌套布局）。
 *
 * 结构：
 *   /login              独立公开页
 *   /  (StudentLayout)  总布局：左导航 + 顶栏，children 四个分区
 *     /dashboard        学习画像（默认首页）
 *     /ai               AI 老师
 *     /practice         题库练习（题库消费层 · F1，需登录，**不加** meta.public）
 *     /today-plan       今日学习计划（T07 自适应编排，只读，不加 meta.public）
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
        meta: { title: '小玥老师' },
      },
      {
        path: 'practice',
        name: 'practice',
        component: () => import('@/views/Practice.vue'),
        meta: { title: '练习' },
      },
      {
        path: 'today-plan',
        name: 'today-plan',
        component: () => import('@/views/TodayPlan.vue'),
        meta: { title: '今日计划' },
      },
      {
        path: 'mistakes',
        name: 'mistakes',
        component: () => import('@/views/Mistakes.vue'),
        meta: { title: '错题本' },
      },
      {
        path: 'subscription',
        name: 'subscription',
        component: () => import('@/views/Subscription.vue'),
        meta: { title: '会员中心' },
      },
      {
        path: 'review',
        name: 'review',
        component: () => import('@/views/Review.vue'),
        meta: { title: '今日复习' },
      },
      {
        path: 'game',
        name: 'game-hub',
        component: () => import('@/views/GameHub.vue'),
        meta: { title: '游戏大厅' },
      },
      {
        path: 'game/speed',
        name: 'speed-challenge',
        component: () => import('@/views/game/SpeedChallenge.vue'),
        meta: { title: '限时挑战' },
      },
      {
        path: 'game/leaderboard',
        name: 'game-leaderboard',
        component: () => import('@/views/game/Leaderboard.vue'),
        meta: { title: '排行榜' },
      },
      {
        path: 'game/battle',
        name: 'math-battle',
        component: () => import('@/views/game/MathBattle.vue'),
        meta: { title: '回合战斗' },
      },
      {
        path: 'game/badges',
        name: 'badge-wall',
        component: () => import('@/views/game/BadgeWall.vue'),
        meta: { title: '徽章墙' },
      },
      {
        path: 'game/scenes',
        name: 'scene-quiz',
        component: () => import('@/views/game/SceneQuiz.vue'),
        meta: { title: '生活场景题库' },
      },
    ],
  },
  {
    path: '/parent/login',
    name: 'parent-login',
    component: () => import('@/views/parent/ParentLogin.vue'),
    meta: { public: true, title: '家长登录' },
  },
  {
    path: '/parent/children',
    name: 'parent-children',
    component: () => import('@/views/parent/Children.vue'),
    meta: { title: '我的孩子' },
  },
  {
    path: '/parent/bind',
    name: 'parent-bind',
    component: () => import('@/views/parent/BindChild.vue'),
    meta: { title: '绑定孩子' },
  },
  {
    path: '/parent/children/:sid',
    name: 'parent-child-detail',
    component: () => import('@/views/parent/ChildDetail.vue'),
    meta: { title: '孩子学习档案' },
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
    // 家长专区未登录 -> 家长登录页；其余 -> 学生登录页
    if (to.path.startsWith('/parent')) {
      return { name: 'parent-login', query: { redirect: to.fullPath } }
    }
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

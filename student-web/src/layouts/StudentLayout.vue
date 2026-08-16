<script setup lang="ts">
/**
 * 学生端总布局（T04 · F5）：左侧导航 + 顶栏 + 路由出口。
 *
 * - 左导航：AI 老师 / 练习 / 错题本 / 学习画像（学习画像为默认首页）
 * - 顶栏：当前页标题 + 退出
 * 旧页面（AiTeacher / Mistakes）已去掉自身顶栏，统一由本布局承载导航与退出。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { logout } from '@/stores/auth'
import { BANDS, segmentState, setBand, type Segment } from '@/stores/segment'
import TutorAvatar from '@/components/TutorAvatar.vue'

interface NavItem {
  name: string
  to: { name: string }
  label: string
  /** 内联 SVG path（24x24 viewBox），避免引入图标库 */
  icon: string
}

const NAV_ITEMS: readonly NavItem[] = [
  {
    name: 'ai-teacher',
    to: { name: 'ai-teacher' },
    label: '小玥老师',
    icon: 'M12 3v2m0 14v2m9-9h-2M5 12H3m14.5-5.5L16 8m-8 8l-2.5 2.5m11 0L16 16m-8-8L5.5 5.5M12 8a4 4 0 100 8 4 4 0 000-8z',
  },
  {
    name: 'practice',
    to: { name: 'practice' },
    label: '练习',
    icon: 'M12 6.5v11m0-11a5.5 5.5 0 00-4-1.7H5a1 1 0 00-1 1v10a1 1 0 001 1h3a5.5 5.5 0 014 1.7m0-13a5.5 5.5 0 014-1.7h3a1 1 0 011 1v10a1 1 0 01-1 1h-3a5.5 5.5 0 00-4 1.7',
  },
  {
    name: 'mistakes',
    to: { name: 'mistakes' },
    label: '错题本',
    icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4',
  },
  {
    name: 'dashboard',
    to: { name: 'dashboard' },
    label: '学习画像',
    icon: 'M3 3v18h18M7 14l3-4 3 3 4-6',
  },
]

const router = useRouter()
const route = useRoute()

const pageTitle = computed<string>(() => (route.meta.title as string) || 'AI 数学教练')

/** 当前路由是否命中某导航项（用于高亮）。 */
function isActiveNav(name: string): boolean {
  return route.name === name
}

function handleLogout(): void {
  logout()
  void router.replace({ name: 'login' })
}

/** 顶栏学段切换：点击即设定并持久化（#68 学段 UI）。传 null 表示「不限」。 */
function handleBand(band: Segment | null): void {
  // 再次点击当前学段 → 切回「不限」
  setBand(segmentState.band === band ? null : band)
}
</script>

<template>
  <div class="flex h-screen w-full overflow-hidden bg-slate-50">
    <!-- 左侧导航 -->
    <nav class="flex w-16 shrink-0 flex-col items-center gap-1 border-r border-slate-100 bg-white py-3 sm:w-20">
      <div class="mb-3">
        <TutorAvatar :size="36" />
      </div>
      <router-link
        v-for="item in NAV_ITEMS"
        :key="item.name"
        :to="item.to"
        class="flex w-full flex-col items-center gap-1 py-2 text-xs font-medium transition"
        :class="isActiveNav(item.name) ? 'text-brand-600' : 'text-slate-400 hover:text-slate-600'"
      >
        <svg
          class="h-6 w-6"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
          viewBox="0 0 24 24"
        >
          <path :d="item.icon" />
        </svg>
        <span>{{ item.label }}</span>
      </router-link>
    </nav>

    <!-- 右侧：顶栏 + 内容 -->
    <div class="flex min-w-0 flex-1 flex-col">
      <header
        class="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-100 bg-white px-4"
      >
        <h1 class="truncate text-sm font-bold text-slate-800">{{ pageTitle }}</h1>

        <!-- 学段切换器（#68）：小学/初中/高中/不限，全局持久化 -->
        <div class="flex items-center gap-1 rounded-full bg-slate-100 p-0.5">
          <button
            v-for="band in BANDS"
            :key="band"
            type="button"
            class="rounded-full px-2.5 py-1 text-xs font-medium transition"
            :class="segmentState.band === band ? 'bg-white text-brand-600 shadow-sm' : 'text-slate-500 active:text-slate-700'"
            @click="handleBand(band)"
          >
            {{ band }}
          </button>
          <button
            type="button"
            class="rounded-full px-2.5 py-1 text-xs font-medium transition"
            :class="segmentState.band === null ? 'bg-white text-brand-600 shadow-sm' : 'text-slate-400 active:text-slate-600'"
            @click="handleBand(null)"
          >
            不限
          </button>
        </div>

        <button
          type="button"
          class="shrink-0 text-sm text-slate-400 transition active:text-slate-600"
          @click="handleLogout"
        >
          退出
        </button>
      </header>

      <main class="min-h-0 flex-1 overflow-hidden">
        <router-view />
      </main>
    </div>
  </div>
</template>

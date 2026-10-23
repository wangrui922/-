<script setup lang="ts">
/**
 * 学生端总布局（移动端 App 化）：顶部细栏（mascot + 标题 + 连击 + 退出）
 * + 底部毛玻璃 Tab 栏（4 大入口，激活高亮）。
 *
 * 去掉原左导航，整体更像「真 App」，更适合中小学生单手使用。
 * 底部 Tab 栏 5 大入口：小玥老师 / 练习 / 今日计划 / 错题宝藏 / 学习画像。
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { logout } from '@/stores/auth'
import { BANDS, segmentState, setBand, type Segment } from '@/stores/segment'
import { useStreak } from '@/stores/streak'
import TutorAvatar from '@/components/TutorAvatar.vue'
import GuardianConsentBanner from '@/components/GuardianConsentBanner.vue'

interface NavItem {
  name: string
  to: { name: string }
  label: string
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
    name: 'today-plan',
    to: { name: 'today-plan' },
    label: '今日计划',
    icon: 'M8 2v3m8-3v3M3.5 8.5h17M6 5.5h12a2.5 2.5 0 012.5 2.5v10a2.5 2.5 0 01-2.5 2.5H6A2.5 2.5 0 013.5 18V8A2.5 2.5 0 016 5.5zM8 13h2m2 0h4M8 16.5h4',
  },
  {
    name: 'mistakes',
    to: { name: 'mistakes' },
    label: '错题宝藏',
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
const { streakDays } = useStreak()

function isActiveNav(name: string): boolean {
  return route.name === name
}

function handleLogout(): void {
  logout()
  void router.replace({ name: 'login' })
}

/** 学段切换：再次点击当前学段 → 切回「不限」。 */
function handleBand(band: Segment | null): void {
  setBand(segmentState.band === band ? null : band)
}
</script>

<template>
  <div class="flex h-screen w-full flex-col overflow-hidden">
    <!-- 顶部细栏 -->
    <header
      class="z-20 flex shrink-0 items-center gap-2 border-b border-white/60 bg-white/70 px-4 py-2.5 backdrop-blur-md"
    >
      <TutorAvatar :size="34" />
      <h1 class="min-w-0 flex-1 truncate text-base font-extrabold text-ink">{{ pageTitle }}</h1>

      <div
        v-if="streakDays > 0"
        class="flex items-center gap-1 rounded-full bg-gradient-to-r from-amber-400 to-coral-400 px-2.5 py-1 text-xs font-extrabold text-white shadow-soft"
      >
        <span class="animate-bounce-soft">🔥</span>{{ streakDays }}
      </div>

      <button
        type="button"
        class="flex h-8 w-8 items-center justify-center rounded-full text-ink-faint transition active:scale-90"
        aria-label="退出登录"
        @click="handleLogout"
      >
        <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9" />
        </svg>
      </button>
    </header>

    <!-- 学段切换（小学/初中/高中/不限） -->
    <div class="z-10 flex shrink-0 items-center justify-center gap-1 bg-white/40 px-3 py-1.5 backdrop-blur-sm">
      <button
        v-for="band in BANDS"
        :key="band"
        type="button"
        class="rounded-full px-3 py-1 text-xs font-semibold transition"
        :class="segmentState.band === band ? 'bg-brand-500 text-white shadow-soft' : 'bg-white/70 text-ink-soft active:text-ink'"
        @click="handleBand(band)"
      >
        {{ band }}
      </button>
      <button
        type="button"
        class="rounded-full px-3 py-1 text-xs font-semibold transition"
        :class="segmentState.band === null ? 'bg-brand-500 text-white shadow-soft' : 'bg-white/70 text-ink-faint active:text-ink'"
        @click="handleBand(null)"
      >
        不限
      </button>
    </div>

    <!-- 监护人同意指引条（V6.5 合规闸门 · B02）：未同意 / 版本失效时常驻，granted / unknown 不渲染 -->
    <GuardianConsentBanner />

    <!-- 内容区 -->
    <main class="min-h-0 flex-1 overflow-hidden">
      <router-view />
    </main>

    <!-- 底部毛玻璃 Tab 栏 -->
    <nav
      class="z-20 flex shrink-0 items-stretch justify-around border-t border-white/60 bg-white/80 px-2 pb-[env(safe-area-inset-bottom)] pt-1.5 backdrop-blur-lg"
    >
      <router-link
        v-for="item in NAV_ITEMS"
        :key="item.name"
        :to="item.to"
        class="relative flex flex-1 flex-col items-center gap-0.5 py-1 text-[11px] font-semibold transition"
        :class="isActiveNav(item.name) ? 'text-brand-600' : 'text-ink-faint'"
      >
        <span
          v-if="isActiveNav(item.name)"
          class="absolute -top-1 h-1.5 w-8 rounded-full bg-gradient-to-r from-brand-500 to-coral-400"
        />
        <span
          class="flex h-9 w-9 items-center justify-center rounded-2xl transition"
          :class="isActiveNav(item.name) ? 'bg-brand-50 shadow-soft' : ''"
        >
          <svg
            class="h-5 w-5"
            fill="none"
            stroke="currentColor"
            stroke-width="1.9"
            stroke-linecap="round"
            stroke-linejoin="round"
            viewBox="0 0 24 24"
          >
            <path :d="item.icon" />
          </svg>
        </span>
        <span>{{ item.label }}</span>
      </router-link>
    </nav>
  </div>
</template>

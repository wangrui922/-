<script setup lang="ts">
/**
 * 学习画像看板（首页）+ 家长周报入口。
 *
 * 视觉升级为「成长伙伴主页」：mascot 问候 hero + 趣味入口 tile + 数据面板。
 * 三路并行加载（掌握度 / 错因 / 推荐），每路独立降级；
 * 数据从 profile store 读取（stores/profile.ts）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import ShareLinkDialog from '@/components/report/ShareLinkDialog.vue'
import ErrorPatternChart from '@/components/profile/ErrorPatternChart.vue'
import MasteryPanel from '@/components/profile/MasteryPanel.vue'
import RecommendationList from '@/components/profile/RecommendationList.vue'
import KnowledgeStarMap from '@/components/profile/KnowledgeStarMap.vue'
import AbilityRadar from '@/components/profile/AbilityRadar.vue'
import tutorFull from '@/assets/tutor-yueyue-full.png'
import { useStreak } from '@/stores/streak'
import { toMessage } from '@/api/http'
import { generateReport } from '@/api/report'
import { loadDashboard, profileState } from '@/stores/profile'
import type { GenerateReportData } from '@/types/report'

const timeLabel = computed<string>(() => {
  const h = new Date().getHours()
  return h < 11 ? '早' : h < 14 ? '中午' : h < 18 ? '下午' : '晚上'
})

interface Tile {
  key: string
  label: string
  desc: string
  grad: string
  icon: string
  to: { name: string }
  badge?: string
}

const tiles: readonly Tile[] = [
  {
    key: 'practice',
    label: '去练习',
    desc: '挑题闯关',
    grad: 'from-brand-500 to-brand-600',
    icon: 'M12 6.5v11m0-11a5.5 5.5 0 00-4-1.7H5a1 1 0 00-1 1v10a1 1 0 001 1h3a5.5 5.5 0 014 1.7m0-13a5.5 5.5 0 014-1.7h3a1 1 0 011 1v10a1 1 0 01-1 1h-3a5.5 5.5 0 00-4 1.7',
    to: { name: 'practice' },
    badge: 'HOT',
  },
  {
    key: 'ai',
    label: '问小玥老师',
    desc: '不懂就问',
    grad: 'from-coral-400 to-coral-500',
    icon: 'M12 3v2m0 14v2m9-9h-2M5 12H3m14.5-5.5L16 8m-8 8l-2.5 2.5m11 0L16 16m-8-8L5.5 5.5M12 8a4 4 0 100 8 4 4 0 000-8z',
    to: { name: 'ai-teacher' },
  },
  {
    key: 'mistakes',
    label: '错题宝藏',
    desc: '挖宝复习',
    grad: 'from-amber-400 to-amber-500',
    icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4',
    to: { name: 'mistakes' },
  },
  {
    key: 'dashboard',
    label: '学习画像',
    desc: '看看进步',
    grad: 'from-mint-400 to-mint-500',
    icon: 'M3 3v18h18M7 14l3-4 3 3 4-6',
    to: { name: 'dashboard' },
  },
]

const router = useRouter()
const { streakDays } = useStreak()

const shareOpen = ref(false)
const generating = ref(false)
const shareData = ref<GenerateReportData | null>(null)
const generateError = ref('')

onMounted(() => {
  void loadDashboard()
})

function onTile(t: Tile): void {
  void router.push(t.to)
}

function handlePracticeKp(payload: { kp_id: string; kp_name: string }): void {
  void router.push({
    name: 'practice',
    query: { kp_id: payload.kp_id, kp_name: payload.kp_name },
  })
}

function handleRetryAll(): void {
  void loadDashboard({ force: true })
}

async function handleGenerateReport(): Promise<void> {
  if (generating.value) return
  generating.value = true
  generateError.value = ''
  try {
    const data = await generateReport({ force: false })
    shareData.value = data
    shareOpen.value = true
  } catch (err) {
    generateError.value = toMessage(err, '周报生成失败，请稍后再试')
  } finally {
    generating.value = false
  }
}
</script>

<template>
  <div class="h-full overflow-y-auto overflow-x-hidden px-4 pb-6 pt-4">
    <div class="space-y-4">
      <!-- hero：小玥老师问候 -->
      <section
        class="relative overflow-hidden rounded-[2rem] bg-gradient-to-br from-brand-500 via-brand-600 to-coral-400 p-5 text-white shadow-float"
      >
        <div class="pointer-events-none absolute -right-6 -top-8 h-28 w-28 rounded-full bg-white/15" />
        <div class="pointer-events-none absolute -bottom-10 -left-4 h-32 w-32 rounded-full bg-white/10" />
        <div class="relative flex items-center gap-3">
          <div class="h-16 w-16 animate-float overflow-hidden rounded-2xl border-2 border-white/60 bg-white/20">
            <img :src="tutorFull" alt="小玥老师" class="h-full w-full object-contain" />
          </div>
          <div class="min-w-0 flex-1">
            <p class="text-sm font-semibold text-white/80">{{ timeLabel }}好呀～我是小玥老师</p>
            <p class="mt-0.5 text-base font-extrabold leading-snug">今天想先练几题，还是看看错题宝藏？</p>
          </div>
          <div
            v-if="streakDays > 0"
            class="flex shrink-0 flex-col items-center rounded-2xl bg-white/20 px-3 py-1.5 backdrop-blur"
          >
            <span class="text-lg font-extrabold leading-none animate-bounce-soft">🔥 {{ streakDays }}</span>
            <span class="text-[10px] text-white/80">连续打卡</span>
          </div>
        </div>
      </section>

      <!-- 趣味入口 -->
      <section class="grid grid-cols-2 gap-3">
        <button
          v-for="t in tiles"
          :key="t.key"
          type="button"
          class="tile animate-fade-up bg-gradient-to-br text-left"
          :class="t.grad"
          @click="onTile(t)"
        >
          <div class="flex items-center justify-between">
            <span class="flex h-10 w-10 items-center justify-center rounded-2xl bg-white/25">
              <svg class="h-5 w-5 text-white" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24">
                <path :d="t.icon" />
              </svg>
            </span>
            <span
              v-if="t.badge"
              class="rounded-full bg-white/30 px-2 py-0.5 text-[10px] font-extrabold tracking-wide"
            >{{ t.badge }}</span>
          </div>
          <div class="mt-3">
            <p class="text-base font-extrabold text-white">{{ t.label }}</p>
            <p class="text-xs font-medium text-white/85">{{ t.desc }}</p>
          </div>
        </button>
      </section>

      <!-- 家长周报 -->
      <section class="relative overflow-hidden rounded-3xl bg-gradient-to-r from-mint-400 to-sky-400 p-4 text-white shadow-soft">
        <div class="pointer-events-none absolute -right-4 -top-4 h-20 w-20 rounded-full bg-white/20" />
        <div class="relative flex items-center justify-between gap-3">
          <div class="min-w-0">
            <h2 class="text-base font-extrabold">📊 家长周报</h2>
            <p class="mt-0.5 text-xs font-medium text-white/85">一键生成本周小结，发给爸爸妈妈</p>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-2xl bg-white px-4 py-2 text-sm font-bold text-mint-600 shadow transition active:scale-95"
            :disabled="generating"
            @click="handleGenerateReport"
          >
            {{ generating ? '生成中…' : '生成' }}
          </button>
        </div>
        <p v-if="generateError" class="relative mt-2 rounded-2xl bg-white/90 px-3 py-2 text-sm font-medium text-red-500">
          {{ generateError }}
        </p>
      </section>

      <!-- 学习数据 -->
      <p class="px-1 pt-1 text-sm font-extrabold text-ink">📈 我的学习数据</p>

      <KnowledgeStarMap
        :data="profileState.mastery"
        :loading="profileState.masteryState.loading"
        :error="profileState.masteryState.error"
        @practice="handlePracticeKp"
      />

      <AbilityRadar
        :data="profileState.radar"
        :loading="profileState.radarState.loading"
        :error="profileState.radarState.error"
      />

      <MasteryPanel
        :data="profileState.mastery"
        :loading="profileState.masteryState.loading"
        :error="profileState.masteryState.error"
      />

      <ErrorPatternChart
        :data="profileState.errorPatterns"
        :loading="profileState.errorPatternsState.loading"
        :error="profileState.errorPatternsState.error"
      />

      <RecommendationList
        :data="profileState.recommendations"
        :loading="profileState.recommendationsState.loading"
        :error="profileState.recommendationsState.error"
        @practice="handlePracticeKp"
      />

      <button type="button" class="btn-ghost w-full" @click="handleRetryAll">
        重新计算画像
      </button>
    </div>

    <ShareLinkDialog :open="shareOpen" :data="shareData" @close="shareOpen = false" />
  </div>
</template>

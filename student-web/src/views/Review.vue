<script setup lang="ts">
/**
 * 今日复习（FSRS 科学复排 · 方向三）。
 *
 * 交互设计（延续 Dashboard 游戏化 HUD 风格）：
 *   - 顶部：小玥老师 + 今日日期 + 连续打卡火焰
 *   - 进度环：已完成 / 今日待复习（SVG 圆环，完成时发金光）
 *   - 复习卡片流：题干用 renderMath 渲染 LaTeX；FSRS 四键
 *     Again / Hard / Good / Easy 评分，提交即下一张（进度环实时增长）
 *   - 全部完成：定格统计页（完成张数、平均评分、明日待复习提示）
 *   - 空态：今日没有待复习卡片 → 引导回学习画像
 *   - 降级：加载失败 / 提交失败均有可重试的错误态
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { renderMath } from '@/utils/renderMath'
import { useStreak } from '@/stores/streak'
import { getDailyReview, submitReview, type ReviewCard } from '@/api/review'
import TutorAvatar from '@/components/TutorAvatar.vue'

const router = useRouter()
const { streakDays } = useStreak()
const nowLabel = new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' })

/* ---------------- 页面状态机 ---------------- */
type Phase = 'loading' | 'reviewing' | 'done' | 'empty' | 'error'
const phase = ref<Phase>('loading')
const errorMsg = ref<string>('')

/* ---------------- 复习数据 ---------------- */
const todayTotal = ref(0)
const cards = ref<ReviewCard[]>([])
const index = ref(0)
const doneCount = ref(0)
const ratingSum = ref(0)
const submitting = ref(false)
const submitError = ref<string>('')

const current = computed<ReviewCard | null>(() => cards.value[index.value] ?? null)
const progressPercent = computed<number>(() => {
  if (todayTotal.value <= 0) return 0
  return Math.min(100, Math.round((doneCount.value / todayTotal.value) * 100))
})
const averageRating = computed<string>(() => {
  if (doneCount.value <= 0) return '0.0'
  return (ratingSum.value / doneCount.value).toFixed(1)
})

/** 题干（纯文本样式保护：外层已做 whitespace-pre-wrap 与词缀解释）。 */
const questionHtml = computed<string>(() => renderMath(current.value?.question_content ?? ''))

const stateLabel = computed<string>(() => {
  const s = current.value?.state ?? 0
  if (s === 1) return '学习中'
  if (s === 3) return '重学中'
  return '巩固中'
})

/** SVG 圆环：直接写 stroke-dasharray 不需要 JS 动画库。 */
const RING = 45
const ringC = computed<number>(() => 2 * Math.PI * RING)
const ringOffset = computed<number>(() => ringC.value * (1 - progressPercent.value / 100))

/* ---------------- 动作 ---------------- */
async function loadToday(): Promise<void> {
  phase.value = 'loading'
  errorMsg.value = ''
  try {
    const data = await getDailyReview()
    cards.value = data.cards ?? []
    todayTotal.value = Number(data.count ?? cards.value.length)
    index.value = 0
    doneCount.value = 0
    ratingSum.value = 0
    phase.value = cards.value.length > 0 ? 'reviewing' : 'empty'
  } catch (e: any) {
    errorMsg.value = e?.message ?? '复习计划加载失败'
    phase.value = 'error'
  }
}

async function rate(rating: number): Promise<void> {
  if (!current.value || submitting.value) return
  submitting.value = true
  submitError.value = ''
  try {
    await submitReview(current.value.id, rating)
    ratingSum.value += rating
    doneCount.value += 1
    if (index.value + 1 >= cards.value.length) {
      phase.value = 'done'
    } else {
      index.value += 1
    }
  } catch (e: any) {
    submitError.value = e?.message ?? '提交失败，请重试'
  } finally {
    submitting.value = false
  }
}

function backHome(): void {
  void router.push({ name: 'dashboard' })
}

onMounted(() => {
  void loadToday()
})
</script>

<template>
  <div class="flex min-h-full flex-col bg-gradient-to-b from-brand-50/60 via-white to-mint-50/40 px-4 pb-8 pt-5">
    <!-- 顶栏：小玥老师 + 日期 + 连击 -->
    <div class="flex items-center gap-3">
      <TutorAvatar :size="42" />
      <div class="min-w-0 flex-1">
        <h1 class="text-lg font-extrabold text-ink">今日复习</h1>
        <p class="text-xs text-ink-soft">{{ nowLabel }}</p>
      </div>
      <div
        class="flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-3 py-1.5 text-sm font-bold text-amber-600 shadow-sm"
      >
        <span>🔥</span>
        <span>{{ streakDays }} 天</span>
      </div>
    </div>

    <!-- 进度环 -->
    <div class="mt-5 flex justify-center">
      <div class="relative h-32 w-32">
        <svg class="h-full w-full -rotate-90" viewBox="0 0 100 100">
          <circle cx="50" cy="50" :r="RING" fill="none" stroke="#e2e8f0" stroke-width="10" />
          <circle
            cx="50"
            cy="50"
            :r="RING"
            fill="none"
            stroke="url(#ringGrad)"
            stroke-width="10"
            stroke-linecap="round"
            :stroke-dasharray="ringC"
            :stroke-dashoffset="ringOffset"
            class="transition-all duration-500"
          />
          <defs>
            <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stop-color="#7c5cfc" />
              <stop offset="100%" stop-color="#22c1c3" />
            </linearGradient>
          </defs>
        </svg>
        <div class="absolute inset-0 flex flex-col items-center justify-center">
          <span class="text-3xl font-extrabold text-ink">{{ doneCount }}</span>
          <span class="text-xs text-ink-soft">/ {{ todayTotal }} 张</span>
        </div>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="phase === 'loading'" class="mt-10 flex flex-col items-center gap-3 text-ink-soft">
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-brand-200 border-t-brand-500" />
      <p class="text-sm">正在翻找今天的复习卡片…</p>
    </div>

    <!-- Error -->
    <div v-else-if="phase === 'error'" class="mt-10 flex flex-col items-center gap-4 rounded-3xl border border-coral-200 bg-white/80 p-8 text-center shadow-sm">
      <span class="text-4xl">😢</span>
      <p class="font-medium text-coral-600">{{ errorMsg }}</p>
      <div class="flex gap-3">
        <button
          class="rounded-full bg-coral-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-coral-600"
          @click="loadToday"
        >
          再试一次
        </button>
        <button
          class="rounded-full border border-ink/10 px-5 py-2 text-sm font-semibold text-ink-soft transition hover:bg-ink/5"
          @click="backHome"
        >
          回学习画像
        </button>
      </div>
    </div>

    <!-- Empty -->
    <div v-else-if="phase === 'empty'" class="mt-10 flex flex-col items-center gap-4 rounded-3xl border border-mint-200 bg-white/80 p-10 text-center shadow-sm">
      <span class="text-5xl">🎉</span>
      <h2 class="text-xl font-extrabold text-ink">今天的复习卡片复习完了！</h2>
      <p class="text-sm text-ink-soft">间隔记忆讲究细水长流，明天小玥老师会准时叫你来巩固。</p>
      <button
        class="mt-2 rounded-full bg-brand-500 px-6 py-2.5 text-sm font-semibold text-white shadow-md shadow-brand-500/30 transition hover:bg-brand-600"
        @click="backHome"
      >
        回学习画像
      </button>
    </div>

    <!-- 复习卡片流 -->
    <template v-else-if="phase === 'reviewing' && current">
      <div class="mt-5 rounded-3xl border border-white bg-white/90 p-5 shadow-lg shadow-brand-500/5 backdrop-blur">
        <!-- 卡片头部：状态 + 知识点 -->
        <div class="mb-3 flex items-center justify-between">
          <span class="rounded-full bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-600">{{ stateLabel }}</span>
          <span v-if="current.kp_name" class="max-w-[60%] truncate text-xs text-ink-soft">{{ current.kp_name }}</span>
        </div>

        <!-- 题干 -->
        <div class="max-h-64 overflow-y-auto overscroll-contain whitespace-pre-wrap break-words text-base leading-relaxed text-ink" v-html="questionHtml" />

        <!-- 复习次数（弱提示） -->
        <div class="mt-4 flex items-center gap-2 text-xs text-ink-soft">
          <span>已复习 {{ current.reps }} 次</span>
          <span v-if="current.lapses > 0" class="text-coral-500">· 卡过 {{ current.lapses }} 次</span>
        </div>
      </div>

      <!-- 提交错误提示 -->
      <p v-if="submitError" class="mt-3 text-center text-sm font-medium text-coral-600">{{ submitError }}</p>

      <!-- FSRS 四键评分 -->
      <div class="mt-4 grid grid-cols-2 gap-3">
        <button
          class="flex flex-col items-center gap-0.5 rounded-2xl border-2 border-coral-200 bg-coral-50/70 px-3 py-3.5 transition active:scale-95 disabled:opacity-60"
          :disabled="submitting"
          @click="rate(1)"
        >
          <span class="text-lg font-extrabold text-coral-600">Again</span>
          <span class="text-xs text-coral-400">忘了，重来</span>
        </button>
        <button
          class="flex flex-col items-center gap-0.5 rounded-2xl border-2 border-amber-200 bg-amber-50/70 px-3 py-3.5 transition active:scale-95 disabled:opacity-60"
          :disabled="submitting"
          @click="rate(2)"
        >
          <span class="text-lg font-extrabold text-amber-600">Hard</span>
          <span class="text-xs text-amber-500">有点难</span>
        </button>
        <button
          class="flex flex-col items-center gap-0.5 rounded-2xl border-2 border-mint-300 bg-mint-50/70 px-3 py-3.5 transition active:scale-95 disabled:opacity-60"
          :disabled="submitting"
          @click="rate(3)"
        >
          <span class="text-lg font-extrabold text-mint-600">Good</span>
          <span class="text-xs text-mint-500">还记得</span>
        </button>
        <button
          class="flex flex-col items-center gap-0.5 rounded-2xl border-2 border-sky-300 bg-sky-50/70 px-3 py-3.5 transition active:scale-95 disabled:opacity-60"
          :disabled="submitting"
          @click="rate(4)"
        >
          <span class="text-lg font-extrabold text-sky-600">Easy</span>
          <span class="text-xs text-sky-500">很简单</span>
        </button>
      </div>

      <p class="mt-3 text-center text-xs text-ink-soft">间隔记忆算法会根据你的每次选择，智能安排下一次复习时间</p>
    </template>

    <!-- 完成统计 -->
    <div v-else-if="phase === 'done'" class="mt-10 flex flex-col items-center gap-5 rounded-3xl border border-amber-200 bg-gradient-to-b from-amber-50/80 to-white p-8 text-center shadow-lg shadow-amber-500/10">
      <span class="text-6xl">🏆</span>
      <h2 class="text-2xl font-extrabold text-ink">复习完成！</h2>
      <div class="grid w-full grid-cols-2 gap-3">
        <div class="rounded-2xl bg-white/90 p-4 shadow-sm">
          <div class="text-2xl font-extrabold text-brand-600">{{ doneCount }}</div>
          <div class="mt-1 text-xs text-ink-soft">完成卡片</div>
        </div>
        <div class="rounded-2xl bg-white/90 p-4 shadow-sm">
          <div class="text-2xl font-extrabold text-mint-600">{{ averageRating }}<span class="text-sm">/4</span></div>
          <div class="mt-1 text-xs text-ink-soft">平均评分</div>
        </div>
      </div>
      <p class="text-sm text-ink-soft">记忆正在悄悄沉淀，明天的你会更轻松。</p>
      <button
        class="mt-1 rounded-full bg-brand-500 px-6 py-2.5 text-sm font-semibold text-white shadow-md shadow-brand-500/30 transition hover:bg-brand-600"
        @click="backHome"
      >
        回学习画像
      </button>
    </div>
  </div>
</template>
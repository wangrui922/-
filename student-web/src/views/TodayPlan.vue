<script setup lang="ts">
/**
 * 今日学习计划（T07 自适应编排 · 只读展示，V6.5 B 批前端接线）。
 *
 * 数据契约见 types/plan.ts（出参 camelCase；relaxationSteps 内层 snake_case），
 * 降级语义见 api/plan.ts（engineVersion === 'legacy-draw' → 综合练习）。
 *
 * 交互范式对齐 views/Dashboard.vue / views/Review.vue（游戏化 + 三态齐全）：
 *   - 加载中 / 失败（ApiError，可重试）/ 空态（引导去练习页）；
 *   - 顶部汇总：计划题数 total / 实际抽到 actualCount / 缺口 missCount，
 *     missCount > 0 时如实提示「部分知识点题库不足」（少给不凑数）；
 *   - relaxationSteps 非空时逐条展示后端放宽过程（label 已是中文）；
 *   - 降级（legacy-draw）时正常展示汇总 + 「综合练习」说明，不当错误处理；
 *   - 「开始练习」仅跳转 /practice——后端会话 API 暂不支持按 questionIds
 *     建会话，MVP 不伪造该能力。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import TutorAvatar from '@/components/TutorAvatar.vue'
import { toMessage } from '@/api/http'
import { ENGINE_VERSION_LEGACY, getTodayPlan } from '@/api/plan'
import type { TodayPlan } from '@/types/plan'

/** 页面状态机：loading → ready / empty / error。 */
type Phase = 'loading' | 'ready' | 'empty' | 'error'

const router = useRouter()

const phase = ref<Phase>('loading')
const plan = ref<TodayPlan | null>(null)
const errorMsg = ref<string>('')

const nowLabel = new Date().toLocaleDateString('zh-CN', {
  month: 'long',
  day: 'numeric',
  weekday: 'long',
})

/** ready 态的非空视图数据（模板里安全窄化，省掉满屏 `plan?.x ?? 0`）。 */
const view = computed<TodayPlan | null>(() => (phase.value === 'ready' ? plan.value : null))

/** 是否降级为综合练习（adaptive.enabled=false 或编排异常）。 */
const isLegacy = computed<boolean>(() => view.value?.engineVersion === ENGINE_VERSION_LEGACY)

/**
 * 难度档位 → 中文标签（1 / 3 / 5）。
 * 异常值原样返回数字字符串，不猜测、不编造。
 */
function difficultyLabel(level: number): string {
  if (level === 1) return '基础'
  if (level === 3) return '中等'
  if (level === 5) return '提优'
  return String(level)
}

async function load(): Promise<void> {
  phase.value = 'loading'
  errorMsg.value = ''
  try {
    const data = await getTodayPlan()
    plan.value = data
    // 有知识点条目、或降级综合练习仍有题可练 → ready；两手都空 → 空态
    phase.value = data.items.length > 0 || data.actualCount > 0 ? 'ready' : 'empty'
  } catch (err) {
    errorMsg.value = toMessage(err, '今日计划加载失败，请稍后再试')
    phase.value = 'error'
  }
}

/** MVP：只做跳转（后端暂不支持按 questionIds 建会话，不伪造能力）。 */
function startPractice(): void {
  void router.push({ name: 'practice' })
}

function backHome(): void {
  void router.push({ name: 'dashboard' })
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="flex min-h-full flex-col bg-gradient-to-b from-brand-50/60 via-white to-mint-50/40 px-4 pb-8 pt-5">
    <!-- 顶栏：小玥老师 + 日期 + 刷新 -->
    <div class="flex items-center gap-3">
      <TutorAvatar :size="42" />
      <div class="min-w-0 flex-1">
        <h1 class="text-lg font-extrabold text-ink">今日学习计划</h1>
        <p class="text-xs text-ink-soft">{{ nowLabel }}</p>
      </div>
      <button
        type="button"
        class="shrink-0 rounded-full border border-ink/10 bg-white/70 px-3 py-1.5 text-xs font-semibold text-ink-soft transition active:scale-95 disabled:opacity-50"
        :disabled="phase === 'loading'"
        @click="load"
      >
        刷新
      </button>
    </div>

    <!-- Loading -->
    <div v-if="phase === 'loading'" class="mt-10 flex flex-col items-center gap-3 text-ink-soft">
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-brand-200 border-t-brand-500" />
      <p class="text-sm">小玥老师正在安排今天的练习…</p>
    </div>

    <!-- Error（ApiError 文案已由 toMessage 拆出） -->
    <div
      v-else-if="phase === 'error'"
      class="mt-10 flex flex-col items-center gap-4 rounded-3xl border border-coral-200 bg-white/80 p-8 text-center shadow-sm"
    >
      <span class="text-4xl">😢</span>
      <p class="font-medium text-coral-600">{{ errorMsg }}</p>
      <div class="flex gap-3">
        <button
          class="rounded-full bg-coral-500 px-5 py-2 text-sm font-semibold text-white transition hover:bg-coral-600"
          @click="load"
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

    <!-- Empty：没有任何可练内容，引导去练习页自由闯关 -->
    <div
      v-else-if="phase === 'empty'"
      class="mt-10 flex flex-col items-center gap-4 rounded-3xl border border-mint-200 bg-white/80 p-10 text-center shadow-sm"
    >
      <span class="text-5xl">🌱</span>
      <h2 class="text-xl font-extrabold text-ink">今天没有专项安排</h2>
      <p class="text-sm text-ink-soft">小玥老师翻遍题库，今天没有需要专项攻克的知识点，去练习页自由闯关吧。</p>
      <button
        class="mt-2 rounded-full bg-brand-500 px-6 py-2.5 text-sm font-semibold text-white shadow-md shadow-brand-500/30 transition hover:bg-brand-600"
        @click="startPractice"
      >
        去练习
      </button>
    </div>

    <!-- Ready -->
    <template v-else-if="view">
      <!-- 汇总卡：total / actualCount / missCount -->
      <section class="card mt-4">
        <div class="flex items-center justify-between">
          <p class="text-sm font-extrabold text-ink">📋 今日安排</p>
          <span
            v-if="isLegacy"
            class="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-bold text-amber-600"
          >综合练习</span>
        </div>
        <div class="mt-3 grid grid-cols-3 gap-2 text-center">
          <div class="rounded-2xl bg-brand-50/70 py-2">
            <div class="text-xl font-extrabold text-brand-600">{{ view.total }}</div>
            <div class="mt-0.5 text-[11px] font-semibold text-ink-soft">计划题数</div>
          </div>
          <div class="rounded-2xl bg-mint-50/70 py-2">
            <div class="text-xl font-extrabold text-mint-600">{{ view.actualCount }}</div>
            <div class="mt-0.5 text-[11px] font-semibold text-ink-soft">实际抽到</div>
          </div>
          <div class="rounded-2xl bg-coral-50/70 py-2">
            <div class="text-xl font-extrabold text-coral-500">{{ view.missCount }}</div>
            <div class="mt-0.5 text-[11px] font-semibold text-ink-soft">缺口</div>
          </div>
        </div>
        <p
          v-if="view.missCount > 0"
          class="mt-3 rounded-2xl bg-amber-50 px-3 py-2 text-xs font-medium text-amber-600"
        >
          ⚠️ 部分知识点题库不足，实际抽到 {{ view.actualCount }} 题 —— 少给几道，不凑数。
        </p>
      </section>

      <!-- 放宽过程：后端 label 已是中文，原样逐行展示 -->
      <section v-if="view.relaxationSteps.length > 0" class="card mt-4">
        <p class="text-sm font-extrabold text-ink">🪄 抽题放宽过程</p>
        <ul class="mt-2 space-y-1.5">
          <li
            v-for="step in view.relaxationSteps"
            :key="step.level"
            class="flex items-center justify-between gap-2 rounded-xl bg-white/70 px-3 py-2 text-xs"
          >
            <span class="min-w-0 flex-1 leading-relaxed text-ink-soft">{{ step.label }}</span>
            <span class="shrink-0 font-semibold text-ink-faint">池 {{ step.pool_size }}</span>
          </li>
        </ul>
      </section>

      <!-- 知识点清单（降级时后端 items 为空，改展示综合练习说明） -->
      <template v-if="!isLegacy">
        <template v-if="view.items.length > 0">
          <p class="px-1 pt-2 text-sm font-extrabold text-ink">🎯 今日练这些</p>
          <article v-for="item in view.items" :key="item.knowledgePointId" class="card mt-3">
            <div class="flex items-center justify-between gap-2">
              <h3 class="min-w-0 flex-1 truncate text-base font-extrabold text-ink">{{ item.kpName }}</h3>
              <span class="shrink-0 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-bold text-brand-600">
                {{ item.questionCount }} 题
              </span>
            </div>
            <div class="mt-2 flex flex-wrap gap-1.5">
              <span v-for="lv in item.difficultyLevels" :key="lv" class="chip">{{ difficultyLabel(lv) }}</span>
              <span v-if="item.strategy" class="chip">{{ item.strategy }}</span>
            </div>
            <p class="mt-2 text-sm leading-relaxed text-ink-soft">{{ item.reason }}</p>
          </article>
        </template>
      </template>
      <section v-else class="card mt-4">
        <h2 class="text-base font-extrabold text-ink">🎯 综合练习</h2>
        <p class="mt-1.5 text-sm leading-relaxed text-ink-soft">
          今天的自适应安排暂时休息，小玥老师为你抽了 {{ view.actualCount }} 道综合练习题，去练习页开始闯关吧！
        </p>
      </section>

      <!-- 开始练习：MVP 只跳转 /practice -->
      <button type="button" class="btn-primary mt-5 w-full" @click="startPractice">
        开始练习
      </button>
    </template>
  </div>
</template>

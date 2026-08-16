<script setup lang="ts">
/**
 * 家长周报公开分享页（Epic6 · T7 · /r/:token）。
 *
 * 免登录访问：用 publicHttp 拉取周报，token 失效/不存在时展示统一失效文案，
 * 不跳登录页。零练习时整页走鼓励性空态，不显示任何 0%。
 *
 * 本页**不套 StudentLayout**，独立成页（移动端画布容器在 App.vue 已提供）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import ShareErrorPatternCard from '@/components/report/ShareErrorPatternCard.vue'
import ShareOverviewCard from '@/components/report/ShareOverviewCard.vue'
import ShareWeakPointsCard from '@/components/report/ShareWeakPointsCard.vue'
import AbilityRadar from '@/components/profile/AbilityRadar.vue'
import { ApiError } from '@/api/http'
import { getSharedReport } from '@/api/report'
import type { SharedReportData } from '@/types/report'

const route = useRoute()

type LoadState = 'loading' | 'ready' | 'invalid' | 'error'

const state = ref<LoadState>('loading')
const report = ref<SharedReportData | null>(null)
const errorMessage = ref('这个链接已失效，请让孩子重新生成一份')

const token = computed<string>(() => String(route.params.token || ''))

onMounted(async () => {
  await load()
})

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    const data = await getSharedReport(token.value)
    report.value = data
    state.value = 'ready'
  } catch (err) {
    if (err instanceof ApiError && err.code === 404) {
      state.value = 'invalid'
    } else {
      state.value = 'error'
      errorMessage.value = err instanceof ApiError ? err.message : '加载失败，请稍后再试'
    }
  }
}

const isEmpty = computed<boolean>(() => Boolean(report.value?.empty_state?.is_empty))
const periodText = computed<string>(() => {
  const p = report.value?.period
  if (!p || !p.start) return ''
  return p.end ? `${p.start} ~ ${p.end}` : `${p.start} 起`
})
const gradeText = computed<string>(() => {
  const g = report.value?.student?.grade
  return g ? `${g} 年级` : ''
})
</script>

<template>
  <div class="min-h-screen bg-slate-50">
    <div class="mx-auto min-h-screen w-full max-w-app bg-white px-4 py-5">
      <!-- 加载中 -->
      <p v-if="state === 'loading'" class="py-16 text-center text-sm text-slate-400">
        正在加载学习小结…
      </p>

      <!-- 链接失效 / 出错 -->
      <div v-else-if="state !== 'ready'" class="py-20 text-center">
        <p class="text-4xl">📭</p>
        <p class="mt-4 text-base text-slate-600">{{ errorMessage }}</p>
      </div>

      <!-- 正常渲染 -->
      <template v-else-if="report">
        <!-- 头部 -->
        <header class="mb-4">
          <p class="text-xs text-slate-400">{{ periodText }}</p>
          <h1 class="mt-1 text-xl font-bold text-slate-800">
            {{ report.student.display_name }}的本周学习小结
            <span v-if="gradeText" class="ml-1 text-sm font-normal text-slate-400">{{ gradeText }}</span>
          </h1>
          <p v-if="report.narrative.headline" class="mt-1 text-sm font-medium text-brand-600">
            {{ report.narrative.headline }}
          </p>
        </header>

        <!-- 空态：整页鼓励，不显示任何 0% -->
        <div v-if="isEmpty" class="py-16 text-center">
          <p class="text-4xl">🌱</p>
          <p class="mt-4 text-base text-slate-600">
            {{ report.empty_state.message || '这周还没有练习记录，周末陪孩子做几道题吧' }}
          </p>
        </div>

        <!-- 有数据：三张卡片 + 叙述 -->
        <template v-else>
          <div class="space-y-4">
            <ShareOverviewCard :engagement="report.engagement" :narrative="report.narrative" />

            <AbilityRadar
              v-if="report.radar && report.radar.axes && report.radar.axes.length"
              :data="report.radar"
              :loading="false"
              error=""
            />

            <ShareWeakPointsCard :weak-points="report.weak_points" />

            <ShareErrorPatternCard :data="report.error_patterns" />
          </div>

          <!-- 第二段：能力变化 -->
          <section v-if="report.narrative.progress_text" class="card mt-4">
            <h2 class="mb-2 text-base font-bold text-slate-800">能力变化</h2>
            <p class="text-sm leading-7 text-slate-600">{{ report.narrative.progress_text }}</p>
          </section>

          <!-- 第三段：建议 -->
          <section
            v-if="report.narrative.advice_items && report.narrative.advice_items.length"
            class="card mt-4"
          >
            <h2 class="mb-2 text-base font-bold text-slate-800">这周末可以这样做</h2>
            <ul class="space-y-2">
              <li
                v-for="(advice, idx) in report.narrative.advice_items"
                :key="idx"
                class="flex gap-2 text-sm leading-7 text-slate-600"
              >
                <span class="shrink-0 font-bold text-brand-500">{{ idx + 1 }}.</span>
                <span>{{ advice }}</span>
              </li>
            </ul>
          </section>
        </template>

        <!-- 低数据提示 -->
        <p v-if="report.low_data?.is_low && report.low_data.message" class="mt-4 text-center text-xs text-amber-500">
          {{ report.low_data.message }}
        </p>

        <!-- 页脚 -->
        <footer class="mt-6 text-center text-xs text-slate-300">
          <p v-if="report.generated_at">生成于 {{ report.generated_at }}</p>
          <p class="mt-1">由 AI 数学教练自动生成 · 仅供家长参考</p>
        </footer>
      </template>
    </div>
  </div>
</template>

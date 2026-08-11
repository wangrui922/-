<script setup lang="ts">
/**
 * 练习结果页（题库消费层 · F4）。
 *
 * 复用 components/common 下已有的 DonutChart（对错分布）与 MasteryBar（本次正确率），
 * 不引任何新依赖。
 *
 * 诚实披露在这里的落法：
 *   - unmapped_count > 0 时明说「其中 N 道暂未关联知识点，不计入掌握度」；
 *   - mastery_refreshed === false 时明说「本次没练到可统计的知识点，掌握度不会变」，
 *     不拿「已更新掌握度」糊弄；
 *   - mistakes_created 如实报数，重复结算为 0 时也解释清楚。
 */
import { computed } from 'vue'

import DonutChart from '@/components/common/DonutChart.vue'
import MasteryBar from '@/components/common/MasteryBar.vue'
import type { FinishResult } from '@/api/practice'
import type { MasteryLevel } from '@/types/profile'

const props = defineProps<{
  result: FinishResult | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  (e: 'again'): void
  (e: 'back'): void
  (e: 'retry'): void
  (e: 'mistakes'): void
}>()

const PERCENT = 100

/** 未作答题数（total 减去已作答）。 */
const skipped = computed<number>(() => {
  const r = props.result
  if (!r) return 0
  return Math.max(0, r.total - r.answered)
})

/** 甜甜圈的三段：对 / 错 / 没做。 */
const segments = computed<Array<{ label: string; value: number; color: string }>>(() => {
  const r = props.result
  if (!r) return []
  return [
    { label: '做对', value: r.correct, color: '#22c55e' },
    { label: '做错', value: r.wrong, color: '#ef4444' },
    { label: '没做', value: skipped.value, color: '#cbd5e1' },
  ]
})

/** 正确率百分比（整数）。 */
const accuracyPct = computed<number>(() => {
  const r = props.result
  if (!r) return 0
  return Math.round(Math.max(0, Math.min(1, r.accuracy)) * PERCENT)
})

/** 正确率 → MasteryBar 的等级配色档。 */
const accuracyLevel = computed<MasteryLevel>(() => {
  const pct = accuracyPct.value
  if (pct < 40) return 0
  if (pct < 60) return 1
  if (pct < 85) return 2
  return 3
})

/** 一句人话总结，按正确率给不同鼓励。 */
const headline = computed<string>(() => {
  const r = props.result
  if (!r) return ''
  if (r.answered === 0) return '这次一道题都还没做完，下次接着来'
  const pct = accuracyPct.value
  if (pct >= 90) return '这一组几乎全对，太稳了！'
  if (pct >= 70) return '大部分都拿下了，错的几道弄明白就更好'
  if (pct >= 40) return '对了一半左右，把错题过一遍进步会很快'
  return '这组有点难，别灰心，错题老师都帮你收好了'
})

/** 掌握度是否真的动了（诚实披露，不动就说不动）。 */
const masteryNote = computed<string>(() => {
  const r = props.result
  if (!r) return ''
  if (r.mastery_refreshed) return '本次练到了可统计的知识点，掌握度已经重新算过了'
  return '本次没练到可统计的知识点，掌握度这次不会变化'
})

/** 无知识点题目的披露文案。 */
const unmappedNote = computed<string>(() => {
  const r = props.result
  if (!r || r.unmapped_count <= 0) return ''
  return `本次有 ${r.unmapped_count} 道题暂未关联知识点，不计入掌握度统计`
})

/** 错题入本的说明（幂等重复结算会是 0，要解释）。 */
const mistakeNote = computed<string>(() => {
  const r = props.result
  if (!r) return ''
  if (r.mistakes_created > 0) return `${r.mistakes_created} 道题已经收进错题本`
  if (r.wrong > 0) return '错题之前已经收过了，错题本里能找到'
  return '这次没有需要收进错题本的题'
})
</script>

<template>
  <div class="space-y-4">
    <p v-if="loading" class="card text-center text-base text-slate-500">正在算成绩…</p>

    <template v-else-if="error">
      <p class="card text-sm leading-relaxed text-red-500">{{ error }}</p>
      <button type="button" class="btn-primary h-14 w-full text-lg" @click="emit('retry')">
        重新结算
      </button>
      <button type="button" class="btn-ghost w-full" @click="emit('back')">回到选题</button>
    </template>

    <template v-else-if="result">
      <!-- 总览 -->
      <section class="card space-y-4">
        <h2 class="text-xl font-bold text-slate-800">{{ headline }}</h2>

        <div class="flex items-center gap-5">
          <div class="relative shrink-0">
            <DonutChart :segments="segments" :size="132" :thickness="16" />
            <div class="absolute inset-0 flex flex-col items-center justify-center">
              <span class="text-2xl font-bold tabular-nums text-slate-800">{{ accuracyPct }}%</span>
              <span class="text-xs text-slate-400">正确率</span>
            </div>
          </div>

          <ul class="min-w-0 flex-1 space-y-2 text-base">
            <li class="flex items-center gap-2">
              <span class="h-3 w-3 shrink-0 rounded-full bg-green-500" />
              <span class="text-slate-600">做对</span>
              <span class="ml-auto font-bold tabular-nums text-slate-800">{{ result.correct }}</span>
            </li>
            <li class="flex items-center gap-2">
              <span class="h-3 w-3 shrink-0 rounded-full bg-red-500" />
              <span class="text-slate-600">做错</span>
              <span class="ml-auto font-bold tabular-nums text-slate-800">{{ result.wrong }}</span>
            </li>
            <li class="flex items-center gap-2">
              <span class="h-3 w-3 shrink-0 rounded-full bg-slate-300" />
              <span class="text-slate-600">没做</span>
              <span class="ml-auto font-bold tabular-nums text-slate-800">{{ skipped }}</span>
            </li>
          </ul>
        </div>

        <MasteryBar :score="result.accuracy" :level="accuracyLevel" label="本次正确率" />

        <p class="text-sm text-slate-500">
          一共 {{ result.total }} 道题，做了 {{ result.answered }} 道
        </p>
      </section>

      <!-- 诚实披露区 -->
      <section class="card space-y-2">
        <h3 class="text-base font-bold text-slate-800">这次练习的几件事</h3>
        <p class="rounded-xl bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-600">
          {{ mistakeNote }}
        </p>
        <p class="rounded-xl bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-600">
          {{ masteryNote }}
        </p>
        <p
          v-if="unmappedNote"
          class="rounded-xl bg-amber-50 px-3 py-2 text-sm leading-relaxed text-amber-700"
        >
          {{ unmappedNote }}
        </p>
      </section>

      <!-- 薄弱知识点 -->
      <section v-if="result.weak_points.length" class="card space-y-3">
        <h3 class="text-base font-bold text-slate-800">这几个点要再练练</h3>
        <ul class="space-y-2">
          <li
            v-for="wp in result.weak_points"
            :key="wp.kp_id"
            class="flex items-center gap-3 rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2.5"
          >
            <span class="min-w-0 flex-1 truncate text-base text-slate-700">{{ wp.kp_name }}</span>
            <span class="shrink-0 rounded-full bg-red-50 px-2.5 py-1 text-xs font-medium text-red-500">
              错 {{ wp.wrong_count }} 题
            </span>
          </li>
        </ul>
      </section>

      <!-- 出口 -->
      <div class="space-y-2">
        <button type="button" class="btn-primary h-14 w-full text-lg" @click="emit('again')">
          再来一组
        </button>
        <button
          v-if="result.wrong > 0"
          type="button"
          class="btn-ghost w-full"
          @click="emit('mistakes')"
        >
          去错题本看看
        </button>
        <button type="button" class="btn-ghost w-full" @click="emit('back')">换个条件练</button>
      </div>
    </template>

    <p v-else class="card text-center text-base text-slate-500">还没有结果</p>
  </div>
</template>

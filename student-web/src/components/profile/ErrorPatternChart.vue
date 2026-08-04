<script setup lang="ts">
/**
 * 错因分布面板（T04 · F8）。
 *
 * 用原生 SVG 甜甜圈（DonutChart）展示五类错因占比，下方列出每类命中次数与占比。
 * 数据恒含 5 类（count 可为 0），与后端 aggregate_error_patterns 对齐。
 */
import { computed } from 'vue'

import DonutChart from '@/components/common/DonutChart.vue'
import {
  ERROR_CATEGORY_COLOR,
  ERROR_CATEGORY_LABEL,
  type ErrorCategory,
  type ErrorPatternData,
} from '@/types/profile'

const props = defineProps<{
  data: ErrorPatternData | null
  loading: boolean
  error: string
}>()

const segments = computed<{ label: string; value: number; color: string }[]>(() => {
  if (!props.data) return []
  return props.data.error_patterns.map((p) => ({
    label: p.label || ERROR_CATEGORY_LABEL[p.category as ErrorCategory] || p.category,
    value: p.count,
    color: ERROR_CATEGORY_COLOR[p.category as ErrorCategory] || '#94a3b8',
  }))
})

const total = computed<number>(() => props.data?.total_mistakes ?? 0)
</script>

<template>
  <section class="card">
    <header class="mb-3 flex items-center justify-between">
      <h2 class="text-base font-bold text-slate-800">错因分布</h2>
      <span v-if="data" class="text-xs text-slate-400">近 {{ data.range_days }} 天 · {{ total }} 道</span>
    </header>

    <p v-if="loading" class="py-6 text-center text-sm text-slate-400">正在统计错因…</p>
    <p v-else-if="error" class="rounded-xl bg-red-50 px-3 py-3 text-sm text-red-500">
      {{ error }}
    </p>

    <template v-else-if="data">
      <div class="relative flex justify-center">
        <DonutChart :segments="segments" :size="160" :thickness="18" />
        <div class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span class="text-2xl font-bold text-slate-800">{{ total }}</span>
          <span class="text-xs text-slate-400">错题</span>
        </div>
      </div>

      <ul class="mt-4 space-y-2">
        <li
          v-for="p in data.error_patterns"
          :key="p.category"
          class="flex items-center gap-2 text-sm"
        >
          <span
            class="h-2.5 w-2.5 shrink-0 rounded-full"
            :style="{ backgroundColor: ERROR_CATEGORY_COLOR[p.category as ErrorCategory] || '#94a3b8' }"
          />
          <span class="min-w-0 flex-1 truncate text-slate-600">{{ p.label }}</span>
          <span class="shrink-0 text-xs text-slate-400">
            {{ p.count }} 次 · {{ Math.round(p.ratio * 100) }}%
          </span>
        </li>
      </ul>

      <p v-if="data.unmapped_mistakes > 0" class="mt-3 text-xs text-slate-400">
        另有 {{ data.unmapped_mistakes }} 道未关联知识点，未计入掌握度
      </p>
    </template>

    <p v-else class="py-6 text-center text-sm text-slate-400">暂无错因数据</p>
  </section>
</template>

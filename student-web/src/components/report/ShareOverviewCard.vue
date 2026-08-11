<script setup lang="ts">
/**
 * 分享页 · 本周概览卡片（Epic6 · T7 · 卡片一）。
 *
 * 展示真实学习投入计数（学习天数 / 题数 / 正确率 / 错题 / 辅导次数），
 * 并附第一段叙述 overview_text。正确率为 null（零练习）时显式展示「暂无」。
 */
import { computed } from 'vue'

import type { ReportEngagement, ReportNarrative } from '@/types/report'

const props = defineProps<{
  engagement: ReportEngagement | null
  narrative: ReportNarrative | null
}>()

function pct(value: number | null): string {
  if (value === null || value === undefined) return '暂无'
  return `${Math.round((value || 0) * 100)}%`
}

const STAT_DEFS: { key: keyof ReportEngagement; label: string }[] = [
  { key: 'study_days', label: '学习天数' },
  { key: 'attempt_count', label: '做题' },
  { key: 'correct_count', label: '做对' },
  { key: 'new_mistake_count', label: '新增错题' },
  { key: 'solved_mistake_count', label: '攻克' },
  { key: 'tutor_session_count', label: '辅导' },
]

const items = computed(() =>
  STAT_DEFS.map((s) => ({
    label: s.label,
    value: props.engagement ? Number(props.engagement[s.key] ?? 0) : 0,
  })),
)
</script>

<template>
  <section class="card">
    <header class="mb-3 flex items-center justify-between">
      <h2 class="text-base font-bold text-slate-800">本周学习概览</h2>
      <span v-if="engagement && engagement.correct_rate !== null" class="text-xs text-slate-400">
        正确率 {{ pct(engagement.correct_rate) }}
      </span>
      <span v-else-if="engagement" class="text-xs text-slate-400">暂无正确率</span>
    </header>

    <ul class="grid grid-cols-3 gap-3">
      <li
        v-for="item in items"
        :key="item.label"
        class="rounded-xl bg-slate-50 px-2 py-3 text-center"
      >
        <p class="text-xl font-bold tabular-nums text-slate-800">{{ item.value }}</p>
        <p class="mt-0.5 text-xs text-slate-400">{{ item.label }}</p>
      </li>
    </ul>

    <p v-if="narrative?.overview_text" class="mt-3 text-sm leading-7 text-slate-600">
      {{ narrative.overview_text }}
    </p>
  </section>
</template>

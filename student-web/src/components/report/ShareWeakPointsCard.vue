<script setup lang="ts">
/**
 * 分享页 · 薄弱知识点卡片（Epic6 · T7 · 卡片二）。
 *
 * 复用通用 MasteryBar 展示 Top3 薄弱知识点当前掌握度，并在每条下方
 * 标注上周对比（delta）。无历史时只显示「新出现」，不编造对比。
 */
import { computed } from 'vue'

import MasteryBar from '@/components/common/MasteryBar.vue'
import type { MasteryLevel } from '@/types/profile'
import type { ReportWeakPoint } from '@/types/report'

const props = defineProps<{
  weakPoints: ReportWeakPoint[]
}>()

interface Row {
  item: ReportWeakPoint
  level: MasteryLevel
  deltaText: string
  deltaClass: string
}

const rows = computed<Row[]>(() =>
  props.weakPoints.map((item) => {
    const lvl = Math.max(0, Math.min(3, Number(item.level) || 0)) as MasteryLevel
    let deltaText = '新出现'
    let deltaClass = 'text-slate-400'
    if (item.delta !== null && item.delta !== undefined) {
      const d = item.delta
      if (d > 0) {
        deltaText = `比上周 +${Math.round(d * 100)}%`
        deltaClass = 'text-green-500'
      } else if (d < 0) {
        deltaText = `比上周 ${Math.round(d * 100)}%`
        deltaClass = 'text-amber-500'
      } else {
        deltaText = '和上周持平'
        deltaClass = 'text-slate-400'
      }
    }
    return { item, level: lvl, deltaText, deltaClass }
  }),
)
</script>

<template>
  <section class="card">
    <header class="mb-3">
      <h2 class="text-base font-bold text-slate-800">还需要多练的知识点</h2>
    </header>

    <p v-if="!rows.length" class="py-4 text-center text-sm text-slate-400">
      这周暂时没有特别薄弱的知识点，保持住就好
    </p>

    <ul v-else class="space-y-3">
      <li v-for="row in rows" :key="row.item.kp_id">
        <MasteryBar :score="row.item.score" :level="row.level" :label="row.item.kp_name" />
        <p class="mt-1 pl-1 text-xs" :class="row.deltaClass">{{ row.deltaText }}</p>
      </li>
    </ul>
  </section>
</template>

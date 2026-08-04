<script setup lang="ts">
/**
 * 掌握度进度条（Tailwind 实现，无第三方图表库）。
 *
 * props:
 *   score  掌握度 0~1
 *   level  等级 0~3（决定配色）
 *   label  可选：左侧文字标签
 */
import { computed } from 'vue'

import type { MasteryLevel } from '@/types/profile'

const props = withDefaults(
  defineProps<{
    score: number
    level?: MasteryLevel
    label?: string
  }>(),
  { level: 0, label: '' },
)

const PERCENT = 100

const pct = computed<number>(() => {
  const v = Number(props.score) || 0
  return Math.max(0, Math.min(1, v)) * PERCENT
})

/** 等级 → 进度条配色（完整类名，确保 Tailwind JIT 命中）。 */
const BAR_COLOR: Record<MasteryLevel, string> = {
  0: 'bg-red-400',
  1: 'bg-amber-400',
  2: 'bg-blue-500',
  3: 'bg-green-500',
}

const barClass = computed<string>(() => BAR_COLOR[props.level])
</script>

<template>
  <div class="flex items-center gap-2">
    <span v-if="label" class="min-w-0 flex-1 truncate text-sm text-slate-600">{{ label }}</span>
    <div class="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-100">
      <div
        class="h-full rounded-full transition-all duration-500"
        :class="barClass"
        :style="{ width: `${pct}%` }"
      />
    </div>
    <span class="w-10 shrink-0 text-right text-xs tabular-nums text-slate-400">
      {{ Math.round(pct) }}%
    </span>
  </div>
</template>

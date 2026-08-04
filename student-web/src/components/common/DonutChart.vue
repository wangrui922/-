<script setup lang="ts">
/**
 * 原生 SVG 甜甜圈图（无第三方图表库，满足「无新依赖」约束）。
 *
 * props.segments: { label, value, color }[]，value<=0 的段会被忽略（不渲染弧）。
 * 所有 segment 的 value 都为 0 时，渲染一圈灰色占位环。
 */
import { computed } from 'vue'

interface Segment {
  label: string
  value: number
  color: string
}

const props = withDefaults(
  defineProps<{
    segments: Segment[]
    size?: number
    thickness?: number
  }>(),
  { size: 160, thickness: 18 },
)

const radius = computed<number>(() => (props.size - props.thickness) / 2)
const center = computed<number>(() => props.size / 2)
const circumference = computed<number>(() => 2 * Math.PI * radius.value)
const total = computed<number>(() =>
  props.segments.reduce((sum, s) => sum + Math.max(0, Number(s.value) || 0), 0),
)

interface Arc {
  color: string
  dash: number
  offset: number
}

/** 把 segments 转成 SVG 描边弧参数；total<=0 时给一条灰环。 */
const arcs = computed<Arc[]>(() => {
  const totalVal = total.value
  if (totalVal <= 0) {
    return [{ color: '#e2e8f0', dash: circumference.value, offset: 0 }]
  }
  let acc = 0
  const result: Arc[] = []
  for (const s of props.segments) {
    const v = Math.max(0, Number(s.value) || 0)
    if (v <= 0) continue
    const len = (v / totalVal) * circumference.value
    result.push({ color: s.color, dash: len, offset: acc })
    acc += len
  }
  return result
})
</script>

<template>
  <svg
    :width="size"
    :height="size"
    :viewBox="`0 0 ${size} ${size}`"
    class="block"
    role="img"
    aria-label="占比图"
  >
    <g :transform="`rotate(-90 ${center} ${center})`">
      <circle
        :cx="center"
        :cy="center"
        :r="radius"
        fill="none"
        stroke="#f1f5f9"
        :stroke-width="thickness"
      />
      <circle
        v-for="(arc, idx) in arcs"
        :key="idx"
        :cx="center"
        :cy="center"
        :r="radius"
        fill="none"
        :stroke="arc.color"
        :stroke-width="thickness"
        :stroke-dasharray="`${arc.dash} ${circumference - arc.dash}`"
        :stroke-dashoffset="`-${arc.offset}`"
        stroke-linecap="butt"
      />
    </g>
  </svg>
</template>

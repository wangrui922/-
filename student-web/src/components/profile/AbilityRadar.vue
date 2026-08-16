<script setup lang="ts">
/**
 * 学情能力雷达图（原生 SVG，无第三方图表库）。
 *
 * 轴 = 后端返回的全部 L1 知识点（固定轴，便于跨时间对比孩子成长）；
 * 值 = 掌握度百分比 0~100；未练习的轴 score=0 且灰显，练习后会「亮起来」。
 * 纯展示组件，数据来自 profile store 的 radar。
 */
import { computed } from 'vue'
import type { RadarAxis, RadarData } from '@/types/profile'

const props = defineProps<{
  data: RadarData | null
  loading: boolean
  error: string
}>()

const CX = 180
const CY = 135
const MAX_R = 92
const LABEL_GAP = 20

const axes = computed<RadarAxis[]>(() => props.data?.axes ?? [])
const n = computed<number>(() => axes.value.length)

const LEVEL_COLOR: Record<number, string> = {
  0: '#ef4444',
  1: '#f59e0b',
  2: '#3b82f6',
  3: '#22c55e',
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

/** 第 i 个轴、给定百分比的极坐标 → 直角坐标。 */
function pointAt(i: number, percent: number): { x: number; y: number } {
  const ang = ((-90 + (i * 360) / Math.max(1, n.value)) * Math.PI) / 180
  const r = (clamp(percent, 0, 100) / 100) * MAX_R
  return { x: CX + r * Math.cos(ang), y: CY + r * Math.sin(ang) }
}

/** 数据多边形顶点（未练习的轴落在中心，诚实显示「还没练」）。 */
const polygonPoints = computed<string>(() =>
  axes.value.map((a, i) => {
    const p = pointAt(i, a.score)
    return `${p.x.toFixed(1)},${p.y.toFixed(1)}`
  }).join(' '),
)

/** 背景同心环（25/50/75/100）。 */
const rings = computed<{ points: string }[]>(() => {
  if (n.value < 3) return []
  return [25, 50, 75, 100].map((lv) => ({
    points: axes.value
      .map((_a, i) => {
        const p = pointAt(i, lv)
        return `${p.x.toFixed(1)},${p.y.toFixed(1)}`
      })
      .join(' '),
  }))
})

const axisLines = computed<{ x2: number; y2: number }[]>(() =>
  axes.value.map((_a, i) => {
    const p = pointAt(i, 100)
    return { x2: p.x, y2: p.y }
  }),
)

/** 标签位置 + 对齐方式。 */
const labels = computed<{ text: string; x: number; y: number; anchor: string; dim: boolean }[]>(() =>
  axes.value.map((a, i) => {
    const ang = ((-90 + (i * 360) / Math.max(1, n.value)) * Math.PI) / 180
    const r = MAX_R + LABEL_GAP
    const x = CX + r * Math.cos(ang)
    const y = CY + r * Math.sin(ang)
    const cos = Math.cos(ang)
    const sin = Math.sin(ang)
    const anchor = cos > 0.15 ? 'start' : cos < -0.15 ? 'end' : 'middle'
    const dy = sin > 0.3 ? 10 : sin < -0.3 ? -4 : 3
    return { text: a.kp_name, x, y: y + dy, anchor, dim: !a.practiced }
  }),
)

const vertices = computed<{ x: number; y: number; color: string }[]>(() =>
  axes.value.map((a, i) => {
    const p = pointAt(i, a.score)
    const color = !a.practiced ? '#cbd5e1' : LEVEL_COLOR[a.level] ?? LEVEL_COLOR[0]
    return { x: p.x, y: p.y, color }
  }),
)

const overall = computed<number>(() => Math.round(props.data?.summary?.overall_score ?? 0))
const practicedKp = computed<number>(() => props.data?.summary?.practiced_kp ?? 0)
const totalKp = computed<number>(() => props.data?.summary?.total_kp ?? 0)
const hasData = computed<boolean>(() => n.value > 0)
</script>

<template>
  <section class="card">
    <header class="mb-1 flex items-center justify-between">
      <h2 class="text-base font-bold text-slate-800">学情能力雷达图</h2>
      <span
        v-if="data?.mode"
        class="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-400"
      >
        {{ data.mode === 'cache' ? '缓存' : '实时' }}
      </span>
    </header>
    <p class="mb-2 text-xs text-slate-400">一眼看清孩子在各能力点的强弱，练习后会慢慢亮起来</p>

    <p v-if="loading" class="py-8 text-center text-sm text-slate-400">正在生成雷达图…</p>
    <p
      v-else-if="error"
      class="rounded-xl bg-red-50 px-3 py-3 text-sm text-red-500"
    >
      {{ error }}
    </p>

    <template v-else-if="hasData">
      <div class="relative flex justify-center">
        <svg viewBox="0 0 360 300" class="w-full max-w-[340px]" role="img" aria-label="学情能力雷达图">
          <!-- 背景同心环 -->
          <polygon
            v-for="(ring, idx) in rings"
            :key="'ring-' + idx"
            :points="ring.points"
            fill="none"
            stroke="#eef2f7"
            stroke-width="1"
          />
          <!-- 轴线 -->
          <line
            v-for="(ln, idx) in axisLines"
            :key="'ax-' + idx"
            :x1="CX"
            :y1="CY"
            :x2="ln.x2"
            :y2="ln.y2"
            stroke="#e2e8f0"
            stroke-width="1"
          />
          <!-- 数据多边形 -->
          <polygon
            :points="polygonPoints"
            fill="rgba(59,130,246,0.18)"
            stroke="#3b82f6"
            stroke-width="2"
            stroke-linejoin="round"
          />
          <!-- 顶点 -->
          <circle
            v-for="(v, idx) in vertices"
            :key="'v-' + idx"
            :cx="v.x"
            :cy="v.y"
            r="3.5"
            :fill="v.color"
          />
          <!-- 轴标签 -->
          <text
            v-for="(lb, idx) in labels"
            :key="'lb-' + idx"
            :x="lb.x"
            :y="lb.y"
            :text-anchor="lb.anchor"
            :fill="lb.dim ? '#cbd5e1' : '#475569'"
            font-size="11"
          >
            {{ lb.text }}
          </text>
          <!-- 中心整体掌握度 -->
          <text :x="CX" :y="CY - 4" text-anchor="middle" font-size="22" font-weight="800" fill="#1e293b">
            {{ overall }}
          </text>
          <text :x="CX" :y="CY + 14" text-anchor="middle" font-size="10" fill="#94a3b8">
            整体掌握度
          </text>
        </svg>
      </div>

      <p class="mt-1 text-center text-xs text-slate-400">
        已覆盖 <span class="font-semibold text-slate-600">{{ practicedKp }}</span> /
        {{ totalKp }} 个能力点
        <span class="ml-1 text-slate-300">· 灰色=还没练</span>
      </p>
    </template>

    <p v-else class="py-8 text-center text-sm text-slate-400">
      还没有足够的练习数据，做几道题就能看到能力雷达啦
    </p>
  </section>
</template>

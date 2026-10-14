<script setup lang="ts">
/**
 * 练习筛选卡（题库消费层 · F3）。
 *
 * 出参一律 snake_case 的 DrawParams，直接喂给 stores/practice.startPractice()。
 * 面向小学生：全部用大号 chip 按钮，没有下拉框，点哪个是哪个；
 * 每一栏都有「不限」，默认全不限（后端会从全 L1 安全池随机抽 10 道）。
 *
 * T11 (2026-08-18) 视觉打磨：
 *   - 年级 chip 内嵌语义线性 SVG 图标，
 *     提升识别度，给孩子"每个分类有自己的标志"的实感；
 *   - 图标全部走 stroke=currentColor，颜色自动跟随 chip 文字色；
 *     选中态=白，未选中=slate-500，无需另写 active 分支。
 *
 * T11.2 (2026-08-18) 图标升级为「鲜艳 3D 微立体」：
 *   - 放弃单色描边，改为渐变填充（同色系深浅两 stop）+ 浮起投影（feDropShadow）
 *     + 顶部白色高光，呈现糖果色立体小图标质感；
 *   - 每个图标配独立鲜艳色（温故蓝 / 提优珊瑚红 / 摸底橙 / 专项绿 / 自测粉…），
 *     整屏色彩跳跃、童趣鲜明；
 *   - 选中态（紫底）下图标自动白化（brightness(0) invert(1)），保持干净；
 *   - 图标 markup 走 v-html 注入完整 <svg>，渐变 defs 在卡片顶部隐藏 svg 中统一定义。
 */
import { computed, ref, watch } from 'vue'

import { MAX_QUESTION_COUNT, type DrawParams } from '@/api/practice'
import { BANDS, bandGradeRange, segmentState, type Segment } from '@/stores/segment'

defineProps<{
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  (e: 'start', params: DrawParams): void
}>()

/** 年级候选按学段联动：
 *  - 顶栏选了某学段 → 只显示该学段年级（小学1-6 / 初中7-9 / 高中10-12）；
 *  - 顶栏「不限」→ 显示全部 1~12，并按学段分组展示。
 * 后端 BAND_GRADE_RANGE 口径一致（app/models/content.py）。
 */
function buildStageGroups(): { band: Segment | null; grades: number[] }[] {
  const range = (lo: number, hi: number): number[] => {
    const out: number[] = []
    for (let i = lo; i <= hi; i++) out.push(i)
    return out
  }
  if (segmentState.band) {
    const [lo, hi] = bandGradeRange(segmentState.band)
    return [{ band: segmentState.band, grades: range(lo, hi) }]
  }
  return BANDS.map((b) => {
    const [lo, hi] = bandGradeRange(b)
    return { band: b, grades: range(lo, hi) }
  })
}
const stageGroups = computed(buildStageGroups)

/** 学段切换时，若当前选中年级不在新学段区间，自动清空，避免抽错学段。 */
watch(
  () => segmentState.band,
  (band) => {
    if (band && grade.value !== null) {
      const [lo, hi] = bandGradeRange(band)
      if (grade.value < lo || grade.value > hi) grade.value = null
    }
  },
)

/** 题量候选（后端上限 30）。 */
const COUNTS: readonly number[] = [5, 10, 15, 20]

const grade = ref<number | null>(null)
const count = ref<number>(10)

/** 选中态样式（大按钮，够手指点）。选中态附带 chip-active 标记用于图标白化。 */
function chipClass(active: boolean): string {
  return active
    ? 'border-brand-500 bg-brand-500 text-white shadow-soft chip-active'
    : 'border-slate-200 bg-white text-slate-600 active:bg-slate-100'
}

/**
 * 鲜艳 3D 微立体图标库（T11.2）。
 *
 * 实现：每个图标是一段 inner SVG markup 字符串，引用卡片顶部隐藏 <defs> 里的
 * 渐变（g-*）与投影滤镜（f-shadow）。颜色走糖果色线性渐变（同色系深浅两 stop），
 * 面状图标加白色高光椭圆，整体浮起投影，呈现立体小图标质感。
 *
 * 设计原则：
 *   - 「不限」= 无限符号 ∞（筛选组共用，紫罗兰）
 *   - 年级 1-6 小学：幼苗→双苗→树→学校→书堆→毕业帽（视觉上"年級越高越复杂"）
 *   - 年级 7-9 初中：罗盘→地图→望远镜
 *   - 年级 10-12 高中：烧瓶→π→博士帽
 */
const ICON_INNER: Record<string, string> = {
  /** 无限符号 ∞（三组不限共用） */
  unlimited:
    '<path d="M9 8a3 3 0 1 0 1 4.5 M15 16a3 3 0 1 0 -1 -4.5" fill="none" stroke="url(#g-violet)" stroke-width="2.6" stroke-linecap="round" filter="url(#f-shadow)"/>',
  /** 年级 1：幼苗（一年起步） */
  g1: '<path d="M12 21v-9" stroke="url(#g-green)" stroke-width="2.4" stroke-linecap="round"/><path d="M12 12c-1.7 0-3-1.3-3-3s1.3-3 3-3 3 1.3 3 3-1.3 3-3 3z" fill="url(#g-green)" filter="url(#f-shadow)"/><ellipse cx="11" cy="9.2" rx="1" ry="1.4" fill="#ffffff" opacity="0.32"/>',
  /** 年级 2：双苗 */
  g2: '<path d="M12 21v-9" stroke="url(#g-green)" stroke-width="2.4" stroke-linecap="round"/><path d="M8 14c-1.4 0-2.5-1.1-2.5-2.5S6.6 9 8 9s2.5 1.1 2.5 2.5S9.4 14 8 14z" fill="url(#g-green)" filter="url(#f-shadow)"/><path d="M16 14c-1.4 0-2.5-1.1-2.5-2.5S14.6 9 16 9s2.5 1.1 2.5 2.5S17.4 14 16 14z" fill="url(#g-green)" filter="url(#f-shadow)"/>',
  /** 年级 3：树（圆冠） */
  g3: '<path d="M12 21v-10" stroke="url(#g-green)" stroke-width="2.4" stroke-linecap="round"/><path d="M12 11a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z" fill="url(#g-green)" filter="url(#f-shadow)"/><ellipse cx="10.6" cy="8" rx="1.2" ry="1.6" fill="#ffffff" opacity="0.3"/>',
  /** 年级 4：学校（三角顶+方身+门） */
  g4: '<path d="M3 11l9-7 9 7" fill="none" stroke="url(#g-orange)" stroke-width="2.4" stroke-linejoin="round" filter="url(#f-shadow)"/><path d="M5 11v9 M19 11v9" stroke="url(#g-orange)" stroke-width="2.4" stroke-linecap="round"/><path d="M9 20v-5h6v5z" fill="url(#g-orange)" opacity="0.9" filter="url(#f-shadow)"/>',
  /** 年级 5：三层书堆 */
  g5: '<rect x="3" y="6" width="7" height="14" rx="1.6" fill="url(#g-blue)" filter="url(#f-shadow)"/><rect x="10.5" y="5" width="6.5" height="14" rx="1.6" fill="url(#g-blue)" opacity="0.92" filter="url(#f-shadow)"/><rect x="17.5" y="7" width="3.5" height="12" rx="1.4" fill="url(#g-blue)" opacity="0.8" filter="url(#f-shadow)"/>',
  /** 年级 6：毕业帽 */
  g6: '<path d="M2 10l10-5 10 5-10 5L2 10z" fill="url(#g-blue)" filter="url(#f-shadow)"/><path d="M6 11.5V17c0 1.7 2.7 3 6 3s6-1.3 6-3v-5.5" fill="none" stroke="url(#g-blue)" stroke-width="2.2" stroke-linecap="round"/><ellipse cx="7" cy="9" rx="2" ry="1" fill="#ffffff" opacity="0.3"/>',
  /** 年级 7：罗盘（圆+针） */
  g7: '<circle cx="12" cy="12" r="9" fill="url(#g-mint)" filter="url(#f-shadow)"/><path d="M12 12l4-4 M16 8l-3 5-3 3" fill="none" stroke="#ffffff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="1.6" fill="#ffffff"/>',
  /** 年级 8：地图（折页） */
  g8: '<path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z" fill="url(#g-emerald)" filter="url(#f-shadow)"/><path d="M9 4v16 M15 6v16" stroke="#ffffff" stroke-width="1.4" opacity="0.65"/>',
  /** 年级 9：望远镜（双筒） */
  g9: '<path d="M3 21l5-9 4 2-5 9z" fill="url(#g-violet)" filter="url(#f-shadow)"/><path d="M8 12l3-5 7 4-3 5z" fill="url(#g-violet)" filter="url(#f-shadow)"/><path d="M18 11l2-1 2 4-2 1z" fill="url(#g-violet)" filter="url(#f-shadow)"/>',
  /** 年级 10：烧瓶（瓶身+瓶颈+液面） */
  g10: '<path d="M10 3v6L5.5 19a2 2 0 0 0 1.7 3h9.6a2 2 0 0 0 1.7-3L14 9V3" fill="none" stroke="url(#g-sky)" stroke-width="2.4" stroke-linejoin="round" filter="url(#f-shadow)"/><path d="M7.5 16h9l2.6 4.2a2 2 0 0 1-1.7 3H6.6a2 2 0 0 1-1.7-3z" fill="url(#g-sky)" opacity="0.9" filter="url(#f-shadow)"/>',
  /** 年级 11：π 字符（数学符号） */
  g11: '<path d="M3 9c0 4 0 6 2 6s2-2 2-6 M9 9c0 4 0 6 2 6s2-2 2-6 M16 6v12 M20 6v12" fill="none" stroke="url(#g-purple)" stroke-width="2.6" stroke-linecap="round" filter="url(#f-shadow)"/>',
  /** 年级 12：博士帽 + 五角星（荣耀） */
  g12: '<path d="M2 10l10-5 10 5-10 5L2 10z" fill="url(#g-blue)" filter="url(#f-shadow)"/><path d="M6 11.5V17c0 1.7 2.7 3 6 3s6-1.3 6-3v-5.5" fill="none" stroke="url(#g-blue)" stroke-width="2.2" stroke-linecap="round"/><path d="M19 3l1 2 2 1-2 1-1 2-1-2-2-1 2-1z" fill="url(#g-gold)" filter="url(#f-shadow)"/>',
  /** 青岛版：海浪 + 小帆（沿海城市） */
  qingdao:
    '<path d="M3 14c1.5 0 1.5-1 3-1s1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 1.5-1 3-1" fill="none" stroke="url(#g-sky)" stroke-width="2.2" stroke-linecap="round" filter="url(#f-shadow)"/><path d="M3 18c1.5 0 1.5-1 3-1s1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 1.5-1 3-1" fill="none" stroke="url(#g-sky)" stroke-width="2.2" stroke-linecap="round"/><path d="M12 4l3 4h-6z" fill="url(#g-sky)" filter="url(#f-shadow)"/>',
  /** 苏教版：小桥流水（江南意象） */
  sujiao:
    '<path d="M3 18h18" stroke="url(#g-lime)" stroke-width="2.4" stroke-linecap="round"/><path d="M3 18c0-2 1-3 2-3 M21 18c0-2-1-3-2-3" fill="none" stroke="url(#g-lime)" stroke-width="2.2" stroke-linecap="round"/><path d="M5 15l4-5 3 3 4-5 3 4" fill="none" stroke="url(#g-lime)" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round" filter="url(#f-shadow)"/>',
  /** 教材版本 fallback：书本（人教版/北师大版等其他） */
  book:
    '<path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14H6a2 2 0 0 1-2-2z" fill="url(#g-blue)" opacity="0.9" filter="url(#f-shadow)"/><path d="M4 17a2 2 0 0 1 2-2h14" fill="none" stroke="#ffffff" stroke-width="1.4" opacity="0.6"/><path d="M9 7h6" stroke="#ffffff" stroke-width="1.4" opacity="0.6"/>',
  /** 温故：回旋箭头（时光倒流） */
  review:
    '<path d="M3 12a9 9 0 1 0 3-6.7" fill="none" stroke="url(#g-blue)" stroke-width="2.6" stroke-linecap="round" filter="url(#f-shadow)"/><path d="M3 8v5h5" fill="none" stroke="url(#g-blue)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
  /** 预习：望远镜 */
  preview:
    '<path d="M3 21l5-9 4 2-5 9z" fill="url(#g-mint)" filter="url(#f-shadow)"/><path d="M8 12l3-5 7 4-3 5z" fill="url(#g-mint)" filter="url(#f-shadow)"/><path d="M18 11l2-1 2 4-2 1z" fill="url(#g-mint)" filter="url(#f-shadow)"/>',
  /** 提优：火箭 */
  boost:
    '<path d="M12 3c3 0 5 4 5 8v3l-5 2-5-2v-3c0-4 2-8 5-8z" fill="url(#g-coral)" filter="url(#f-shadow)"/><circle cx="12" cy="9" r="2" fill="#ffffff" opacity="0.85"/><path d="M7 14l-2 5 4-2z M17 14l2 5-4-2z" fill="url(#g-coral)" filter="url(#f-shadow)"/><path d="M12 16v4" stroke="url(#g-coral)" stroke-width="2" stroke-linecap="round"/>',
  /** 摸底：放大镜 + 标尺 */
  assess:
    '<circle cx="10" cy="10" r="6" fill="url(#g-orange)" opacity="0.28" filter="url(#f-shadow)"/><circle cx="10" cy="10" r="6" fill="none" stroke="url(#g-orange)" stroke-width="2.4" filter="url(#f-shadow)"/><path d="M14.5 14.5L20 20" stroke="url(#g-orange)" stroke-width="2.6" stroke-linecap="round" filter="url(#f-shadow)"/><path d="M10 4v3 M10 14v2" stroke="url(#g-orange)" stroke-width="1.6" opacity="0.7" stroke-linecap="round"/>',
  /** 专项：十字瞄准（精准聚焦） */
  focus:
    '<path d="M12 3v4 M12 17v4 M3 12h4 M17 12h4" stroke="url(#g-green)" stroke-width="2.4" stroke-linecap="round" filter="url(#f-shadow)"/><circle cx="12" cy="12" r="3.5" fill="url(#g-green)" filter="url(#f-shadow)"/><circle cx="12" cy="12" r="1.3" fill="#ffffff"/>',
  /** 自测：勾选清单 */
  test:
    '<rect x="3" y="3" width="18" height="18" rx="3" fill="url(#g-pink)" opacity="0.28" filter="url(#f-shadow)"/><rect x="3" y="3" width="18" height="18" rx="3" fill="none" stroke="url(#g-pink)" stroke-width="2.2"/><path d="M8 12l3 3 5-6" fill="none" stroke="url(#g-pink)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" filter="url(#f-shadow)"/>',
}

/** 组装完整 <svg> 字符串（含尺寸 class，供 v-html 注入）。 */
function iconSvg(name: string): string {
  const inner = ICON_INNER[name] ?? ICON_INNER.unlimited
  return `<svg class="h-5 w-5 shrink-0" viewBox="0 0 24 24" fill="none" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`
}

/** 取年级 icon 完整 svg（g1~g12）。 */
function gradeIcon(g: number): string {
  return iconSvg(`g${g}`)
}

/**
 * 把当前已选条件拼成人话摘要（模板里 `${summary}` 用）。
 */
const summary = computed<string>(() => {
  const parts: string[] = []
  // 学段优先展示：顶栏切换的全局学段（null=不限）
  parts.push(segmentState.band ? segmentState.band : '不限学段')
  parts.push(grade.value === null ? '不限年级' : `${grade.value}年级`)
  parts.push(`${count.value}道题`)
  return parts.join(' · ')
})

function pickGrade(value: number | null): void {
  grade.value = grade.value === value ? null : value
}

function pickCount(value: number): void {
  count.value = Math.min(MAX_QUESTION_COUNT, value)
}

/** 组装 snake_case 出参：没选的项一律不传。 */
function handleStart(): void {
  const params: DrawParams = { count: count.value }
  if (grade.value !== null) params.grade = grade.value
  // 顶栏学段切换：把全局学段翻译成 grade_band 下发给后端（null=不限，不传）
  if (segmentState.band) params.grade_band = segmentState.band
  emit('start', params)
}

/** 一键清空所有条件。 */
function handleReset(): void {
  grade.value = null
  count.value = 10
}
</script>

<template>
  <section class="card space-y-5">
    <!-- 渐变 / 投影定义：图标 markup 通过 id 引用，隐藏不占布局 -->
    <svg class="absolute h-0 w-0 overflow-hidden" aria-hidden="true">
      <defs>
        <linearGradient id="g-violet" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#c4a3ff" /><stop offset="1" stop-color="#7c3aed" />
        </linearGradient>
        <linearGradient id="g-green" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#86efac" /><stop offset="1" stop-color="#16a34a" />
        </linearGradient>
        <linearGradient id="g-orange" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#ffd6a5" /><stop offset="1" stop-color="#fb923c" />
        </linearGradient>
        <linearGradient id="g-blue" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#93c5fd" /><stop offset="1" stop-color="#2563eb" />
        </linearGradient>
        <linearGradient id="g-mint" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#99f6e4" /><stop offset="1" stop-color="#14b8a6" />
        </linearGradient>
        <linearGradient id="g-emerald" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#6ee7b7" /><stop offset="1" stop-color="#059669" />
        </linearGradient>
        <linearGradient id="g-purple" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#d8b4fe" /><stop offset="1" stop-color="#a855f7" />
        </linearGradient>
        <linearGradient id="g-gold" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#ffe08a" /><stop offset="1" stop-color="#f59e0b" />
        </linearGradient>
        <linearGradient id="g-sky" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#7dd3fc" /><stop offset="1" stop-color="#0ea5e9" />
        </linearGradient>
        <linearGradient id="g-lime" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#bef264" /><stop offset="1" stop-color="#65a30d" />
        </linearGradient>
        <linearGradient id="g-coral" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#ffadad" /><stop offset="1" stop-color="#ef4444" />
        </linearGradient>
        <linearGradient id="g-pink" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#ffc2e2" /><stop offset="1" stop-color="#ec4899" />
        </linearGradient>
        <filter id="f-shadow" x="-40%" y="-40%" width="180%" height="180%">
          <feDropShadow dx="0" dy="1.2" stdDeviation="1.1" flood-color="#1e293b" flood-opacity="0.28" />
        </filter>
      </defs>
    </svg>

    <header>
      <h2 class="text-lg font-bold text-slate-800">今天想练点什么？</h2>
      <p class="mt-1 text-sm text-slate-500">全都不选也行，老师会随机挑几道给你</p>
    </header>

    <!-- 年级（按学段联动：1-12，顶栏选学段即筛到对应年级段） -->
    <div>
      <h3 class="mb-2 text-sm font-semibold text-slate-700">年级</h3>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="inline-flex h-12 items-center gap-1.5 rounded-xl border pl-3 pr-4 text-base font-medium transition"
          :class="chipClass(grade === null)"
          @click="pickGrade(null)"
        >
          <span class="icon-svg" v-html="iconSvg('unlimited')" />
          <span>不限</span>
        </button>
      </div>
      <div v-for="grp in stageGroups" :key="grp.band || 'all'" class="mt-2">
        <p v-if="!segmentState.band" class="mb-1 text-xs text-slate-400">{{ grp.band }}</p>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="g in grp.grades"
            :key="g"
            type="button"
            class="inline-flex h-12 items-center gap-1.5 rounded-xl border pl-3 pr-4 text-base font-medium transition"
            :class="chipClass(grade === g)"
            @click="pickGrade(g)"
          >
            <span class="icon-svg" v-html="gradeIcon(g)" />
            <span>{{ g }}年级</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 题量 -->
    <div>
      <h3 class="mb-2 text-sm font-semibold text-slate-700">做几道</h3>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="c in COUNTS"
          :key="c"
          type="button"
          class="h-12 min-w-[64px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(count === c)"
          @click="pickCount(c)"
        >
          {{ c }}道
        </button>
      </div>
    </div>

    <p v-if="error" class="rounded-xl bg-red-50 px-3 py-3 text-sm leading-relaxed text-red-500">
      {{ error }}
    </p>

    <div class="space-y-2 border-t border-dashed border-slate-200 pt-4">
      <p class="text-sm text-slate-500">已选：{{ summary }}</p>
      <button
        type="button"
        class="btn-primary h-14 w-full text-lg"
        :disabled="loading"
        @click="handleStart"
      >
        {{ loading ? '正在挑题…' : '开始练习' }}
      </button>
      <button type="button" class="btn-ghost w-full" :disabled="loading" @click="handleReset">
        清空条件
      </button>
    </div>
  </section>
</template>

<style scoped>
/* 选中态（紫底）下图标白化，保持干净统一 */
.chip-active .icon-svg {
  filter: brightness(0) invert(1);
}
</style>

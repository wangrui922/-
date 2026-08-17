<script setup lang="ts">
/**
 * 练习筛选卡（题库消费层 · F3）。
 *
 * 出参一律 snake_case 的 DrawParams，直接喂给 stores/practice.startPractice()。
 * 面向小学生：全部用大号 chip 按钮，没有下拉框，点哪个是哪个；
 * 每一栏都有「不限」，默认全不限（后端会从全 L1 安全池随机抽 10 道）。
 *
 * P0-F（T9）2026-08-11 重构新增：
 *   - 选「苏教版」chip 时附「题目较少」灰色角标（Q6 拍板「不动 importer」下的
 *     折中披露，不阻塞点击）；
 *   - 苏教版 + 6 年级这种典型稀疏组合，给一句人话建议 + 「换青岛版」chip
 *     （本期使用前端启发式按"苏教版稀疏"预设规则替代后端 pool-estimate，
 *     QX1 拍板「本期不实现 pool-estimate 接口」）；
 *   - 苏教版 + 较高题量（>10 道）也可能撞上少给，也给建议文案。
 *
 * T11 (2026-08-18) 视觉打磨：
 *   - 三组 chip（年级 / 教材版本 / 练哪一块）全部内嵌语义线性 SVG 图标，
 *     提升识别度，给孩子"每个分类有自己的标志"的实感；
 *   - 图标全部走 stroke=currentColor，颜色自动跟随 chip 文字色；
 *     选中态=白，未选中=slate-500，无需另写 active 分支。
 *   - 选"苏教版"角标等所有既有逻辑一律保留，仅样式升级。
 */
import { computed, ref, watch } from 'vue'

import {
  EDITIONS,
  MAX_QUESTION_COUNT,
  SECTIONS,
  type DrawParams,
} from '@/api/practice'
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
const edition = ref<string>('')
const section = ref<string>('')
const count = ref<number>(10)

/** 选中态样式（大按钮，够手指点）。 */
function chipClass(active: boolean): string {
  return active
    ? 'border-brand-500 bg-brand-500 text-white shadow-soft'
    : 'border-slate-200 bg-white text-slate-600 active:bg-slate-100'
}

/**
 * 语义线性 SVG 图标库（T11）。
 *
 * 全部走 24×24 viewBox + stroke="currentColor" + fill="none"，
 * 颜色自动跟随父级 text-*，选中态无需另写 active 变体。
 *
 * 设计原则（线宽 1.8，round cap/join，圆头端点，整体跟底部 nav 同款风）：
 *   - 「不限」= 无限符号 ∞（三组共用）
 *   - 年级 1-6 小学：幼苗→小树→树→学校→书堆→毕业帽（视觉上"年級越高越复杂"）
 *   - 年级 7-12 初高中：罗盘→地图→望远镜→烧瓶→π 字符→博士帽+星
 *   - 教材版本：青岛版=海浪+小帆；苏教版=小桥流水（江南意象）
 *   - 练哪一块：温故=回旋；预习=望远镜；提优=火箭；摸底=放大镜+尺；
 *     专项=十字瞄准；自测=勾选清单
 */
const ICONS = {
  /** 无限符号 ∞（三组不限共用） */
  unlimited:
    'M9 8a3 3 0 1 0 1 4.5 M15 16a3 3 0 1 0 -1 -4.5',
  /** 年级 1：幼苗（一年起步） */
  g1: 'M12 21v-9 M12 12c-1.7 0-3-1.3-3-3s1.3-3 3-3 3 1.3 3 3-1.3 3-3 3z',
  /** 年级 2：双苗 */
  g2: 'M12 21v-9 M8 14c-1.4 0-2.5-1.1-2.5-2.5S6.6 9 8 9s2.5 1.1 2.5 2.5S9.4 14 8 14z M16 14c-1.4 0-2.5-1.1-2.5-2.5S14.6 9 16 9s2.5 1.1 2.5 2.5S17.4 14 16 14z',
  /** 年级 3：树（圆冠） */
  g3: 'M12 21v-10 M12 11a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z',
  /** 年级 4：学校（三角顶+方身+门） */
  g4: 'M3 11l9-7 9 7 M5 11v9 M19 11v9 M9 20v-5h6v5 M12 8v2',
  /** 年级 5：三层书堆 */
  g5: 'M3 6h7v14H3z M10 5h7v14h-7z M17 6h4v14h-4z',
  /** 年级 6：毕业帽 */
  g6: 'M2 10l10-5 10 5-10 5L2 10z M6 11.5V17c0 1.7 2.7 3 6 3s6-1.3 6-3v-5.5 M20 11v5',
  /** 年级 7：罗盘（圆+针） */
  g7: 'M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18z M12 12l4-4 M16 8l-3 5-3 3 M14 10l-2 2',
  /** 年级 8：地图（折页） */
  g8: 'M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z M9 4v16 M15 6v16',
  /** 年级 9：望远镜（双筒） */
  g9: 'M3 21l5-9 4 2-5 9z M8 12l3-5 7 4-3 5z M18 11l2-1 2 4-2 1z',
  /** 年级 10：烧瓶（瓶身+瓶颈+液面） */
  g10: 'M9 3h6 M10 3v6L5.5 19a2 2 0 0 0 1.7 3h9.6a2 2 0 0 0 1.7-3L14 9V3 M7 16h10',
  /** 年级 11：π 字符（数学符号） */
  g11: 'M3 9c0 4 0 6 2 6s2-2 2-6 M9 9c0 4 0 6 2 6s2-2 2-6 M16 6v12 M20 6v12',
  /** 年级 12：博士帽 + 五角星（荣耀） */
  g12:
    'M2 10l10-5 10 5-10 5L2 10z M6 11.5V17c0 1.7 2.7 3 6 3s6-1.3 6-3v-5.5 M19 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1z',
  /** 青岛版：海浪 + 小帆（沿海城市） */
  qingdao:
    'M3 18c1.5 0 1.5-1 3-1s1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 M3 13c1.5 0 1.5-1 3-1s1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 1.5-1 3-1 1.5 1 3 1 M12 5l3 3h-6z M13 8v3',
  /** 苏教版：小桥流水（江南意象） */
  sujiao:
    'M3 18h18 M3 18c0-2 1-3 2-3 M21 18c0-2-1-3-2-3 M5 15l4-5 3 3 4-5 3 4',
  /** 教材版本 fallback：书本（人教版/北师大版等其他） */
  book:
    'M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v14H6a2 2 0 0 1-2-2z M4 17a2 2 0 0 1 2-2h14 M9 7h6',
  /** 温故：回旋箭头（时光倒流） */
  review: 'M3 12a9 9 0 1 0 3-6.7L3 8 M3 3v5h5',
  /** 预习：望远镜 */
  preview: 'M3 21l5-9 4 2-5 9z M8 12l3-5 7 4-3 5z M18 11l2-1 2 4-2 1z',
  /** 提优：火箭 */
  boost:
    'M5 13c-1 3-2 5-2 7 2 0 4-1 7-2 M19 13c1 3 2 5 2 7-2 0-4-1-7-2 M12 3c3 0 5 4 5 8v3l-5 2-5-2v-3c0-4 2-8 5-8z M9 15l3-2 3 2 M12 6v3',
  /** 摸底：放大镜 + 标尺 */
  assess:
    'M11 3a8 8 0 1 1 0 16 8 8 0 0 1 0-16z M16 16l5 5 M3 8h2 M3 11h3 M8 3v2 M11 3v2',
  /** 专项：十字瞄准（精准聚焦） */
  focus:
    'M12 3v4 M12 17v4 M3 12h4 M17 12h4 M12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10z M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6z',
  /** 自测：勾选清单 */
  test: 'M9 11l3 3L22 4 M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11',
} as const

/** 取年级 icon key。 */
function gradeIcon(g: number): string {
  const key = `g${g}` as keyof typeof ICONS
  return ICONS[key] ?? ICONS.unlimited
}

/** 取教材版本 icon path。 */
function editionIcon(name: string): string {
  if (name === '青岛版') return ICONS.qingdao
  if (name === '苏教版') return ICONS.sujiao
  return ICONS.book
}

/** 取分区 icon path。 */
function sectionIcon(name: string): string {
  if (name === '温故') return ICONS.review
  if (name === '预习') return ICONS.preview
  if (name === '提优') return ICONS.boost
  if (name === '摸底') return ICONS.assess
  if (name === '专项') return ICONS.focus
  if (name === '自测') return ICONS.test
  return ICONS.unlimited
}

/**
 * 把当前已选条件拼成人话摘要（模板里 `${summary}` 用）。
 */
const summary = computed<string>(() => {
  const parts: string[] = []
  // 学段优先展示：顶栏切换的全局学段（null=不限）
  parts.push(segmentState.band ? segmentState.band : '不限学段')
  parts.push(grade.value === null ? '不限年级' : `${grade.value}年级`)
  if (edition.value) parts.push(edition.value)
  if (section.value) parts.push(section.value)
  parts.push(`${count.value}道题`)
  return parts.join(' · ')
})

/** 苏教版 chip 角标（题量较少）—— 选中与否都给提示，但视觉权重不抢主按钮。 */
const isSujiaoEdition = computed<boolean>(() => edition.value === '苏教版')

/**
 * 高年级 + 苏教版 是典型的「苏教版稀疏」组合（T9 设计 §1.6.1）。
 * 这里用纯前端启发式替代 pool-estimate（QX1 拍板）。
 */
const sparseHint = computed<string | null>(() => {
  if (!isSujiaoEdition.value) {
    return null
  }
  // 苏教版为小学教材版本，初中/高中年级不参与稀疏提示
  if (grade.value !== null && grade.value > 6) {
    return null
  }
  if (grade.value !== null && grade.value >= 5) {
    return `苏教版 ${grade.value} 年级能自动判分的题较少，要不要换青岛版？`
  }
  if (count.value > 10) {
    return `苏教版题目总量不大，${count.value} 道可能要等「少给」才凑得齐`
  }
  return `苏教版题库总量较少，做不到 ${count.value} 道时老师会少给`
})

function pickGrade(value: number | null): void {
  grade.value = grade.value === value ? null : value
}

function pickEdition(value: string): void {
  edition.value = edition.value === value ? '' : value
}

function pickSection(value: string): void {
  section.value = section.value === value ? '' : value
}

function pickCount(value: number): void {
  count.value = Math.min(MAX_QUESTION_COUNT, value)
}

/** 「换青岛版」chip 点击：把当前选中切到青岛版、其它保留。 */
function switchToQingdao(): void {
  edition.value = '青岛版'
}

/** 组装 snake_case 出参：没选的项一律不传。 */
function handleStart(): void {
  const params: DrawParams = { count: count.value }
  if (grade.value !== null) params.grade = grade.value
  // 顶栏学段切换：把全局学段翻译成 grade_band 下发给后端（null=不限，不传）
  if (segmentState.band) params.grade_band = segmentState.band
  if (edition.value) params.edition = edition.value
  if (section.value) params.section = section.value
  emit('start', params)
}

/** 一键清空所有条件。 */
function handleReset(): void {
  grade.value = null
  edition.value = ''
  section.value = ''
  count.value = 10
}
</script>

<template>
  <section class="card space-y-5">
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
          <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
            <path :d="ICONS.unlimited" />
          </svg>
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
            <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <path :d="gradeIcon(g)" />
            </svg>
            <span>{{ g }}年级</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 教材版本 -->
    <div>
      <h3 class="mb-2 text-sm font-semibold text-slate-700">教材版本</h3>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="inline-flex h-12 items-center gap-1.5 rounded-xl border pl-3 pr-4 text-base font-medium transition"
          :class="chipClass(edition === '')"
          @click="pickEdition('')"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
            <path :d="ICONS.unlimited" />
          </svg>
          <span>不限</span>
        </button>
        <button
          v-for="ed in EDITIONS"
          :key="ed"
          type="button"
          class="relative inline-flex h-12 items-center gap-1.5 rounded-xl border pl-3 pr-4 text-base font-medium transition"
          :class="chipClass(edition === ed)"
          @click="pickEdition(ed)"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
            <path :d="editionIcon(ed)" />
          </svg>
          <span>{{ ed }}</span>
          <!-- Q6 拍板：苏教版标"题目较少" —— 不阻塞点击，纯披露 -->
          <span
            v-if="ed === '苏教版'"
            class="absolute -right-2 -top-2 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500"
          >
            题目较少
          </span>
        </button>
      </div>
    </div>

    <!-- 分区 -->
    <div>
      <h3 class="mb-2 text-sm font-semibold text-slate-700">练哪一块</h3>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="inline-flex h-12 items-center gap-1.5 rounded-xl border pl-3 pr-4 text-base font-medium transition"
          :class="chipClass(section === '')"
          @click="pickSection('')"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
            <path :d="ICONS.unlimited" />
          </svg>
          <span>不限</span>
        </button>
        <button
          v-for="sec in SECTIONS"
          :key="sec"
          type="button"
          class="inline-flex h-12 items-center gap-1.5 rounded-xl border pl-3 pr-4 text-base font-medium transition"
          :class="chipClass(section === sec)"
          @click="pickSection(sec)"
        >
          <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
            <path :d="sectionIcon(sec)" />
          </svg>
          <span>{{ sec }}</span>
        </button>
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

    <!-- T9 智能提示：苏教版稀疏场景的换版本建议 -->
    <div v-if="sparseHint" class="space-y-2 rounded-2xl bg-amber-50 px-4 py-3">
      <p class="text-sm leading-relaxed text-amber-800">
        💡 {{ sparseHint }}
      </p>
      <button
        v-if="isSujiaoEdition"
        type="button"
        class="rounded-full border border-amber-300 bg-white px-3 py-1 text-xs font-medium text-amber-700 transition active:bg-amber-100"
        @click="switchToQingdao"
      >
        换青岛版
      </button>
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

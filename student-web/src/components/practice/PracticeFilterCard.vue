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
 */
import { computed, ref } from 'vue'

import {
  EDITIONS,
  MAX_QUESTION_COUNT,
  SECTIONS,
  type DrawParams,
} from '@/api/practice'

defineProps<{
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  (e: 'start', params: DrawParams): void
}>()

/** 年级候选：小学 1~6 年级（题库主体在这个区间）。 */
const GRADES: readonly number[] = [1, 2, 3, 4, 5, 6]

/** 题量候选（后端上限 30）。 */
const COUNTS: readonly number[] = [5, 10, 15, 20]

const grade = ref<number | null>(null)
const edition = ref<string>('')
const section = ref<string>('')
const count = ref<number>(10)

/** 选中态样式（大按钮，够手指点）。 */
function chipClass(active: boolean): string {
  return active
    ? 'border-brand-500 bg-brand-500 text-white'
    : 'border-slate-200 bg-white text-slate-600 active:bg-slate-100'
}

/**
 * 把当前已选条件拼成人话摘要（模板里 `${summary}` 用）。
 */
const summary = computed<string>(() => {
  const parts: string[] = []
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

    <!-- 年级 -->
    <div>
      <h3 class="mb-2 text-sm font-semibold text-slate-700">年级</h3>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="h-11 min-w-[64px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(grade === null)"
          @click="pickGrade(null)"
        >
          不限
        </button>
        <button
          v-for="g in GRADES"
          :key="g"
          type="button"
          class="h-11 min-w-[64px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(grade === g)"
          @click="pickGrade(g)"
        >
          {{ g }}年级
        </button>
      </div>
    </div>

    <!-- 教材版本 -->
    <div>
      <h3 class="mb-2 text-sm font-semibold text-slate-700">教材版本</h3>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="h-11 min-w-[72px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(edition === '')"
          @click="pickEdition('')"
        >
          不限
        </button>
        <button
          v-for="ed in EDITIONS"
          :key="ed"
          type="button"
          class="relative h-11 min-w-[72px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(edition === ed)"
          @click="pickEdition(ed)"
        >
          {{ ed }}
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
          class="h-11 min-w-[64px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(section === '')"
          @click="pickSection('')"
        >
          不限
        </button>
        <button
          v-for="sec in SECTIONS"
          :key="sec"
          type="button"
          class="h-11 min-w-[64px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(section === sec)"
          @click="pickSection(sec)"
        >
          {{ sec }}
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
          class="h-11 min-w-[64px] rounded-xl border px-4 text-base font-medium transition"
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

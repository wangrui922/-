<script setup lang="ts">
/**
 * 练习筛选卡（题库消费层 · F3）。
 *
 * 出参一律 snake_case 的 DrawParams，直接喂给 stores/practice.startPractice()。
 * 面向小学生：全部用大号 chip 按钮，没有下拉框，点哪个是哪个；
 * 每一栏都有「不限」，默认全不限（后端会从全 L1 安全池随机抽 10 道）。
 */
import { computed, ref } from 'vue'

import {
  EDITIONS,
  MAX_QUESTION_COUNT,
  SECTIONS,
  type DrawParams,
  // 难度筛选下架期间这个类型在本组件里用不到，恢复时连同下方注释块一起放开。
  // type PracticeDifficulty,
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

// ---------------------------------------------------------------------------
// 难度筛选暂时下架（整档，不是只藏「简单」「挑战」两个选项）。
//
// 抽题只从 **L1 安全池**（能自动判分的题）里取，所以要看的是这个池子的难度分布，
// 而不是全库分布。实测（judge_engine.is_l1_safe 口径，dev.db）：
//
//     L1 安全池共 592 道
//       difficulty=3    : 475 道
//       difficulty=NULL : 117 道
//       difficulty=1    :   0 道   ← 一道都没有
//       difficulty=5    :   0 道   ← 一道都没有
//
// 后果是：选「简单」或「挑战」必然抽到 0 道，直接撞 404；选「适中」会静默排除
// 那 117 道未标注的题（约占池子 20%），而这些题大多本来就是普通难度。
// 也就是说三个选项没有一个能给出诚实的结果——这不是「筛选不够精确」，
// 是筛选结果具有误导性。加提示也救不了：提示说的是「这个难度题目较少」，
// 实际发生的是「整档为空」或「五分之一题库被无声排除」，披露和真相对不上。
//
// 参考：全库 active 题（含不可自动判分的）分布为 NULL 917 / 1:8 / 3:886 / 5:1，
// 但那批题根本进不了抽题池，不能用来论证这里的取舍。
//
// 恢复条件：难度标注在 L1 安全池内覆盖率 >90%，且 1/3/5 各档题量都够抽一组，
// 届时把下面这段与模板里的「难度」栏、handleStart/handleReset 里的 difficulty
// 三处注释一起放开即可。api/practice.ts 的 DIFFICULTIES 常量与
// PracticeDifficulty 类型、DrawParams.difficulty 字段均已原样保留。
//
// interface DifficultyOption {
//   value: PracticeDifficulty
//   label: string
// }
//
// /** 难度候选：后端只认 1 / 3 / 5，给孩子看的是人话。 */
// const DIFFICULTIES: readonly DifficultyOption[] = [
//   { value: 1, label: '简单' },
//   { value: 3, label: '适中' },
//   { value: 5, label: '挑战' },
// ]
//
// const difficulty = ref<PracticeDifficulty | null>(null)
//
// /** 难度池子很小（简单/挑战题目本来就少），选了大概率触发后端自动放宽。 */
// const difficultyHint = computed<string>(() =>
//   difficulty.value === null ? '' : '这个难度的题目不多，不够时老师会自动放宽范围，并在做题页告诉你',
// )
//
// function pickDifficulty(value: PracticeDifficulty | null): void {
//   difficulty.value = difficulty.value === value ? null : value
// }
// ---------------------------------------------------------------------------

const grade = ref<number | null>(null)
const edition = ref<string>('')
const section = ref<string>('')
const count = ref<number>(10)

/** 已选条件的人话摘要，让孩子点「开始」前心里有数。 */
const summary = computed<string>(() => {
  const parts: string[] = []
  parts.push(grade.value === null ? '不限年级' : `${grade.value}年级`)
  if (edition.value) parts.push(edition.value)
  if (section.value) parts.push(section.value)
  // 难度下架期间不进摘要，恢复时放开：
  // const hit = DIFFICULTIES.find((d) => d.value === difficulty.value)
  // if (hit) parts.push(hit.label)
  parts.push(`${count.value}道题`)
  return parts.join(' · ')
})

/** 选中态样式（大按钮，够手指点）。 */
function chipClass(active: boolean): string {
  return active
    ? 'border-brand-500 bg-brand-500 text-white'
    : 'border-slate-200 bg-white text-slate-600 active:bg-slate-100'
}

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

/** 组装 snake_case 出参：没选的项一律不传。 */
function handleStart(): void {
  const params: DrawParams = { count: count.value }
  if (grade.value !== null) params.grade = grade.value
  if (edition.value) params.edition = edition.value
  if (section.value) params.section = section.value
  // 难度下架期间**不传** difficulty，恢复时放开：
  // if (difficulty.value !== null) params.difficulty = difficulty.value
  emit('start', params)
}

/** 一键清空所有条件。 */
function handleReset(): void {
  grade.value = null
  edition.value = ''
  section.value = ''
  // difficulty.value = null
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
          class="h-11 min-w-[72px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(edition === ed)"
          @click="pickEdition(ed)"
        >
          {{ ed }}
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

    <!--
      难度筛选暂时下架：抽题只走 L1 安全池，而池内 difficulty=1 / =5 各 0 道、
      NULL 117 道（占约 20%），三个选项没有一个能给出诚实结果。
      详细数据与恢复条件见 <script> 顶部同名注释块。恢复时放开下面这段即可。

    <div>
      <h3 class="mb-2 text-sm font-semibold text-slate-700">难度</h3>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="h-11 min-w-[64px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(difficulty === null)"
          @click="pickDifficulty(null)"
        >
          不限
        </button>
        <button
          v-for="d in DIFFICULTIES"
          :key="d.value"
          type="button"
          class="h-11 min-w-[64px] rounded-xl border px-4 text-base font-medium transition"
          :class="chipClass(difficulty === d.value)"
          @click="pickDifficulty(d.value)"
        >
          {{ d.label }}
        </button>
      </div>
      <p v-if="difficultyHint" class="mt-2 text-xs leading-relaxed text-slate-400">
        {{ difficultyHint }}
      </p>
    </div>
    -->

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

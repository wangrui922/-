<script setup lang="ts">
/**
 * 题库题卡（题库消费层 · F3）。
 *
 * ⚠️ 刻意**不复用** components/QuestionCard.vue —— 那个组件的 props 绑的是
 * Exercise 模型，字段结构和题库题（BankQuestion）完全不同，硬套会引入隐性 bug。
 *
 * 只负责「把题显示出来 + 收集选择」，不碰判分、不碰答案。
 * 判分结论与「看答案」入口在 AnswerFeedbackBar 里，本卡片只用 locked 控制能否改选。
 *
 * 诚实披露：kp_id 为空的题会挂一条「本题暂未关联知识点，不计入掌握度」的说明。
 */
import { computed } from 'vue'

import {
  JUDGE_ANSWER_FALSE,
  JUDGE_ANSWER_TRUE,
  QUESTION_TYPE_JUDGE,
  type BankQuestion,
} from '@/api/practice'

const props = defineProps<{
  question: BankQuestion
  /** 0 基题序 */
  index: number
  total: number
  /** 当前选中的键位 */
  selected: string
  /** 已判过分 → 锁定，不许改答案 */
  locked: boolean
}>()

const emit = defineEmits<{
  (e: 'select', key: string): void
}>()

interface ChoiceItem {
  key: string
  text: string
}

/** 是否判断题（判断题后端返回的 options 是空数组，前端自己造两个大按钮）。 */
const isJudge = computed<boolean>(() => props.question.type === QUESTION_TYPE_JUDGE)

/** 统一成 {key, text} 列表：判断题造 √ / ×，选择题用后端给的 options。 */
const choices = computed<ChoiceItem[]>(() => {
  if (isJudge.value) {
    return [
      { key: JUDGE_ANSWER_TRUE, text: '对' },
      { key: JUDGE_ANSWER_FALSE, text: '错' },
    ]
  }
  return props.question.options.map((opt) => ({ key: opt.key, text: opt.text }))
})

/** 难度 1/3/5 → 人话。 */
const difficultyLabel = computed<string>(() => {
  const map: Record<number, string> = { 1: '简单', 3: '适中', 5: '挑战' }
  const d = props.question.difficulty
  return d === null ? '' : map[d] ?? ''
})

/** 题头上的元信息小标签（没有的项不显示，不硬凑）。 */
const metaTags = computed<string[]>(() => {
  const tags: string[] = []
  if (props.question.grade !== null) tags.push(`${props.question.grade}年级`)
  if (props.question.edition) tags.push(props.question.edition)
  if (props.question.section) tags.push(props.question.section)
  if (difficultyLabel.value) tags.push(difficultyLabel.value)
  if (props.question.type) tags.push(`${props.question.type}题`)
  return tags
})

/** 这道题不进掌握度统计（后端抽题时可能混入无知识点的新题）。 */
const isUnmapped = computed<boolean>(() => !props.question.kp_id)

function handleSelect(key: string): void {
  if (props.locked) return
  emit('select', key)
}

/** 选项按钮配色：选中蓝，锁定后灰，其余白。 */
function optionClass(key: string): string {
  const active = props.selected === key
  if (active) {
    return props.locked
      ? 'border-brand-500 bg-brand-50 text-brand-700'
      : 'border-brand-500 bg-brand-50 text-brand-700 ring-2 ring-brand-100'
  }
  return props.locked
    ? 'border-slate-200 bg-white text-slate-400'
    : 'border-slate-200 bg-white text-slate-700 active:bg-slate-50'
}

/** 键位徽标配色。 */
function badgeClass(key: string): string {
  return props.selected === key
    ? 'bg-brand-500 text-white'
    : 'bg-slate-100 text-slate-500'
}
</script>

<template>
  <article class="card space-y-4">
    <!-- 题头 -->
    <header class="flex flex-wrap items-center gap-2">
      <span class="rounded-full bg-brand-50 px-3 py-1 text-sm font-bold text-brand-600">
        第 {{ index + 1 }} / {{ total }} 题
      </span>
      <span
        v-for="tag in metaTags"
        :key="tag"
        class="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-500"
      >
        {{ tag }}
      </span>
    </header>

    <!-- 诚实披露：这道题不计入掌握度 -->
    <p
      v-if="isUnmapped"
      class="rounded-xl bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-700"
    >
      这道题暂时还没关联知识点，做对做错都不会计入掌握度统计
    </p>

    <!-- 题干：字号偏大，行高放宽 -->
    <p class="whitespace-pre-wrap break-words text-lg leading-relaxed text-slate-800">
      {{ question.stem }}
    </p>

    <!-- 选项：整行都是点击区，够手指点 -->
    <div class="space-y-3">
      <button
        v-for="item in choices"
        :key="item.key"
        type="button"
        class="flex w-full items-center gap-3 rounded-2xl border p-4 text-left transition"
        :class="optionClass(item.key)"
        :disabled="locked"
        @click="handleSelect(item.key)"
      >
        <span
          class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-base font-bold transition"
          :class="badgeClass(item.key)"
        >
          {{ item.key }}
        </span>
        <span class="min-w-0 flex-1 whitespace-pre-wrap break-words text-base leading-relaxed">
          {{ item.text }}
        </span>
      </button>
    </div>

    <p v-if="choices.length === 0" class="rounded-xl bg-slate-50 px-3 py-3 text-sm text-slate-500">
      这道题没有可选项，先跳过它吧
    </p>
  </article>
</template>

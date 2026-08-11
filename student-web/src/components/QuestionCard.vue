<script setup lang="ts">
/**
 * 练习题卡片：展示 q / a / type / knowledge / difficulty（答案默认折叠）。
 *
 * T8 新增 `answerLocked`：AI 老师出的练习题必须**跟老师走完一轮对话、拿到判词**
 * 才解锁答案——否则孩子一点开就抄答案，出题就白出了。
 * 锁住时**整个「看答案」按钮都不渲染**（不是禁用），断掉一切旁路；
 * 题库练习不传这个 prop，行为与改动前完全一致。
 */
import { computed, ref, watch } from 'vue'

import type { Exercise } from '@/api/chat'
import { formatMath } from '@/utils/formatMath'

interface Props {
  question: Exercise
  /** 序号，从 1 开始 */
  index?: number
  /** true 时锁住答案：不渲染展开按钮，只给一行提示 */
  answerLocked?: boolean
}

const props = withDefaults(defineProps<Props>(), { index: 0, answerLocked: false })

const showAnswer = ref<boolean>(false)

/** 从「已解锁」翻回「锁住」时（比如重新出题）要把已展开的答案收回去。 */
watch(
  () => props.answerLocked,
  (locked) => {
    if (locked) showAnswer.value = false
  },
)

/** 真正渲染答案的开关：锁住时无论本地状态如何都不给看。 */
const answerVisible = computed<boolean>(() => !props.answerLocked && showAnswer.value)

const roleLabel = computed<string>(() => {
  if (props.question.role === 'fix') return '修复练习'
  if (props.question.role === 'transfer') return '迁移练习'
  return '练习'
})

const difficultyLabel = computed<string>(() => {
  const level = Number(props.question.difficulty)
  if (level <= 1) return '易'
  if (level >= 5) return '难'
  return '中'
})

/** 题目和答案都走 LaTeX → 纯文本兜底，避免孩子看见 $\frac{5}{13}$。 */
const displayQuestion = computed<string>(() => formatMath(props.question.q ?? ''))
const displayAnswer = computed<string>(() => formatMath(props.question.a ?? ''))
</script>

<template>
  <article class="card space-y-3">
    <div class="flex flex-wrap items-center gap-2">
      <span class="rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-600">
        {{ index ? `${index}. ` : '' }}{{ roleLabel }}
      </span>
      <span v-if="question.knowledge" class="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">
        {{ question.knowledge }}
      </span>
      <span v-if="question.type" class="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">
        {{ question.type }}
      </span>
      <span class="rounded-full bg-amber-50 px-2.5 py-1 text-xs text-amber-600">
        难度 {{ difficultyLabel }}
      </span>
    </div>

    <p class="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-800">
      {{ displayQuestion }}
    </p>

    <div class="border-t border-dashed border-slate-200 pt-3">
      <!-- 锁住：连按钮都不给，只留一句解释，免得孩子以为坏了 -->
      <p v-if="answerLocked" class="text-sm text-slate-400">🔒 跟老师聊完这道题，答案才会出来</p>

      <template v-else>
        <button type="button" class="text-sm font-medium text-brand-600" @click="showAnswer = !showAnswer">
          {{ showAnswer ? '收起答案' : '先自己做，做完看答案' }}
        </button>
        <p
          v-if="answerVisible"
          class="mt-2 whitespace-pre-wrap break-words rounded-xl bg-slate-50 p-3 text-sm leading-relaxed text-slate-700"
        >
          {{ displayAnswer || '（AI 未给出参考答案）' }}
        </p>
      </template>
    </div>
  </article>
</template>

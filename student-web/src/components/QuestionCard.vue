<script setup lang="ts">
/** 练习题卡片：展示 q / a / type / knowledge / difficulty（答案默认折叠）。 */
import { computed, ref } from 'vue'

import type { Exercise } from '@/api/chat'

interface Props {
  question: Exercise
  /** 序号，从 1 开始 */
  index?: number
}

const props = withDefaults(defineProps<Props>(), { index: 0 })

const showAnswer = ref<boolean>(false)

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
      {{ question.q }}
    </p>

    <div class="border-t border-dashed border-slate-200 pt-3">
      <button type="button" class="text-sm font-medium text-brand-600" @click="showAnswer = !showAnswer">
        {{ showAnswer ? '收起答案' : '先自己做，做完看答案' }}
      </button>
      <p
        v-if="showAnswer"
        class="mt-2 whitespace-pre-wrap break-words rounded-xl bg-slate-50 p-3 text-sm leading-relaxed text-slate-700"
      >
        {{ question.a || '（AI 未给出参考答案）' }}
      </p>
    </div>
  </article>
</template>

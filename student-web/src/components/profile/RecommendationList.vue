<script setup lang="ts">
/**
 * 练习推荐面板（T04 · F9）。
 *
 * 列出后端按优先级排好的「该练什么」，每条给出推荐理由、掌握度、错率、主要错因、
 * 可练题数，并提供「去练习」入口（目前统一回 AI 老师页，待深链能力补齐）。
 */
import {
  ERROR_CATEGORY_LABEL,
  type ErrorCategory,
  type RecommendationData,
  type RecommendationItem,
} from '@/types/profile'

defineProps<{
  data: RecommendationData | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  (e: 'practice', payload: { kpId: string; kpName: string }): void
}>()

function errorLabel(category: string | null): string {
  if (!category) return ''
  return ERROR_CATEGORY_LABEL[category as ErrorCategory] || category
}

/** 是否展示「主要错因」行（有且非空时）。 */
function hasTopError(item: RecommendationItem): boolean {
  return Boolean(item.top_error_category)
}
</script>

<template>
  <section class="card">
    <header class="mb-3 flex items-center justify-between">
      <h2 class="text-base font-bold text-slate-800">该练什么</h2>
      <span v-if="data?.mode" class="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-400">
        {{ data.mode === 'cache' ? '缓存' : '实时' }}
      </span>
    </header>

    <p v-if="loading" class="py-6 text-center text-sm text-slate-400">正在生成推荐…</p>
    <p v-else-if="error" class="rounded-xl bg-red-50 px-3 py-3 text-sm text-red-500">
      {{ error }}
    </p>

    <template v-else-if="data && data.recommendations.length">
      <ul class="space-y-3">
        <li
          v-for="item in data.recommendations"
          :key="item.kp_id"
          class="rounded-xl border border-slate-100 bg-slate-50/60 p-3"
        >
          <div class="flex items-center justify-between gap-2">
            <h3 class="min-w-0 flex-1 truncate text-sm font-semibold text-slate-800">
              {{ item.kp_name }}
            </h3>
            <span class="shrink-0 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-600">
              优先 {{ Math.round(item.priority * 100) }}
            </span>
          </div>

          <p class="mt-1 text-sm leading-relaxed text-slate-600">{{ item.reason }}</p>

          <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
            <span>掌握度 {{ Math.round(item.score * 100) }}%</span>
            <span>错率 {{ Math.round(item.error_rate * 100) }}%</span>
            <span v-if="hasTopError(item)">主要错因：{{ errorLabel(item.top_error_category) }}</span>
            <span v-if="item.suggested_question_ids.length">
              可练 {{ item.suggested_question_ids.length }} 题
            </span>
          </div>

          <button
            type="button"
            class="btn-ghost mt-3 h-9 w-full text-sm"
            @click="emit('practice', { kpId: item.kp_id, kpName: item.kp_name })"
          >
            去练习
          </button>
        </li>
      </ul>
    </template>

    <p v-else class="py-6 text-center text-sm text-slate-400">
      暂时没有特别要补的点，保持手感继续练就好
    </p>
  </section>
</template>

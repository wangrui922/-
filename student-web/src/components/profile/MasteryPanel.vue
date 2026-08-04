<script setup lang="ts">
/**
 * 掌握度面板（T04 · F7）。
 *
 * 数据来自 profile store 的 mastery；本组件只负责展示，不做请求。
 * 支持三种态：loading / error（局部降级）/ 空数据 / 正常列表。
 */
import MasteryBar from '@/components/common/MasteryBar.vue'
import type { MasteryData, MasteryLevel } from '@/types/profile'

defineProps<{
  data: MasteryData | null
  loading: boolean
  error: string
}>()

const LEVEL_LABEL: Record<MasteryLevel, string> = {
  0: '未入门',
  1: '薄弱',
  2: '基本掌握',
  3: '熟练',
}

const LEVEL_CHIP: Record<MasteryLevel, string> = {
  0: 'bg-red-50 text-red-500',
  1: 'bg-amber-50 text-amber-600',
  2: 'bg-blue-50 text-blue-600',
  3: 'bg-green-50 text-green-600',
}

/** 等级汇总，按 0→3 顺序展示（data 为 null 时给全 0）。 */
function levelChips(data: MasteryData | null): { level: MasteryLevel; count: number }[] {
  const counts = data?.summary?.level_count ?? {}
  return ([0, 1, 2, 3] as MasteryLevel[]).map((lv) => ({
    level: lv,
    count: Number(counts[String(lv)] ?? 0),
  }))
}
</script>

<template>
  <section class="card">
    <header class="mb-3 flex items-center justify-between">
      <h2 class="text-base font-bold text-slate-800">知识点掌握度</h2>
      <span
        v-if="data?.mode"
        class="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-400"
      >
        {{ data.mode === 'cache' ? '缓存' : '实时' }}
      </span>
    </header>

    <p v-if="loading" class="py-6 text-center text-sm text-slate-400">正在算掌握度…</p>
    <p v-else-if="error" class="rounded-xl bg-red-50 px-3 py-3 text-sm text-red-500">
      {{ error }}
    </p>

    <template v-else-if="data && data.mastery.length">
      <div class="mb-4 flex flex-wrap gap-2">
        <span
          v-for="chip in levelChips(data)"
          :key="chip.level"
          class="rounded-full px-2.5 py-1 text-xs font-medium"
          :class="LEVEL_CHIP[chip.level]"
        >
          {{ LEVEL_LABEL[chip.level] }} {{ chip.count }}
        </span>
      </div>

      <ul class="space-y-3">
        <li v-for="item in data.mastery" :key="item.kp_id">
          <MasteryBar :score="item.score" :level="item.level" :label="item.kp_name" />
          <p class="mt-1 pl-1 text-xs text-slate-400">
            练了 {{ item.attempts }} 次 · 对 {{ item.correct_count }} 次 · 错率
            {{ Math.round(item.error_rate * 100) }}%
          </p>
        </li>
      </ul>
    </template>

    <p v-else class="py-6 text-center text-sm text-slate-400">
      还没有足够的练习数据，做几道题就能看到掌握度啦
    </p>
  </section>
</template>

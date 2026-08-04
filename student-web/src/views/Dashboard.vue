<script setup lang="ts">
/**
 * 学习画像看板（T04 · F6，首页）。
 *
 * 三路并行加载（掌握度 / 错因 / 推荐），每路独立降级；
 * 数据从 profile store 读取（stores/profile.ts）。
 */
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import ErrorPatternChart from '@/components/profile/ErrorPatternChart.vue'
import MasteryPanel from '@/components/profile/MasteryPanel.vue'
import RecommendationList from '@/components/profile/RecommendationList.vue'
import { loadDashboard, profileState } from '@/stores/profile'

const router = useRouter()

onMounted(() => {
  void loadDashboard()
})

function handlePractice(): void {
  void router.push({ name: 'ai-teacher' })
}

/** 强制重新计算（绕过缓存）。 */
function handleRetryAll(): void {
  void loadDashboard({ force: true })
}
</script>

<template>
  <div class="h-full overflow-y-auto overflow-x-hidden bg-slate-50">
    <div class="space-y-4 px-4 py-4">
      <MasteryPanel
        :data="profileState.mastery"
        :loading="profileState.masteryState.loading"
        :error="profileState.masteryState.error"
      />

      <ErrorPatternChart
        :data="profileState.errorPatterns"
        :loading="profileState.errorPatternsState.loading"
        :error="profileState.errorPatternsState.error"
      />

      <RecommendationList
        :data="profileState.recommendations"
        :loading="profileState.recommendationsState.loading"
        :error="profileState.recommendationsState.error"
        @practice="handlePractice"
      />

      <button type="button" class="btn-ghost w-full" @click="handleRetryAll">
        重新计算画像
      </button>
    </div>
  </div>
</template>

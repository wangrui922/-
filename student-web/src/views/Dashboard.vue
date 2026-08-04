<script setup lang="ts">
/**
 * 学习画像看板（T04 · F6，首页）+ Epic6 家长周报入口。
 *
 * 三路并行加载（掌握度 / 错因 / 推荐），每路独立降级；
 * 数据从 profile store 读取（stores/profile.ts）。
 * 家长周报：点按钮按需生成，生成后弹出分享链接弹窗（免费、免注册）。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import ShareLinkDialog from '@/components/report/ShareLinkDialog.vue'
import ErrorPatternChart from '@/components/profile/ErrorPatternChart.vue'
import MasteryPanel from '@/components/profile/MasteryPanel.vue'
import RecommendationList from '@/components/profile/RecommendationList.vue'
import { toMessage } from '@/api/http'
import { generateReport } from '@/api/report'
import { loadDashboard, profileState } from '@/stores/profile'
import type { GenerateReportData } from '@/types/report'

const router = useRouter()

const shareOpen = ref(false)
const generating = ref(false)
const shareData = ref<GenerateReportData | null>(null)
const generateError = ref('')

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

/** 生成家长周报并弹出分享链接。 */
async function handleGenerateReport(): Promise<void> {
  if (generating.value) return
  generating.value = true
  generateError.value = ''
  try {
    const data = await generateReport({ force: false })
    shareData.value = data
    shareOpen.value = true
  } catch (err) {
    generateError.value = toMessage(err, '周报生成失败，请稍后再试')
  } finally {
    generating.value = false
  }
}
</script>

<template>
  <div class="h-full overflow-y-auto overflow-x-hidden bg-slate-50">
    <div class="space-y-4 px-4 py-4">
      <!-- 家长周报入口 -->
      <section class="card">
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <h2 class="text-base font-bold text-slate-800">家长周报</h2>
            <p class="mt-0.5 text-xs text-slate-400">生成本周学习小结，把链接发给孩子家长</p>
          </div>
          <button
            type="button"
            class="btn-primary shrink-0 px-4"
            :disabled="generating"
            @click="handleGenerateReport"
          >
            {{ generating ? '生成中…' : '生成周报' }}
          </button>
        </div>
        <p v-if="generateError" class="mt-2 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-500">
          {{ generateError }}
        </p>
      </section>

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

    <ShareLinkDialog :open="shareOpen" :data="shareData" @close="shareOpen = false" />
  </div>
</template>

<script setup lang="ts">
/**
 * 学习画像看板（T04 · F6，首页）+ Epic6 家长周报入口。
 *
 * 三路并行加载（掌握度 / 错因 / 推荐），每路独立降级；
 * 数据从 profile store 读取（stores/profile.ts）。
 * 家长周报：点按钮按需生成，生成后弹出分享链接弹窗（免费、免注册）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import ShareLinkDialog from '@/components/report/ShareLinkDialog.vue'
import ErrorPatternChart from '@/components/profile/ErrorPatternChart.vue'
import MasteryPanel from '@/components/profile/MasteryPanel.vue'
import RecommendationList from '@/components/profile/RecommendationList.vue'
import KnowledgeStarMap from '@/components/profile/KnowledgeStarMap.vue'
import AbilityRadar from '@/components/profile/AbilityRadar.vue'
import TutorAvatar from '@/components/TutorAvatar.vue'
import { useStreak } from '@/stores/streak'
import { toMessage } from '@/api/http'
import { generateReport } from '@/api/report'
import { loadDashboard, profileState } from '@/stores/profile'
import type { GenerateReportData } from '@/types/report'

/** 小玥老师问候语：按当前时段给一句暖话，降低孩子打开 App 的距离感。 */
const greeting = computed<string>(() => {
  const h = new Date().getHours()
  const hello = h < 11 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好'
  return `${hello}！今天想先练几道题，还是让小玥老师陪你看看错题？`
})

const router = useRouter()

/** 连续打卡天数（本地维护，打卡=今天做过题）。 */
const { streakDays } = useStreak()

const shareOpen = ref(false)
const generating = ref(false)
const shareData = ref<GenerateReportData | null>(null)
const generateError = ref('')

onMounted(() => {
  void loadDashboard()
})

/** 顶部「去练习」：不带知识点，到练习页自己挑条件。 */
function handlePractice(): void {
  void router.push({ name: 'practice' })
}

/**
 * 推荐项「练这个」：带 kp_id 深链到练习页，落地即开练。
 *
 * 推荐项的 kp_id 与题库 `questions.kp_id` 是**同一套 ID**（都指向
 * `knowledge_points.id`，已实测 JOIN 命中），所以直接透传，不需要映射层。
 * query 键名保持 snake_case，与后端 `/practice/session` 的入参同名。
 *
 * kp_name 只用于练习页回显「正在练：分数」，不参与抽题请求。
 *
 * @param payload 推荐项的知识点 ID 与名称
 */
function handlePracticeKp(payload: { kp_id: string; kp_name: string }): void {
  void router.push({
    name: 'practice',
    query: { kp_id: payload.kp_id, kp_name: payload.kp_name },
  })
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
      <!-- 小玥老师问候卡（升级版陪伴感） -->
      <section class="card flex items-center gap-3 bg-gradient-to-br from-orange-50 to-blue-50">
        <TutorAvatar :size="48" />
        <div class="min-w-0 flex-1">
          <p class="text-base font-bold text-slate-800">小玥老师来啦 👋</p>
          <p class="mt-0.5 text-xs text-slate-500">{{ greeting }}</p>
        </div>
        <div
          v-if="streakDays > 0"
          class="flex shrink-0 flex-col items-center rounded-2xl bg-white/70 px-3 py-1.5 shadow-sm"
        >
          <span class="text-lg font-extrabold leading-none text-orange-500">🔥 {{ streakDays }}</span>
          <span class="text-[10px] leading-tight text-slate-500">连续打卡</span>
        </div>
      </section>

      <!-- 题库练习入口（题库消费层 · F1） -->
      <section class="card">
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <h2 class="text-base font-bold text-slate-800">开始练习</h2>
            <p class="mt-0.5 text-xs text-slate-400">从题库挑几道题练一练，做错老师带你想</p>
          </div>
          <button type="button" class="btn-primary shrink-0 px-5" @click="handlePractice">
            去练习
          </button>
        </div>
      </section>

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

      <KnowledgeStarMap
        :data="profileState.mastery"
        :loading="profileState.masteryState.loading"
        :error="profileState.masteryState.error"
        @practice="handlePracticeKp"
      />

      <AbilityRadar
        :data="profileState.radar"
        :loading="profileState.radarState.loading"
        :error="profileState.radarState.error"
      />

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
        @practice="handlePracticeKp"
      />

      <button type="button" class="btn-ghost w-full" @click="handleRetryAll">
        重新计算画像
      </button>
    </div>

    <ShareLinkDialog :open="shareOpen" :data="shareData" @close="shareOpen = false" />
  </div>
</template>

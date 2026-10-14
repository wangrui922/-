<script setup lang="ts">
/**
 * 学习画像看板（首页）+ 家长周报入口。
 *
 * 视觉升级为「游戏化成长主页」：
 *   - 顶部游戏 HUD：小玥老师 + 等级 + 经验条 + 连击
 *   - 游戏数据卡：知识宝石 / 连击 / 能力维度
 *   - 成就徽章墙（由真实数据派生，不造假）
 *   - 趣味入口 tile + 家长周报 + 学习数据面板
 * 三路并行加载（掌握度 / 错因 / 推荐），每路独立降级；
 * 数据从 profile store 读取（stores/profile.ts）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import ShareLinkDialog from '@/components/report/ShareLinkDialog.vue'
import ErrorPatternChart from '@/components/profile/ErrorPatternChart.vue'
import MasteryPanel from '@/components/profile/MasteryPanel.vue'
import RecommendationList from '@/components/profile/RecommendationList.vue'
import KnowledgeStarMap from '@/components/profile/KnowledgeStarMap.vue'
import AbilityRadar from '@/components/profile/AbilityRadar.vue'
import TileIcon from '@/components/dashboard/TileIcon.vue'
import tutorAvatar from '@/assets/tutor-yueyue-v2.png'
import { useStreak } from '@/stores/streak'
import { toMessage } from '@/api/http'
import { generateReport } from '@/api/report'
import { loadDashboard, profileState } from '@/stores/profile'
import type { GenerateReportData } from '@/types/report'

const timeLabel = computed<string>(() => {
  const h = new Date().getHours()
  return h < 11 ? '早' : h < 14 ? '中午' : h < 18 ? '下午' : '晚上'
})

/* ---------- 游戏化派生指标（全部来自真实数据） ---------- */
const { streakDays } = useStreak()

/**
 * 掌握度展示数据源：DNA-005 聚合（埋点实时驱动）优先，失败降级 T04 物化缓存。
 * 由同一数据源派生 HUD / 星图 / 面板三态，避免"面板显示错误、HUD 却用旧数据"的分叉。
 */
const masteryDisplay = computed(() => profileState.dnaMastery ?? profileState.mastery)
const masteryLoading = computed<boolean>(
  () =>
    !masteryDisplay.value &&
    (profileState.dnaState.loading || profileState.masteryState.loading),
)
const masteryError = computed<string>(() => {
  if (masteryDisplay.value) return ''
  return profileState.dnaState.error || profileState.masteryState.error
})

const masteredCount = computed<number>(() => {
  const list = masteryDisplay.value?.mastery ?? []
  return list.filter((m) => m.level >= 2).length // 基本掌握 / 熟练 = 已挖到的宝石
})
const totalKp = computed<number>(() => masteryDisplay.value?.summary?.total_kp ?? 0)
const radarDims = computed<number>(() => profileState.radar?.axes?.length ?? 0)

// 经验 = 连击*10 + 已掌握知识点*15 + 总知识点*5（趣味公式，驱动等级）
const xp = computed<number>(() => streakDays.value * 10 + masteredCount.value * 15 + totalKp.value * 5)
const level = computed<number>(() => Math.floor(xp.value / 100) + 1)
const xpInLevel = computed<number>(() => xp.value % 100)
const levelProgress = computed<number>(() => Math.min(100, Math.round((xpInLevel.value / 100) * 100)))
const xpToNext = computed<number>(() => 100 - xpInLevel.value)

function levelTitle(lv: number): string {
  if (lv >= 8) return '数学大师'
  if (lv >= 6) return '学霸养成'
  if (lv >= 4) return '小专家'
  if (lv >= 2) return '进步之星'
  return '新手上路'
}

interface Badge {
  icon: string
  label: string
  got: boolean
}
const badges = computed<Badge[]>(() => [
  { icon: '🔥', label: '坚持打卡', got: streakDays.value >= 3 },
  { icon: '⭐', label: '知识新星', got: masteredCount.value >= 1 },
  { icon: '💎', label: '通关达人', got: masteredCount.value >= 5 },
  { icon: '📚', label: '博学多闻', got: totalKp.value >= 8 },
  { icon: '🏆', label: '全能学霸', got: radarDims.value >= 4 },
  { icon: '🌟', label: '七日活动', got: streakDays.value >= 7 },
])

/* ---------- 趣味入口 ---------- */
interface Tile {
  key: 'practice' | 'ai' | 'mistakes' | 'subscription' | 'review' | 'game'
  label: string
  desc: string
  grad: string
  to: { name: string }
  badge?: string
}
const tiles: readonly Tile[] = [
  {
    key: 'game',
    label: '游戏大厅',
    desc: '挑战闯关',
    grad: 'from-violet-500 to-fuchsia-500',
    to: { name: 'game-hub' },
    badge: 'NEW',
  },
  {
    key: 'review',
    label: '今日复习',
    desc: '科学巩固',
    grad: 'from-brand-500 to-brand-600',
    to: { name: 'review' },
    badge: 'NEW',
  },
  {
    key: 'practice',
    label: '去练习',
    desc: '挑题闯关',
    grad: 'from-coral-400 to-coral-500',
    to: { name: 'practice' },
    badge: 'HOT',
  },
  {
    key: 'ai',
    label: '问小玥老师',
    desc: '不懂就问',
    grad: 'from-sky-400 to-sky-500',
    to: { name: 'ai-teacher' },
  },
  {
    key: 'mistakes',
    label: '错题宝藏',
    desc: '查漏补缺',
    grad: 'from-mint-400 to-sky-400',
    to: { name: 'mistakes' },
  },
  {
    key: 'subscription',
    label: '会员中心',
    desc: '升级权益',
    grad: 'from-amber-400 to-orange-500',
    to: { name: 'subscription' },
    badge: 'VIP',
  },
]

const router = useRouter()

const shareOpen = ref(false)
const generating = ref(false)
const shareData = ref<GenerateReportData | null>(null)
const generateError = ref('')

onMounted(() => {
  void loadDashboard()
})

function onTile(t: Tile): void {
  void router.push(t.to)
}

function handlePracticeKp(payload: { kp_id: string; kp_name: string }): void {
  void router.push({
    name: 'practice',
    query: { kp_id: payload.kp_id, kp_name: payload.kp_name },
  })
}

function handleRetryAll(): void {
  void loadDashboard({ force: true })
}

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
  <div class="h-full overflow-y-auto overflow-x-hidden px-4 pb-6 pt-4">
    <div class="space-y-4">
      <!-- 游戏 HUD：小玥老师 + 等级 + 经验条 + 连击 -->
      <section
        class="relative overflow-hidden rounded-[2rem] bg-gradient-to-br from-brand-500 via-brand-500 to-coral-400 p-5 text-white shadow-float"
      >
        <div class="pointer-events-none absolute -right-6 -top-8 h-28 w-28 rounded-full bg-white/15" />
        <div class="pointer-events-none absolute -bottom-10 -left-4 h-32 w-32 rounded-full bg-white/10" />

        <!-- 卡通装饰：HUD 内漂浮 -->
        <div class="pointer-events-none absolute right-3 top-2 animate-bounce-soft" style="animation-delay: 0.2s">
          <svg width="28" height="28" viewBox="0 0 28 28" xmlns="http://www.w3.org/2000/svg">
            <path d="M14 1l3.4 6.9 7.6 1.1-5.5 5.4 1.3 7.6L14 18.5l-6.8 3.5 1.3-7.6-5.5-5.4 7.6-1.1z" fill="#FFD27A" stroke="#E89A1F" stroke-width="1.2" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 animate-float" style="animation-delay: 1.1s; opacity: 0.7">
          <svg width="56" height="28" viewBox="0 0 56 28" xmlns="http://www.w3.org/2000/svg">
            <ellipse cx="28" cy="14" rx="26" ry="11" fill="white" opacity="0.85"/>
            <text x="28" y="19" text-anchor="middle" font-size="11" font-weight="800" fill="#7C5CFC" font-family="system-ui">3+2=5</text>
          </svg>
        </div>
        <div class="pointer-events-none absolute right-12 bottom-3 animate-bounce-soft" style="animation-delay: 0.5s; transform: rotate(20deg)">
          <svg width="22" height="32" viewBox="0 0 22 32" xmlns="http://www.w3.org/2000/svg">
            <path d="M11 1c2.5 4 5 8 5 13v9H6v-9c0-5 2.5-9 5-13z" fill="white" stroke="#7C5CFC" stroke-width="1.2"/>
            <circle cx="11" cy="11" r="2" fill="#7C5CFC"/>
            <polygon points="6,21 3,27 6,27" fill="#FF7043"/>
            <polygon points="16,21 19,27 16,27" fill="#FF7043"/>
            <path d="M8 28c0.5 2 2 3 3 3.5 1-0.5 2.5-1.5 3-3.5" fill="#FFC24B"/>
          </svg>
        </div>

        <div class="relative flex items-center gap-3">
          <div class="relative h-16 w-16 animate-float overflow-hidden rounded-2xl border-2 border-white/60 bg-white/20">
            <img :src="tutorAvatar" alt="小玥老师" class="h-full w-full object-contain" />
            <span class="absolute -bottom-1 -right-1 flex h-6 min-w-6 items-center justify-center rounded-full bg-amber-400 px-1 text-[10px] font-extrabold text-white shadow">
              L{{ level }}
            </span>
          </div>
          <div class="min-w-0 flex-1">
            <p class="text-sm font-semibold text-white/85">{{ timeLabel }}好呀～我是小玥老师</p>
            <p class="mt-0.5 text-base font-extrabold leading-snug">{{ levelTitle(level) }} · Lv.{{ level }}</p>
          </div>
          <div
            v-if="streakDays > 0"
            class="flex shrink-0 flex-col items-center rounded-2xl bg-white/20 px-3 py-1.5 backdrop-blur"
          >
            <span class="text-lg font-extrabold leading-none animate-bounce-soft">🔥 {{ streakDays }}</span>
            <span class="text-[10px] text-white/80">连续打卡</span>
          </div>
        </div>

        <!-- 经验条 -->
        <div class="relative mt-4">
          <div class="flex items-center justify-between text-[11px] font-medium text-white/85">
            <span>经验 {{ xpInLevel }}/100</span>
            <span>还差 {{ xpToNext }} 经验升级</span>
          </div>
          <div class="mt-1.5 h-2.5 w-full overflow-hidden rounded-full bg-white/25">
            <div
              class="h-full rounded-full bg-gradient-to-r from-amber-300 to-amber-400 transition-all duration-700"
              :style="{ width: levelProgress + '%' }"
            />
          </div>
        </div>
      </section>

      <!-- 游戏数据卡 -->
      <section class="grid grid-cols-3 gap-3">
        <div class="card flex flex-col items-center justify-center py-3 text-center">
          <span class="text-2xl font-extrabold text-brand-600">{{ masteredCount }}</span>
          <span class="mt-0.5 text-[11px] font-semibold text-ink-soft">💎 知识宝石</span>
        </div>
        <div class="card flex flex-col items-center justify-center py-3 text-center">
          <span class="text-2xl font-extrabold text-coral-500">{{ streakDays }}</span>
          <span class="mt-0.5 text-[11px] font-semibold text-ink-soft">🔥 连续打卡</span>
        </div>
        <div class="card flex flex-col items-center justify-center py-3 text-center">
          <span class="text-2xl font-extrabold text-mint-500">{{ radarDims }}</span>
          <span class="mt-0.5 text-[11px] font-semibold text-ink-soft">📊 能力维度</span>
        </div>
      </section>

      <!-- 成就徽章墙 -->
      <section class="card">
        <div class="mb-2 flex items-center justify-between">
          <p class="text-sm font-extrabold text-ink">🏅 我的成就</p>
          <span class="text-[11px] font-semibold text-ink-faint">{{ badges.filter((b) => b.got).length }}/{{ badges.length }} 已解锁</span>
        </div>
        <div class="grid grid-cols-3 gap-2">
          <div
            v-for="b in badges"
            :key="b.label"
            class="flex flex-col items-center gap-1 rounded-2xl border px-1 py-2 text-center transition"
            :class="b.got ? 'border-amber-200 bg-amber-50' : 'border-slate-100 bg-slate-50 opacity-50'"
          >
            <span class="text-xl leading-none grayscale" :class="{ 'grayscale-0': b.got }">{{ b.icon }}</span>
            <span class="text-[10px] font-semibold leading-tight" :class="b.got ? 'text-ink' : 'text-ink-faint'">{{ b.label }}</span>
          </div>
        </div>
      </section>

      <!-- 趣味入口 -->
      <section class="grid grid-cols-2 gap-3">
        <button
          v-for="t in tiles"
          :key="t.key"
          type="button"
          class="tile animate-fade-up bg-gradient-to-br text-left"
          :class="t.grad"
          @click="onTile(t)"
        >
          <div class="flex items-center justify-between">
            <span class="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/30 shadow-[inset_0_-3px_6px_rgba(0,0,0,0.08)] backdrop-blur-sm">
              <TileIcon :kind="t.key" />
            </span>
            <span
              v-if="t.badge"
              class="rounded-full bg-white/30 px-2 py-0.5 text-[10px] font-extrabold tracking-wide"
            >{{ t.badge }}</span>
          </div>
          <div class="mt-3">
            <p class="text-base font-extrabold text-white">{{ t.label }}</p>
            <p class="text-xs font-medium text-white/85">{{ t.desc }}</p>
          </div>
        </button>
      </section>

      <!-- 家长周报 -->
      <section class="relative overflow-hidden rounded-3xl bg-gradient-to-r from-mint-400 to-sky-400 p-4 text-white shadow-soft">
        <div class="pointer-events-none absolute -right-4 -top-4 h-20 w-20 rounded-full bg-white/20" />
        <div class="relative flex items-center justify-between gap-3">
          <div class="min-w-0">
            <h2 class="text-base font-extrabold">📊 家长周报</h2>
            <p class="mt-0.5 text-xs font-medium text-white/85">一键生成本周小结，发给爸爸妈妈</p>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-2xl bg-white px-4 py-2 text-sm font-bold text-mint-600 shadow transition active:scale-95"
            :disabled="generating"
            @click="handleGenerateReport"
          >
            {{ generating ? '生成中…' : '生成' }}
          </button>
        </div>
        <p v-if="generateError" class="relative mt-2 rounded-2xl bg-white/90 px-3 py-2 text-sm font-medium text-red-500">
          {{ generateError }}
        </p>
      </section>

      <!-- 学习数据 -->
      <p class="px-1 pt-1 text-sm font-extrabold text-ink">📈 我的学习数据</p>

      <KnowledgeStarMap
        :data="masteryDisplay"
        :loading="masteryLoading"
        :error="masteryError"
        @practice="handlePracticeKp"
      />

      <AbilityRadar
        :data="profileState.radar"
        :loading="profileState.radarState.loading"
        :error="profileState.radarState.error"
      />

      <MasteryPanel
        :data="masteryDisplay"
        :loading="masteryLoading"
        :error="masteryError"
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

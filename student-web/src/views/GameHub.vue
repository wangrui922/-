<script setup lang="ts">
/**
 * 游戏大厅（方向六 · F7）。
 *
 * 展示学生游戏化数据总览：
 *   - HUD：总积分 / 本周 / 本月 / 最佳成绩 / 徽章数
 *   - 游戏入口 tile：限时挑战 + 回合战斗
 *   - 最近游戏记录
 *   - 快捷入口：徽章墙 / 排行榜
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getHub } from '@/api/game'
import type { HubData, GameHistoryItem } from '@/api/game'

const router = useRouter()
const loading = ref(false)
const hub = ref<HubData | null>(null)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    hub.value = await getHub()
  } catch (e: any) {
    error.value = e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const recentGames = computed<GameHistoryItem[]>(() => hub.value?.recent_games ?? [])

function goSpeed() {
  router.push({ name: 'speed-challenge' })
}

function goBattle() {
  router.push({ name: 'math-battle' })
}

function goScenes() {
  router.push({ name: 'scene-quiz' })
}

function goBadges() {
  router.push({ name: 'badge-wall' })
}

function goHubLeaderboard() {
  router.push({ name: 'game-leaderboard' })
}

function formatGameType(t: string) {
  return t === 'speed_challenge' ? '限时挑战' : t === 'math_battle' ? '回合战斗' : t
}

function formatDate(iso: string | null) {
  if (!iso) return '-'
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
</script>

<template>
  <div class="max-w-3xl mx-auto p-4 space-y-6">
    <h1 class="text-2xl font-bold text-slate-800">🎮 游戏大厅</h1>

    <!-- 加载 / 错误 -->
    <div v-if="loading" class="text-slate-500">加载中…</div>
    <div v-else-if="error" class="text-red-500">{{ error }}</div>

    <template v-else-if="hub">
      <!-- HUD 卡片 -->
      <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div class="bg-gradient-to-br from-amber-400 to-orange-500 rounded-xl p-4 text-white shadow">
          <div class="text-xs opacity-90">总积分</div>
          <div class="text-2xl font-bold">{{ hub.total_points }}</div>
        </div>
        <div class="bg-white rounded-xl p-4 shadow border border-slate-100">
          <div class="text-xs text-slate-500">本周积分</div>
          <div class="text-xl font-bold text-slate-800">{{ hub.weekly_points }}</div>
        </div>
        <div class="bg-white rounded-xl p-4 shadow border border-slate-100">
          <div class="text-xs text-slate-500">本月积分</div>
          <div class="text-xl font-bold text-slate-800">{{ hub.monthly_points }}</div>
        </div>
        <div class="bg-white rounded-xl p-4 shadow border border-slate-100">
          <div class="text-xs text-slate-500">限时挑战最高分</div>
          <div class="text-xl font-bold text-slate-800">{{ hub.speed_best_score }}</div>
        </div>
        <div class="bg-white rounded-xl p-4 shadow border border-slate-100">
          <div class="text-xs text-slate-500">战斗最高波次</div>
          <div class="text-xl font-bold text-slate-800">{{ hub.battle_best_wave }}</div>
        </div>
        <div class="bg-white rounded-xl p-4 shadow border border-slate-100">
          <div class="text-xs text-slate-500">徽章</div>
          <div class="text-xl font-bold text-slate-800">{{ hub.badge_count }}/{{ hub.badge_total }}</div>
        </div>
      </div>

      <!-- 游戏入口 -->
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <button
          @click="goSpeed"
          class="group relative overflow-hidden rounded-2xl bg-gradient-to-br from-sky-400 to-blue-600 p-6 text-left text-white shadow-lg transition hover:scale-[1.02] active:scale-[0.98]"
        >
          <div class="text-3xl mb-2">⚡</div>
          <div class="text-lg font-bold">限时挑战</div>
          <div class="text-sm opacity-90 mt-1">60 秒速算，连击加分！</div>
        </button>
        <button
          @click="goBattle"
          class="group relative overflow-hidden rounded-2xl bg-gradient-to-br from-rose-400 to-red-600 p-6 text-left text-white shadow-lg transition hover:scale-[1.02] active:scale-[0.98]"
        >
          <div class="text-3xl mb-2">⚔️</div>
          <div class="text-lg font-bold">回合战斗</div>
          <div class="text-sm opacity-90 mt-1">答对=攻击，答错=扣血，通关 10 波！</div>
        </button>
        <button
          @click="goScenes"
          class="group relative overflow-hidden rounded-2xl bg-gradient-to-br from-emerald-400 to-teal-600 p-6 text-left text-white shadow-lg transition hover:scale-[1.02] active:scale-[0.98]"
        >
          <div class="text-3xl mb-2">🏪</div>
          <div class="text-lg font-bold">生活场景题库</div>
          <div class="text-sm opacity-90 mt-1">购物、分蛋糕、时间、路程</div>
        </button>
        <button
          @click="goBadges"
          class="group relative overflow-hidden rounded-2xl bg-gradient-to-br from-amber-400 to-orange-500 p-6 text-left text-white shadow-lg transition hover:scale-[1.02] active:scale-[0.98]"
        >
          <div class="text-3xl mb-2">🏅</div>
          <div class="text-lg font-bold">徽章墙</div>
          <div class="text-sm opacity-90 mt-1">收集荣耀，点亮徽章</div>
        </button>
      </div>

      <!-- 快捷入口 -->
      <div class="flex gap-3">
        <button
          @click="goHubLeaderboard"
          class="flex-1 rounded-xl bg-white border border-slate-200 p-3 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
        >
          📊 排行榜
        </button>
      </div>

      <!-- 最近游戏 -->
      <div v-if="recentGames.length" class="bg-white rounded-xl border border-slate-100 shadow p-4">
        <h2 class="text-sm font-bold text-slate-700 mb-3">最近游戏</h2>
        <div class="space-y-2">
          <div
            v-for="g in recentGames"
            :key="g.id"
            class="flex items-center justify-between text-sm"
          >
            <div class="flex items-center gap-2">
              <span class="text-base">{{ g.game_type === 'speed_challenge' ? '⚡' : '⚔️' }}</span>
              <span class="text-slate-700">{{ formatGameType(g.game_type) }}</span>
              <span class="text-slate-400 text-xs">{{ formatDate(g.ended_at) }}</span>
            </div>
            <div class="text-slate-600">
              <span class="font-bold text-slate-800">{{ g.score }}</span> 分
              <span class="text-xs text-slate-400 ml-1">{{ g.correct_count }}/{{ g.correct_count + g.wrong_count }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

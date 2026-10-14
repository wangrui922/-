<script setup lang="ts">
/**
 * 游戏排行榜（方向六 · 限时挑战 / 回合战斗周榜月榜）。
 *
 * 对齐后端 GET /api/game/leaderboard：
 *   - rank_type: weekly（默认）| monthly
 *   - 每行：名次 / 学生 / 得分 / 场次 / 最佳波次
 *
 * 展示层约束：
 *   - 前三名用奖牌样式，其余用序号；
 *   - 我的名字（从 auth store 取 real_name）高亮；
 *   - 周榜 / 月榜 Tab 切换。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getLeaderboard, type LeaderboardItem } from '@/api/game'
import { toMessage } from '@/api/http'
import { authState } from '@/stores/auth'

const router = useRouter()

function goHub(): void {
  void router.push({ name: 'game-hub' })
}

type RankType = 'weekly' | 'monthly'
const rankType = ref<RankType>('weekly')
const rows = ref<LeaderboardItem[]>([])
const loading = ref(false)
const error = ref('')

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    rows.value = await getLeaderboard({ rank_type: rankType.value, top_n: 50 })
  } catch (e) {
    error.value = toMessage(e, '排行榜加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)

function switchType(t: RankType): void {
  if (t === rankType.value) return
  rankType.value = t
  void load()
}

function medal(rank: number): string {
  if (rank === 1) return '🥇'
  if (rank === 2) return '🥈'
  if (rank === 3) return '🥉'
  return String(rank)
}

function isMe(item: LeaderboardItem): boolean {
  return !!authState.studentId && item.student_id === authState.studentId
}
</script>

<template>
  <div class="mx-auto max-w-xl p-4">
    <div class="mb-3 flex items-center justify-between">
      <button type="button" class="btn-ghost px-3 py-1.5 text-sm" @click="goHub">← 返回大厅</button>
      <h1 class="text-base font-extrabold text-ink">📊 排行榜</h1>
      <span class="w-16" />
    </div>

    <!-- Tab 切换 -->
    <div class="mb-3 flex gap-1 rounded-full bg-slate-100 p-1">
      <button
        type="button"
        class="flex-1 rounded-full py-1.5 text-sm font-bold transition"
        :class="rankType === 'weekly' ? 'bg-white text-brand-600 shadow-soft' : 'text-ink-faint'"
        @click="switchType('weekly')"
      >周榜</button>
      <button
        type="button"
        class="flex-1 rounded-full py-1.5 text-sm font-bold transition"
        :class="rankType === 'monthly' ? 'bg-white text-brand-600 shadow-soft' : 'text-ink-faint'"
        @click="switchType('monthly')"
      >月榜</button>
    </div>

    <p v-if="error" class="mb-3 rounded-xl bg-red-50 px-3 py-2 text-sm font-medium text-red-500">{{ error }}</p>
    <p v-else-if="loading" class="py-6 text-center text-sm text-ink-faint">加载中…</p>
    <p v-else-if="!rows.length" class="py-6 text-center text-sm text-ink-faint">还没有上榜记录，快去挑战吧～</p>

    <div v-else class="card divide-y divide-slate-100 p-2">
      <div
        v-for="item in rows"
        :key="item.rank"
        class="flex items-center gap-3 px-2 py-2.5"
        :class="isMe(item) ? 'rounded-xl bg-brand-50' : ''"
      >
        <span class="w-8 shrink-0 text-center text-base font-extrabold" :class="item.rank <= 3 ? '' : 'text-ink-faint'">
          {{ medal(item.rank) }}
        </span>
        <span class="min-w-0 flex-1 truncate text-sm font-bold text-slate-800">
          {{ item.student_name || '神秘同学' }}
          <span v-if="isMe(item)" class="ml-1 rounded-full bg-brand-500 px-1.5 py-0.5 text-[10px] font-extrabold text-white">我</span>
        </span>
        <div class="shrink-0 text-right">
          <span class="text-base font-extrabold text-brand-600">{{ item.score }}</span>
          <span class="text-xs text-ink-faint"> 分</span>
        </div>
        <div class="w-20 shrink-0 text-right text-xs text-ink-faint">
          场次 {{ item.games_count }} · 波 {{ item.best_wave }}
        </div>
      </div>
    </div>
  </div>
</template>
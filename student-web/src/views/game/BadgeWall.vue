<script setup lang="ts">
/**
 * 徽章墙（方向六 · 第二期）。
 *
 * 数据源：GET /api/game/badges（BadgeItem[]）。
 * 展示：
 *   - 顶部：已解锁 / 总数统计 + 分类筛选标签；
 *   - 中部：徽章网格，已解锁彩色展示，未解锁灰色遮罩；
 *   - 点击徽章：详情弹层（名称 / 描述 / 解锁时间）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { getBadges, type BadgeItem } from '@/api/game'
import { toMessage } from '@/api/http'

const router = useRouter()

const loading = ref(false)
const error = ref('')
const badges = ref<BadgeItem[]>([])
const activeCategory = ref<string>('全部')

const categoryMeta: Record<string, { label: string; icon: string; cls: string }> = {
  全部: { label: '全部', icon: '🎖️', cls: 'bg-brand-50 text-brand-600' },
  连胜: { label: '连胜', icon: '🔥', cls: 'bg-orange-50 text-orange-500' },
  准确率: { label: '准确率', icon: '🎯', cls: 'bg-emerald-50 text-emerald-600' },
  速度: { label: '速度', icon: '⚡', cls: 'bg-sky-50 text-sky-500' },
  探索: { label: '探索', icon: '🧭', cls: 'bg-violet-50 text-violet-500' },
  战斗: { label: '战斗', icon: '⚔️', cls: 'bg-rose-50 text-rose-500' },
}

const rarityLabel: Record<string, string> = {
  common: '普通',
  rare: '稀有',
  epic: '史诗',
  legendary: '传说',
}

const rarityCls: Record<string, string> = {
  common: 'from-slate-100 to-slate-200 text-slate-600',
  rare: 'from-sky-100 to-sky-200 text-sky-600',
  epic: 'from-violet-100 to-violet-200 text-violet-600',
  legendary: 'from-amber-100 to-amber-200 text-amber-600',
}

const unlockedCount = computed<number>(() => badges.value.filter((b) => b.unlocked_at).length)
const totalCount = computed<number>(() => badges.value.length)
const categories = computed<string[]>(() => {
  const set = new Set(badges.value.map((b) => b.category || '其他'))
  return ['全部', ...Array.from(set)]
})
const filtered = computed<BadgeItem[]>(() => {
  if (activeCategory.value === '全部') return badges.value
  return badges.value.filter((b) => (b.category || '其他') === activeCategory.value)
})

/** 详情弹层选中项。 */
const detail = ref<BadgeItem | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    badges.value = await getBadges()
  } catch (e) {
    error.value = toMessage(e, '徽章加载失败')
  } finally {
    loading.value = false
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function goHome(): void {
  void router.push({ name: 'game-hub' })
}

onMounted(load)
</script>

<template>
  <div class="mx-auto max-w-xl p-4">
    <div class="mb-3 flex items-center justify-between">
      <button type="button" class="btn-ghost px-3 py-1.5 text-sm" @click="goHome">← 返回大厅</button>
      <h1 class="text-base font-extrabold text-ink">🏅 徽章墙</h1>
      <span class="w-16" />
    </div>

    <p v-if="error" class="mb-3 rounded-xl bg-red-50 px-3 py-2 text-sm font-medium text-red-500">{{ error }}</p>
    <div v-if="loading" class="py-10 text-center text-slate-400">加载中…</div>

    <template v-else>
      <!-- 统计 -->
      <div class="card mb-3 flex items-center justify-between p-4">
        <div>
          <p class="text-xs text-ink-faint">已解锁徽章</p>
          <p class="text-2xl font-black text-ink">{{ unlockedCount }}<span class="text-base font-bold text-ink-faint"> / {{ totalCount }}</span></p>
        </div>
        <div class="flex gap-2">
          <span v-for="(m, k) in categoryMeta" :key="k" class="chip" :class="m.cls">{{ m.icon }} {{ m.label }}</span>
        </div>
      </div>

      <!-- 分类筛选 -->
      <div class="mb-3 flex flex-wrap gap-2">
        <button
          v-for="c in categories"
          :key="c"
          type="button"
          class="rounded-full px-3 py-1.5 text-sm font-semibold transition active:scale-95"
          :class="activeCategory === c ? (categoryMeta[c]?.cls ?? 'bg-brand-50 text-brand-600') : 'bg-white/70 text-ink-soft border border-white/70'"
          @click="activeCategory = c"
        >
          {{ c === '全部' ? '🎖️' : categoryMeta[c]?.icon ?? '🏷️' }} {{ c }}
        </button>
      </div>

      <!-- 徽章网格 -->
      <div v-if="filtered.length" class="grid grid-cols-2 gap-3">
        <button
          v-for="b in filtered"
          :key="b.badge_id"
          type="button"
          class="card-solid group relative overflow-hidden p-4 text-center transition hover:scale-[1.02] active:scale-[0.98]"
          @click="detail = b"
        >
          <!-- 未解锁遮罩 -->
          <div
            v-if="!b.unlocked_at"
            class="absolute inset-0 z-10 flex items-center justify-center bg-slate-200/70 backdrop-blur-[1px]"
          >
            <span class="text-3xl opacity-60">🔒</span>
          </div>

          <div
            class="mx-auto mb-2 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br text-3xl shadow-soft"
            :class="b.unlocked_at ? (rarityCls[b.rarity] ?? rarityCls.common) : 'from-slate-200 to-slate-300 grayscale'"
          >
            {{ b.icon_url || (b.unlocked_at ? '🏅' : '🏅') }}
          </div>
          <p class="text-sm font-extrabold text-ink">{{ b.display_name }}</p>
          <p class="mt-0.5 text-xs text-ink-faint">{{ rarityLabel[b.rarity] ?? b.rarity }} · {{ b.category }}</p>
          <p v-if="b.unlocked_at" class="mt-1 text-[11px] font-medium text-emerald-600">✓ {{ formatDate(b.unlocked_at)?.slice(0, 10) }}</p>
        </button>
      </div>
      <div v-else class="py-10 text-center text-slate-400">暂无徽章</div>
    </template>

    <!-- 详情弹层 -->
    <div
      v-if="detail"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
      @click.self="detail = null"
    >
      <div class="card-solid w-full max-w-sm p-6 text-center animate-pop">
        <div
          class="mx-auto mb-3 flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br text-5xl shadow-glow"
          :class="detail.unlocked_at ? (rarityCls[detail.rarity] ?? rarityCls.common) : 'from-slate-200 to-slate-300 grayscale'"
        >
          {{ detail.icon_url || '🏅' }}
        </div>
        <p class="text-lg font-black text-ink">{{ detail.display_name }}</p>
        <p class="mt-1 text-xs font-semibold text-ink-faint">{{ rarityLabel[detail.rarity] ?? detail.rarity }} · {{ detail.category }}</p>
        <p class="mt-3 text-sm leading-relaxed text-ink-soft">{{ detail.description }}</p>
        <p v-if="detail.unlocked_at" class="mt-3 text-xs font-bold text-emerald-600">解锁于 {{ formatDate(detail.unlocked_at) }}</p>
        <p v-else class="mt-3 text-xs font-medium text-ink-faint">继续挑战以解锁此徽章 🔒</p>
        <button type="button" class="btn-primary mt-5 w-full" @click="detail = null">知道了</button>
      </div>
    </div>
  </div>
</template>
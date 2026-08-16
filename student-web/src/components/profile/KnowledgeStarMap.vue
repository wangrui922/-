<script setup lang="ts">
import { computed, ref, watch } from 'vue'

/**
 * 知识星图（游戏化外层 · 升级版）。
 *
 * 把每个知识点画成一颗星：掌握度越高星越亮。孩子做对题 → 后端掌握度上升
 * → 这颗星从灰变蓝变金，集齐一片就"点亮一片天"。数据来自 profile store 的
 * mastery（已有），本组件只负责展示 + 点击深链到练习。
 *
 * 等级配色：
 *   0 未入门 → 灰（只描边）
 *   1 薄弱   → 琥珀（只描边）
 *   2 基本掌握 → 蓝（填充）
 *   3 熟练   → 金（填充 + 微光）
 */
import type { MasteryData, MasteryLevel } from '@/types/profile'
import { pickCopy, UNLOCK } from '@/feedback/feedbackCopy'

const props = defineProps<{
  data: MasteryData | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  (e: 'practice', payload: { kp_id: string; kp_name: string }): void
}>()

/** 5 角星路径（24×24 viewBox）。 */
const STAR_PATH =
  'M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z'

interface StarStyle {
  fill: string
  stroke: string
  /** 是否发光（level=3） */
  glow: boolean
  label: string
}

const LEVEL_STYLE: Record<MasteryLevel, StarStyle> = {
  0: { fill: '#f1f5f9', stroke: '#cbd5e1', glow: false, label: '未入门' },
  1: { fill: '#fffbeb', stroke: '#fcd34d', glow: false, label: '薄弱' },
  2: { fill: '#60a5fa', stroke: '#3b82f6', glow: false, label: '基本掌握' },
  3: { fill: '#fbbf24', stroke: '#f59e0b', glow: true, label: '熟练' },
}

const STAR_LEGEND: { level: MasteryLevel; label: string }[] = [
  { level: 0, label: '未入门' },
  { level: 1, label: '薄弱' },
  { level: 2, label: '基本掌握' },
  { level: 3, label: '熟练' },
]

/** 已点亮的星数（level ≥ 2）。 */
const litCount = computed(() => {
  const list = props.data?.mastery ?? []
  return list.filter((k) => k.level >= 2).length
})

function styleOf(level: MasteryLevel): StarStyle {
  return LEVEL_STYLE[level] ?? LEVEL_STYLE[0]
}

function onStar(kp_id: string, kp_name: string): void {
  if (kp_id) emit('practice', { kp_id, kp_name })
}

const items = computed(() => props.data?.mastery ?? [])

/**
 * 解锁成就动效（UNLOCK 文案）。
 *
 * 思路：持久化"已点亮过"的 kp 集合（localStorage）。每次 mastery 数据刷新，
 * 对比找出"本次新点亮（level 从 <2 升到 ≥2）"的星，弹庆祝 overlay。
 * 首次加载时只建立快照、不庆祝（booted 标记跨实例保留，避免回首页重复庆祝）。
 */
const SEEN_KEY = 'aicoach.star.seen.v1'

function loadSeen(): Set<string> {
  try {
    const raw = localStorage.getItem(SEEN_KEY)
    if (raw) return new Set(JSON.parse(raw) as string[])
  } catch {
    /* ignore */
  }
  return new Set()
}

function saveSeen(s: Set<string>): void {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify([...s]))
  } catch {
    /* ignore */
  }
}

const celebrate = ref<{ name: string; text: string } | null>(null)
let celebrateTimer: ReturnType<typeof setTimeout> | undefined

/** 模块级：是否已完成首次快照建立。跨组件实例保留，防止回首页重复庆祝。 */
let booted = false

watch(
  () => props.data,
  (data) => {
    if (!data?.mastery?.length) return
    const seen = loadSeen()
    const litNow = data.mastery.filter((k) => k.level >= 2)
    // 新点亮的星 = 当前点亮但此前没见过
    const newlyLit = litNow.filter((k) => !seen.has(k.kp_id))
    litNow.forEach((k) => seen.add(k.kp_id))
    saveSeen(seen)
    if (booted && newlyLit.length > 0) {
      const kp = newlyLit[0]
      const copy = pickCopy(UNLOCK)
      celebrate.value = { name: kp.kp_name, text: copy.text.replace('{name}', kp.kp_name) }
      if (celebrateTimer) clearTimeout(celebrateTimer)
      celebrateTimer = setTimeout(() => {
        celebrate.value = null
      }, 2800)
    }
    booted = true
  },
  { immediate: true },
)

</script>

<template>
  <section class="card">
    <!-- 解锁成就庆祝 overlay -->
    <Transition name="celebrate">
      <div v-if="celebrate" class="celebrate-overlay" @click="celebrate = null">
        <div class="celebrate-card">
          <div class="celebrate-stars">⭐ ✨ 🌟</div>
          <p class="mt-1 text-sm font-bold text-amber-600">{{ celebrate.text }}</p>
        </div>
      </div>
    </Transition>

    <header class="mb-3 flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="text-lg">⭐</span>
        <h2 class="text-base font-bold text-slate-800">我的知识星图</h2>
      </div>
      <span
        v-if="data && data.mastery.length"
        class="rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-600"
      >
        已点亮 {{ litCount }}/{{ data.mastery.length }}
      </span>
    </header>

    <p v-if="loading" class="py-6 text-center text-sm text-slate-400">正在点亮星图…</p>
    <p v-else-if="error" class="rounded-xl bg-red-50 px-3 py-3 text-sm text-red-500">
      {{ error }}
    </p>

    <template v-else-if="items.length">
      <!-- 星阵 -->
      <div class="grid grid-cols-3 gap-3 sm:grid-cols-4">
        <button
          v-for="item in items"
          :key="item.kp_id"
          type="button"
          class="group flex flex-col items-center gap-1 rounded-2xl border border-slate-100 bg-white px-2 py-3 transition active:scale-95 active:bg-slate-50"
          @click="onStar(item.kp_id, item.kp_name)"
        >
          <svg
            viewBox="0 0 24 24"
            class="h-9 w-9 transition"
            :class="styleOf(item.level).glow ? 'drop-shadow-[0_0_6px_rgba(251,191,36,0.7)]' : ''"
          >
            <path
              :d="STAR_PATH"
              :fill="styleOf(item.level).fill"
              :stroke="styleOf(item.level).stroke"
              stroke-width="1.2"
              stroke-linejoin="round"
            />
          </svg>
          <span class="line-clamp-1 w-full text-center text-xs font-medium text-slate-700">
            {{ item.kp_name }}
          </span>
          <span
            class="rounded-full px-1.5 py-0.5 text-[10px] leading-none"
            :class="{
              'bg-slate-100 text-slate-400': item.level === 0,
              'bg-amber-50 text-amber-500': item.level === 1,
              'bg-blue-50 text-blue-500': item.level === 2,
              'bg-amber-100 text-amber-600': item.level === 3,
            }"
          >
            {{ styleOf(item.level).label }}
          </span>
        </button>
      </div>

      <!-- 图例 -->
      <div class="mt-3 flex flex-wrap items-center gap-3 border-t border-dashed border-slate-200 pt-3">
        <span
          v-for="lg in STAR_LEGEND"
          :key="lg.level"
          class="flex items-center gap-1 text-xs text-slate-400"
        >
          <svg viewBox="0 0 24 24" class="h-4 w-4">
            <path
              :d="STAR_PATH"
              :fill="styleOf(lg.level).fill"
              :stroke="styleOf(lg.level).stroke"
              stroke-width="1.2"
              stroke-linejoin="round"
            />
          </svg>
          {{ lg.label }}
        </span>
        <span class="ml-auto text-[11px] text-slate-300">点星星就能去练这一关 →</span>
      </div>
    </template>

    <p v-else class="py-6 text-center text-sm text-slate-400">
      做几道题，你的第一颗星就会被点亮啦
    </p>
  </section>
</template>

<style scoped>
.celebrate-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.35);
  backdrop-filter: blur(2px);
}
.celebrate-card {
  max-width: 80vw;
  border-radius: 20px;
  background: #fff;
  padding: 20px 28px;
  text-align: center;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.2);
}
.celebrate-stars {
  font-size: 30px;
  letter-spacing: 4px;
  animation: twinkle 1.2s ease-in-out infinite;
}
@keyframes twinkle {
  0%,
  100% {
    opacity: 0.7;
    transform: scale(1);
  }
  50% {
    opacity: 1;
    transform: scale(1.15);
  }
}
.celebrate-enter-active,
.celebrate-leave-active {
  transition: opacity 0.3s ease;
}
.celebrate-enter-from,
.celebrate-leave-to {
  opacity: 0;
}
.celebrate-enter-active .celebrate-card {
  animation: pop 0.45s cubic-bezier(0.18, 0.89, 0.32, 1.28);
}
@keyframes pop {
  0% {
    transform: scale(0.6);
    opacity: 0;
  }
  60% {
    transform: scale(1.08);
  }
  100% {
    transform: scale(1);
    opacity: 1;
  }
}
</style>


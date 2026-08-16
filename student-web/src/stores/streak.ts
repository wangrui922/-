/**
 * 连续打卡（streak）本地 store。
 *
 * 为什么在前端维护：后端 user.streak_days 只是个读取字段，没有"今天首次做题
 * 就 +1"的自动更新逻辑（实测 grep 只有读取无写入）。打卡本质是孩子行为激励，
 * 用 localStorage 维护零风险、零后端改动，符合"最小成本、不断升级"。
 *
 * 打卡定义：孩子今天完成（提交）过至少一题 = 今天打卡。
 *   - 今天已打卡 → 天数不变
 *   - 昨天打卡过 → 连续天数 +1
 *   - 断签 / 首次 → 重置为 1
 */
import { computed, reactive } from 'vue'

const KEY = 'aicoach.student.streak.v1'

interface StreakState {
  streakDays: number
  /** 最近一次打卡日期 YYYY-MM-DD */
  lastCheckinDate: string
}

function dateStr(offset = 0): string {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

function load(): StreakState {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) {
      const p = JSON.parse(raw) as Partial<StreakState>
      return { streakDays: p.streakDays ?? 0, lastCheckinDate: p.lastCheckinDate ?? '' }
    }
  } catch {
    /* 损坏则重置 */
  }
  return { streakDays: 0, lastCheckinDate: '' }
}

const state = reactive<StreakState>(load())

function save(): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ streakDays: state.streakDays, lastCheckinDate: state.lastCheckinDate }))
  } catch {
    /* 隐私模式忽略 */
  }
}

/** 标记今天打卡（完成至少一题）。返回最新连续天数。 */
function markCheckin(): number {
  const today = dateStr(0)
  if (state.lastCheckinDate === today) return state.streakDays
  if (state.lastCheckinDate === dateStr(-1)) {
    state.streakDays += 1
  } else {
    state.streakDays = 1
  }
  state.lastCheckinDate = today
  save()
  return state.streakDays
}

const checkedInToday = computed(() => state.lastCheckinDate === dateStr(0))

export function useStreak() {
  return {
    streakDays: computed(() => state.streakDays),
    checkedInToday,
    markCheckin,
  }
}

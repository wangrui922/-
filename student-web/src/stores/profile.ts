/**
 * 学习画像响应式存储（T04 · F12）。
 *
 * 仿 stores/auth.ts 用 Vue 的 reactive（**不引 pinia**，package.json 里没有 pinia）。
 *
 * 设计：把画像三大块（掌握度 / 错因 / 推荐）的数据与各自的加载状态集中管理，
 * Dashboard 调 loadDashboard() 三路并行拉取，子面板直接读 profileState。
 * 每个面板独立 try/catch，单路失败不影响其它路（局部降级）。
 */
import { reactive } from 'vue'

import { getErrorPatterns, getMastery, getRadar, getRecommendations } from '@/api/profile'
import { getLearningDna, toMasteryData } from '@/api/dna'
import { getStudentId } from '@/stores/auth'
import type {
  ErrorPatternData,
  MasteryData,
  RadarData,
  RecommendationData,
} from '@/types/profile'

/** 单个面板的加载 / 错误状态。 */
export interface PanelState {
  loading: boolean
  error: string
  loaded: boolean
}

/** 画像全局状态。 */
export interface ProfileState {
  mastery: MasteryData | null
  errorPatterns: ErrorPatternData | null
  recommendations: RecommendationData | null
  radar: RadarData | null
  masteryState: PanelState
  errorPatternsState: PanelState
  recommendationsState: PanelState
  radarState: PanelState
  /** DNA-005 实时掌握度（埋点驱动，无缓存） */
  dnaMastery: MasteryData | null
  dnaState: PanelState
}

function emptyPanel(): PanelState {
  return { loading: false, error: '', loaded: false }
}

/** 供组件模板直接读取的响应式画像态。 */
export const profileState = reactive<ProfileState>({
  mastery: null,
  errorPatterns: null,
  recommendations: null,
  radar: null,
  masteryState: emptyPanel(),
  errorPatternsState: emptyPanel(),
  recommendationsState: emptyPanel(),
  radarState: emptyPanel(),
  dnaMastery: null,
  dnaState: emptyPanel(),
})

/**
 * 通用安全加载：成功写 data，失败写 error，永不抛异常（实现局部降级）。
 *
 * @returns 成功返回数据，失败返回 null
 */
async function safeLoad<T>(panel: PanelState, loader: () => Promise<T>): Promise<T | null> {
  panel.loading = true
  panel.error = ''
  try {
    const data = await loader()
    panel.loaded = true
    return data
  } catch (error: unknown) {
    panel.error = error instanceof Error ? error.message : '加载失败，请稍后重试'
    panel.loaded = true
    return null
  } finally {
    panel.loading = false
  }
}

/** 拉取掌握度看板。 */
export async function loadMastery(force?: boolean): Promise<void> {
  const data = await safeLoad(profileState.masteryState, () => getMastery({ force }))
  if (data) profileState.mastery = data
}

/** 拉取错因分布。 */
export async function loadErrorPatterns(rangeDays?: number): Promise<void> {
  const data = await safeLoad(profileState.errorPatternsState, () =>
    getErrorPatterns(rangeDays ? { rangeDays } : undefined),
  )
  if (data) profileState.errorPatterns = data
}

/** 拉取练习推荐。 */
export async function loadRecommendations(limit?: number, force?: boolean): Promise<void> {
  const data = await safeLoad(profileState.recommendationsState, () =>
    getRecommendations({ limit, force }),
  )
  if (data) profileState.recommendations = data
}

/** 拉取学情能力雷达图。 */
export async function loadRadar(force?: boolean): Promise<void> {
  const data = await safeLoad(profileState.radarState, () => getRadar({ force }))
  if (data) profileState.radar = data
}

/**
 * 拉取 DNA-005 实时掌握度（埋点驱动，恒 fresh，无缓存概念）。
 *
 * 失败不抛异常（safeLoad 局部降级），由 Dashboard 决定降级到 T04 mastery。
 */
export async function loadDna(): Promise<void> {
  const data = await safeLoad(profileState.dnaState, async () =>
    toMasteryData(await getLearningDna(getStudentId())),
  )
  if (data) profileState.dnaMastery = data
}

/**
 * 并行拉取画像（Dashboard 入口）。
 *
 * 掌握度展示数据源为 DNA-005（dnaMastery，埋点实时驱动）；radar/errorPatterns/
 * recommendations 仍走 T04。四位并行，单路失败不影响其它（局部降级）。
 *
 * @param opts.force      绕过掌握度/推荐缓存
 * @param opts.rangeDays  错因统计窗口天数
 * @param opts.limit      推荐条数
 */
export async function loadDashboard(opts?: {
  force?: boolean
  rangeDays?: number
  limit?: number
}): Promise<void> {
  await Promise.all([
    loadMastery(opts?.force),
    loadRadar(opts?.force),
    loadErrorPatterns(opts?.rangeDays),
    loadRecommendations(opts?.limit, opts?.force),
    loadDna(),
  ])
}

/**
 * T04 学习画像接口封装（GET /api/profile/*）。
 *
 * 后端统一响应 { code, msg, data }，http.ts 已拆封，这里只关心 data 载荷。
 */
import { get } from '@/api/http'
import type {
  ErrorPatternData,
  MasteryData,
  RadarData,
  RecommendationData,
} from '@/types/profile'

/**
 * 掌握度看板。
 *
 * @param force 传 true 时绕过物化缓存强制实时重算
 * @param kpId  仅返回指定知识点（可选）；传空数组等同不传
 */
export function getMastery(params?: {
  force?: boolean
  kpId?: string[]
}): Promise<MasteryData> {
  const query: Record<string, unknown> = {}
  if (params?.force) query.force = 1
  if (params?.kpId && params.kpId.length > 0) query.kp_id = params.kpId
  return get<MasteryData>('/profile/mastery', query)
}

/**
 * 错因分布。
 *
 * @param rangeDays 统计窗口天数（1~3650），默认后端 90
 */
export function getErrorPatterns(params?: { rangeDays?: number }): Promise<ErrorPatternData> {
  const query: Record<string, unknown> = {}
  if (params?.rangeDays && params.rangeDays > 0) query.range_days = params.rangeDays
  return get<ErrorPatternData>('/profile/error-patterns', query)
}

/**
 * 练习推荐。
 *
 * @param limit 返回条数（1~20），默认后端 TOP_N
 * @param force 传 true 时绕过缓存
 */
export function getRecommendations(params?: {
  limit?: number
  force?: boolean
}): Promise<RecommendationData> {
  const query: Record<string, unknown> = {}
  if (params?.limit && params.limit > 0) query.limit = params.limit
  if (params?.force) query.force = 1
  return get<RecommendationData>('/profile/recommendations', query)
}

/**
 * 学情能力雷达图（axis=全部 L1 知识点，value=掌握度百分比 0~100）。
 *
 * @param force 传 true 时绕过掌握度缓存强制实时重算
 */
export function getRadar(params?: { force?: boolean }): Promise<RadarData> {
  const query: Record<string, unknown> = {}
  if (params?.force) query.force = 1
  return get<RadarData>('/profile/radar', query)
}

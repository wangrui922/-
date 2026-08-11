/**
 * Epic6 家长周报接口封装（T6 生成 / T7 公开读取）。
 *
 * 生成走鉴权实例 http（学生本人）；公开读取走 publicHttp（免登录）。
 * 后端统一响应 { code, msg, data }，http.ts / public.ts 已拆封，
 * 这里只关心 data 载荷。
 */
import { post } from '@/api/http'
import { getPublic } from '@/api/public'
import type { GenerateReportData, SharedReportData } from '@/types/report'

/**
 * 生成（或复用）本周家长周报。
 *
 * @param force 传 true 时跳过缓存强制重算并重调模型
 */
export function generateReport(params?: { force?: boolean }): Promise<GenerateReportData> {
  return post<GenerateReportData>('/student/report/generate', { force: params?.force ?? false })
}

/**
 * 家长免登录查看周报。
 *
 * @param token 分享令牌
 */
export function getSharedReport(token: string): Promise<SharedReportData> {
  const safe = encodeURIComponent(token || '')
  return getPublic<SharedReportData>(`/report/share/${safe}`)
}

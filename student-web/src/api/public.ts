/**
 * 公开页专用 axios 实例（Epic6 · T7）。
 *
 * 家长周报分享页（/r/:token）是**免登录**访问，必须用独立实例：
 *   1. 不注入 Authorization（家长没有 token）；
 *   2. **不挂 401 → 跳登录** 的拦截（否则家长打开失效链接会被踢到登录页，
 *      与设计文档 S15 的「统一失效文案」相悖）。
 *
 * 复用 http.ts 的 ApiEnvelope / ApiError 类型，但不复用其 http 单例，
 * 以免把鉴权拦截一并带进来。
 */
import axios, { type AxiosInstance, type AxiosResponse } from 'axios'

import { ApiError, type ApiEnvelope } from '@/api/http'

const PUBLIC_TIMEOUT = 30_000

export const publicHttp: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: PUBLIC_TIMEOUT,
})

// ---- 响应拦截：只拆 { code, msg, data }，不碰登录态 -----------------------
publicHttp.interceptors.response.use(
  (response: AxiosResponse<ApiEnvelope>) => {
    const body = response.data
    if (!body || typeof body !== 'object' || !('code' in body)) {
      return response
    }
    if (body.code === 0) {
      response.data = (body.data ?? {}) as never
      return response
    }
    return Promise.reject(new ApiError(body.code, body.msg || '请求失败'))
  },
  (error) => {
    const status: number | undefined = error?.response?.status
    const body = error?.response?.data as ApiEnvelope | undefined
    const code = typeof body?.code === 'number' ? body.code : status ?? 500
    const msg =
      body?.msg ||
      (error?.code === 'ECONNABORTED' ? '请求超时了，再试一次吧' : error?.message) ||
      '网络开小差了，请稍后再试'
    return Promise.reject(new ApiError(code, msg))
  },
)

/** 公开 GET，返回拆封后的 data。 */
export async function getPublic<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const response = await publicHttp.request<T>({ url, method: 'GET', params })
  return response.data as T
}

export default publicHttp

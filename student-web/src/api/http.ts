/**
 * axios 实例与统一响应拆封。
 *
 * 跨模块硬约定：
 *   1. 所有后端调用走 /api/*（Vite dev server 代理到 http://localhost:8000）；
 *   2. 后端响应恒为 { code, msg, data }（见 backend/app/core/response.py），
 *      code === 0 时本层直接把 data 交给业务，code !== 0 抛 ApiError；
 *   3. 401（HTTP 401 或 body.code === 401）清空 token 并跳 /login。
 */
import axios, {
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
} from 'axios'

import router from '@/router'
import { getToken, logout, getRefreshToken, refreshAccessToken } from '@/stores/auth'

/** 后端统一响应外壳。 */
export interface ApiEnvelope<T = unknown> {
  code: number
  msg: string
  data: T
}

/** 业务异常：携带后端返回的 code / msg。 */
export class ApiError extends Error {
  readonly code: number

  constructor(code: number, msg: string) {
    super(msg)
    this.name = 'ApiError'
    this.code = code
  }
}

const HTTP_TIMEOUT = 90_000 // AI 生成较慢，给 90s

export const http: AxiosInstance = axios.create({
  // 本地/开发：走 Vite 代理到 localhost:8000（VITE_API_BASE 未设置时）；
  // 生产：构建时注入 VITE_API_BASE（如 https://api.xxx.com）即可直连已托管后端，
  // 无需前端持有任何密钥。
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: HTTP_TIMEOUT,
})

/** 刷新单例：并发 401 只真正刷新一次。 */
let refreshPromise: Promise<boolean> | null = null
/** 跳转登录锁：避免并发 401 多次 replace。 */
let isRedirecting = false

function doRefresh(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = refreshAccessToken().finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

/** 清登录态并跳登录页（带 redirect 回跳，加重入锁）。 */
function logoutAndRedirect(): void {
  logout()
  if (isRedirecting) return
  isRedirecting = true
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    void router.replace({ name: 'login', query: { redirect: current.fullPath } })
  }
  setTimeout(() => {
    isRedirecting = false
  }, 1500)
}

/**
 * 401 统一恢复：先静默续期，成功则用新 token 重发原请求；无 refresh / 续期失败则跳登录。
 * 返回重发后的响应；失败抛 ApiError(401)。
 */
async function recoverOrLogout(config?: AxiosRequestConfig): Promise<AxiosResponse> {
  if (!getRefreshToken()) {
    logoutAndRedirect()
    return Promise.reject(new ApiError(401, '登录已过期，请重新登录'))
  }
  const ok = await doRefresh()
  if (ok && config) {
    return http(config)
  }
  logoutAndRedirect()
  return Promise.reject(new ApiError(401, '登录已过期，请重新登录'))
}

// ---- 请求拦截：注入 Bearer token ------------------------------------------
http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.set?.('Authorization', `Bearer ${token}`)
  }
  return config
})

// ---- 响应拦截：拆 { code, msg, data } --------------------------------------
http.interceptors.response.use(
  (response: AxiosResponse<ApiEnvelope>) => {
    const body = response.data

    // 非统一外壳（如 /api/file/{key} 的二进制流）原样返回
    if (!body || typeof body !== 'object' || !('code' in body)) {
      return response
    }

    if (body.code === 0) {
      response.data = (body.data ?? {}) as never
      return response
    }

    if (body.code === 401) {
      return recoverOrLogout(response.config)
    }
    return Promise.reject(new ApiError(body.code, body.msg || '请求失败'))
  },
  (error) => {
    const status: number | undefined = error?.response?.status
    const body = error?.response?.data as ApiEnvelope | undefined
    const code = typeof body?.code === 'number' ? body.code : status ?? 500

    if (status === 401 || code === 401) {
      return recoverOrLogout(error.config)
    }

    const msg =
      body?.msg ||
      (error?.code === 'ECONNABORTED' ? '请求超时了，再试一次吧' : error?.message) ||
      '网络开小差了，请稍后再试'
    return Promise.reject(new ApiError(code, msg))
  },
)

/** 发请求并直接拿到拆封后的 data。 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await http.request<T>(config)
  return response.data as T
}

/** GET，返回 data。 */
export function get<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  return request<T>({ url, method: 'GET', params })
}

/** POST，返回 data。 */
export function post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return request<T>({ url, method: 'POST', data, ...config })
}

/** PUT，返回 data。 */
export function put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return request<T>({ url, method: 'PUT', data, ...config })
}

/** 把任意异常转成可展示的提示文案。 */
export function toMessage(error: unknown, fallback = '出错了，请稍后再试'): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message || fallback
  return fallback
}

export default http

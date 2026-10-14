/**
 * 登录态存储（localStorage，轻量实现，不引 pinia）。
 *
 * 后端 POST /api/auth/login 返回：
 *   { access, refresh, accessToken, refreshToken, tokenType, expiresIn, isNewUser,
 *     user: { userId, studentId, phone, nickname, avatarUrl, role, grade, stage } }
 * 这里只落 access / refresh / studentId 三项，够学生端用。
 */
import { reactive } from 'vue'

const TOKEN_KEY = 'aicoach.student.token'
const REFRESH_KEY = 'aicoach.student.refresh'
const STUDENT_ID_KEY = 'aicoach.student.sid'

export interface AuthState {
  token: string
  studentId: string
}

/** 供组件模板直接读取的响应式登录态。 */
export const authState = reactive<AuthState>({
  token: localStorage.getItem(TOKEN_KEY) || '',
  studentId: localStorage.getItem(STUDENT_ID_KEY) || '',
})

/** 取当前 access token（无则空串）。 */
export function getToken(): string {
  return authState.token || localStorage.getItem(TOKEN_KEY) || ''
}

/** 取刷新令牌（V4.1 暂未做静默续期，先存着）。 */
export function getRefreshToken(): string {
  return localStorage.getItem(REFRESH_KEY) || ''
}

/** 是否已登录。 */
export function isLoggedIn(): boolean {
  return Boolean(getToken())
}

/** 取当前学生 ID。 */
export function getStudentId(): string {
  return authState.studentId || localStorage.getItem(STUDENT_ID_KEY) || ''
}

/**
 * 写入登录态。
 *
 * @param token access 令牌
 * @param options refresh 令牌与 studentId（可选）
 */
export function login(token: string, options: { refresh?: string; studentId?: string } = {}): void {
  authState.token = token
  localStorage.setItem(TOKEN_KEY, token)

  if (options.refresh) {
    localStorage.setItem(REFRESH_KEY, options.refresh)
  }
  if (options.studentId) {
    authState.studentId = options.studentId
    localStorage.setItem(STUDENT_ID_KEY, options.studentId)
  }
}

/** 清空登录态（401 或主动退出时调用）。 */
export function logout(): void {
  authState.token = ''
  authState.studentId = ''
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
  localStorage.removeItem(STUDENT_ID_KEY)
}

/**
 * 用 refresh token 静默换新令牌。
 *
 * 直接走 fetch（不经过 axios 响应拦截），避免刷新接口自身返回 401 时
 * 触发拦截器里的续期逻辑造成死循环。
 *
 * @returns 续期成功并写入登录态返回 true；无 refresh / 续期失败返回 false。
 */
export async function refreshAccessToken(): Promise<boolean> {
  const refresh = getRefreshToken()
  if (!refresh) return false
  const base = ((import.meta as { env?: Record<string, unknown> }).env?.VITE_API_BASE as string) || '/api'
  try {
    const res = await fetch(`${base}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh }),
    })
    if (!res.ok) return false
    const body = (await res.json()) as { code?: number; data?: Record<string, unknown> }
    if (body?.code !== 0 || !body.data) return false
    const token = (body.data.access || body.data.accessToken) as string | undefined
    if (!token) return false
    login(token, {
      refresh: (body.data.refresh || body.data.refreshToken) as string | undefined,
      studentId: (body.data.user as { studentId?: string } | undefined)?.studentId ?? getStudentId(),
    })
    return true
  } catch {
    return false
  }
}

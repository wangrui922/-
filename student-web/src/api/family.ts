/**
 * 家长端 API（V4.2 商业核心 · 家长视角）。
 *
 * 契约（backend/app/api/family.py + auth.py）：
 *   POST /api/auth/code                        { phone }                -> { sent, devCode }
 *   POST /api/auth/register                    { phone, code, role }    -> { access, refresh, ... , user }
 *   POST /api/family/bind                      { phone, relation }      -> { studentId, relation }
 *   GET  /api/family/children                  -> [ { studentId, nickname, realName, grade, stage, relation } ]
 *   DELETE /api/family/bind/{studentId}        -> {}
 *   GET  /api/family/children/{sid}/summary    -> { studentId, profile, weekly, radar }
 *   GET  /api/family/children/{sid}/mistakes   -> { total, page, size, items[] }
 *   GET  /api/family/children/{sid}/attempts   -> { total, page, size, items[] }
 *   GET  /api/family/children/{sid}/mastery    -> { total, page, size, items[] }
 *   GET  /api/family/children/{sid}/entitlement-> { plan, planName, dailyAiLimit, ... }
 */
import { get, post, http } from '@/api/http'

export interface ChildBrief {
  studentId: string
  nickname?: string | null
  realName?: string | null
  grade?: number | null
  stage?: string | null
  relation?: string | null
}

export interface PageResult<T> {
  total: number
  page: number
  size: number
  items: T[]
}

export interface ChildMistake {
  id: string
  questionId: string
  stem?: string | null
  difficulty?: number | null
  kpId?: string | null
  errorType?: string | null
  errorReason?: string | null
  note?: string | null
  solved: boolean
  solvedAt?: string | null
  createdAt?: string | null
}

export interface ChildAttempt {
  id: string
  questionId?: string | null
  stem?: string | null
  selectedAnswer?: string | null
  isCorrect: boolean
  durationSec?: number | null
  createdAt?: string | null
}

export interface ChildMastery {
  kpId: string
  kpName?: string | null
  score: number
  level: string
  levelInt: number
  attempts: number
  updatedAt?: string | null
}

export interface ChildEntitlement {
  plan: string
  planName: string
  dailyAiLimit: number
  aiSessionsToday: number
  aiAttemptsToday: number
  expiresAt?: string | null
}

/** 发送登录验证码。 */
export function sendParentCode(phone: string): Promise<{ sent: boolean; devCode?: string | null }> {
  return post('/auth/code', { phone })
}

/** 家长注册 / 登录（手机号 + 验证码，role=parent）。 */
export function parentLogin(phone: string, code: string): Promise<Record<string, unknown>> {
  return post('/auth/register', { phone, code, role: 'parent' })
}

/** 孩子列表。 */
export function getChildren(): Promise<ChildBrief[]> {
  return get('/family/children')
}

/** 绑定孩子。 */
export function bindChild(phone: string, relation: string): Promise<{ studentId: string; relation: string }> {
  return post('/family/bind', { phone, relation })
}

/** 解绑孩子。 */
export function unbindChild(studentId: string): Promise<Record<string, unknown>> {
  return http.delete(`/family/bind/${studentId}`).then((res) => (res.data as Record<string, unknown>).data as Record<string, unknown>)
}

/** 孩子概览（画像 + 周报 + 雷达）。 */
export function getChildSummary(studentId: string): Promise<Record<string, unknown>> {
  return get(`/family/children/${studentId}/summary`)
}

/** 孩子错题列表。 */
export function getChildMistakes(studentId: string, page = 1, size = 20): Promise<PageResult<ChildMistake>> {
  return get(`/family/children/${studentId}/mistakes`, { page, size })
}

/** 孩子练习记录。 */
export function getChildAttempts(studentId: string, page = 1, size = 20): Promise<PageResult<ChildAttempt>> {
  return get(`/family/children/${studentId}/attempts`, { page, size })
}

/** 孩子掌握度。 */
export function getChildMastery(studentId: string, page = 1, size = 50): Promise<PageResult<ChildMastery>> {
  return get(`/family/children/${studentId}/mastery`, { page, size })
}

/** 孩子权益状态。 */
export function getChildEntitlement(studentId: string): Promise<ChildEntitlement> {
  return get(`/family/children/${studentId}/entitlement`)
}
/**
 * 订阅/权益 API（billing 域）。
 *
 * 契约（backend/app/api/billing.py）：
 *   GET  /api/billing/plans            -> [ { slug, name, priceMonthly, ... } ]
 *   GET  /api/billing/entitlement      -> { plan, planName, dailyAiLimit, ... }
 *   GET  /api/billing/subscription     -> { id, planSlug, status, ... } | null
 *   POST /api/billing/checkout         -> { checkoutUrl, sessionId }
 */
import { get, post } from '@/api/http'

export interface Plan {
  slug: string
  name: string
  description?: string | null
  priceMonthly: number
  priceYearly: number
  aiDailyLimit: number
  practiceDailyLimit: number
  maxMistakeStorage: number
  hasAdvancedAnalytics: boolean
  hasFamilySharing: boolean
  hasOcrPremium: boolean
}

export interface Entitlement {
  plan: string
  planName: string
  dailyAiLimit: number
  practiceDailyLimit: number
  maxMistakeStorage: number
  hasAdvancedAnalytics: boolean
  hasFamilySharing: boolean
  hasOcrPremium: boolean
  aiSessionsToday: number
  aiAttemptsToday: number
  expiresAt?: string | null
  isActive: boolean
}

export interface Subscription {
  id: string
  planSlug: string
  status: string
  provider: string
  currentPeriodStart?: string | null
  currentPeriodEnd?: string | null
  cancelAtPeriodEnd: boolean
  interval: string
  amountPaid: number
  currency: string
  createdAt?: string | null
}

export interface CheckoutResult {
  checkoutUrl: string
  sessionId: string
}

/** 获取计划列表。 */
export function getPlans(): Promise<Plan[]> {
  return get('/billing/plans')
}

/** 获取当前用户权益。 */
export function getEntitlement(): Promise<Entitlement> {
  return get('/billing/entitlement')
}

/** 获取当前订阅。 */
export function getSubscription(): Promise<Subscription | null> {
  return get('/billing/subscription')
}

/** 创建结账会话。 */
export function createCheckout(planSlug: string, interval: 'month' | 'year'): Promise<CheckoutResult> {
  return post('/billing/checkout', { planSlug, interval })
}

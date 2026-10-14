/**
 * FSRS 科学复习排期 API（方向三 · T3）。
 *
 * 契约（后端 app/api/review.py，路由前缀 /api/review）：
 *   GET  /daily   → { cards, count, date }  今日待复习卡片
 *   POST /submit  → { card_id, rating }     提交复习结果（FSRS rating 1-4）
 *   GET  /stats   → 复习统计（总卡 / 今日到期 / 近30天 / 平均分 / 状态分布）
 *   POST /sync    → { kp_only }             从掌握度同步复习计划
 *
 * 字段命名对齐后端 snake_case；响应由 http.ts 统一拆包（code===0 返回 data）。
 */
import { http } from '@/api/http'

/** 复习卡片（后端 review_service.get_due_cards 返回的展开结构）。 */
export interface ReviewCard {
  id: number | string
  question_id: number | null
  kp_id: number | null
  due: string
  state: number
  scheduled_days: number
  reps: number
  lapses: number
  question_content: string | null
  question_type: string | null
  kp_name: string | null
  kp_code: string | null
  /** 知识点卡片时为 { type: 'knowledge_point', kp_name, kp_code } */
  content?: { type: string; kp_name?: string; kp_code?: string } | null
}

export interface DailyReviewData {
  cards: ReviewCard[]
  count: number
  date: string
}

export interface ReviewStats {
  total_cards: number
  today_due: number
  recent_reviews: number
  average_rating: number
  /** state → 数量（0=未学 1=学习中 2=已掌握 3=重学） */
  state_distribution: Record<string, number>
  period_days: number
}

/** 今日待复习卡片（count 为后端返回的当日待复习总数）。 */
export function getDailyReview(): Promise<DailyReviewData> {
  return http.get('/review/daily')
}

/** 提交复习结果；rating 1-4 对应 Again / Hard / Good / Easy。 */
export function submitReview(cardId: number | string, rating: number): Promise<unknown> {
  return http.post('/review/submit', { card_id: String(cardId), rating })
}

/** 复习统计（近 periodDays 天，默认 30）。 */
export function getReviewStats(periodDays = 30): Promise<ReviewStats> {
  return http.get('/review/stats', { params: { days: periodDays } })
}

/** 从掌握度同步复习计划（kp_only=true 只为知识点建档）。 */
export function syncReviewCards(kpOnly = false): Promise<unknown> {
  return http.post('/review/sync', { kp_only: kpOnly })
}
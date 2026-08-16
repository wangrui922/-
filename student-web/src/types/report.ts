/**
 * Epic6 家长周报类型定义（前端）。
 *
 * 字段名与后端 backend/app/schemas/report.py 一一对应（snake_case 响应）。
 * 路由层返回 { code, msg, data }，http.ts 拆封后直接把 data 交给业务，
 * 因此这里只描述 data 载荷结构。
 *
 * error_patterns 与后端 /api/profile/error-patterns 的 data 同构，
 * 直接复用 ErrorPatternData 类型，方便 ErrorPatternChart 零改造复用。
 */

/** 分享页展示的学生信息（只有这两个字段，绝不回显 ID / 手机号）。 */
export interface ReportStudent {
  display_name: string
  grade: number | null
}

/** 统计周期（北京时间日期）。 */
export interface ReportPeriodData {
  start: string | null
  end: string | null
  is_partial: boolean
}

/** 本周学习投入的真实计数（**不含学习时长**）。 */
export interface ReportEngagement {
  study_days: number
  attempt_count: number
  correct_count: number
  /** 正确率 0~1；零练习时为 null（不是 0）。 */
  correct_rate: number | null
  new_mistake_count: number
  solved_mistake_count: number
  tutor_session_count: number
}

/** 一条薄弱知识点（含上周对比）。 */
export interface ReportWeakPoint {
  kp_id: string
  kp_name: string
  /** 当前分值 0~1 */
  score: number
  level: number
  error_rate: number
  /** 上周分值，无历史时为 null */
  prev_score: number | null
  /** 相对上周的变化，无历史时为 null */
  delta: number | null
}

/** 与上一份周报的环比结果。 */
export interface ReportComparison {
  available: boolean
  reason: string
  prev_period_start: string | null
  correct_rate_delta: number | null
  improved: { kp_id: string; kp_name: string; delta: number }[]
  declined: { kp_id: string; kp_name: string; delta: number }[]
}

/** 给家长的三段话 + 来源标记。 */
export interface ReportNarrative {
  headline: string
  overview_text: string
  progress_text: string
  advice_items: string[]
  /** ai | fallback（前端不得展示）。 */
  source: string
}

/** 空态 / 低数据提示。 */
export interface ReportFlag {
  is_empty: boolean
  message: string | null
}

/** 练习量偏少提示（字段名与后端 ReportLowDataFlag 对齐）。 */
export interface ReportLowDataFlag {
  is_low: boolean
  message: string | null
}

/** GET /api/report/share/{token} 的 data 载荷（公开、无登录）。 */
export interface SharedReportData {
  student: ReportStudent
  period: ReportPeriodData
  engagement: ReportEngagement
  /** 与 ErrorPatternData 同构，直接复用。 */
  error_patterns: import('@/types/profile').ErrorPatternData
  weak_points: ReportWeakPoint[]
  comparison: ReportComparison
  /** 学情能力雷达图，与 RadarData 同构，AbilityRadar 零改造复用。 */
  radar: import('@/types/profile').RadarData | null
  narrative: ReportNarrative
  empty_state: ReportFlag
  low_data: ReportLowDataFlag
  generated_at: string | null
  expires_at: string | null
}

/** POST /api/student/report/generate 的 data 载荷。 */
export interface GenerateReportData {
  report_id: string
  /** 分享令牌（重新生成不会变）。 */
  share_token: string
  /** 发给家长的完整链接。 */
  share_url: string
  period_start: string | null
  period_end: string | null
  is_partial: boolean
  expires_at: string | null
  /** true=命中缓存未重算。 */
  cached: boolean
  /** ai=模型生成 / fallback=本地降级文案。 */
  ai_source: string
}

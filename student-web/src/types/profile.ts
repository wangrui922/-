/**
 * T04 学习画像类型定义（前端）。
 *
 * 字段名与后端 backend/app/schemas/profile.py 一一对应（snake_case 响应）。
 * 路由层返回 { code, msg, data }，http.ts 拆封后直接把 data 交给业务，
 * 因此这里只描述 data 载荷结构。
 */

/** 掌握度等级：0 未入门 / 1 薄弱 / 2 基本掌握 / 3 熟练。 */
export type MasteryLevel = 0 | 1 | 2 | 3

/** 单个知识点的掌握度。 */
export interface MasteryItem {
  kp_id: string
  kp_name: string
  /** 掌握度 0~1 */
  score: number
  /** 0 未入门 / 1 薄弱 / 2 基本掌握 / 3 熟练 */
  level: MasteryLevel
  /** 参与计算的事件总数 */
  attempts: number
  /** 其中正确次数 */
  correct_count: number
  /** 错误率 0~1 */
  error_rate: number
  /** 时效衰减后的有效样本量 */
  effective_attempts: number
  /** 最近练习时间 ISO8601，可能为 null */
  last_practiced_at: string | null
}

/** 掌握度汇总。 */
export interface MasterySummary {
  /** 纳入统计的知识点总数 */
  total_kp: number
  /** 各等级知识点数量，如 {"0":1,"1":2,"2":0,"3":0} */
  level_count: Record<string, number>
}

/** GET /api/profile/mastery 的 data 载荷。 */
export interface MasteryData {
  student_id: string
  /** 按等级升序（最弱在前） */
  mastery: MasteryItem[]
  summary: MasterySummary
  computed_at: string | null
  /** fresh 实时计算 / cache 命中物化缓存 */
  mode: 'fresh' | 'cache'
}

/** 雷达图的一个轴（L1 知识点）。 */
export interface RadarAxis {
  kp_id: string
  kp_name: string
  /** 掌握度百分比 0~100 */
  score: number
  /** 0 未入门 / 1 薄弱 / 2 基本掌握 / 3 熟练 */
  level: MasteryLevel
  /** 该知识点是否已有练习数据 */
  practiced: boolean
}

/** 雷达图汇总。 */
export interface RadarSummary {
  /** 课程表全部 L1 知识点数 */
  total_kp: number
  /** 其中孩子已练习过的知识点数 */
  practiced_kp: number
  /** 已练知识点掌握度均值 0~100 */
  overall_score: number
  /** 各等级知识点数量 */
  level_count: Record<string, number>
}

/** GET /api/profile/radar 的 data 载荷。 */
export interface RadarData {
  student_id: string
  /** 全部 L1 知识点轴 */
  axes: RadarAxis[]
  summary: RadarSummary
  computed_at: string | null
  /** fresh 实时计算 / cache 命中物化缓存 */
  mode: 'fresh' | 'cache'
}

/** 单类错因的聚合结果。 */
export interface ErrorPatternItem {
  /** READ/METHOD/CALC/KNOWLEDGE/CHECK（与后端 error_categories 一致） */
  category: string
  /** 中文标签 */
  label: string
  /** 命中次数 */
  count: number
  /** 占比 0~1 */
  ratio: number
  /** 样例错因文本 */
  sample_hits: string[]
}

/** GET /api/profile/error-patterns 的 data 载荷。 */
export interface ErrorPatternData {
  student_id: string
  /** 恒返回 5 类，count 可为 0 */
  error_patterns: ErrorPatternItem[]
  /** 窗口内错题总数（含未关联知识点的） */
  total_mistakes: number
  /** 其中未关联知识点、不进掌握度计算的条数 */
  unmapped_mistakes: number
  /** 统计窗口天数 */
  range_days: number
  computed_at: string | null
  /** 本接口恒为 fresh */
  mode: string
}

/** 一条练习推荐。 */
export interface RecommendationItem {
  kp_id: string
  kp_name: string
  /** 后端生成的中文推荐理由 */
  reason: string
  /** 优先级 0~1，越大越该练 */
  priority: number
  /** 当前掌握度 */
  score: number
  /** 错误率 */
  error_rate: number
  /** 该点最突出的错因类别，可能为 null */
  top_error_category: string | null
  /** 可直接练的题目 ID（status=active） */
  suggested_question_ids: string[]
}

/** GET /api/profile/recommendations 的 data 载荷。 */
export interface RecommendationData {
  student_id: string
  /** Top N 推荐 */
  recommendations: RecommendationItem[]
  computed_at: string | null
  mode: string
}

/** 五类错因类别枚举（与后端 error_categories 一致）。 */
export type ErrorCategory = 'READ' | 'METHOD' | 'CALC' | 'KNOWLEDGE' | 'CHECK'

/** 错因类别 → 中文标签（与后端 error_categories.ERROR_LABELS 保持一致，作为兜底）。 */
export const ERROR_CATEGORY_LABEL: Record<ErrorCategory, string> = {
  READ: '读题错',
  METHOD: '方法错',
  CALC: '计算错',
  KNOWLEDGE: '知识点缺',
  CHECK: '检查漏',
}

/** 错因类别 → 图表配色（用于 DonutChart 分段）。 */
export const ERROR_CATEGORY_COLOR: Record<ErrorCategory, string> = {
  READ: '#06b6d4',
  METHOD: '#f59e0b',
  CALC: '#3b82f6',
  KNOWLEDGE: '#ef4444',
  CHECK: '#8b5cf6',
}

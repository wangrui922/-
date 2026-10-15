/**
 * 今日学习计划类型契约（T07 自适应编排 · V6.5 B 批）。
 *
 * ⚠️ 命名口径（与 practice 模块**刻意不同**，勿混）：
 *   本模块对齐后端 `backend/app/api/progress.py` 的 `_adaptive_plan_to_api`，
 *   信封外层字段是 **camelCase**（generatedAt / engineVersion / actualCount …）。
 *   但 `relaxationSteps` 数组是抽题放宽记录的**原样直通**——后端只改了外层键名，
 *   内层字段保持 snake_case（level / label / pool_size / applied_at / applied），
 *   前端必须按 snake_case 读，**不要**臆测转驼峰。
 *
 *   对比参考：`types/practice.ts` 出入参一律 snake_case。
 */

/** 降级引擎标识：`adaptive.enabled=false` 或编排异常时，出参 `engineVersion` 变成它。 */
export const ENGINE_VERSION_LEGACY = 'legacy-draw'

/** 抽题放宽记录（后端 `_draw_with_relaxation` 直通，**内层 snake_case**）。 */
export interface RelaxationStep {
  /** 档位编号：1 = 可归因 + 回避 7 天；2 = 取消回避；3 = 放开 kp */
  level: number
  /** 放宽说明，后端已生成中文（如「已放开：包含最近做过的题（同样条件）」），原样展示 */
  label: string
  /** 本档候选池大小 */
  pool_size: number
  /** ISO 时间戳；早期 fallback 可能缺省 */
  applied_at?: string
  /** 本档是否真的放宽过 */
  applied?: boolean
}

/** 今日计划里的单个知识点条目。 */
export interface PlanItem {
  /** 知识点 ID */
  knowledgePointId: string
  /** 知识点名称 */
  kpName: string
  /** 该知识点计划练习的题数 */
  questionCount: number
  /** 难度档位数组（后端只产 1 / 3 / 5，异常值原样展示） */
  difficultyLevels: number[]
  /** 难度带策略说明（后端生成，可空） */
  strategy: string
  /** 入选理由，后端已生成中文，直接展示 */
  reason: string
}

/** `GET /api/progress/adaptive-plan` 出参（只读，不建会话、不写库）。 */
export interface TodayPlan {
  /** ISO 生成时间 */
  generatedAt: string
  /** 引擎版本；`legacy-draw` 表示已降级为普通抽题（此时 items 为空） */
  engineVersion: string
  /** 本批计划题数 */
  total: number
  /** 实际抽到的题数，可能 < total（少给不凑数） */
  actualCount: number
  /** total - actualCount；> 0 即部分知识点题库不足 */
  missCount: number
  /** 抽到的题目 ID 列表（MVP 仅透传展示，会话 API 暂不支持按它建会话） */
  questionIds: string[]
  /** 今日该练的知识点清单；降级时为空数组 */
  items: PlanItem[]
  /** 抽题放宽过程记录（仅放宽过的档），原样逐行展示 */
  relaxationSteps: RelaxationStep[]
}

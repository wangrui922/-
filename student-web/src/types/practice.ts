/**
 * 练习模块类型契约（题库消费层 · F2）。
 *
 * ⚠️ 对齐 `backend/app/schemas/practice.py` 的 snake_case 接口契约：
 *   - 出入参一律 snake_case，本目录**绝不**转驼峰；
 *   - 新增字段必须配套默认值（老组件不读新字段不破）。
 *
 * 2026-08-11 重构新增（T7）：
 *   - `OptionsStatus` 4 态枚举；
 *   - `RelaxationStep` 抽题逐档披露记录；
 *   - `BankQuestion.options_status`、`DrawResult.* 4 字段`。
 */

export type OptionsStatus = 'complete' | 'partial' | 'missing' | 'n/a'

/** 抽题 fallback 的逐档记录（后端 `RelaxationStepOut` 对齐）。 */
export interface RelaxationStep {
  /** 档位编号：1 = 可归因 + 回避 7 天；2 = 可归因 + 取消回避；3 = 放开 kp；6 = 兜底 */
  level: number
  label: string
  pool_size: number
  /** ISO 时间戳；空串表示后端未填（早期 fallback 兼容） */
  applied_at?: string
  /** 本档是否真的放宽过（口径与 pool_size > 0 对齐） */
  applied?: boolean
}

/** 池子披露的兼容默认（被多处 `emptyPool` 复用）。 */
export const DEFAULT_RELAXATION_STEPS: ReadonlyArray<RelaxationStep> = Object.freeze([])

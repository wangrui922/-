/**
 * 今日学习计划接口封装（T07 自适应编排 · V6.5 B 批前端接线）。
 *
 * 对应后端 `backend/app/api/progress.py`：
 *   GET /api/progress/adaptive-plan  今日学习计划（**只读**，不建会话、不写库）
 *
 * ⚠️ 命名口径：本模块出参是 **camelCase**（后端 `_adaptive_plan_to_api` 转换过），
 *    与 practice 模块的 snake_case 刻意不同——两套口径**不要**混用，
 *    详见 types/plan.ts 头部注释。
 *
 * Query 参数：
 *   count  本批题数（1~30，缺省 10）
 *   grade  年级筛选（1~12，缺省不限）
 *
 * 降级语义：`adaptive.enabled=false` 或编排异常时，后端自动降级为普通抽题，
 *   出参 `engineVersion` 变成 `'legacy-draw'`、`items` 为空数组，但
 *   `total / actualCount / questionIds` 仍然有效——前端可正常展示，
 *   标注「综合练习」即可，不需要当错误处理。
 *
 * 响应外壳 { code, msg, data } 已由 api/http.ts 拦截器拆封，
 * 本层拿到、返回的都直接是 data；code !== 0 会抛 ApiError。
 */
import { get } from '@/api/http'

import { ENGINE_VERSION_LEGACY, type TodayPlan } from '@/types/plan'

export { ENGINE_VERSION_LEGACY }

// ==================================================================== 常量

/** 本批题数下限（后端 ge=1）。 */
export const PLAN_COUNT_MIN = 1

/** 本批题数上限（后端 le=30）。 */
export const PLAN_COUNT_MAX = 30

/** 年级下限（后端 ge=1）。 */
export const PLAN_GRADE_MIN = 1

/** 年级上限（后端 le=12）。 */
export const PLAN_GRADE_MAX = 12

// ==================================================================== 接口

/** `getTodayPlan` 查询参数，全部可选；不传则用后端缺省（10 题 / 不限年级）。 */
export interface TodayPlanParams {
  /** 本批题数 1~30，越界值前端先夹取再发 */
  count?: number
  /** 年级 1~12，越界值前端先夹取再发 */
  grade?: number
}

/**
 * 拉取今日学习计划（自适应编排产出的知识点清单，只读）。
 *
 * 与 practice.ts 的 `drawSession` 同款防御：只把「真正填了」的参数塞进 query，
 * undefined / NaN 一律不传，避免把空条件发给后端。
 *
 * @param params count / grade，全部可选
 * @returns 今日计划信封（camelCase），注意降级时 engineVersion 为 legacy-draw
 * @throws ApiError 400 未绑定学生 / 参数越界；401 未登录（http.ts 已处理跳转）
 */
export function getTodayPlan(params: TodayPlanParams = {}): Promise<TodayPlan> {
  const query: Record<string, unknown> = {}

  if (typeof params.count === 'number' && Number.isFinite(params.count)) {
    query.count = Math.max(PLAN_COUNT_MIN, Math.min(PLAN_COUNT_MAX, Math.round(params.count)))
  }
  if (typeof params.grade === 'number' && Number.isFinite(params.grade)) {
    query.grade = Math.max(PLAN_GRADE_MIN, Math.min(PLAN_GRADE_MAX, Math.round(params.grade)))
  }

  return get<TodayPlan>('/progress/adaptive-plan', query)
}

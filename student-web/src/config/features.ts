/**
 * T04 功能开关（前端）。
 *
 * 后端能力就绪后在此统一开关，避免散落在各页面硬编码 true/false。
 */

/**
 * 是否支持「标记已掌握」（PUT /api/mistake/{id}/solve）。
 * 后端 T04 · B10 / OQ5 已落地该接口，故置 true。
 */
export const SUPPORTS_MARK_SOLVED = true

/** 学习画像看板是否启用（预留，当前恒 true）。 */
export const FEATURE_DASHBOARD = true

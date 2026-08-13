/**
 * 学段（小学/初中/高中）全局状态（轻量实现，不引 pinia）。
 *
 * 设计定位（#68 学段 UI）：
 *   - 这是「题库过滤」的粗粒度开关，持久化在浏览器 localStorage，
 *     与登录态（auth.ts）相互独立——换账号、退出登录都不会清掉学段偏好。
 *   - 后端 `Student.grade` 仍是 1-6 + stage(primary/junior) 的遗留约束，
 *     不能塞 7-12；所以学段**不写学生档案**，只在抽题时翻译成年级区间
 *     （grade_band）下发给 `POST /api/practice/session`，由后端按
 *     `Question.grade` 的区间做 SQL 过滤。
 *   - 默认 `null`（= 不限/全部学段）：不传 grade_band，行为与历史完全一致。
 *
 * 导入点对齐 auth.ts：模块级 `reactive` + 显式读写 localStorage，组件直接读
 * `segmentState.band`。
 */
import { reactive } from 'vue'

/** 学段取值。 */
export type Segment = '小学' | '初中' | '高中'

/** 持久化键（与 auth.ts 命名风格一致：aicoach.*）。 */
const BAND_KEY = 'aicoach.segment.band'

/** 学段候选（顺序即展示顺序）。 */
export const BANDS: readonly Segment[] = ['小学', '初中', '高中'] as const

function _readBand(): Segment | null {
  const raw = localStorage.getItem(BAND_KEY)
  return raw === '小学' || raw === '初中' || raw === '高中' ? raw : null
}

/** 供组件模板与 stores 直接读取的响应式学段态。 */
export const segmentState = reactive<{ band: Segment | null }>({
  band: _readBand(),
})

/** 取当前学段（null 表示不限）。 */
export function currentBand(): Segment | null {
  return segmentState.band
}

/**
 * 设定学段。
 *
 * @param band 学段；传 null 表示「不限」（清除持久化）。
 */
export function setBand(band: Segment | null): void {
  segmentState.band = band
  if (band) {
    localStorage.setItem(BAND_KEY, band)
  } else {
    localStorage.removeItem(BAND_KEY)
  }
}

/**
 * 学段 → 年级区间（小学 1-6 / 初中 7-9 / 高中 10-12）。
 *
 * 仅前端展示/启发式用；真正的区间过滤发生在后端 `practice_service`
 * （`BAND_GRADE_RANGE`），前后端口径必须一致，改这里要同步改 `app/models/content.py`。
 */
export function bandGradeRange(band: Segment): [number, number] {
  if (band === '初中') return [7, 9]
  if (band === '高中') return [10, 12]
  return [1, 6]
}

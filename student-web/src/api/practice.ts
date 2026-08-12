/**
 * 题库练习接口封装（题库消费层 · F2）。
 *
 * 对应后端 `backend/app/api/practice.py` 的 5 个端点，全部 POST：
 *   POST /api/practice/session  抽题建会话
 *   POST /api/practice/answer   提交单题作答（L1 规则判分）
 *   POST /api/practice/reveal   看参考答案（只对做错的题开放）
 *   POST /api/practice/finish   结算（错题入本 + 掌握度失效）
 *   POST /api/practice/guide    让老师带我想一想（AI 过程引导，慢）
 *
 * ⚠️ 命名硬约束（对齐后端 schemas/practice.py 的 C10）：
 *   出入参一律 **snake_case**，后端刻意没启用 alias_generator，
 *   这里**绝不能**转驼峰，否则字段对不上。
 *
 * 响应外壳 { code, msg, data } 已由 api/http.ts 拦截器拆封，
 * 本层拿到、返回的都直接是 data；code !== 0 会抛 ApiError。
 */
import { post } from '@/api/http'

import type { OptionsStatus, RelaxationStep } from '@/types/practice'

// ==================================================================== 常量

/** 判分层级：规则判分可信。 */
export const JUDGE_LEVEL_RULE = 1

/** 判分层级：无法自动判定（此时 is_correct 恒为 false，**不可**展示成「你错了」）。 */
export const JUDGE_LEVEL_UNKNOWN = 4

/** 单题耗时上限（秒）。前端先截断，后端再兜一层 min(d, 600)。 */
export const MAX_DURATION_SEC = 600

/** 题量下限（后端 ge=1）。 */
export const MIN_QUESTION_COUNT = 1

/** 题量上限（后端 le=30）。 */
export const MAX_QUESTION_COUNT = 30

/** 教材版本可选值。 */
export const EDITIONS = ['青岛版', '苏教版'] as const

/** 册内分区可选值。 */
export const SECTIONS = ['温故', '预习', '提优', '摸底', '专项', '自测'] as const

/** 难度可选值（后端只认 1 / 3 / 5）。 */
export const DIFFICULTIES = [1, 3, 5] as const

/** 教材版本。 */
export type PracticeEdition = (typeof EDITIONS)[number]

/** 册内分区。 */
export type PracticeSection = (typeof SECTIONS)[number]

/** 难度档位。 */
export type PracticeDifficulty = (typeof DIFFICULTIES)[number]

/** 题型：题库消费层目前只出这两类（L1 可判分）。 */
export const QUESTION_TYPE_CHOICE = '选择'
export const QUESTION_TYPE_JUDGE = '判断'

/** 判断题的两个作答键位（与后端 judge_engine 的归一表一致）。 */
export const JUDGE_ANSWER_TRUE = '√'
export const JUDGE_ANSWER_FALSE = '×'

// ==================================================================== 类型

/** 抽题筛选条件，全部可选；不传则从全部 L1 安全池随机抽。 */
export interface DrawParams {
  /** 年级 1~9（升入年级口径） */
  grade?: number
  /** 教材版本：青岛版 / 苏教版 */
  edition?: string
  /** 册内分区：温故 / 预习 / 提优 / 摸底 / 专项 / 自测 */
  section?: string
  /** 指定知识点 ID */
  kp_id?: string
  /** 难度 1 / 3 / 5 */
  difficulty?: PracticeDifficulty
  /** 题量 1~30，默认 10 */
  count?: number
}

/** 归一化后的选项。 */
export interface BankOption {
  key: string
  text: string
}

/** 抽题出参里的单题。 */
export interface BankQuestion {
  question_id: string
  /** '选择' | '判断' */
  type: string
  stem: string
  /** 判断题为空数组 */
  options: BankOption[]
  /**
   * 选项完整性枚举（设计 §3.1）。前端 `BankQuestionCard` 按这个值渲染兜底态：
   *
   * - `complete`  归一化后 ≥4 个选项（正常渲染）；
   * - `partial`   1~3 个选项 → 选项按钮 disabled + amber 提示；
   * - `missing`   0 个选项；
   * - `n/a`       非选择题，跳过 disabled 逻辑。
   *
   * 默认 `'n/a'` 让老组件（不读此字段）行为不变。
   */
  options_status?: OptionsStatus
  difficulty: number | null
  grade: number | null
  edition: string | null
  section: string | null
  /** 知识点 ID，新题可能为空 —— 为空即不计入掌握度，必须如实告知 */
  kp_id: string | null
}

/** POST /practice/session 出参。 */
export interface DrawResult {
  session_id: string
  questions: BankQuestion[]
  /** 实际使用的抽题池大小 */
  pool_size: number
  /** 池中不可归因（无知识点）的题数 —— 诚实披露，不许藏 */
  unmapped_count: number
  /** 是否自动放开到了非第 1 档 —— 兼容老客户端的布尔位 = (relaxed_to_level > 1) */
  auto_relaxed: boolean
  /** 请求题量（夹取后） */
  requested_count: number
  // ---- T5/T7 新增 4 字段（设计 §3.3）----
  /** 本轮实际抽到的题数；可能 < requested_count（少给不凑数） */
  actual_count?: number
  /** requested_count - actual_count；前端看到 >0 就该提示「少给几道」 */
  miss_count?: number
  /** 实际放宽到的档位：1-3 命中某档；6 兜底（其实不会有，因为是 404） */
  relaxed_to_level?: number
  /** 逐档尝试记录，前端原样逐行展示 */
  relaxation_steps?: ReadonlyArray<RelaxationStep>
}

/** POST /practice/answer 入参。 */
export interface SubmitParams {
  session_id: string
  question_id: string
  /** 选择题传键位如 'B'；判断题传 '√' / '×' */
  selected_answer?: string
  /** 单题耗时（秒），前端已 min(d, 600) */
  duration_sec?: number
  /** 客户端幂等键，重复提交同一个不会重复落库 */
  client_event_id?: string
}

/** POST /practice/answer 出参。 */
export interface SubmitResult {
  question_id: string
  /** level === 4 时恒为 false，**不代表真的做错了** */
  is_correct: boolean
  /** 1 = 规则判分可信；4 = 无法自动判定 */
  level: number
  reason: string
  duration_sec: number | null
}

/** POST /practice/reveal 出参。 */
export interface RevealResult {
  question_id: string
  correct_answer: string
  analysis: string
}

/** 结算出参里的逐题快照。 */
export interface FinishItem {
  question_id: string
  status: 'pending' | 'done'
  is_correct: boolean | null
  level: number | null
  selected_answer: string | null
  duration_sec: number | null
}

/** 结算出参里的薄弱知识点。 */
export interface WeakPoint {
  kp_id: string
  kp_name: string
  wrong_count: number
}

/** POST /practice/finish 出参。 */
export interface FinishResult {
  session_id: string
  total: number
  correct: number
  wrong: number
  /** 新建的错题本条数（幂等，重复结算为 0） */
  mistakes_created: number
  /** 本次是否练到了可归因知识点（会进掌握度闭环） */
  mastery_refreshed: boolean
  answered: number
  /** 正确率 0~1（按已作答题计） */
  accuracy: number
  /** 本次题目里无知识点的题数 */
  unmapped_count: number
  items: FinishItem[]
  /** 薄弱知识点 Top5 */
  weak_points: WeakPoint[]
}

/** POST /practice/guide 入参。 */
export interface GuideParams {
  session_id: string
  question_id: string
  /** 孩子写的思路 / 过程，可空 */
  work?: string
}

/** POST /practice/guide 出参。 */
export interface GuideResult {
  question_id: string
  /** 给孩子看的引导语（首轮不给答案） */
  reply: string
  /** 老师是否在问「这是你的最终答案吗」 */
  ask_final_confirm: boolean
  prompt_version: string
}

// ==================================================================== 接口

/**
 * 抽题并开一个练习会话。
 *
 * 只把「真正填了」的筛选项塞进请求体：空串 / undefined / NaN 一律不传，
 * 避免把 `edition: ''` 这种空条件发给后端导致抽不到题。
 *
 * @param params 筛选条件，全部可选
 * @returns 会话 ID + 题目列表 + 池子披露信息
 * @throws ApiError 404「这个筛选条件下暂时没有可以练的题，换个条件试试」
 */
export function drawSession(params: DrawParams = {}): Promise<DrawResult> {
  const body: Record<string, unknown> = {}

  if (typeof params.grade === 'number' && Number.isFinite(params.grade)) {
    body.grade = params.grade
  }
  if (params.edition) body.edition = params.edition
  if (params.section) body.section = params.section
  if (params.kp_id) body.kp_id = params.kp_id
  if (typeof params.difficulty === 'number') body.difficulty = params.difficulty
  if (typeof params.count === 'number' && Number.isFinite(params.count)) {
    body.count = Math.max(MIN_QUESTION_COUNT, Math.min(MAX_QUESTION_COUNT, Math.round(params.count)))
  }

  return post<DrawResult>('/practice/session', body)
}

/**
 * 提交单题作答，拿 L1 判分结论。
 *
 * @param params 作答载荷；duration_sec 由调用方先行截断到 600
 * @returns 判分结论，注意 level === 4 要按「无法自动判定」渲染
 */
export function submitAnswer(params: SubmitParams): Promise<SubmitResult> {
  const body: Record<string, unknown> = {
    session_id: params.session_id,
    question_id: params.question_id,
  }

  if (params.selected_answer !== undefined) body.selected_answer = params.selected_answer
  if (typeof params.duration_sec === 'number' && Number.isFinite(params.duration_sec)) {
    body.duration_sec = Math.max(0, Math.min(MAX_DURATION_SEC, Math.round(params.duration_sec)))
  }
  if (params.client_event_id) body.client_event_id = params.client_event_id

  return post<SubmitResult>('/practice/answer', body)
}

/**
 * 看参考答案。
 *
 * 后端约束：**只有答错后才能看**（答对返 403，没作答返 400）。
 * 调用方必须先按这个约束控制入口可见性，别让孩子撞墙。
 *
 * @param params 会话 ID + 题目 ID
 */
export function revealAnswer(params: {
  session_id: string
  question_id: string
}): Promise<RevealResult> {
  return post<RevealResult>('/practice/reveal', {
    session_id: params.session_id,
    question_id: params.question_id,
  })
}

/**
 * 结算本次练习（幂等，重复调 mistakes_created 为 0）。
 *
 * @param sessionId 练习会话 ID
 */
export function finishSession(sessionId: string): Promise<FinishResult> {
  return post<FinishResult>('/practice/finish', { session_id: sessionId })
}

/**
 * 「让老师带我想一想」：AI 过程引导，首轮不公布答案。
 *
 * ⚠️ 走大模型，**慢**（http.ts 超时 90s）。调用方必须给 loading 态并防重复点击。
 *
 * @param params 会话 ID + 题目 ID + 孩子写的过程（可空）
 */
export function guideQuestion(params: GuideParams): Promise<GuideResult> {
  const body: Record<string, unknown> = {
    session_id: params.session_id,
    question_id: params.question_id,
  }
  if (params.work) body.work = params.work

  return post<GuideResult>('/practice/guide', body)
}

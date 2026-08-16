/**
 * 练习会话响应式存储（题库消费层 · F2）。
 *
 * 仿 stores/auth.ts / stores/profile.ts 用 Vue 的 reactive（**不引 pinia**，
 * package.json 里没有 pinia）。
 *
 * 状态机三态：
 *   filter（挑条件） → doing（一题一题做） → result（结算页）
 * 只有 startPractice / finishPractice / backToFilter 会切 phase。
 *
 * 两条产品铁律在本层的落点：
 *   1. **首轮绝不给答案**：本 store 不缓存任何答案，只有用户显式调
 *      revealCurrent() 才会去后端取，且 canReveal() 严格对齐后端约束
 *      （必须已作答 && 判定不正确）。
 *   2. **诚实披露**：抽题返回的 pool_size / unmapped_count / auto_relaxed
 *      原样存进 state.pool，另外按本次实际题目算 unmappedInSession()，
 *      页面如实展示，不做任何隐藏。
 */
import { reactive, watch } from 'vue'
import { useStreak } from '@/stores/streak'

import { ApiError, toMessage } from '@/api/http'
import {
  drawSession,
  finishSession,
  guideQuestion,
  revealAnswer,
  submitAnswer,
  JUDGE_LEVEL_UNKNOWN,
  MAX_DURATION_SEC,
  type BankQuestion,
  type DrawParams,
  type FinishResult,
  type RevealResult,
  type SubmitResult,
} from '@/api/practice'
import type { GuideMessage } from '@/types/guide'
import type { RelaxationStep } from '@/types/practice'

/** 页面三态。 */
export type PracticePhase = 'filter' | 'doing' | 'result'

/**
 * 引导抽屉里的一条对话。
 *
 * ⚠️ T1 起单一真源在 `@/types/guide`（AI 老师页复用同一个面板），
 * 这里只做转发导出，保持既有 `import type { GuideMessage } from '@/stores/practice'` 不破。
 */
export type { GuideMessage }

/** AI 引导抽屉状态。 */
export interface GuideState {
  open: boolean
  loading: boolean
  error: string
  /** 抽屉当前挂在哪道题上 */
  question_id: string
  messages: GuideMessage[]
  /** 老师是否正在问「这是你的最终答案吗」 */
  ask_final_confirm: boolean
}

/** 抽题池的披露信息（原样来自后端，不加工）。 */
export interface PoolInfo {
  pool_size: number
  unmapped_count: number
  auto_relaxed: boolean
  requested_count: number
  // ---- T5/T7 新增字段（设计 §3.3）----
  /** 本轮实际抽到的题数；可能 < requested_count（少给不凑数） */
  actual_count: number
  /** requested_count - actual_count；>0 时前端该提示「少给几道」 */
  miss_count: number
  /** 实际放宽到的档位：1-3 / 6 兜底 */
  relaxed_to_level: number
  /** 逐档尝试记录，答题页顶部的常驻披露条直接渲染 */
  relaxation_steps: ReadonlyArray<RelaxationStep>
}

/**
 * 知识点直达上下文（从画像页「去练习」或错题本「练同类题」深链进来时才有）。
 *
 * 字段一律 snake_case，与 `/practice/session` 的 `kp_id` 参数同名同形，
 * 中途不转驼峰，避免透传时打穿后端契约。
 *
 * `grade` 与 `source` 为 T02 新增：
 *   - `grade` 是 P0-3 止血——知识点树仅 13 个扁平粗桶，深链同时带 grade
 *     收窄到「同年级 × 同知识点」，避免抽到跨全学段的题。画像页不传
 *     grade，行为与现状完全一致。
 *   - `source` 仅用于练习页顶部来源提示文案（「来自你的错题」），
 *     不参与抽题请求。画像页不传 source，提示区不渲染后缀。
 */
export interface DeepLinkInfo {
  kp_id: string
  kp_name: string
  /** 年级 1~9，深链同时带 grade 收窄抽题范围（P0-3 止血） */
  grade?: number
  /** 深链来源，用于顶部来源提示文案区分 */
  source?: 'mistakes' | 'profile'
}

/** 练习全局状态。 */
export interface PracticeState {
  phase: PracticePhase
  /** 抽题中 */
  drawing: boolean
  drawError: string
  /**
   * 抽题失败是否属于「这个条件下一道题都没有」（后端 code=404）。
   *
   * 单独存一个标记而不是靠文案匹配：深链场景要拿它换一套指名道姓的空态文案
   * （「方程这个知识点暂时没有能自动判分的题」），普通筛选场景仍用通用文案。
   */
  drawEmpty: boolean
  /** 本次会话是否由知识点深链发起；null 表示用户自己挑的条件 */
  deep_link: DeepLinkInfo | null
  session_id: string
  questions: BankQuestion[]
  /** 当前题序（0 基） */
  index: number
  pool: PoolInfo
  /** question_id → 选中的键位（'B' / '√' / '×'） */
  selected: Record<string, string>
  /** question_id → 判分结论 */
  feedback: Record<string, SubmitResult>
  /** question_id → 参考答案（只有用户主动看过才有） */
  reveal: Record<string, RevealResult>
  /**
   * question_id → 孩子对 level=4 题目的自评结论（true 表示自评做对了）。
   * 纯前端记录：后端没有自评端点，这里只用于给孩子一个闭环反馈。
   */
  selfCheck: Record<string, boolean>
  submitting: boolean
  submitError: string
  revealing: boolean
  revealError: string
  guide: GuideState
  finishing: boolean
  finishError: string
  result: FinishResult | null
  /** 上一次用的筛选条件，「再来一组」直接复用 */
  lastParams: DrawParams
}

/** 毫秒 → 秒的换算基数。 */
const MS_PER_SEC = 1000

function emptyPool(): PoolInfo {
  return {
    pool_size: 0,
    unmapped_count: 0,
    auto_relaxed: false,
    requested_count: 0,
    actual_count: 0,
    miss_count: 0,
    relaxed_to_level: 1,
    relaxation_steps: [],
  }
}

function emptyGuide(): GuideState {
  return {
    open: false,
    loading: false,
    error: '',
    question_id: '',
    messages: [],
    ask_final_confirm: false,
  }
}

/** 供组件模板直接读取的响应式练习态。 */
export const practiceState = reactive<PracticeState>({
  phase: 'filter',
  drawing: false,
  drawError: '',
  drawEmpty: false,
  deep_link: null,
  session_id: '',
  questions: [],
  index: 0,
  pool: emptyPool(),
  selected: {},
  feedback: {},
  reveal: {},
  selfCheck: {},
  submitting: false,
  submitError: '',
  revealing: false,
  revealError: '',
  guide: emptyGuide(),
  finishing: false,
  finishError: '',
  result: null,
  lastParams: {},
})

// ---- 非响应式的辅助状态 ----------------------------------------------------

/** 当前题目的开始作答时间戳（毫秒）。切题时重置。 */
let questionStartedAt: number = Date.now()

/** question_id → 幂等键。同一题重试提交复用同一个键，避免重复落库。 */
const eventIds = new Map<string, string>()

/**
 * 生成客户端幂等键。
 *
 * crypto.randomUUID 在非安全上下文（http 局域网调试）下不可用，给一个退化实现。
 */
function newEventId(): string {
  const webCrypto = globalThis.crypto
  if (webCrypto && typeof webCrypto.randomUUID === 'function') {
    return webCrypto.randomUUID()
  }
  return `pe-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/** 取（或首次创建）某题的幂等键。 */
function eventIdFor(questionId: string): string {
  const existing = eventIds.get(questionId)
  if (existing) return existing
  const created = newEventId()
  eventIds.set(questionId, created)
  return created
}

/** 重置计时器（进入一道题时调用）。 */
function resetTimer(): void {
  questionStartedAt = Date.now()
}

/**
 * 结算本题耗时，前端就地截断到 600 秒（后端还会再兜一层）。
 *
 * @returns 0 ~ 600 的整数秒
 */
function elapsedSec(): number {
  const raw = Math.round((Date.now() - questionStartedAt) / MS_PER_SEC)
  return Math.max(0, Math.min(MAX_DURATION_SEC, raw))
}

// ---- 只读派生（组件里用 computed 包一层即可） -------------------------------

/** 当前题目，越界时返回 null。 */
export function currentQuestion(): BankQuestion | null {
  return practiceState.questions[practiceState.index] ?? null
}

/** 当前题目的判分结论，未作答返回 null。 */
export function currentFeedback(): SubmitResult | null {
  const q = currentQuestion()
  if (!q) return null
  return practiceState.feedback[q.question_id] ?? null
}

/** 当前题目已看过的参考答案，没看过返回 null。 */
export function currentReveal(): RevealResult | null {
  const q = currentQuestion()
  if (!q) return null
  return practiceState.reveal[q.question_id] ?? null
}

/** 当前题目选中的键位，未选返回空串。 */
export function currentSelected(): string {
  const q = currentQuestion()
  if (!q) return ''
  return practiceState.selected[q.question_id] ?? ''
}

/** 已作答（拿到判分结论）的题数。 */
export function answeredCount(): number {
  return Object.keys(practiceState.feedback).length
}

/**
 * 会被后端收进错题本的题数。
 *
 * 口径**刻意**对齐后端 `finish_session` 的 C4：仅 `is_correct === false` 的题
 * 入本——注意 level=4（无法自动判定）时 is_correct 恒为 false，所以它**也会**入本。
 * 这里不把 level=4 剔掉，否则退出确认弹窗承诺的条数会少于实际入本条数，
 * 变成一句对不上账的假话。
 */
export function wrongCount(): number {
  return Object.values(practiceState.feedback).filter((fb) => fb.is_correct === false).length
}

/**
 * 已作答的题里「老师没法自动判分」（level=4）的题数。
 *
 * 铁律：level=4 不许说成「你做错了」。退出确认的文案要靠这个数把
 * 「确实判错的」和「没判出来的」分开讲，不能囫囵算作错题。
 */
export function unjudgedCount(): number {
  return Object.values(practiceState.feedback).filter(
    (fb) => fb.level === JUDGE_LEVEL_UNKNOWN,
  ).length
}

/**
 * 池子是否小于用户想要的题量（抽到的题比请求的少）。
 *
 * 后端在这种情况下**不会**给任何标记：`auto_relaxed` 仍是 false，只是
 * questions 数组短了一截。深链到冷门知识点时非常容易撞上
 * （例：知识点「分数」在 L1 安全池里只有 2 道题，请求 10 道就只给 2 道），
 * 不主动说的话孩子只会觉得「怎么点一下就没了」。
 */
export function isShortPool(): boolean {
  const { pool_size, requested_count } = practiceState.pool
  return pool_size > 0 && requested_count > 0 && pool_size < requested_count
}

/** 是否所有题都已作答。 */
export function isAllAnswered(): boolean {
  return practiceState.questions.length > 0 && answeredCount() >= practiceState.questions.length
}

/** 本次抽到的题里，暂未关联知识点的题数（不计入掌握度，要如实告知）。 */
export function unmappedInSession(): number {
  return practiceState.questions.filter((q) => !q.kp_id).length
}

/**
 * 「看答案」入口是否可见。
 *
 * 严格对齐后端 reveal 的约束：必须**已作答**且**判定不正确**。
 * 答对了调会 403、没作答调会 400，所以这里不放行，免得孩子撞墙。
 * 注意 level=4 时 is_correct 恒为 false，属于「可以核对答案」的场景，放行。
 *
 * 注：「是不是 level=4」的判断不放在这里——AnswerFeedbackBar 直接从自己的
 * result prop 算，两边都以 api/practice.ts 的 JUDGE_LEVEL_UNKNOWN 为准，
 * 避免同一套判定散在两处漂移。
 */
export function canReveal(): boolean {
  const fb = currentFeedback()
  return fb !== null && fb.is_correct === false
}

// ---- 动作 ------------------------------------------------------------------

/** 把会话相关的数据清干净（保留 lastParams）。 */
function clearSession(): void {
  practiceState.session_id = ''
  practiceState.questions = []
  practiceState.index = 0
  practiceState.pool = emptyPool()
  practiceState.selected = {}
  practiceState.feedback = {}
  practiceState.reveal = {}
  practiceState.selfCheck = {}
  practiceState.submitError = ''
  practiceState.revealError = ''
  practiceState.finishError = ''
  practiceState.result = null
  practiceState.guide = emptyGuide()
  eventIds.clear()
  clearProgress()
}

/**
 * 抽题开练。成功后切到 doing 态。
 *
 * @param params 筛选条件
 * @returns 是否成功进入答题态
 */
export async function startPractice(params: DrawParams = {}): Promise<boolean> {
  if (practiceState.drawing) return false

  practiceState.drawing = true
  practiceState.drawError = ''
  practiceState.drawEmpty = false
  // 默认按「用户自己挑的条件」处理：清掉上一轮的知识点直达上下文，
  // 否则深链抽题失败后用户改用筛选卡开练，答题页会挂着一条
  // 「正在练：方程」的假回显。startDeepLink 会在本函数返回后补记回来。
  practiceState.deep_link = null

  try {
    const data = await drawSession(params)

    if (!data.questions || data.questions.length === 0) {
      practiceState.drawError = '这个筛选条件下暂时没有可以练的题，换个条件试试'
      practiceState.drawEmpty = true
      return false
    }

    clearSession()
    practiceState.lastParams = { ...params }
    practiceState.session_id = data.session_id
    practiceState.questions = data.questions
    practiceState.pool = {
      pool_size: data.pool_size ?? 0,
      unmapped_count: data.unmapped_count ?? 0,
      auto_relaxed: Boolean(data.auto_relaxed),
      requested_count: data.requested_count ?? data.questions.length,
      actual_count: data.actual_count ?? data.questions.length,
      miss_count: data.miss_count ?? Math.max(0, (data.requested_count ?? data.questions.length) - data.questions.length),
      relaxed_to_level: data.relaxed_to_level ?? (data.auto_relaxed ? 2 : 1),
      relaxation_steps: data.relaxation_steps ?? [],
    }
    practiceState.index = 0
    practiceState.phase = 'doing'
    resetTimer()
    return true
  } catch (error) {
    practiceState.drawError = toMessage(error, '抽题失败了，换个条件再试试')
    // 后端 code=404 才是「真的一道题都没有」，其余（网络/500）是故障，别混为一谈
    practiceState.drawEmpty = error instanceof ApiError && error.code === 404
    return false
  } finally {
    practiceState.drawing = false
  }
}

/**
 * 从知识点直达开练（画像页「去练习」或错题本「练同类题」深链）。
 *
 * 推荐项的 kp_id 与题库 `questions.kp_id` 是同一套 ID（都指向
 * `knowledge_points.id`），所以**直接透传**，中间不做任何映射。
 *
 * `grade` 是 P0-3 止血参数：知识点树仅 13 个扁平粗桶，不带 grade 会抽到
 * 跨全学段的题。画像页不传 grade（undefined），drawSession 里
 * `typeof params.grade === 'number'` 检查不过，不会发给后端，行为不变。
 *
 * 注意抽题**成功与否都要**把 deep_link 记下来：失败时页面要靠 kp_name
 * 渲染指名道姓的空态文案（「方程这个知识点暂时没有能自动判分的题」）。
 *
 * @param info 知识点 ID + 名称（名称只用于回显，不参与请求）+ 可选 grade / source
 * @param count 题量，默认 10
 * @returns 是否成功进入答题态
 */
export async function startDeepLink(info: DeepLinkInfo, count: number = 10): Promise<boolean> {
  const ok = await startPractice({ kp_id: info.kp_id, grade: info.grade, count })
  // startPractice 开头会把 deep_link 清成 null（默认按普通筛选处理），
  // 所以这里在它返回之后再补记——成功、失败两条路都要记：
  // 失败时页面要靠 kp_name 渲染指名道姓的空态文案。
  practiceState.deep_link = {
    kp_id: info.kp_id,
    kp_name: info.kp_name,
    grade: info.grade,
    source: info.source,
  }
  return ok
}

/**
 * 选一个答案（未提交前可以反复改主意）。
 *
 * @param key 选择题键位 'A'~'D'，或判断题的 '√' / '×'
 */
export function chooseOption(key: string): void {
  const q = currentQuestion()
  if (!q) return
  // 已经判过分的题不许改答案
  if (practiceState.feedback[q.question_id]) return
  practiceState.selected[q.question_id] = key
  practiceState.submitError = ''
}

/**
 * 提交当前题作答。
 *
 * 幂等键按题缓存，网络抖动重试不会重复落库。
 */
export async function submitCurrent(): Promise<void> {
  const q = currentQuestion()
  if (!q || practiceState.submitting) return
  if (practiceState.feedback[q.question_id]) return

  const answer = practiceState.selected[q.question_id] ?? ''
  if (!answer) {
    practiceState.submitError = '先选一个答案再提交哦'
    return
  }

  practiceState.submitting = true
  practiceState.submitError = ''

  try {
    const data = await submitAnswer({
      session_id: practiceState.session_id,
      question_id: q.question_id,
      selected_answer: answer,
      duration_sec: elapsedSec(),
      client_event_id: eventIdFor(q.question_id),
    })
    practiceState.feedback[q.question_id] = data
    // 打卡：今天完成至少一题 = 今天打卡（连续天数本地维护）
    useStreak().markCheckin()
  } catch (error) {
    practiceState.submitError = toMessage(error, '提交失败了，再点一次试试')
  } finally {
    practiceState.submitting = false
  }
}

/**
 * 看当前题的参考答案。
 *
 * 调用前请先用 canReveal() 判可见性；这里再兜一层，避免撞后端的 400 / 403。
 */
export async function revealCurrent(): Promise<void> {
  const q = currentQuestion()
  if (!q || practiceState.revealing) return
  if (practiceState.reveal[q.question_id]) return
  if (!canReveal()) {
    practiceState.revealError = '这道题现在还看不了答案哦'
    return
  }

  practiceState.revealing = true
  practiceState.revealError = ''

  try {
    const data = await revealAnswer({
      session_id: practiceState.session_id,
      question_id: q.question_id,
    })
    practiceState.reveal[q.question_id] = data
  } catch (error) {
    practiceState.revealError = toMessage(error, '答案没取到，稍后再试')
  } finally {
    practiceState.revealing = false
  }
}

/**
 * 记录孩子对 level=4 题目的自评结论（纯前端，不落库）。
 *
 * @param correct true 表示孩子自己核对后认为做对了
 */
export function markSelfCheck(correct: boolean): void {
  const q = currentQuestion()
  if (!q) return
  practiceState.selfCheck[q.question_id] = correct
}

/** 下一题（到底了就不动）。 */
export function goNext(): void {
  if (practiceState.index >= practiceState.questions.length - 1) return
  practiceState.index += 1
  practiceState.submitError = ''
  practiceState.revealError = ''
  resetTimer()
}

/** 上一题（到头了就不动）。 */
export function goPrev(): void {
  if (practiceState.index <= 0) return
  practiceState.index -= 1
  practiceState.submitError = ''
  practiceState.revealError = ''
  resetTimer()
}

/**
 * 跳到指定题号（题号导航用）。
 *
 * @param index 0 基题序
 */
export function goTo(index: number): void {
  if (index < 0 || index >= practiceState.questions.length) return
  if (index === practiceState.index) return
  practiceState.index = index
  practiceState.submitError = ''
  practiceState.revealError = ''
  resetTimer()
}

/** 打开引导抽屉（换题时会清空上一题的对话）。 */
export function openGuide(): void {
  const q = currentQuestion()
  if (!q) return
  if (practiceState.guide.question_id !== q.question_id) {
    practiceState.guide.question_id = q.question_id
    practiceState.guide.messages = []
    practiceState.guide.ask_final_confirm = false
    practiceState.guide.error = ''
  }
  practiceState.guide.open = true
}

/** 关掉引导抽屉（对话内容保留，再打开还在）。 */
export function closeGuide(): void {
  practiceState.guide.open = false
}

/**
 * 向老师要一次引导。
 *
 * 走大模型，慢；loading 期间禁止重复触发（由 guide.loading 把门）。
 *
 * @param work 孩子写的思路 / 过程，可空
 */
export async function sendGuide(work: string): Promise<void> {
  const q = currentQuestion()
  if (!q || practiceState.guide.loading) return

  const trimmed = work.trim()
  practiceState.guide.loading = true
  practiceState.guide.error = ''
  if (trimmed) {
    practiceState.guide.messages.push({ role: 'student', text: trimmed })
  }

  try {
    const data = await guideQuestion({
      session_id: practiceState.session_id,
      question_id: q.question_id,
      work: trimmed || undefined,
    })
    practiceState.guide.messages.push({ role: 'teacher', text: data.reply })
    practiceState.guide.ask_final_confirm = Boolean(data.ask_final_confirm)
  } catch (error) {
    practiceState.guide.error = toMessage(error, '老师这会儿有点忙，再点一次试试')
  } finally {
    practiceState.guide.loading = false
  }
}

/** 结算并切到结果页。结算幂等，失败可重试。 */
export async function finishPractice(): Promise<void> {
  if (!practiceState.session_id || practiceState.finishing) return

  practiceState.finishing = true
  practiceState.finishError = ''

  try {
    const data = await finishSession(practiceState.session_id)
    practiceState.result = data
    practiceState.phase = 'result'
    practiceState.guide.open = false
  } catch (error) {
    practiceState.finishError = toMessage(error, '结算失败了，再点一次试试')
  } finally {
    practiceState.finishing = false
  }
}

/**
 * 结算后直接回筛选页（中途退出时的「保存并退出」）。
 *
 * 与 finishPractice 的区别：**不进结果页**，落库成功后直接回筛选态。
 * 为什么一定要走一趟 `/finish`：作答在 `/answer` 时就已落库、掌握度已经受影响，
 * 不结算丢掉的是「错题入本」——而「做错自动进错题本」正是本产品的核心价值，
 * 静默丢掉是最差的选项。`/finish` 幂等，重复调不会重复入本。
 *
 * @returns 是否结算成功（失败时留在答题态，错误挂在 finishError 上供重试）
 */
export async function finishAndExit(): Promise<boolean> {
  if (!practiceState.session_id || practiceState.finishing) return false

  practiceState.finishing = true
  practiceState.finishError = ''

  try {
    await finishSession(practiceState.session_id)
    backToFilter()
    return true
  } catch (error) {
    practiceState.finishError = toMessage(error, '保存失败了，再点一次试试')
    return false
  } finally {
    practiceState.finishing = false
  }
}

/** 回到筛选页重新挑条件（同时退出知识点直达上下文）。 */
export function backToFilter(): void {
  clearSession()
  practiceState.drawError = ''
  practiceState.drawEmpty = false
  practiceState.deep_link = null
  practiceState.phase = 'filter'
}

/**
 * 用上一次的条件再来一组。
 *
 * 上一组若是知识点直达来的，这一组仍在同一个知识点里（lastParams 带着 kp_id），
 * 所以要把直达上下文一并带过去，否则「正在练：分数」的回显会凭空消失、
 * 披露文案也会退回通用版本。
 */
export async function restartSame(): Promise<boolean> {
  const previous = practiceState.deep_link
  const params: DrawParams = { ...practiceState.lastParams }
  if (previous && params.kp_id === previous.kp_id) {
    return startDeepLink(previous, params.count ?? 10)
  }
  return startPractice(params)
}

// ---- 进度本地持久化（防丢进度：切后台 / 刷新 / 跳登录不蒸发） ---------------
//
// 仅持久化「可恢复」的进度白名单字段；瞬时态（submitting / revealing /
// finishing）与临时态（guide 聊天、drawing）不入库。模块加载时恢复 doing /
// result 态；clearSession 时清除。后端会话若已过期，恢复后可继续看题，但提交
// 会报错提示——相比「整段进度蒸发」已是显著改善。

const PERSIST_KEY = 'aicoach.practice.progress'
const PERSIST_FIELDS = [
  'phase',
  'index',
  'questions',
  'pool',
  'selected',
  'feedback',
  'reveal',
  'selfCheck',
  'session_id',
  'deep_link',
  'lastParams',
  'result',
  'drawEmpty',
  'drawError',
] as const

let saveTimer: ReturnType<typeof setTimeout> | null = null

function saveProgress(): void {
  const snap: Record<string, unknown> = {}
  for (const f of PERSIST_FIELDS) {
    snap[f] = practiceState[f]
  }
  try {
    localStorage.setItem(PERSIST_KEY, JSON.stringify(snap))
  } catch {
    /* 隐私模式 / 配额满，忽略 */
  }
}

export function clearProgress(): void {
  try {
    localStorage.removeItem(PERSIST_KEY)
  } catch {
    /* ignore */
  }
}

function loadProgress(): void {
  try {
    const raw = localStorage.getItem(PERSIST_KEY)
    if (!raw) return
    const snap = JSON.parse(raw) as Record<string, unknown>
    const phase = snap.phase
    if (
      (phase === 'doing' || phase === 'result') &&
      Array.isArray(snap.questions) &&
      snap.questions.length > 0
    ) {
      for (const f of PERSIST_FIELDS) {
        if (f in snap) {
          ;(practiceState as Record<string, unknown>)[f] = snap[f]
        }
      }
    }
  } catch {
    /* 损坏数据，忽略 */
  }
}

loadProgress()
watch(
  practiceState,
  () => {
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(saveProgress, 400)
  },
  { deep: true },
)

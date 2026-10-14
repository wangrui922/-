/**
 * T03 · 3.5 学生端 API 封装（AI 辅导对话 / 练习题 / 错题本 / 文件上传）。
 *
 * ⚠️ 后端实际契约（以 backend/app/api/*.py 为准，与需求文档的 camelCase 描述有出入）：
 *   - POST /api/chat 的 data 由 app/ai/orchestrator.start_session 直接透传，
 *     键名是 **snake_case**：{ session_id, analysis, first_question }；
 *     analysis = { knowledge, difficulty, type, question_text }。
 *     本文件在 normalizeStartChat 里统一转成前端用的 camelCase，
 *     并同时兼容后端未来改成 camelCase 的情况。
 *   - POST /api/exercise/gen 的每道题除 q/a/type/knowledge/difficulty 外，
 *     还带 role（fix|transfer）与 tag。
 *   - GET /api/mistake/list 已经是 camelCase（api/mistake.py::_to_item）。
 */
import { get, post, put } from '@/api/http'

// ------------------------------------------------------------------ 类型定义
/** 题目分析结论（对应 Question Agent 输出）。 */
export interface ChatAnalysis {
  /** 知识点，如「一元一次方程」 */
  knowledge: string
  /** 难度：1 易 / 3 中 / 5 难 */
  difficulty: number
  /** 题型：计算 / 应用 / 几何 / 函数 / 证明 */
  type: string
  /** 合并 OCR + 输入后的完整题面 */
  questionText: string
}

/** POST /api/chat 归一化后的结果。 */
export interface StartChatResult {
  sessionId: string
  analysis: ChatAnalysis
  firstQuestion: string
}

/**
 * AI 老师的响应模式（后端 `app/ai/intent.py` 是唯一真相源，这里只做镜像）。
 *
 * - `GUIDE` 苏格拉底引导（默认）
 * - `HINT`  只给一个方向性小线索
 * - `DEMO`  完整讲解并说出结果
 * - `JUDGE` 判对错 + 指出错在哪一步
 */
export type TutorMode = 'GUIDE' | 'HINT' | 'DEMO' | 'JUDGE'

/** 合法模式集合，用于归一化脏输入。 */
const TUTOR_MODES: readonly TutorMode[] = ['GUIDE', 'HINT', 'DEMO', 'JUDGE']

/** 后端不认识 / 没返回 mode 时的兜底（与后端 DEFAULT_MODE 一致）。 */
export const DEFAULT_TUTOR_MODE: TutorMode = 'GUIDE'

/** 把任意脏输入归一化成合法 mode，识别不了一律回落 `GUIDE`。 */
export function normalizeTutorMode(raw: unknown): TutorMode {
  const value = String(raw ?? '').trim().toUpperCase()
  return (TUTOR_MODES as readonly string[]).includes(value)
    ? (value as TutorMode)
    : DEFAULT_TUTOR_MODE
}

/** POST /api/chat/{id}/message 结果。 */
export interface SendMessageResult {
  reply: string
  /** 本轮实际生效的模式（渲染顶部指示器用） */
  mode: TutorMode
  /** 下一轮建议默认模式：讲完一轮（DEMO）后端会自动回切 GUIDE */
  nextMode: TutorMode
}

/** 一道生成的练习题。 */
export interface Exercise {
  /**
   * 落库后的 questions.id（后端 D1 起生成即落库为 archived 归档题）。
   * 落库失败时为 null——此时这道题不能走 /exercise/guide 与 /exercise/judge。
   */
  questionId: string | null
  q: string
  a: string
  type: string
  knowledge: string
  difficulty: number
  /** fix=修复练习，transfer=迁移练习 */
  role?: string
  tag?: string
}

/** POST /api/exercise/guide 结果（练习题的对话式逐步引导）。 */
export interface GuideExerciseResult {
  questionId: string
  reply: string
  /** 模型认为孩子已写完，可以问「这是你的最终答案吗」 */
  askFinalConfirm: boolean
  /** correct=已答对 / wrong=已答错 / pending=还在过程中 */
  verdict: 'correct' | 'wrong' | 'pending'
  /** verdict=wrong 时给五类错因机器码之一，否则为 null */
  errorType: string | null
  mode: TutorMode
  promptVersion: string
}

/** POST /api/exercise/judge 结果（提交最终答案 + 判错即时入本）。 */
export interface JudgeExerciseResult {
  questionId: string
  correct: boolean
  /** 给孩子看的判词（对/错 + 错在哪一步） */
  verdictText: string
  /** 判错时给五类错因机器码之一，判对为 null */
  errorType: string | null
  /** 判错时后端已自动写入错题本，这里回带错题 ID；判对为 null */
  mistakeId: string | null
  /** 是否已刷新掌握度画像缓存（题目没挂知识点时为 false） */
  masteryRefreshed: boolean
}

/** 薄弱错因标签：code 为五类机器码，label 走后端 `error_label`，前端不硬编码中文。 */
export interface WeakTag {
  /** 五类机器码之一：READ/METHOD/CALC/KNOWLEDGE/CHECK */
  code: string
  /** 中文展示标签，由后端唯一真相源提供 */
  label: string
}

/** POST /api/exercise/gen 结果（增量：weakTags / weakSource）。 */
export interface GenExerciseResult {
  exercises: Exercise[]
  /** 这批练习题定向 drill 的薄弱错因标签；为空表示走兜底（新学生 / 无历史错题）。 */
  weakTags: WeakTag[]
  /** "profile"=来自历史画像；"fallback"=画像为空，仅按当前错题练。 */
  weakSource: 'profile' | 'fallback'
}

/** 错题本条目（后端已是 camelCase）。 */
export interface MistakeItem {
  id: string
  questionId: string
  errorType: string | null
  errorReason: string | null
  solved: boolean
  solvedAt: string | null
  createdAt: string | null
  /** 攻克备注（PUT /api/mistake/{id}/solve 写入，T04 · B10） */
  note?: string | null
  /**
   * 关联知识点 ID（建档时从 Question.kp_id 快照，T01 后端 _to_item 已落地）。
   * 为空时前端不渲染知识点条、不显示「练同类题」按钮。
   */
  kpId: string | null
  /** 知识点名称（通过 Mistake.kp relationship 取，kpId 为空时同为 null） */
  kpName: string | null
  /** 题目年级（通过 Mistake.question.grade 取，深链时收窄抽题范围用） */
  grade: number | null
  questionText: string
  originalImageUrl: string | null
  workImageUrls: string[]
}

/** GET /api/mistake/list 结果。 */
export interface MistakeListResult {
  items: MistakeItem[]
  total: number
}

/** 错题本三态。 */
export type MistakeState = 'unsolved' | 'practicing' | 'mastered'

/** POST /api/file/upload 结果。 */
export interface UploadResult {
  key: string
  /** 形如 /api/file/xxx.jpg，可直接给 <img src> */
  url: string
  /** 仅在 ocr=1 时返回，识别失败为空串 */
  ocrText?: string
}

/** POST /api/mistake 结果。 */
export interface CreateMistakeResult {
  id: string
  solved: boolean
}

// ------------------------------------------------------------------ 归一化
/** 后端原始返回（snake_case 与 camelCase 都可能出现）。 */
interface RawStartChat {
  session_id?: string
  sessionId?: string
  first_question?: string
  firstQuestion?: string
  next_question?: string
  analysis?: Record<string, unknown>
}

/** POST /api/chat/{id}/message 的后端原始返回。 */
interface RawSendMessage {
  reply?: string
  mode?: string
  next_mode?: string
  nextMode?: string
}

function normalizeSendMessage(raw: RawSendMessage): SendMessageResult {
  const mode = normalizeTutorMode(raw.mode)
  return {
    reply: String(raw.reply ?? ''),
    mode,
    // 后端没给 next_mode（旧版本）时退化为「维持本轮模式」
    nextMode: normalizeTutorMode(raw.next_mode ?? raw.nextMode ?? mode),
  }
}

/** POST /api/exercise/guide 的后端原始返回（service 直接透传，snake_case）。 */
interface RawGuideExercise {
  question_id?: string
  questionId?: string
  reply?: string
  ask_final_confirm?: boolean
  askFinalConfirm?: boolean
  verdict?: string
  error_type?: string | null
  errorType?: string | null
  mode?: string
  prompt_version?: string
  promptVersion?: string
}

function normalizeGuideExercise(raw: RawGuideExercise): GuideExerciseResult {
  const verdict = String(raw.verdict ?? 'pending')
  return {
    questionId: String(raw.question_id ?? raw.questionId ?? ''),
    reply: String(raw.reply ?? ''),
    askFinalConfirm: Boolean(raw.ask_final_confirm ?? raw.askFinalConfirm ?? false),
    verdict: verdict === 'correct' || verdict === 'wrong' ? verdict : 'pending',
    errorType: (raw.error_type ?? raw.errorType) || null,
    mode: normalizeTutorMode(raw.mode),
    promptVersion: String(raw.prompt_version ?? raw.promptVersion ?? ''),
  }
}

/** POST /api/exercise/judge 的后端原始返回（service 直接透传，snake_case）。 */
interface RawJudgeExercise {
  question_id?: string
  questionId?: string
  correct?: boolean
  verdict_text?: string
  verdictText?: string
  error_type?: string | null
  errorType?: string | null
  mistake_id?: string | null
  mistakeId?: string | null
  mastery_refreshed?: boolean
  masteryRefreshed?: boolean
}

function normalizeJudgeExercise(raw: RawJudgeExercise): JudgeExerciseResult {
  return {
    questionId: String(raw.question_id ?? raw.questionId ?? ''),
    correct: Boolean(raw.correct ?? false),
    verdictText: String(raw.verdict_text ?? raw.verdictText ?? ''),
    errorType: (raw.error_type ?? raw.errorType) || null,
    mistakeId: (raw.mistake_id ?? raw.mistakeId) || null,
    masteryRefreshed: Boolean(raw.mastery_refreshed ?? raw.masteryRefreshed ?? false),
  }
}

/** POST /api/exercise/gen 的后端原始返回（question_id 是 snake_case）。 */
interface RawExercise {
  question_id?: string | null
  questionId?: string | null
  q?: string
  a?: string
  type?: string
  knowledge?: string
  difficulty?: number
  role?: string
  tag?: string
}

function normalizeExercise(raw: RawExercise): Exercise {
  return {
    questionId: (raw.question_id ?? raw.questionId) || null,
    q: String(raw.q ?? ''),
    a: String(raw.a ?? ''),
    type: String(raw.type ?? ''),
    knowledge: String(raw.knowledge ?? ''),
    difficulty: Number(raw.difficulty ?? 3),
    role: raw.role,
    tag: raw.tag,
  }
}

/** POST /api/exercise/gen 的后端原始返回（snake_case / camelCase 双写兼容）。 */
interface RawGenExercise {
  exercises?: RawExercise[]
  /** snake_case（后端当前实际透传键） */
  weak_tags?: Array<{ code?: string; label?: string }>
  /** camelCase（兼容后端未来改名） */
  weakTags?: Array<{ code?: string; label?: string }>
  /** snake_case */
  weak_source?: string
  /** camelCase */
  weakSource?: string
}

function normalizeGenExercise(raw: RawGenExercise): GenExerciseResult {
  const weakTags = (raw.weak_tags ?? raw.weakTags ?? []).map((tag) => ({
    code: String(tag.code ?? ''),
    label: String(tag.label ?? ''),
  }))
  const weakSource = raw.weak_source ?? raw.weakSource ?? 'fallback'
  return {
    exercises: (raw.exercises ?? []).map(normalizeExercise),
    weakTags,
    weakSource: weakSource === 'profile' ? 'profile' : 'fallback',
  }
}

function normalizeStartChat(raw: RawStartChat): StartChatResult {
  const analysis = (raw.analysis ?? {}) as Record<string, unknown>
  return {
    sessionId: String(raw.session_id ?? raw.sessionId ?? ''),
    analysis: {
      knowledge: String(analysis.knowledge ?? '综合'),
      difficulty: Number(analysis.difficulty ?? 3),
      type: String(analysis.type ?? ''),
      questionText: String(analysis.question_text ?? analysis.questionText ?? ''),
    },
    // 后端当前给 first_question；文档里叫 next_question，一并兼容
    firstQuestion: String(raw.first_question ?? raw.firstQuestion ?? raw.next_question ?? ''),
  }
}

// ------------------------------------------------------------------ 接口封装
/**
 * 拍题开场：读题分析 + 首轮苏格拉底引导。
 *
 * @param payload image 为 base64 或 data URL（后端会剥前缀），text 为题目文字；两者不能同时为空。
 */
export async function startChat(payload: {
  image?: string
  imageKey?: string
  text?: string
  clientEventId?: string
}): Promise<StartChatResult> {
  const raw = await post<RawStartChat>('/chat', {
    image: payload.image || null,
    imageKey: payload.imageKey || null,
    text: payload.text || null,
    clientEventId: payload.clientEventId,
  })
  return normalizeStartChat(raw)
}

/**
 * 多轮引导：把孩子这一轮的回答发过去，拿下一句引导。
 *
 * @param sessionId 辅导会话 ID
 * @param text 孩子这一轮说的话
 * @param options `mode` 由快捷按钮显式指定（不传则后端按文本自动识别意图）；
 *   `clientEventId` 为客户端幂等键。
 */
export async function sendMessage(
  sessionId: string,
  text: string,
  options: { mode?: TutorMode; clientEventId?: string } = {},
): Promise<SendMessageResult> {
  const raw = await post<RawSendMessage>(`/chat/${encodeURIComponent(sessionId)}/message`, {
    text,
    mode: options.mode,
    clientEventId: options.clientEventId,
  })
  return normalizeSendMessage(raw)
}

/**
 * 基于会话分析结论生成 2 道练习题（修复题 + 迁移题）。
 *
 * 后端 D1 起**生成即落库**为 `status='archived'` 的归档题（不进题库抽题池），
 * 每道题回带 `questionId`，供 {@link guideExercise} / {@link judgeExercise} 使用。
 */
export async function genExercise(
  sessionId: string,
  clientEventId?: string,
): Promise<GenExerciseResult> {
  const raw = await post<RawGenExercise>('/exercise/gen', {
    sessionId,
    clientEventId,
  })
  return normalizeGenExercise(raw)
}

/**
 * 练习题的对话式逐步引导（可多轮往复，对应 POST /api/exercise/guide）。
 *
 * @param payload `work` 是孩子当前写下的过程；`mode` 由快捷按钮显式指定，
 *   不传则后端按文本自动识别（DEMO/JUDGE 会跳过泄题闸门，孩子请你讲就真的讲）。
 */
export async function guideExercise(payload: {
  sessionId: string
  questionId: string
  work?: string
  mode?: TutorMode
  workImage?: string
  workImageKey?: string
}): Promise<GuideExerciseResult> {
  const raw = await post<RawGuideExercise>('/exercise/guide', {
    sessionId: payload.sessionId,
    questionId: payload.questionId,
    work: payload.work ?? '',
    mode: payload.mode,
    workImage: payload.workImage || null,
    workImageKey: payload.workImageKey || null,
  })
  return normalizeGuideExercise(raw)
}

/**
 * 提交练习题最终答案：判对错，**判错后端会即时写入错题本**
 * （对应 POST /api/exercise/judge，前端无需再调 {@link createMistake}）。
 */
export async function judgeExercise(payload: {
  sessionId: string
  questionId: string
  answer: string
  work?: string
  workImage?: string
  workImageKey?: string
  workImageKeys?: string[]
  finalConfirmed: true
}): Promise<JudgeExerciseResult> {
  const raw = await post<RawJudgeExercise>('/exercise/judge', {
    sessionId: payload.sessionId,
    questionId: payload.questionId,
    answer: payload.answer,
    work: payload.work ?? '',
    workImage: payload.workImage || null,
    workImageKey: payload.workImageKey || null,
    workImageKeys: payload.workImageKeys || [],
    finalConfirmed: payload.finalConfirmed,
  })
  return normalizeJudgeExercise(raw)
}

/** 两道相似题都完成后生成本次辅导小结，后端会缓存结果。 */
export async function summarizeExercise(sessionId: string): Promise<{
  summary: string
  cached: boolean
}> {
  const raw = await post<{ summary?: string; cached?: boolean }>('/exercise/summary', {
    sessionId,
  })
  return {
    summary: String(raw.summary ?? ''),
    cached: Boolean(raw.cached),
  }
}

/** 错题本列表，state 不传返回全部。 */
export function listMistakes(state?: MistakeState): Promise<MistakeListResult> {
  return get<MistakeListResult>('/mistake/list', state ? { state } : undefined)
}

/**
 * 记录一道错题（后端幂等：同题存在未攻克记录时直接复用）。
 *
 * ⚠️ questionId 必须是 questions 表里已存在的 UUID（后端有外键校验）。
 *    /api/exercise/gen 生成的题目现在**已经落库**并回带 `questionId`，可以直接用它建档；
 *    但 AI 练习的常规路径是走 {@link judgeExercise}——判错时后端会自动入本，
 *    不需要前端再手工调本接口，重复调用只会命中幂等返回同一条记录。
 */
export function createMistake(payload: {
  questionId: string
  errorType?: string
  errorReason?: string
}): Promise<CreateMistakeResult> {
  return post<CreateMistakeResult>('/mistake', payload)
}

/**
 * 上传题目照片。
 *
 * @param file 图片文件（png/jpg/jpeg/webp/gif/bmp/heic，≤10MB）
 * @param withOcr 传 true 时后端顺带做一次 OCR，返回 ocrText
 */
export function uploadFile(file: File, withOcr = false): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  return post<UploadResult>(`/file/upload${withOcr ? '?ocr=1' : ''}`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/**
 * 后端是否已提供「标记已掌握」接口（PUT /api/mistake/{id}/solve，T04 · B10 / OQ5）。
 * 单一真源在 @/config/features，这里转发导出以保持旧 import 路径兼容。
 */
export { SUPPORTS_MARK_SOLVED } from '@/config/features'

/**
 * 标记一道错题为已掌握（攻克）。
 *
 * 对应 PUT /api/mistake/{id}/solve；可选补 note（后端仅在该点从 unsolved→mastered
 * 翻转时才落库 note，且幂等保留旧 note，详见 learning_service.mark_mistake_solved）。
 *
 * @param mistakeId 错题 ID
 * @param note 可选攻克备注
 * @returns 更新后的错题条目（camelCase，含 note）
 */
export function markMistakeSolved(
  mistakeId: string,
  note?: string,
): Promise<MistakeItem> {
  return put<MistakeItem>(`/mistake/${encodeURIComponent(mistakeId)}/solve`, note ? { note } : {})
}

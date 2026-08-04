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

/** POST /api/chat/{id}/message 结果。 */
export interface SendMessageResult {
  reply: string
}

/** 一道生成的练习题。 */
export interface Exercise {
  q: string
  a: string
  type: string
  knowledge: string
  difficulty: number
  /** fix=修复练习，transfer=迁移练习 */
  role?: string
  tag?: string
}

/** POST /api/exercise/gen 结果。 */
export interface GenExerciseResult {
  exercises: Exercise[]
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
  text?: string
  clientEventId?: string
}): Promise<StartChatResult> {
  const raw = await post<RawStartChat>('/chat', {
    image: payload.image || null,
    text: payload.text || null,
    clientEventId: payload.clientEventId,
  })
  return normalizeStartChat(raw)
}

/** 多轮引导：把孩子这一轮的回答发过去，拿下一句引导。 */
export function sendMessage(
  sessionId: string,
  text: string,
  clientEventId?: string,
): Promise<SendMessageResult> {
  return post<SendMessageResult>(`/chat/${encodeURIComponent(sessionId)}/message`, {
    text,
    clientEventId,
  })
}

/** 基于会话分析结论生成 2 道练习题（修复题 + 迁移题，后端不落库）。 */
export function genExercise(sessionId: string, clientEventId?: string): Promise<GenExerciseResult> {
  return post<GenExerciseResult>('/exercise/gen', { sessionId, clientEventId })
}

/** 错题本列表，state 不传返回全部。 */
export function listMistakes(state?: MistakeState): Promise<MistakeListResult> {
  return get<MistakeListResult>('/mistake/list', state ? { state } : undefined)
}

/**
 * 记录一道错题（后端幂等：同题存在未攻克记录时直接复用）。
 *
 * ⚠️ questionId 必须是 questions 表里已存在的 UUID（后端有外键校验）。
 *    /api/exercise/gen 生成的题目**不落库**，所以暂时无法直接用它建档。
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

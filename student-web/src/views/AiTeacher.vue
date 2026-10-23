<script setup lang="ts">
/**
 * AI 老师页（T03 · 3.5 核心页；T8 升级为「更像真老师」）。
 *
 * 链路：拍照/输入题目 → POST /api/chat（读题分析 + 首轮引导）
 *      → 学生回答 → POST /api/chat/{id}/message（多轮引导，回带 mode/nextMode）
 *      → POST /api/exercise/gen（修复题 + 迁移题，**生成即落库**回带 questionId）
 *      → POST /api/exercise/guide（练习题逐步引导）
 *      → POST /api/exercise/judge（提交最终答案，判错后端自动入错题本）
 *
 * T8 三件事：
 *   1. 顶部**常驻模式指示器**（🎓 引导 / 💡 提示 / 📖 讲解 / ✓ 判题），
 *      数据来自后端 `mode`——孩子随时知道「老师现在在干嘛」；
 *   2. 输入框上方一行**快捷按钮**，点击 = 填话术 + 显式带 `mode` 发送，
 *      这是「前端显式指定意图」通道，优先级高于后端自动识别，
 *      保证点了「不会做，给我讲」就一定进讲解模式；
 *   3. 「再出两道」不再是**丢两道带答案的题给孩子自己看**，而是直接打开
 *      `GuideChatPanel` 进对话式作答；`QuestionCard` 全程 `answerLocked`，
 *      走完对话拿到判词才解锁答案。
 *
 * 图片处理说明：后端 POST /api/chat 的 image 字段收的是 base64 / data URL
 * （见 api/chat.py::_strip_data_url），**不接受 URL**。所以这里：
 *   1. 本地把图片读成 data URL 用于预览 + 发给 /api/chat；
 *   2. 同时调 POST /api/file/upload 把原图存一份（拿到 key/url 便于后续归档），
 *      上传失败不阻塞对话流程。
 */
import { computed, nextTick, ref, watch } from 'vue'

import {
  DEFAULT_TUTOR_MODE,
  genExercise,
  guideExercise,
  judgeExercise,
  sendMessage,
  startChat,
  summarizeExercise,
  uploadFile,
  type Exercise,
  type TutorMode,
  type WeakTag,
} from '@/api/chat'
import { toMessage } from '@/api/http'
import {
  autoRead,
  setAutoRead,
  speak,
  sttSupported,
  stopSpeaking,
  ttsSupported,
  useSpeechRecognition,
} from '@/composables/useVoice'
import ChatBubble from '@/components/ChatBubble.vue'
import GuideChatPanel from '@/components/guide/GuideChatPanel.vue'
import QuestionCard from '@/components/QuestionCard.vue'
import ImageQuestionCropper from '@/components/practice/ImageQuestionCropper.vue'
import tutorAvatar from '@/assets/tutor-yueyue-v2.png'
import { getStudentId } from '@/stores/auth'
import type { GuideMessage, QuickAction } from '@/types/guide'

interface ChatItem {
  id: number
  role: 'user' | 'ai'
  content: string
  /** 学生拍照的题目照片（base64 dataUrl），用于在气泡里回显 */
  image?: string
}

/** 一道练习题的作答进度（与 `exercises` 同下标一一对应）。 */
interface ExerciseThread {
  /** 与老师的往复对话 */
  messages: GuideMessage[]
  /** 老师在问「这是你的最终答案吗」 */
  askFinalConfirm: boolean
  /** 当前评判：correct 对 / wrong 错 / pending 还在过程中 */
  verdict: 'correct' | 'wrong' | 'pending'
  /** 作答中 → 评判中 → 已完成 */
  status: 'answering' | 'judging' | 'done'
  /** 判错时的五类错因机器码 */
  errorType: string | null
  /** 判错时后端自动建的错题本 ID */
  mistakeId: string | null
  /** 孩子最近一次写下的过程，判题时兜底当答案用 */
  lastWork: string
  /** 最近一张手写过程图及本题全部已上传图片 key。 */
  lastWorkImage: string
  workImageKeys: string[]
}

/** 模式指示器的展示文案（后端 `app/ai/intent.py` 是 mode 的唯一真源，这里只做映射）。 */
const MODE_META: Record<TutorMode, { icon: string; label: string; hint: string }> = {
  GUIDE: { icon: '🎓', label: '引导中', hint: '老师在一步步问你，自己想明白最牢' },
  HINT: { icon: '💡', label: '提示中', hint: '只给一个小线索，剩下的你来' },
  DEMO: { icon: '📖', label: '讲解中', hint: '老师完整讲一遍，听完再自己做一次' },
  JUDGE: { icon: '✓', label: '判题中', hint: '老师在看你哪一步出了问题' },
}

/** 每一步评判的三态图标（PRD P1-2），数据来自 `verdict`。 */
const VERDICT_META: Record<ExerciseThread['verdict'], { icon: string; text: string; cls: string }> = {
  pending: { icon: '⚠', text: '还在过程中', cls: 'text-amber-600' },
  correct: { icon: '✓', text: '这一步对了', cls: 'text-emerald-600' },
  wrong: { icon: '✗', text: '这一步有问题', cls: 'text-red-500' },
}

/** 题目状态指示器文案（PRD P1-4）。 */
const STATUS_LABEL: Record<ExerciseThread['status'], string> = {
  answering: '作答中',
  judging: '评判中',
  done: '已完成',
}

/** 低置信度重拍引导话术兜底（后端 message 缺失时用；与后端 orchestrator.REPHOTO_MESSAGE 一致）。 */
const DEFAULT_REPHOTO_MESSAGE = '这道题有些地方我没看清，重新拍一张给我看看好吗？'

/** 主对话区的快捷按钮：一句话说清「我现在要什么」，省得孩子组织语言。 */
const CHAT_QUICK_ACTIONS: readonly QuickAction[] = [
  { label: '不会做，给我讲', text: '我不会做，你给我讲一遍吧', mode: 'DEMO' },
  { label: '给点提示', text: '给点提示吧，我想再自己试试', mode: 'HINT' },
  { label: '看演示', text: '你先完整演示一遍给我看看', mode: 'DEMO' },
]

/** “我做完了”只请老师检查完整性；老师确认后才会展示最终答案确认。 */
const EXERCISE_QUICK_ACTIONS: readonly QuickAction[] = [
  { label: '不会做，给我讲', text: '我不会做，你给我讲一遍吧', mode: 'DEMO' },
  { label: '给点提示', text: '给点提示吧，我想再自己试试', mode: 'HINT' },
  { label: '我做完了', text: '我做完了，请先看看我的过程是否完整', mode: 'GUIDE' },
]

/** 「是的/对/没错…」这类确认词——老师问最终答案时命中即进判题。 */
const FINAL_CONFIRM_RE = /^(是的|是|对的|对|没错|确定|就这样|就是这个)/
/** 确认词后面残留的标点，剥掉才能看出后面还有没有真答案。 */
const CONFIRM_TAIL_RE = /^[，,。.、；;：:!！\s]+/

const messages = ref<ChatItem[]>([])
const sessionId = ref<string>('')
const analysisText = ref<string>('')
const inputText = ref<string>('')
const imageDataUrl = ref<string>('')
const imageName = ref<string>('')
/** 选题意中间态：拍照后先框出单题，不直接把整图发给 AI。 */
const cropping = ref<boolean>(false)
const pendingImage = ref<string>('')
const pendingFile = ref<File | null>(null)
/** 拍照上传后后端顺带 OCR 出的题目文字（withOcr=true），展示给孩子核对/纠错。 */
const ocrText = ref<string>('')
const sourceImageKey = ref<string>('')
const exercises = ref<Exercise[]>([])
const threads = ref<ExerciseThread[]>([])

/** 这批练习题定向 drill 的薄弱错因标签（来自后端 get_weak_profile / generate_exercise）。 */
const weakTags = ref<WeakTag[]>([])
/** "profile"=来自历史画像；"fallback"=画像为空，仅按当前错题练。 */
const weakSource = ref<'profile' | 'fallback'>('fallback')

/** 本轮实际生效的模式（顶部指示器用）。 */
const currentMode = ref<TutorMode>(DEFAULT_TUTOR_MODE)
/** 后端建议的下一轮默认模式：讲完一轮（DEMO）会自动回切 GUIDE。 */
const nextMode = ref<TutorMode>(DEFAULT_TUTOR_MODE)

/** 当前打开的练习题下标，-1 表示面板关着。 */
const activeExerciseIdx = ref<number>(-1)
const guideLoading = ref<boolean>(false)
const guideError = ref<string>('')
const summaryText = ref<string>('')
const summaryLoading = ref<boolean>(false)
const summaryError = ref<string>('')
const AI_TEACHER_PROGRESS_KEY = 'aicoach.ai-teacher.progress'
const AI_TEACHER_PROGRESS_MAX_AGE_MS = 2 * 60 * 60 * 1000

const sending = ref<boolean>(false)
const uploading = ref<boolean>(false)
const generating = ref<boolean>(false)
const errorMsg = ref<string>('')
/**
 * 低置信度重拍引导：后端自评「没看清」时不建会话、不进引导态，
 * 改为展示重拍卡片（引导话术 + 「重新拍照」按钮），AI 不硬猜。
 */
const rephoto = ref<{ message: string; confidence: number | null } | null>(null)

/** 做对一道题时弹出的「攻克庆祝」横幅（纯视觉，2.6s 自动消失）。 */
const celebrate = ref<boolean>(false)
/** 快捷按钮的卡通图标（按 label 映射，不改 QuickAction 类型）。 */
const QUICK_ICON: Record<string, string> = {
  '不会做，给我讲': '📖',
  '给点提示': '💡',
  '看演示': '🎬',
  '我做完了': '✓',
}

const fileInput = ref<HTMLInputElement | null>(null)
const scrollArea = ref<HTMLElement | null>(null)

// ===== 语音能力（TTS 朗读 + STT 语音输入，给不会看题/不会打字的小朋友）=====
/** 麦克风：说完自动作为回答发出（已开场时）。 */
const mic = useSpeechRecognition({
  onInterim: (t: string) => {
    inputText.value = t
  },
  onFinal: (t: string) => {
    inputText.value = t
    if (started.value) void handleSend()
  },
  onError: (m: string) => {
    errorMsg.value = `语音输入：${m}（也可以直接打字）`
  },
})
/** 模板里直接读 mic.recognizing 需 .value，这里取个别名方便用。 */
const micRecognizing = mic.recognizing

function toggleMic(): void {
  if (mic.recognizing.value) {
    mic.stop()
    return
  }
  inputText.value = ''
  mic.start()
}

/** 自动朗读：开的时候老师的话会自动念出来。 */
function maybeSpeak(key: string, text: string): void {
  if (autoRead.value) speak(text, key)
}

function toggleAutoRead(): void {
  setAutoRead(!autoRead.value)
}

/** 单独把 OCR 识别到的题目念一遍（读题）。 */
function readQuestionAloud(): void {
  if (ocrText.value) speak(`题目是，${ocrText.value}`, 'ocr')
}

let seed = 0

/** 已开场（拿到 sessionId）后才允许多轮对话与出练习题。 */
const started = computed<boolean>(() => Boolean(sessionId.value))
/** 至少一轮学生回答后再露出「生成练习」，避免刚开场就出题。 */
const canGenerate = computed<boolean>(
  () => started.value && messages.value.some((item) => item.role === 'user') && !generating.value,
)
const canSend = computed<boolean>(
  () => !sending.value && (Boolean(inputText.value.trim()) || Boolean(imageDataUrl.value)),
)
const busyHint = computed<string>(() => (started.value ? 'AI 老师正在想…' : '正在读题…'))

const modeMeta = computed(() => MODE_META[currentMode.value])
const nextModeMeta = computed(() => MODE_META[nextMode.value])
/** 只有「这轮讲完下轮回引导」这种切换才值得提示，同模式不啰嗦。 */
const showNextModeHint = computed<boolean>(() => started.value && nextMode.value !== currentMode.value)

const panelOpen = computed<boolean>(() => activeExerciseIdx.value >= 0)
const activeExercise = computed<Exercise | null>(() => exercises.value[activeExerciseIdx.value] ?? null)
const activeThread = computed<ExerciseThread | null>(() => threads.value[activeExerciseIdx.value] ?? null)
const activeMessages = computed<GuideMessage[]>(() => activeThread.value?.messages ?? [])
const activeVerdictMeta = computed(() => VERDICT_META[activeThread.value?.verdict ?? 'pending'])
const activeStatusLabel = computed<string>(() => STATUS_LABEL[activeThread.value?.status ?? 'answering'])
const hasNextExercise = computed<boolean>(
  () => activeExerciseIdx.value >= 0 && activeExerciseIdx.value + 1 < exercises.value.length,
)

function push(role: ChatItem['role'], content: string, image?: string): number {
  const id = ++seed
  messages.value.push({ id, role, content, image })
  void nextTick(() => {
    const el = scrollArea.value
    if (el) el.scrollTop = el.scrollHeight
  })
  return id
}

/** 新建一道题的作答进度。 */
function createThread(): ExerciseThread {
  return {
    messages: [],
    askFinalConfirm: false,
    verdict: 'pending',
    status: 'answering',
    errorType: null,
    mistakeId: null,
    lastWork: '',
    lastWorkImage: '',
    workImageKeys: [],
  }
}

function saveAiTeacherProgress(): void {
  if (!sessionId.value) return
  const snapshot = {
    studentId: getStudentId(),
    savedAt: Date.now(),
    sessionId: sessionId.value,
    analysisText: analysisText.value,
    ocrText: ocrText.value,
    sourceImageKey: sourceImageKey.value,
    messages: messages.value.map(({ image: _image, ...item }) => item),
    exercises: exercises.value,
    threads: threads.value.map((thread) => ({
      ...thread,
      messages: thread.messages.map(({ image: _image, ...item }) => item),
      lastWorkImage: '',
    })),
    weakTags: weakTags.value,
    weakSource: weakSource.value,
    currentMode: currentMode.value,
    nextMode: nextMode.value,
    summaryText: summaryText.value,
  }
  try {
    localStorage.setItem(AI_TEACHER_PROGRESS_KEY, JSON.stringify(snapshot))
  } catch {
    /* 隐私模式或配额不足时仍可正常使用，服务端记录不受影响 */
  }
}

function restoreAiTeacherProgress(): void {
  try {
    const raw = localStorage.getItem(AI_TEACHER_PROGRESS_KEY)
    if (!raw) return
    const snapshot = JSON.parse(raw) as Record<string, unknown>
    const savedAt = Number(snapshot.savedAt || 0)
    if (
      snapshot.studentId !== getStudentId() ||
      !savedAt ||
      Date.now() - savedAt > AI_TEACHER_PROGRESS_MAX_AGE_MS
    ) {
      localStorage.removeItem(AI_TEACHER_PROGRESS_KEY)
      return
    }
    if (typeof snapshot.sessionId !== 'string' || !snapshot.sessionId) return
    sessionId.value = snapshot.sessionId
    analysisText.value = String(snapshot.analysisText ?? '')
    ocrText.value = String(snapshot.ocrText ?? '')
    sourceImageKey.value = String(snapshot.sourceImageKey ?? '')
    messages.value = Array.isArray(snapshot.messages) ? (snapshot.messages as ChatItem[]) : []
    seed = messages.value.reduce((maxId, item) => Math.max(maxId, Number(item.id) || 0), 0)
    exercises.value = Array.isArray(snapshot.exercises) ? (snapshot.exercises as Exercise[]) : []
    threads.value = Array.isArray(snapshot.threads)
      ? (snapshot.threads as ExerciseThread[]).map((thread) => ({
          ...createThread(),
          ...thread,
          messages: Array.isArray(thread.messages) ? thread.messages : [],
          workImageKeys: Array.isArray(thread.workImageKeys) ? thread.workImageKeys : [],
        }))
      : []
    weakTags.value = Array.isArray(snapshot.weakTags) ? (snapshot.weakTags as WeakTag[]) : []
    weakSource.value = snapshot.weakSource === 'profile' ? 'profile' : 'fallback'
    currentMode.value = (snapshot.currentMode as TutorMode) || DEFAULT_TUTOR_MODE
    nextMode.value = (snapshot.nextMode as TutorMode) || DEFAULT_TUTOR_MODE
    summaryText.value = String(snapshot.summaryText ?? '')
  } catch {
    localStorage.removeItem(AI_TEACHER_PROGRESS_KEY)
  }
}

/** 这道题的答案是否还锁着：没走完对话（status !== done）一律锁死。 */
function isAnswerLocked(idx: number): boolean {
  return threads.value[idx]?.status !== 'done'
}

/** 练习题卡片下方按钮的文案，顺带把进度透出来。 */
function exerciseActionLabel(idx: number): string {
  const status = threads.value[idx]?.status ?? 'answering'
  if (status === 'done') return '回看这道题的讲解'
  if (threads.value[idx]?.messages.length) return '继续跟老师作答'
  return '跟老师一起做这道题'
}

/**
 * 选图：本地转 base64，但**不立即发图**——先进入「选题意」中间态，
 * 让用户框出要讲的那一道（作业帮式），避免把整页多题混着发给 AI。
 */
async function handleFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 允许连续选同一张
  if (!file) return

  errorMsg.value = ''
  imageName.value = file.name

  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('图片读取失败'))
    reader.readAsDataURL(file)
  }).catch((error: unknown) => {
    errorMsg.value = toMessage(error, '图片读取失败')
    return ''
  })

  if (!dataUrl) return
  // 进入选题意：先框选单题，不直接发整图
  pendingFile.value = file
  pendingImage.value = dataUrl
  cropping.value = true
}

/** dataURL → File（裁切后的子图需要这个形态才能走 uploadFile 存档 + OCR）。 */
function dataUrlToFile(dataUrl: string, name: string): File {
  const [head, b64] = dataUrl.split(',')
  const mime = /:(.*?);/.exec(head)?.[1] || 'image/jpeg'
  const bin = atob(b64)
  const arr = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i)
  return new File([arr], name, { type: mime })
}

/** 存档上传并顺带 OCR（withOcr=true），识别结果展示给孩子核对。 */
async function uploadOcr(file: File): Promise<void> {
  uploading.value = true
  try {
    const up = await uploadFile(file, true)
    sourceImageKey.value = up.key
    ocrText.value = up.ocrText ?? ''
  } catch (error) {
    errorMsg.value = `${toMessage(error, '照片上传失败')}（不影响继续提问）`
  } finally {
    uploading.value = false
  }
}

/** 框选确认：裁切子图作为题目图，并上传 OCR。 */
async function onCropConfirm(dataUrl: string): Promise<void> {
  imageDataUrl.value = dataUrl
  cropping.value = false
  await uploadOcr(dataUrlToFile(dataUrl, imageName.value || 'crop.jpg'))
}

/** 用整张图：不框选，直接发原图（兜底）。 */
async function onCropWhole(): Promise<void> {
  if (!pendingImage.value) return
  imageDataUrl.value = pendingImage.value
  cropping.value = false
  if (pendingFile.value) await uploadOcr(pendingFile.value)
}

/** 放弃本次选图。 */
function onCropCancel(): void {
  cropping.value = false
  pendingImage.value = ''
  pendingFile.value = null
  clearImage()
}

function clearImage(): void {
  imageDataUrl.value = ''
  imageName.value = ''
  sourceImageKey.value = ''
}

/**
 * 发送：首轮走 startChat，后续走 sendMessage。
 *
 * @param mode 快捷按钮显式指定的模式；自然打字不传，交给后端自动识别意图。
 */
async function handleSend(mode?: TutorMode): Promise<void> {
  if (!canSend.value) return

  const text = inputText.value.trim()
  const image = imageDataUrl.value
  sending.value = true
  errorMsg.value = ''
  rephoto.value = null

  try {
    if (!started.value) {
      push('user', text || '（拍了一张题目照片）', image || undefined)
      inputText.value = ''

      const result = await startChat({
        image: image || undefined,
        imageKey: sourceImageKey.value || undefined,
        text: text || undefined,
      })
      // 低置信度重拍：没看清就不硬答——不建会话、不进引导态，展示重拍引导卡片
      if (result.needRePhoto) {
        rephoto.value = {
          message: result.rephotoMessage || DEFAULT_REPHOTO_MESSAGE,
          confidence: result.confidence ?? null,
        }
        // 清掉这次没拍清的照片与 OCR，避免原样重发同一张
        clearImage()
        ocrText.value = ''
        if (autoRead.value) speak(rephoto.value.message, 'rephoto')
        return
      }
      sessionId.value = result.sessionId
      analysisText.value = `这道题考：${result.analysis.knowledge} · ${result.analysis.type} · 难度 ${result.analysis.difficulty}`
      push('ai', analysisText.value)
      const qId = push('ai', result.firstQuestion)
      // 「读题 + 讲题」自动朗读：先念 OCR 到的题目，再念老师的开场提问
      if (autoRead.value) {
        const intro = [ocrText.value ? `题目是，${ocrText.value}` : '', result.firstQuestion]
          .filter(Boolean)
          .join('。')
        speak(intro, `m-${qId}`)
      }
      // 开场一定是苏格拉底引导
      currentMode.value = DEFAULT_TUTOR_MODE
      nextMode.value = DEFAULT_TUTOR_MODE
      clearImage()
    } else {
      if (!text) return
      push('user', text)
      inputText.value = ''

      const result = await sendMessage(sessionId.value, text, { mode })
      const id = push('ai', result.reply)
      maybeSpeak(`m-${id}`, result.reply)
      // mode 只认后端定稿的那一份，前端不自己推断
      currentMode.value = result.mode
      nextMode.value = result.nextMode
    }
  } catch (error) {
    errorMsg.value = toMessage(error, '发送失败，请重试')
  } finally {
    sending.value = false
  }
}

/** 主对话快捷按钮：填入话术 + 显式带 mode 发送。 */
async function handleChatQuickAction(action: QuickAction): Promise<void> {
  if (!started.value || sending.value) return
  inputText.value = action.text
  await handleSend(action.mode)
}

/** 生成 2 道练习题（修复题 + 迁移题），拿到题直接进对话式作答。 */
async function handleGenExercise(): Promise<void> {
  if (!canGenerate.value) return
  generating.value = true
  errorMsg.value = ''
  guideError.value = ''
  try {
    const result = await genExercise(sessionId.value)
    exercises.value = result.exercises || []
    // T5 薄弱环节横幅：从结果取出薄弱点标签与来源，模板渲染整批横幅
    weakTags.value = result.weakTags || []
    weakSource.value = result.weakSource || 'fallback'
    threads.value = exercises.value.map(() => createThread())
    // T8：不再把带答案的题丢给孩子自己看，直接打开第一题的对话面板
    // 走 openExercise 而不是直接赋值，落库失败时才能第一时间把提示打出来
    if (exercises.value.length) {
      openExercise(0)
    } else {
      activeExerciseIdx.value = -1
    }
    void nextTick(() => {
      const el = scrollArea.value
      if (el) el.scrollTop = el.scrollHeight
    })
  } catch (error) {
    errorMsg.value = toMessage(error, '生成练习失败，请重试')
  } finally {
    generating.value = false
  }
}

/** 打开某道练习题的对话面板。 */
function openExercise(idx: number): void {
  if (idx < 0 || idx >= exercises.value.length) return
  guideError.value = ''
  activeExerciseIdx.value = idx
  if (!exercises.value[idx]?.questionId) {
    guideError.value = '这道题没有存档成功，暂时不能对话，可以点「再出两道」重新出题。'
  }
}

function closeExercise(): void {
  activeExerciseIdx.value = -1
}

/**
 * 一轮引导：把孩子写下的过程发给老师，拿下一句引导。
 *
 * @param idx 练习题下标
 * @param work 孩子这一轮写下的过程（可为空——空着就是纯请老师起个头）
 * @param mode 快捷按钮显式指定的模式，不传则由后端识别
 */
async function runGuide(
  idx: number,
  work: string,
  mode?: TutorMode,
  workImage?: string,
  workImageKey?: string,
): Promise<void> {
  const exercise = exercises.value[idx]
  const thread = threads.value[idx]
  if (!exercise || !thread || guideLoading.value) return

  const questionId = exercise.questionId
  if (!questionId) {
    guideError.value = '这道题没有存档成功，暂时不能对话，可以点「再出两道」重新出题。'
    return
  }

  const text = work.trim()
  if (text) {
    thread.messages.push({ role: 'student', text })
    thread.lastWork = text
  }
  if (workImage) {
    thread.messages.push({ role: 'student', text: '这是我的手写过程', image: workImage })
    thread.lastWorkImage = workImage
  }
  if (workImageKey && !thread.workImageKeys.includes(workImageKey)) {
    thread.workImageKeys.push(workImageKey)
  }

  guideLoading.value = true
  guideError.value = ''
  try {
    const result = await guideExercise({
      sessionId: sessionId.value,
      questionId,
      work: text,
      mode,
      workImage,
      workImageKey,
    })
    const tIdx = thread.messages.length
    thread.messages.push({ role: 'teacher', text: result.reply })
    thread.askFinalConfirm = result.askFinalConfirm
    thread.verdict = result.verdict
    maybeSpeak(`guide-${tIdx}`, result.reply)
    if (result.errorType) thread.errorType = result.errorType
    // 练习题面板里的模式同样反映到顶部常驻指示器
    currentMode.value = result.mode
    nextMode.value = result.mode === 'DEMO' ? DEFAULT_TUTOR_MODE : result.mode
  } catch (error) {
    guideError.value = toMessage(error, '老师没接住，再说一次试试')
  } finally {
    guideLoading.value = false
  }
}

/**
 * 提交最终答案：判对错，**判错后端会即时写进错题本**，前端不用再建档。
 *
 * @param idx 练习题下标
 * @param rawAnswer 这一轮明确给出的答案；为空则回落到上一轮写下的过程
 */
async function runJudge(idx: number, rawAnswer: string): Promise<void> {
  const exercise = exercises.value[idx]
  const thread = threads.value[idx]
  if (!exercise || !thread || guideLoading.value) return

  const questionId = exercise.questionId
  if (!questionId) {
    guideError.value = '这道题没有存档成功，暂时不能判题，可以点「再出两道」重新出题。'
    return
  }

  const answer = rawAnswer.trim() || thread.lastWork
  if (!answer) {
    guideError.value = '先把你的答案写出来，再让老师判。'
    return
  }

  const previousStatus = thread.status
  thread.status = 'judging'
  guideLoading.value = true
  guideError.value = ''
  try {
    const result = await judgeExercise({
      sessionId: sessionId.value,
      questionId,
      answer,
      work: thread.lastWork || answer,
      workImage: thread.lastWorkImage || undefined,
      workImageKeys: thread.workImageKeys,
      finalConfirmed: true,
    })
    thread.verdict = result.correct ? 'correct' : 'wrong'
    thread.errorType = result.errorType
    thread.mistakeId = result.mistakeId
    thread.askFinalConfirm = false
    thread.status = 'done'
    currentMode.value = 'JUDGE'
    nextMode.value = DEFAULT_TUTOR_MODE
    const vIdx = thread.messages.length
    thread.messages.push({
      role: 'teacher',
      text: result.verdictText || (result.correct ? '这道题你做对了 👍' : '这一步还不对，我们再看看。'),
    })
    maybeSpeak(
      `guide-${vIdx}`,
      result.verdictText || (result.correct ? '这道题你做对了' : '这一步还不对，我们再看看。'),
    )
    if (!result.correct && result.mistakeId) {
      const mIdx = thread.messages.length
      thread.messages.push({ role: 'teacher', text: '这道题我已经记进错题本了，过几天再来练一次。' })
      maybeSpeak(`guide-${mIdx}`, '这道题我已经记进错题本了，过几天再来练一次。')
    }
    if (threads.value.length === 2 && threads.value.every((item) => item.status === 'done')) {
      await generateLearningSummary()
    }
  } catch (error) {
    // 判题失败要把状态放回去，否则状态条会一直卡在「评判中」
    thread.status = previousStatus
    guideError.value = toMessage(error, '判题失败，再试一次')
  } finally {
    guideLoading.value = false
  }
}

async function generateLearningSummary(): Promise<void> {
  if (!sessionId.value || summaryLoading.value || summaryText.value) return
  summaryLoading.value = true
  summaryError.value = ''
  try {
    const result = await summarizeExercise(sessionId.value)
    summaryText.value = result.summary
  } catch (error) {
    summaryError.value = toMessage(error, '小结暂时没写好，稍后再试一次')
  } finally {
    summaryLoading.value = false
  }
}

/** 剥掉「是的，」这类确认前缀，看看后面还带没带真答案。 */
function stripConfirm(text: string): string {
  return text.replace(FINAL_CONFIRM_RE, '').replace(CONFIRM_TAIL_RE, '').trim()
}

/** 面板的普通发送：老师在问最终答案且孩子点了确认，就直接进判题。 */
function handleGuideSend(work: string): void {
  const idx = activeExerciseIdx.value
  const thread = threads.value[idx]
  // guideLoading 在途时直接拒：否则第二次点击会先 push 一个重复学生气泡，
  // 才轮到 runJudge 的 loading 守卫拒绝（tests/teacher-guidance.test.mjs 锁此行为）。
  if (!thread || guideLoading.value) return

  const text = work.trim()
  if (thread.askFinalConfirm && text && FINAL_CONFIRM_RE.test(text)) {
    thread.messages.push({ role: 'student', text })
    const rest = stripConfirm(text)
    if (rest) thread.lastWork = rest
    void runJudge(idx, rest)
    return
  }
  void runGuide(idx, text)
}

async function handleGuideImage(file: File, dataUrl: string): Promise<void> {
  const idx = activeExerciseIdx.value
  if (idx < 0 || guideLoading.value) return
  guideLoading.value = true
  guideError.value = ''
  summaryText.value = ''
  summaryError.value = ''
  try {
    const uploaded = await uploadFile(file, false)
    guideLoading.value = false
    await runGuide(idx, '', undefined, dataUrl, uploaded.key)
  } catch (error) {
    guideError.value = toMessage(error, '手写过程上传失败，再拍一次试试')
    guideLoading.value = false
  }
}

/**
 * 面板的快捷按钮：JUDGE 走判题接口，其余走引导接口。
 *
 * @param action 被点的按钮（自带显式 mode）
 * @param draft 点击瞬间输入框里的草稿，不能丢——孩子常常是写完才点「帮我判」
 */
function handleGuideQuick(action: QuickAction, draft: string): void {
  const idx = activeExerciseIdx.value
  const thread = threads.value[idx]
  // 同 handleGuideSend：loading 在途时拒掉重复点击，避免气泡/请求重复
  if (!thread || guideLoading.value) return

  const typed = draft.trim()
  // 孩子写了一半再点「给点提示」：把他写的和诉求一起发过去，老师才接得住
  void runGuide(idx, typed ? `${typed}\n${action.text}` : action.text, action.mode)
}

/** 换一题：清空当前会话，重新开场。 */
function handleReset(): void {
  stopSpeaking()
  sessionId.value = ''
  messages.value = []
  exercises.value = []
  threads.value = []
  activeExerciseIdx.value = -1
  analysisText.value = ''
  inputText.value = ''
  errorMsg.value = ''
  rephoto.value = null
  guideError.value = ''
  currentMode.value = DEFAULT_TUTOR_MODE
  nextMode.value = DEFAULT_TUTOR_MODE
  clearImage()
  localStorage.removeItem(AI_TEACHER_PROGRESS_KEY)
}

watch(
  [sessionId, analysisText, ocrText, sourceImageKey, messages, exercises, threads, weakTags, weakSource, currentMode, nextMode, summaryText],
  saveAiTeacherProgress,
  { deep: true },
)

restoreAiTeacherProgress()
</script>

<template>
  <div class="relative flex h-full w-full flex-col overflow-x-hidden bg-transparent">
    <!-- 陪伴条：小玥老师头像 + 实时状态，像真老师在身边 -->
    <header class="shrink-0 border-b border-white/60 bg-white/70 px-4 py-2.5 backdrop-blur-md">
      <div class="flex items-center gap-3">
        <div class="relative h-11 w-11 shrink-0 animate-float">
          <img :src="tutorAvatar" alt="小玥老师" class="h-full w-full rounded-full border-2 border-white object-cover shadow-float" />
          <span class="absolute -bottom-0.5 -right-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-green-400 text-[11px] shadow ring-2 ring-white">💬</span>
        </div>
        <div class="min-w-0 flex-1">
          <p class="text-sm font-bold text-slate-800">小玥老师 · {{ modeMeta.icon }} {{ modeMeta.label }}</p>
          <p class="truncate text-xs text-slate-400">{{ modeMeta.hint }}</p>
        </div>
        <!-- 自动朗读开关：低年级不会看题，默认开，老师的话自动念 -->
        <button
          type="button"
          class="shrink-0 rounded-full px-2 py-1 text-xs font-medium transition active:bg-slate-100"
          :class="autoRead ? 'bg-brand-50 text-brand-600' : 'text-slate-400'"
          :title="autoRead ? '自动朗读：开（老师的话会自动念出来）' : '自动朗读：关'"
          @click="toggleAutoRead"
        >
          {{ autoRead ? '🔊 朗读' : '🔇 静音' }}
        </button>
        <span v-if="showNextModeHint" class="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-400">
          下一轮 {{ nextModeMeta.icon }} {{ nextModeMeta.label }}
        </span>
      </div>
    </header>

    <!-- 对话区 -->
    <main ref="scrollArea" class="flex-1 space-y-3 overflow-y-auto overflow-x-hidden px-4 py-4">
      <!-- 陪伴欢迎（空状态） -->
      <div v-if="!messages.length" class="card flex flex-col items-center gap-2 px-4 py-5 text-center">
        <img :src="tutorAvatar" alt="小玥老师" class="h-20 w-20 animate-float rounded-full border-2 border-white object-cover shadow-float" />
        <p class="text-base font-bold text-slate-800">嗨～我是小玥老师 👋</p>
        <p class="text-sm leading-relaxed text-slate-500">
          不会的题发给我，我陪你一步步想明白。<br />
          点「拍照」上传题目，或直接打字都行。
        </p>
        <p class="text-xs text-slate-400">实在卡住了，点「不会做，给我讲」，我完整讲一遍。</p>
      </div>

      <!-- 攻克庆祝横幅（做对时浮现，2.6s 自动消失） -->
      <Transition name="cheer">
        <div
          v-if="celebrate"
          class="flex items-center gap-2 rounded-2xl bg-gradient-to-r from-amber-400 to-coral-400 px-4 py-3 text-white shadow-float"
        >
          <span class="text-xl">🎉</span>
          <div class="flex-1">
            <p class="text-sm font-bold">这道题攻克啦！</p>
            <p class="text-xs opacity-90">宝藏书库 +1 颗宝石 ✨ 明天再来练一次更牢</p>
          </div>
        </div>
      </Transition>

      <TransitionGroup name="bubble" tag="div" class="space-y-3">
        <ChatBubble
          v-for="item in messages"
          :key="item.id"
          :id="item.id"
          :role="item.role"
          :content="item.content"
          :image="item.image"
        />
        <ChatBubble v-if="sending" key="__sending__" role="ai" :content="busyHint" pending />
      </TransitionGroup>

      <!-- 低置信度重拍引导：没看清就引导重拍，不让 AI 硬猜（家长信任的基石） -->
      <div
        v-if="rephoto"
        class="rounded-2xl border border-amber-100 bg-amber-50/80 px-4 py-3"
      >
        <p class="text-sm leading-relaxed text-amber-700">{{ rephoto.message }}</p>
        <button
          type="button"
          class="mt-2 rounded-full bg-amber-500 px-4 py-1.5 text-xs font-medium text-white shadow-sm transition active:bg-amber-600 disabled:opacity-50"
          :disabled="sending"
          @click="fileInput?.click()"
        >
          重新拍照
        </button>
      </div>

      <!-- 生成练习 -->
      <section v-if="canGenerate || exercises.length" class="space-y-3 pt-2">
        <!-- 薄弱点横幅：整批一道，生成练习按钮之前。profile 有弱项→标题+chip；否则柔和兜底 -->
        <div
          v-if="weakSource === 'profile' && weakTags.length"
          class="rounded-xl border border-brand-100 bg-brand-50/60 px-3 py-2"
        >
          <p class="mb-1.5 text-sm font-medium text-brand-700">本次练习针对你的薄弱点 🎯</p>
          <div class="flex flex-wrap gap-2">
            <span
              v-for="tag in weakTags"
              :key="tag.code"
              class="rounded-full border border-brand-200 bg-brand-100 px-2.5 py-1 text-xs font-medium text-brand-700"
            >
              {{ tag.label }}
            </span>
          </div>
        </div>
        <div
          v-else
          class="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-500"
        >
          这次先按当前这道题的类型练一练
        </div>

        <button
          v-if="canGenerate || generating"
          type="button"
          class="btn-ghost w-full"
          :disabled="generating"
          @click="handleGenExercise"
        >
          {{ generating ? '正在出题…' : exercises.length ? '再出两道' : '生成练习（2 道）' }}
        </button>

        <div v-for="(item, idx) in exercises" :key="`${item.role || 'ex'}-${idx}`" class="space-y-2">
          <QuestionCard :question="item" :index="idx + 1" :answer-locked="isAnswerLocked(idx)" />
          <button
            type="button"
            class="btn-ghost w-full"
            :disabled="!item.questionId"
            @click="openExercise(idx)"
          >
            {{ item.questionId ? exerciseActionLabel(idx) : '这道题没存档成功，换两道试试' }}
          </button>
        </div>
        <section v-if="summaryLoading || summaryText || summaryError" class="rounded-2xl border border-brand-100 bg-brand-50/70 p-4">
          <h2 class="text-base font-bold text-slate-800">📚 今天的学习小结</h2>
          <p v-if="summaryLoading" class="mt-2 text-sm text-slate-500">老师正在根据你的整个解题过程写小结…</p>
          <p v-else-if="summaryText" class="mt-2 whitespace-pre-wrap text-sm leading-7 text-slate-700">{{ summaryText }}</p>
          <p v-else class="mt-2 text-sm text-red-500">{{ summaryError }}</p>
        </section>
      </section>

      <p v-if="errorMsg" class="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-500">
        {{ errorMsg }}
      </p>
    </main>

    <!-- 输入区 -->
    <footer class="shrink-0 space-y-2 border-t border-slate-100 bg-white px-4 py-3">
      <div v-if="imageDataUrl" class="flex items-center gap-2 rounded-xl bg-slate-50 p-2">
        <img :src="imageDataUrl" alt="题目预览" class="h-14 w-14 shrink-0 rounded-lg object-cover" />
        <span class="min-w-0 flex-1 truncate text-xs text-slate-500">
          {{ uploading ? '照片上传中…' : imageName || '已选择照片' }}
        </span>
        <button type="button" class="shrink-0 text-xs text-slate-400" @click="clearImage">移除</button>
      </div>
      <div
        v-if="ocrText"
        class="flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600"
      >
        <div class="min-w-0 flex-1">
          <span class="font-medium text-slate-500">识别到题目：</span>{{ ocrText }}
          <span class="text-slate-400">（识别有偏差就直接把题目文字打出来也行）</span>
        </div>
        <button
          v-if="ttsSupported"
          type="button"
          class="shrink-0 rounded-lg bg-white px-2 py-1 text-sm text-brand-600 shadow-sm transition active:bg-brand-50"
          title="把题目念出来"
          @click="readQuestionAloud"
        >
          🔊
        </button>
      </div>

      <!-- 快捷按钮：点一下 = 填话术 + 显式带 mode 发送（卡通图标 + 微缩放） -->
      <div v-if="started" class="flex gap-2 overflow-x-auto pb-1">
        <button
          v-for="action in CHAT_QUICK_ACTIONS"
          :key="action.label"
          type="button"
          class="h-9 shrink-0 rounded-full border border-brand-200 bg-white/80 px-3 text-xs font-medium text-brand-600 shadow-sm transition hover:scale-105 active:bg-brand-100 disabled:opacity-50"
          :disabled="sending"
          @click="handleChatQuickAction(action)"
        >
          {{ QUICK_ICON[action.label] || '💬' }} {{ action.label }}
        </button>
      </div>

      <textarea
        v-model="inputText"
        rows="2"
        :placeholder="started ? '说说你是怎么想的…' : '把题目打在这里，或点右边拍照'"
        class="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:bg-white"
        @keydown.enter.exact.prevent="handleSend()"
      />

      <div class="flex items-center gap-2">
        <input
          ref="fileInput"
          type="file"
          accept="image/*"
          capture="environment"
          class="hidden"
          @change="handleFileChange"
        />
        <button
          v-if="sttSupported"
          type="button"
          class="btn-ghost shrink-0 px-3"
          :class="micRecognizing ? 'bg-brand-500 text-white' : ''"
          :title="micRecognizing ? '正在听，点一下停止' : '点一下，用说话来回答'"
          @click="toggleMic"
        >
          {{ micRecognizing ? '🎤 听…' : '🎤 说话' }}
        </button>
        <button
          type="button"
          class="btn-ghost shrink-0 px-3"
          :disabled="started || sending"
          @click="fileInput?.click()"
        >
          拍照
        </button>
        <button type="button" class="btn-ghost shrink-0 px-3" :disabled="!started" @click="handleReset">
          换一题
        </button>
        <button type="button" class="btn-primary min-w-0 flex-1" :disabled="!canSend" @click="handleSend()">
          {{ sending ? '发送中…' : started ? '回答' : '开始问 AI' }}
        </button>
      </div>
      <p v-if="micRecognizing" class="text-xs text-brand-500">
        老师在听你说～ 说完再点一下「🎤 听…」就发送
      </p>
    </footer>

    <!-- 练习题对话面板：作答全程走这里，答案不提前给 -->
    <GuideChatPanel
      :open="panelOpen"
      :loading="guideLoading"
      :error="guideError"
      :messages="activeMessages"
      :ask-final-confirm="activeThread?.askFinalConfirm ?? false"
      :stem="activeExercise?.q ?? ''"
      title="跟老师一起做这道题"
      placeholder="把你的每一步写在这里，比如「先设 x 是…」"
      :quick-actions="[...EXERCISE_QUICK_ACTIONS]"
      @close="closeExercise"
      @send="handleGuideSend"
      @quick="handleGuideQuick"
      @send-image="handleGuideImage"
    >
      <!-- 题目状态指示器：第 1/2 题 · 作答中 + 三态评判图标 -->
      <template #header-extra>
        <div class="mt-2 flex items-center gap-2 text-xs">
          <span class="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">
            第 {{ activeExerciseIdx + 1 }}/{{ exercises.length }} 题 · {{ activeStatusLabel }}
          </span>
          <span class="min-w-0 flex-1 truncate" :class="activeVerdictMeta.cls">
            {{ activeVerdictMeta.icon }} {{ activeVerdictMeta.text }}
          </span>
          <button
            v-if="hasNextExercise"
            type="button"
            class="shrink-0 rounded-lg px-2 py-0.5 text-brand-600 transition active:bg-brand-50 disabled:opacity-50"
            :disabled="guideLoading"
            @click="openExercise(activeExerciseIdx + 1)"
          >
            下一题 ›
          </button>
        </div>
      </template>
    </GuideChatPanel>

    <!-- 选题意中间态：拍照后先框出要讲的那道单题，再发给 AI -->
    <div
      v-if="cropping"
      class="absolute inset-0 z-40 flex flex-col bg-white"
    >
      <header class="shrink-0 border-b border-slate-100 px-4 py-2 text-sm font-medium text-slate-700">
        选题目 · 框住你要讲的那道题
      </header>
      <ImageQuestionCropper
        :src="pendingImage"
        @confirm="onCropConfirm"
        @whole="onCropWhole"
        @cancel="onCropCancel"
      />
    </div>
  </div>
</template>

<style scoped>
/* 对话气泡弹入：新消息从下方轻微放大浮现，像老师/你刚说出口 */
.bubble-enter-from {
  opacity: 0;
  transform: translateY(10px) scale(0.96);
}
.bubble-enter-active {
  transition: all 0.35s cubic-bezier(0.22, 1, 0.36, 1);
}
/* 攻克庆祝横幅：从上滑入放大 */
.cheer-enter-from {
  opacity: 0;
  transform: translateY(-8px) scale(0.95);
}
.cheer-enter-active {
  transition: all 0.4s cubic-bezier(0.22, 1, 0.36, 1);
}
</style>

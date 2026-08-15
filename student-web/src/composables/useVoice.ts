/**
 * 语音能力封装（TTS 朗读 + STT 语音输入）。
 *
 * 用浏览器自带的 Web Speech API，不依赖任何后端、不花一分钱：
 *   - TTS：SpeechSynthesis，挑中文嗓音、语速略慢，适合小朋友听题/听讲解；
 *   - STT：SpeechRecognition，中文、带实时字幕，说完自动交出去。
 *
 * 适用场景：低年级小朋友不会看题 → 自动把题目和老师的讲解念出来；
 *           不会打字 → 用麦克风说话代替输入。
 *
 * 兼容性：TTS 在 Chrome/Edge/Safari 桌面与手机均支持；
 *         STT 在 Chrome/Edge 桌面与安卓支持，iOS Safari 需用户点一下触发，
 *         Firefox 不支持识别 → 自动隐藏麦克风按钮，降级为打字。
 */
import { onUnmounted, ref, type Ref } from 'vue'

/** 浏览器是否支持语音合成（朗读）。 */
export const ttsSupported = ref<boolean>(typeof window !== 'undefined' && 'speechSynthesis' in window)

/** 浏览器是否支持语音识别（说话输入）。不支持时隐藏麦克风。 */
export const sttSupported = ref<boolean>(
  typeof window !== 'undefined' &&
    ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window),
)

/** 是否正在朗读。 */
export const isSpeaking = ref<boolean>(false)
/** 正在朗读的内容 key（用于高亮对应的喇叭按钮）。 */
export const playingKey = ref<string>('')

/** 自动朗读：低年级孩子不会看题，默认开启，持久化到 localStorage。 */
const AUTO_READ_KEY = 'ai_coach_auto_read'
export const autoRead = ref<boolean>(localStorage.getItem(AUTO_READ_KEY) !== '0')

/** 朗读速率/音高：略慢、略甜，适合小朋友。 */
const SPEAK_RATE = 0.92
const SPEAK_PITCH = 1.1

let voices: SpeechSynthesisVoice[] = []

function loadVoices(): void {
  if (!ttsSupported.value) return
  voices = window.speechSynthesis.getVoices()
}

if (ttsSupported.value) {
  loadVoices()
  window.speechSynthesis.onvoiceschanged = loadVoices
}

function pickVoice(): SpeechSynthesisVoice | null {
  if (!voices.length) voices = window.speechSynthesis.getVoices()
  return voices.find((v) => v.lang?.toLowerCase().startsWith('zh')) ?? null
}

/** 去掉 Markdown / LaTeX / 链接噪声，朗读更顺。 */
function cleanForSpeech(text: string): string {
  return text
    .replace(/\*\*/g, '')
    .replace(/[*_`]/g, '')
    .replace(/\$[^$]*\$/g, '')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** 按句切分，避免长文本在 ~15s 后被浏览器掐断。 */
function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[。！？!?；;\n])/)
    .map((s) => s.trim())
    .filter(Boolean)
}

let queue: string[] = []

function playNext(): void {
  if (!queue.length) {
    isSpeaking.value = false
    playingKey.value = ''
    return
  }
  const seg = queue.shift() as string
  const u = new SpeechSynthesisUtterance(seg)
  u.lang = 'zh-CN'
  u.rate = SPEAK_RATE
  u.pitch = SPEAK_PITCH
  const v = pickVoice()
  if (v) u.voice = v
  u.onstart = () => {
    isSpeaking.value = true
  }
  u.onend = () => playNext()
  u.onerror = () => {
    isSpeaking.value = false
    playingKey.value = ''
    queue = []
  }
  window.speechSynthesis.speak(u)
}

/** 朗读一段文字（自动分句、挑中文嗓音、略慢）。返回是否成功发起朗读。 */
export function speak(text: string, key = ''): boolean {
  if (!ttsSupported.value) return false
  const clean = cleanForSpeech(text)
  if (!clean) return false
  window.speechSynthesis.cancel()
  queue = splitSentences(clean)
  playingKey.value = key
  playNext()
  return true
}

/** 停止朗读。 */
export function stopSpeaking(): void {
  if (ttsSupported.value) window.speechSynthesis.cancel()
  isSpeaking.value = false
  playingKey.value = ''
  queue = []
}

export function setAutoRead(value: boolean): void {
  autoRead.value = value
  localStorage.setItem(AUTO_READ_KEY, value ? '1' : '0')
}

interface RecognitionHandlers {
  /** 实时字幕（含已确认 + 正在说的部分）。 */
  onInterim?: (text: string) => void
  /** 一句话说完整段（一次性交付，触发后清空，不会重复）。 */
  onFinal?: (text: string) => void
  onEnd?: () => void
  onError?: (message: string) => void
}

interface RecognizerController {
  recognizing: Ref<boolean>
  interim: Ref<string>
  start: () => void
  stop: () => void
}

type RecCtor = new () => {
  lang: string
  interimResults: boolean
  continuous: boolean
  maxAlternatives: number
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((e: any) => void) | null
  onerror: ((e: any) => void) | null
  onend: (() => void) | null
}

function getRecognitionCtor(): RecCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecCtor; webkitSpeechRecognition?: RecCtor }
  return w.SpeechRecognition || w.webkitSpeechRecognition || null
}

/** 把浏览器语音识别的错误码翻译成小朋友/家长也能看懂的中文。 */
function friendlyRecError(code: string): string {
  switch (code) {
    case 'not-allowed':
    case 'service-not-allowed':
      return '没允许用麦克风——点一下地址栏左边的🔒给个权限就行'
    case 'audio-capture':
      return '没找到麦克风，直接用打字也可以哦'
    case 'network':
      return '网络不太顺，语音连不上，先打字吧'
    default:
      return '语音没听清，再点一下麦克风试试'
  }
}

/**
 * 创建一个语音识别控制器（中文、带实时字幕）。
 * 行为要点（给不会打字的小朋友）：
 *   - no-speech（没在窗口里检测到声音）不报错、不打断，静默自动重试几次，
 *     给孩子多几次开口的机会；
 *   - 真出错（没权限 / 没麦 / 断网）才用听得懂的中文提示。
 * 必须在组件 setup 内调用（内部注册 onUnmounted 自动清理）。
 */
export function useSpeechRecognition(handlers: RecognitionHandlers): RecognizerController {
  const recognizing = ref<boolean>(false)
  const interim = ref<string>('')
  let finalText = ''
  let recog: InstanceType<RecCtor> | null = null
  let userStopped = false
  let pendingNoSpeech = false
  let noSpeechRetries = 0
  const MAX_NO_SPEECH_RETRIES = 2

  function build(): InstanceType<RecCtor> | null {
    const Ctor = getRecognitionCtor()
    if (!Ctor) return null
    const r = new Ctor()
    r.lang = 'zh-CN'
    r.interimResults = true
    r.continuous = false
    r.maxAlternatives = 1
    r.onresult = (e: any) => {
      let live = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i]
        if (res.isFinal) {
          finalText += res[0].transcript
          pendingNoSpeech = false
        } else live += res[0].transcript
      }
      interim.value = finalText + live
      handlers.onInterim?.(interim.value)
      if (finalText) {
        const out = finalText
        finalText = ''
        handlers.onFinal?.(out)
      }
    }
    r.onerror = (e: any) => {
      const code: string = e?.error || 'error'
      if (code === 'no-speech') {
        // 没听到声音：交给 onend 决定是否静默重试，不报错、不打断
        pendingNoSpeech = true
        return
      }
      if (code === 'aborted') return // 我们自己 stop 的，忽略
      const msg = friendlyRecError(code)
      if (msg) handlers.onError?.(msg)
    }
    r.onend = () => {
      recognizing.value = false
      // no-speech 且不是用户主动停 → 静默重试，多给孩子几次开口机会
      if (pendingNoSpeech && !userStopped && noSpeechRetries < MAX_NO_SPEECH_RETRIES) {
        noSpeechRetries += 1
        pendingNoSpeech = false
        interim.value = ''
        setTimeout(() => {
          if (userStopped) return
          try {
            recog = build()
            recog?.start()
            recognizing.value = true
          } catch {
            /* 偶尔 start 竞态，忽略 */
          }
        }, 120)
        return
      }
      // 走到这里：要么正常说完结束，要么重试用尽（确实没声音）
      if (pendingNoSpeech) {
        pendingNoSpeech = false
        handlers.onError?.('没听清，再点一下麦克风试试')
      }
      noSpeechRetries = 0
      interim.value = ''
      handlers.onEnd?.()
    }
    return r
  }

  function start(): void {
    if (!sttSupported.value || recognizing.value) return
    userStopped = false
    pendingNoSpeech = false
    noSpeechRetries = 0
    finalText = ''
    interim.value = ''
    recog = build()
    if (!recog) {
      handlers.onError?.('当前浏览器不支持语音输入，请改用打字')
      return
    }
    recognizing.value = true
    try {
      recog.start()
    } catch {
      recognizing.value = false
    }
  }

  function stop(): void {
    userStopped = true
    if (recog) {
      try {
        recog.stop()
      } catch {
        /* noop */
      }
    }
    recognizing.value = false
    interim.value = ''
  }

  onUnmounted(() => {
    userStopped = true
    if (recog) {
      try {
        recog.abort()
      } catch {
        /* noop */
      }
    }
    stopSpeaking()
  })

  return { recognizing, interim, start, stop }
}

export function useVoice() {
  return {
    ttsSupported,
    sttSupported,
    isSpeaking,
    playingKey,
    autoRead,
    speak,
    stopSpeaking,
    setAutoRead,
    useSpeechRecognition,
  }
}

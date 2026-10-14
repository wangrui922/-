<script setup lang="ts">
/**
 * 通用引导对话面板（T1 · 由 `practice/PracticeGuideDrawer.vue` 通用化而来）。
 *
 * 「让老师带我想一想」的对话面板：底部抽屉，孩子可以把自己的思路写进去，
 * 老师一步步带着想。**首轮不公布答案**这条由后端 no-leak 校验守住，
 * 前端这里不做任何「顺便展示答案」的旁路。
 *
 * 接口很慢（走大模型，超时 90s），所以：
 *   - loading 期间发送按钮禁用 + 文案变「老师正在想…」；
 *   - loading 期间不允许关抽屉误操作丢上下文（关闭按钮仍可用，对话会保留）。
 *
 * `askFinalConfirm` 为 true 时，老师在问「这是你的最终答案吗」，
 * 给两个快捷回复按钮，省得孩子打字。
 *
 * 通用化改动（对题库练习**完全向后兼容**，新增 props 全部有默认值）：
 *   - `title` / `placeholder` 可定制，AI 老师那边叫法不一样；
 *   - `quickActions` 输入框上方一行快捷按钮，点击时通过 `quick` 事件把
 *     `mode` 一起抛上去（题库练习不传就不渲染，行为与改动前一致）。
 */
import { computed, nextTick, ref, watch } from 'vue'

import type { GuideMessage, QuickAction } from '@/types/guide'
import { formatMath } from '@/utils/formatMath'
import {
  autoRead,
  isSpeaking,
  playingKey,
  setAutoRead,
  speak,
  sttSupported,
  stopSpeaking,
  ttsSupported,
  useSpeechRecognition,
} from '@/composables/useVoice'

const props = withDefaults(
  defineProps<{
    open: boolean
    loading: boolean
    error: string
    messages: GuideMessage[]
    askFinalConfirm: boolean
    /** 题干，抽屉里带一行上下文，孩子不用来回切 */
    stem: string
    /** 面板标题 */
    title?: string
    /** 输入框占位提示 */
    placeholder?: string
    /** 输入框上方的快捷按钮，空数组则不渲染这一行 */
    quickActions?: QuickAction[]
  }>(),
  {
    title: '老师带你想一想',
    placeholder: '把你的想法写在这里，比如「我先算了 3×4，然后…」',
    quickActions: () => [],
  },
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'send', work: string): void
  /** 第二个参数是点击瞬间输入框里还没发出去的草稿（可能为空串） */
  (e: 'quick', action: QuickAction, draft: string): void
  (e: 'send-image', file: File, dataUrl: string): void
}>()

const imageInput = ref<HTMLInputElement | null>(null)

async function handleImageChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || props.loading) return
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('图片读取失败'))
    reader.readAsDataURL(file)
  }).catch(() => '')
  if (dataUrl) emit('send-image', file, dataUrl)
}

// ===== 语音：老师消息朗读 + 小朋友用说话来作答 =====
/** 麦克风：说话实时填进输入框，说完整段自动发给老师。 */
const mic = useSpeechRecognition({
  onInterim: (t: string) => {
    draft.value = t
  },
  onFinal: (t: string) => {
    draft.value = t
    handleSend()
  },
  onError: () => {
    /* 静默降级为打字，不弹错误打扰孩子 */
  },
})
/** 模板里直接读 mic.recognizing 需 .value，取别名方便用。 */
const micRecognizing = mic.recognizing

function toggleMic(): void {
  if (mic.recognizing.value) {
    mic.stop()
  } else {
    draft.value = ''
    mic.start()
  }
}

function toggleAutoRead(): void {
  setAutoRead(!autoRead.value)
}

/** 题干「读题」按钮：点一下念完整题目，再点一下停。 */
function onReadStem(): void {
  if (!props.stem) return
  const key = 'guide-stem'
  if (isSpeaking.value && playingKey.value === key) stopSpeaking()
  else speak(props.stem, key)
}

/** 老师消息上的朗读按钮：点一下念，再点一下停。 */
function onTeacherSpeak(idx: number): void {
  const msg = props.messages[idx]
  if (!msg) return
  const key = `guide-${idx}`
  if (isSpeaking.value && playingKey.value === key) stopSpeaking()
  else speak(msg.text, key)
}

/** 输入框里的草稿。 */
const draft = ref<string>('')

/** 对话滚动容器，收到新消息后滚到底。 */
const scroller = ref<HTMLElement | null>(null)

/** 还一句话都没说时的开场提示。 */
const isFirstRound = computed<boolean>(() => props.messages.length === 0)

/** 发送按钮文案。 */
const sendLabel = computed<string>(() => {
  if (props.loading) return '老师正在想…'
  return isFirstRound.value ? '让老师带我想一想' : '发给老师'
})

/** 题干太长就截一下，抽屉头部只做提示不做正文。 */
const stemPreview = computed<string>(() => {
  const max = 60
  const text = formatMath(props.stem || '')
  return text.length > max ? `${text.slice(0, max)}…` : text
})

/** 滚到对话底部。 */
async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => props.messages.length,
  () => {
    void scrollToBottom()
  },
)

watch(
  () => props.open,
  (opened) => {
    if (opened) {
      void scrollToBottom()
      // 不会看题的小朋友：打开练习题就把题目念一遍（自动朗读开着时）
      if (autoRead.value && props.stem) void speak(props.stem, 'guide-stem')
    } else {
      stopSpeaking()
    }
  },
)

/** 发送当前草稿（可以为空——空着就是纯请老师起个头）。 */
function handleSend(): void {
  if (props.loading) return
  const work = draft.value
  draft.value = ''
  emit('send', work)
}

/**
 * 快捷回复（`askFinalConfirm` 场景）。
 *
 * @param text 直接作为 work 发出去
 */
function handleQuickReply(text: string): void {
  if (props.loading) return
  draft.value = ''
  emit('send', text)
}

/**
 * 快捷按钮：把话术连同显式 `mode` 一起抛给父组件。
 *
 * 走独立的 `quick` 事件而不是复用 `send`，是因为 `send` 只有一个 string 参数，
 * 塞不下 `mode`；父组件不监听 `quick` 时按钮就是个摆设，不会误触发普通发送。
 *
 * 草稿一并抛上去：孩子常常是「写完过程 → 点【我做完了，帮我判】」，
 * 这时候把输入框里的内容丢掉等于把他的答案吃了，父组件需要拿到它。
 */
function handleQuickAction(action: QuickAction): void {
  if (props.loading) return
  const typed = draft.value
  draft.value = ''
  emit('quick', action, typed)
}
</script>

<template>
  <div v-if="open" class="fixed inset-0 z-40 flex flex-col justify-end">
    <!-- 遮罩 -->
    <div class="absolute inset-0 bg-slate-900/40" @click="emit('close')" />

    <!-- 抽屉主体 -->
    <section
      class="relative flex max-h-[85vh] flex-col rounded-t-3xl bg-white shadow-2xl"
      role="dialog"
      :aria-label="title"
    >
      <!-- 头 -->
      <header class="shrink-0 border-b border-slate-100 px-4 py-3">
        <div class="flex items-center justify-between gap-3">
          <h2 class="text-base font-bold text-slate-800">{{ title }}</h2>
          <div class="flex items-center gap-1">
            <button
              v-if="ttsSupported"
              type="button"
              class="h-9 rounded-lg px-2 text-base transition active:bg-slate-100"
              :title="autoRead ? '自动朗读：开（老师的话会自动念出来）' : '自动朗读：关'"
              @click="toggleAutoRead"
            >
              {{ autoRead ? '🔊' : '🔇' }}
            </button>
            <button
              type="button"
              class="h-9 rounded-lg px-3 text-sm text-slate-400 transition active:bg-slate-100"
              @click="emit('close')"
            >
              收起
            </button>
          </div>
        </div>
        <p v-if="stemPreview" class="mt-1 flex items-center gap-2 text-xs text-slate-400">
          <span class="min-w-0 flex-1 truncate">{{ stemPreview }}</span>
          <button
            v-if="ttsSupported"
            type="button"
            class="shrink-0 rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-600 transition active:bg-brand-100"
            :title="isSpeaking && playingKey === 'guide-stem' ? '停止读题' : '读题'"
            @click="onReadStem"
          >
            {{ isSpeaking && playingKey === 'guide-stem' ? '⏹ 停止' : '🔊 读题' }}
          </button>
        </p>
        <!-- 具名插槽：AI 老师那边往这里塞「第 1/2 题 · 作答中」状态条 -->
        <slot name="header-extra" />
      </header>

      <!-- 对话区 -->
      <div ref="scroller" class="min-h-[160px] flex-1 space-y-3 overflow-y-auto px-4 py-4">
        <div v-if="isFirstRound && !loading" class="rounded-2xl bg-slate-50 p-4">
          <p class="text-base leading-relaxed text-slate-700">
            把你是怎么想的写下来，老师顺着你的思路带你一步步找问题。
          </p>
          <p class="mt-2 text-sm leading-relaxed text-slate-500">
            不知道从哪儿下手也没关系，直接点下面的按钮，老师先问你一个小问题。
          </p>
        </div>

        <div
          v-for="(msg, idx) in messages"
          :key="idx"
          class="flex"
          :class="msg.role === 'student' ? 'justify-end' : 'justify-start'"
        >
          <div
            class="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-4 py-3 text-base leading-relaxed"
            :class="
              msg.role === 'student'
                ? 'bg-brand-500 text-white'
                : 'bg-slate-100 text-slate-800'
            "
          >
            <img
              v-if="msg.image"
              :src="msg.image"
              alt="学生手写过程"
              class="mb-2 max-h-48 w-full rounded-xl object-contain"
            />
            <span>{{ formatMath(msg.text) }}</span>
            <button
              v-if="msg.role === 'teacher' && ttsSupported"
              type="button"
              class="mt-2 block text-xs font-medium text-brand-600"
              @click="onTeacherSpeak(idx)"
            >
              {{ isSpeaking && playingKey === `guide-${idx}` ? '⏹ 停止' : '🔊 读一遍' }}
            </button>
          </div>
        </div>

        <div v-if="loading" class="flex justify-start">
          <div class="rounded-2xl bg-slate-100 px-4 py-3 text-base text-slate-500">
            老师正在想…（这一步有点慢，稍等一下）
          </div>
        </div>

        <p v-if="error" class="rounded-xl bg-red-50 px-3 py-2 text-sm leading-relaxed text-red-500">
          {{ error }}
        </p>
      </div>

      <!-- 输入区 -->
      <footer class="shrink-0 space-y-3 border-t border-slate-100 px-4 py-3">
        <!-- 老师在问「这是最终答案吗」时的快捷回复 -->
        <div v-if="askFinalConfirm" class="flex gap-3">
          <button
            type="button"
            class="h-12 flex-1 rounded-xl bg-brand-500 text-base font-medium text-white transition active:bg-brand-600 disabled:opacity-50"
            :disabled="loading"
            @click="handleQuickReply('是的，这就是我的最终答案')"
          >
            是的
          </button>
          <button
            type="button"
            class="h-12 flex-1 rounded-xl border border-slate-200 bg-white text-base font-medium text-slate-600 transition active:bg-slate-100 disabled:opacity-50"
            :disabled="loading"
            @click="handleQuickReply('我再想想')"
          >
            我再想想
          </button>
        </div>

        <!-- 快捷按钮：一句话说清「我现在要什么」，省得孩子组织语言 -->
        <div v-if="quickActions.length" class="flex gap-2 overflow-x-auto pb-1">
          <button
            v-for="action in quickActions"
            :key="action.label"
            type="button"
            class="h-10 shrink-0 rounded-full border border-brand-200 bg-brand-50 px-4 text-sm font-medium text-brand-600 transition active:bg-brand-100 disabled:opacity-50"
            :disabled="loading"
            @click="handleQuickAction(action)"
          >
            {{ action.label }}
          </button>
        </div>

        <textarea
          v-model="draft"
          rows="3"
          class="w-full resize-none rounded-2xl border border-slate-200 px-4 py-3 text-base leading-relaxed text-slate-800 outline-none transition focus:border-brand-500"
          :placeholder="placeholder"
          :disabled="loading"
        />

        <div class="flex items-center gap-2">
          <input
            ref="imageInput"
            type="file"
            accept="image/*"
            capture="environment"
            class="hidden"
            @change="handleImageChange"
          />
          <button
            type="button"
            class="btn-ghost h-14 shrink-0 px-3"
            :disabled="loading"
            title="拍下手写解题过程"
            @click="imageInput?.click()"
          >
            📷 拍过程
          </button>
          <button
            v-if="sttSupported"
            type="button"
            class="btn-ghost h-14 shrink-0 px-4"
            :class="micRecognizing ? 'bg-brand-500 text-white' : ''"
            :title="micRecognizing ? '正在听，点一下停止' : '点一下，用说话来回答老师'"
            @click="toggleMic"
          >
            {{ micRecognizing ? '🎤 听…' : '🎤 说话' }}
          </button>
          <button
            type="button"
            class="btn-primary h-14 flex-1 text-lg"
            :disabled="loading"
            @click="handleSend"
          >
            {{ sendLabel }}
          </button>
        </div>
        <p v-if="micRecognizing" class="text-xs text-brand-500">
          老师在听你说话～ 说完再点一下「🎤 听…」就发给我
        </p>
      </footer>
    </section>
  </div>
</template>

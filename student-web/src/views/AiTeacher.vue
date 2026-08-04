<script setup lang="ts">
/**
 * AI 老师页（T03 · 3.5 核心页）。
 *
 * 链路：拍照/输入题目 → POST /api/chat（读题分析 + 首轮引导）
 *      → 学生回答 → POST /api/chat/{id}/message（多轮苏格拉底引导）
 *      → POST /api/exercise/gen（修复题 + 迁移题）
 *
 * 图片处理说明：后端 POST /api/chat 的 image 字段收的是 base64 / data URL
 * （见 api/chat.py::_strip_data_url），**不接受 URL**。所以这里：
 *   1. 本地把图片读成 data URL 用于预览 + 发给 /api/chat；
 *   2. 同时调 POST /api/file/upload 把原图存一份（拿到 key/url 便于后续归档），
 *      上传失败不阻塞对话流程。
 */
import { computed, nextTick, ref } from 'vue'

import { genExercise, sendMessage, startChat, uploadFile, type Exercise } from '@/api/chat'
import { toMessage } from '@/api/http'
import ChatBubble from '@/components/ChatBubble.vue'
import QuestionCard from '@/components/QuestionCard.vue'

interface ChatItem {
  id: number
  role: 'user' | 'ai'
  content: string
}

const messages = ref<ChatItem[]>([])
const sessionId = ref<string>('')
const analysisText = ref<string>('')
const inputText = ref<string>('')
const imageDataUrl = ref<string>('')
const imageName = ref<string>('')
const exercises = ref<Exercise[]>([])

const sending = ref<boolean>(false)
const uploading = ref<boolean>(false)
const generating = ref<boolean>(false)
const errorMsg = ref<string>('')

const fileInput = ref<HTMLInputElement | null>(null)
const scrollArea = ref<HTMLElement | null>(null)

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

function push(role: ChatItem['role'], content: string): void {
  messages.value.push({ id: ++seed, role, content })
  void nextTick(() => {
    const el = scrollArea.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

/** 选图：本地转 base64 预览，并异步存档一份到后端。 */
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
  imageDataUrl.value = dataUrl

  // 存档上传：失败只提示，不影响用 base64 继续对话
  uploading.value = true
  try {
    await uploadFile(file)
  } catch (error) {
    errorMsg.value = `${toMessage(error, '照片上传失败')}（不影响继续提问）`
  } finally {
    uploading.value = false
  }
}

function clearImage(): void {
  imageDataUrl.value = ''
  imageName.value = ''
}

/** 发送：首轮走 startChat，后续走 sendMessage。 */
async function handleSend(): Promise<void> {
  if (!canSend.value) return

  const text = inputText.value.trim()
  const image = imageDataUrl.value
  sending.value = true
  errorMsg.value = ''

  try {
    if (!started.value) {
      push('user', text || '（拍了一张题目照片）')
      inputText.value = ''

      const result = await startChat({ image: image || undefined, text: text || undefined })
      sessionId.value = result.sessionId
      analysisText.value = `这道题考：${result.analysis.knowledge} · ${result.analysis.type} · 难度 ${result.analysis.difficulty}`
      push('ai', analysisText.value)
      push('ai', result.firstQuestion)
      clearImage()
    } else {
      if (!text) return
      push('user', text)
      inputText.value = ''

      const result = await sendMessage(sessionId.value, text)
      push('ai', result.reply)
    }
  } catch (error) {
    errorMsg.value = toMessage(error, '发送失败，请重试')
  } finally {
    sending.value = false
  }
}

/** 生成 2 道练习题（修复题 + 迁移题）。 */
async function handleGenExercise(): Promise<void> {
  if (!canGenerate.value) return
  generating.value = true
  errorMsg.value = ''
  try {
    const result = await genExercise(sessionId.value)
    exercises.value = result.exercises || []
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

/** 换一题：清空当前会话，重新开场。 */
function handleReset(): void {
  sessionId.value = ''
  messages.value = []
  exercises.value = []
  analysisText.value = ''
  inputText.value = ''
  errorMsg.value = ''
  clearImage()
}
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-x-hidden bg-slate-50">
    <!-- 对话区 -->
    <main ref="scrollArea" class="flex-1 space-y-3 overflow-y-auto overflow-x-hidden px-4 py-4">
      <div v-if="!messages.length" class="card text-sm leading-relaxed text-slate-500">
        <p class="mb-2 font-medium text-slate-700">先把不会的题发给我 👇</p>
        <p>· 点「拍照」上传题目照片</p>
        <p>· 或直接把题目文字打出来</p>
        <p class="mt-2 text-xs text-slate-400">我不会直接给答案，而是一步步问你，帮你自己想明白。</p>
      </div>

      <ChatBubble
        v-for="item in messages"
        :key="item.id"
        :role="item.role"
        :content="item.content"
      />
      <ChatBubble v-if="sending" role="ai" :content="busyHint" pending />

      <!-- 生成练习 -->
      <section v-if="canGenerate || exercises.length" class="space-y-3 pt-2">
        <button
          v-if="canGenerate || generating"
          type="button"
          class="btn-ghost w-full"
          :disabled="generating"
          @click="handleGenExercise"
        >
          {{ generating ? '正在出题…' : exercises.length ? '再出两道' : '生成练习（2 道）' }}
        </button>

        <QuestionCard
          v-for="(item, idx) in exercises"
          :key="`${item.role || 'ex'}-${idx}`"
          :question="item"
          :index="idx + 1"
        />
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

      <textarea
        v-model="inputText"
        rows="2"
        :placeholder="started ? '说说你是怎么想的…' : '把题目打在这里，或点右边拍照'"
        class="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:bg-white"
        @keydown.enter.exact.prevent="handleSend"
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
        <button type="button" class="btn-primary min-w-0 flex-1" :disabled="!canSend" @click="handleSend">
          {{ sending ? '发送中…' : started ? '回答' : '开始问 AI' }}
        </button>
      </div>
    </footer>
  </div>
</template>

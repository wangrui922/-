<script setup lang="ts">
/**
 * 统一公式渲染组件（方向四 · T7）。
 *
 * 按内容类型按需加载三种渲染能力，杜绝一次性引入全部重资源：
 *   1. katex —— 纯文本/LaTeX 静态渲染（走既有 renderMath，CDN 懒加载兜底）；
 *   2. manim —— Manim 讲解动画：已有 task.id 时轮询 /api/manim/{id} 取 video_url
 *              再懒加载 <video controls preload="metadata">；
 *   3. geogebra —— GeoGebra 交互 applet：动态注入 deployggb.js，
 *              用 applet_config（id/appName/material_id/codebase/…）实例化。
 *
 * 降级策略：任务未就绪 → 进度占位；拉取失败 → 可重试的错误态；
 * applet 注入失败 → 保留 config JSON 供复制，不白屏。
 *
 * 用法：
 *   <MathRenderer mode="katex" :text="question_content" />
 *   <MathRenderer mode="manim" :task-id="taskId" />
 *   <MathRenderer mode="geogebra" :applet-config="cfg" />
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { renderMath } from '@/utils/renderMath'

const props = withDefaults(
  defineProps<{
    mode: 'katex' | 'manim' | 'geogebra'
    text?: string | null
    taskId?: number | string | null
    appletConfig?: Record<string, unknown> | null
  }>(),
  { text: null, taskId: null, appletConfig: null },
)

/* ---------- katex：纯静态，无副作用 ---------- */
const safeText = computed<string>(() => props.text ?? '')
const html = computed<string>(() => renderMath(props.mode === 'katex' ? safeText.value : ''))

/* ---------- manim：轮询任务状态 → 播放 ---------- */
type ManimStatus = 'idle' | 'pending' | 'done' | 'error'
const manimState = ref<ManimStatus>('idle')
const manimUrl = ref<string | null>(null)
const manimError = ref<string>('')
let pollTimer: ReturnType<typeof setTimeout> | null = null

async function fetchManimTask(): Promise<void> {
  if (!props.taskId) {
    manimState.value = 'error'
    manimError.value = '缺少动画任务 ID'
    return
  }
  manimState.value = 'pending'
  manimError.value = ''
  try {
    const res: any = await fetch(`/api/manim/${props.taskId}`, { headers: { Authorization: 'Bearer ' + (localStorage.getItem('aicoach.token') || '') } })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const body = await res.json()
    const data = body?.data ?? body
    if (data?.status === 'done' && data?.video_url) {
      manimState.value = 'done'
      manimUrl.value = data.video_url
      return
    }
    if (data?.status === 'failed') {
      manimState.value = 'error'
      manimError.value = '动画生成失败，请稍后重试'
      return
    }
    // pending → 1.5s 后重试（最多约 40 次）
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = setTimeout(() => void fetchManimTask(), 1500)
  } catch (e: any) {
    manimState.value = 'error'
    manimError.value = e?.message ?? '加载失败'
  }
}

function retryManim(): void {
  void fetchManimTask()
}

/* ---------- geogebra：动态注入 applet ---------- */
const ggContainer = ref<HTMLElement | null>(null)
const ggState = ref<'idle' | 'loading' | 'error'>('idle')
const ggError = ref<string>('')
let geogebraInjected = false
let appletInstance: { inject(container: HTMLElement): void; remove?(): void } | null = null

declare global {
  interface Window {
    GGBApplet?: new (config: Record<string, unknown>) => {
      inject(container: HTMLElement): void
      remove?(): void
    }
  }
}

function loadGeogebraScript(): Promise<void> {
  if (window.GGBApplet) return Promise.resolve()
  if (geogebraInjected) {
    // 脚本注入中：等待 Applet 构造函数出现
    return new Promise((resolve, reject) => {
      const t0 = Date.now()
      const iv = setInterval(() => {
        if (window.GGBApplet) {
          clearInterval(iv)
          resolve()
        } else if (Date.now() - t0 > 10000) {
          clearInterval(iv)
          reject(new Error('geogebra 加载超时'))
        }
      }, 200)
    })
  }
  geogebraInjected = true
  return new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = 'https://www.geogebra.org/apps/deployggb.js'
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => {
      geogebraInjected = false
      reject(new Error('geogebra CDN 加载失败'))
    }
    document.head.appendChild(s)
  })
}

async function mountApplet(): Promise<void> {
  if (props.mode !== 'geogebra' || !props.appletConfig || !ggContainer.value) return
  ggState.value = 'loading'
  ggError.value = ''
  try {
    await loadGeogebraScript()
    const GGB = window.GGBApplet
    if (!GGB) throw new Error('geogebra 运行时不可用')
    appletInstance = new GGB({ ...props.appletConfig, showResetIcon: true })
    appletInstance.inject(ggContainer.value)
    ggState.value = 'idle'
  } catch (e: any) {
    ggState.value = 'error'
    ggError.value = e?.message ?? '交互组件加载失败'
  }
}

function retryGeogebra(): void {
  void mountApplet()
}

/* ---------- 生命周期 ---------- */
watch(
  () => props.mode,
  (m) => {
    if (m === 'manim') {
      manimState.value = 'idle'
      manimUrl.value = null
      void fetchManimTask()
    }
    if (m === 'geogebra') {
      // 等 DOM 渲染后注入
      requestAnimationFrame(() => void mountApplet())
    }
  },
  { immediate: false },
)

onMounted(() => {
  if (props.mode === 'manim') void fetchManimTask()
  if (props.mode === 'geogebra') requestAnimationFrame(() => void mountApplet())
})

onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer)
  if (appletInstance?.remove) appletInstance.remove()
})

/* ---------- 状态标签 ---------- */
const manimLabel = computed<string>(() => {
  if (manimState.value === 'pending') return '小玥老师正在生成讲解动画…'
  if (manimState.value === 'error') return '讲解动画加载失败'
  return ''
})
</script>

<template>
  <!-- ① 静态公式 -->
  <div v-if="mode === 'katex'" class="whitespace-pre-wrap break-words" v-html="html" />

  <!-- ② Manim 动画 -->
  <div v-else-if="mode === 'manim'" class="w-full">
    <div
      v-if="manimState === 'pending'"
      class="flex min-h-40 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-brand-200 bg-brand-50/60 p-6 text-center"
    >
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-brand-200 border-t-brand-500" />
      <p class="text-sm text-ink-soft">{{ manimLabel }}</p>
    </div>

    <div
      v-else-if="manimState === 'error'"
      class="flex min-h-40 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-coral-200 bg-coral-50/60 p-6 text-center"
    >
      <p class="text-sm font-medium text-coral-600">{{ manimError }}</p>
      <button
        class="rounded-full bg-coral-500 px-4 py-1.5 text-sm font-semibold text-white transition hover:bg-coral-600"
        @click="retryManim"
      >
        重新加载
      </button>
    </div>

    <video
      v-else-if="manimState === 'done' && manimUrl"
      :src="manimUrl"
      class="aspect-video w-full rounded-2xl border border-brand-100 bg-black shadow-sm"
      controls
      preload="metadata"
      playsinline
    />
  </div>

  <!-- ③ GeoGebra 交互 -->
  <div v-else-if="mode === 'geogebra'">
    <div
      v-if="ggState === 'loading'"
      class="flex min-h-40 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-mint-300 bg-mint-50/60 p-6"
    >
      <div class="h-10 w-10 animate-spin rounded-full border-4 border-mint-300 border-t-mint-500" />
      <p class="text-sm text-ink-soft">正在准备几何画板…</p>
    </div>

    <div
      v-else-if="ggState === 'error'"
      class="flex min-h-40 flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-coral-200 bg-coral-50/60 p-6 text-center"
    >
      <p class="text-sm font-medium text-coral-600">{{ ggError }}</p>
      <button
        class="rounded-full bg-coral-500 px-4 py-1.5 text-sm font-semibold text-white transition hover:bg-coral-600"
        @click="retryGeogebra"
      >
        重新加载
      </button>
      <details class="mt-1 w-full text-left text-xs text-ink-soft">
        <summary>查看配置</summary>
        <pre class="mt-1 max-h-32 overflow-auto rounded-lg bg-ink/5 p-2">{{ props.appletConfig }}</pre>
      </details>
    </div>

    <div v-else ref="ggContainer" class="w-full overflow-hidden rounded-2xl border border-mint-200" />
  </div>
</template>
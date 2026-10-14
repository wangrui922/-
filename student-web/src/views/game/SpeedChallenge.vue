<script setup lang="ts">
/**
 * 限时挑战（方向六 · 限时口算挑战）。
 *
 * 规则（对齐后端 game_service）：
 *   - 60 秒倒计时（speed/start 下发 duration_sec）；
 *   - 逐题即时判分：答对 = 基础分 + 连击加成（连续答对递增）+ 快速加成（≤3 秒）；
 *   - 答错连击清零、不得分；
 *   - 倒计时结束或全部题目答完自动结算（speed/finish），结算页展示新徽章。
 *
 * 前端约束：
 *   - snack_case 入参（C10）：elapsed_ms 为单题作答毫秒数；
 *   - 提交期间防重复点击；每题反馈后自动进入下一题。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  finishSpeedChallenge,
  startSpeedChallenge,
  submitSpeedAnswer,
  type GameQuestion,
  type SpeedFinishResult,
} from '@/api/game'
import { renderMath } from '@/utils/renderMath'
import { toMessage } from '@/api/http'

const router = useRouter()
const route = useRoute()

// ---- 生活场景参数（来自 /game/scenes 场景入口，可选） ----
const sceneType = ref<string>((route.query.scenario_type as string) || '')
const sceneLabel = ref<string>((route.query.scene_label as string) || '')

type Stage = 'idle' | 'playing' | 'feedback' | 'finished'

const stage = ref<Stage>('idle')
const starting = ref(false)
const error = ref('')

// ---- 对局状态 ----
const sessionId = ref('')
const questions = ref<GameQuestion[]>([])
const questionIndex = ref(0)
const durationSec = ref(60)
const remainSec = ref(60)
/** 当前题作答起始时间戳（ms），用于计算 elapsed_ms */
const answerStartAt = ref(0)
const currentAnswer = ref('')
const submitting = ref(false)

// ---- 实时反馈 ----
const lastFeedback = ref<{ ok: boolean; combo: number; gain: number; answer: string } | null>(null)

// ---- 结算 ----
const result = ref<SpeedFinishResult | null>(null)

let timer: ReturnType<typeof setInterval> | null = null

const currentQuestion = computed<GameQuestion | null>(() => questions.value[questionIndex.value] ?? null)

/** 选择题选项（归一化 [{key,text}]；兜底空数组）。 */
const choiceOptions = computed<Array<{ key: string; text: string }>>(() => {
  const raw = currentQuestion.value?.options
  if (Array.isArray(raw)) {
    return raw
      .map((it) => {
        if (it && typeof it === 'object') {
          const obj = it as Record<string, unknown>
          return { key: String(obj.key ?? '').trim(), text: String(obj.text ?? '') }
        }
        return { key: String(it).trim(), text: String(it) }
      })
      .filter((it) => it.key && it.text)
  }
  if (raw && typeof raw === 'object') {
    return Object.entries(raw as Record<string, unknown>).map(([key, text]) => ({
      key: key.trim(),
      text: String(text ?? ''),
    }))
  }
  return []
})

const isChoice = computed<boolean>(() => {
  const t = currentQuestion.value?.type ?? ''
  return t.includes('选择') || choiceOptions.value.length > 0
})
const isJudge = computed<boolean>(() => (currentQuestion.value?.type ?? '').includes('判断'))

const totalCount = computed<number>(() => questions.value.length)
const answeredCount = computed<number>(() => Math.min(questionIndex.value, totalCount.value))

function startTimer(): void {
  timer = setInterval(() => {
    remainSec.value -= 1
    if (remainSec.value <= 0) {
      remainSec.value = 0
      stopTimer()
      void doFinish()
    }
  }, 1000)
}

function stopTimer(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

async function handleStart(difficulty: number): Promise<void> {
  if (starting.value) return
  starting.value = true
  error.value = ''
  try {
    const data = await startSpeedChallenge(
      sceneType.value ? { difficulty, scenario_type: sceneType.value } : { difficulty }
    )
    sessionId.value = data.game_session_id
    questions.value = data.questions
    durationSec.value = data.duration_sec
    remainSec.value = data.duration_sec
    questionIndex.value = 0
    lastFeedback.value = null
    result.value = null
    stage.value = 'playing'
    answerStartAt.value = Date.now()
    startTimer()
  } catch (e) {
    error.value = toMessage(e, '开局失败，请稍后再试')
  } finally {
    starting.value = false
  }
}

function elapsedMs(): number {
  return Math.max(0, Date.now() - answerStartAt.value)
}

async function handleOption(key: string): Promise<void> {
  if (stage.value !== 'playing' || submitting.value) return
  await submit(key)
}

/** 填空：回车或点提交。 */
async function handleSubmitInput(): Promise<void> {
  if (stage.value !== 'playing' || submitting.value) return
  const ans = currentAnswer.value.trim()
  if (!ans) return
  await submit(ans)
}

async function submit(answer: string): Promise<void> {
  const q = currentQuestion.value
  if (!q) return
  submitting.value = true
  try {
    const res = await submitSpeedAnswer({
      game_session_id: sessionId.value,
      question_id: q.id,
      student_answer: answer,
      elapsed_ms: elapsedMs(),
    })
    lastFeedback.value = { ok: res.is_correct, combo: res.combo, gain: res.score_gain, answer }
    stage.value = 'feedback'

    // 反馈停留 → 下一题 / 全部答完则直接结算
    setTimeout(() => {
      if (questionIndex.value + 1 >= totalCount.value) {
        void doFinish()
      } else {
        questionIndex.value += 1
        currentAnswer.value = ''
        lastFeedback.value = null
        stage.value = 'playing'
        if (stage.value === 'playing') answerStartAt.value = Date.now()
      }
    }, 900)
  } catch (e) {
    error.value = toMessage(e, '提交失败，请重试')
    submitting.value = false
  }
}

async function doFinish(): Promise<void> {
  if (stage.value === 'finished') return
  stopTimer()
  if (sessionId.value) {
    try {
      result.value = await finishSpeedChallenge(sessionId.value)
      stage.value = 'finished'
    } catch (e) {
      error.value = toMessage(e, '结算失败，稍后重试')
      stage.value = 'idle'
    }
  } else {
    stage.value = 'idle'
  }
}

function goHome(): void {
  stopTimer()
  void router.push({ name: 'game-hub' })
}

onMounted(() => {})
onBeforeUnmount(stopTimer)
</script>

<template>
  <div class="mx-auto max-w-xl p-4">
    <div class="mb-3 flex items-center justify-between">
      <button type="button" class="btn-ghost px-3 py-1.5 text-sm" @click="goHome">
        ← 返回大厅
      </button>
      <h1 class="text-base font-extrabold text-ink">⚡ {{ sceneLabel ? sceneLabel + ' · 挑战' : '限时挑战' }}</h1>
      <span class="w-16" />
    </div>

    <p v-if="error" class="mb-3 rounded-xl bg-red-50 px-3 py-2 text-sm font-medium text-red-500">{{ error }}</p>

    <!-- 开局 -->
    <div v-if="stage === 'idle'" class="card space-y-4 p-6 text-center">
      <div class="text-4xl">⚡</div>
      <div>
        <p class="text-base font-extrabold text-ink">60 秒，能答对多少题？</p>
        <p class="mt-1 text-sm text-ink-soft">答对得分，连击加分，别答错哦～</p>
      </div>
      <div class="grid grid-cols-3 gap-2">
        <button
          v-for="d in [1, 2, 3]"
          :key="d"
          type="button"
          class="rounded-2xl border-2 border-slate-200 bg-white px-3 py-3 font-bold transition active:scale-95"
          :class="d === 1 ? 'text-emerald-600' : d === 2 ? 'text-amber-600' : 'text-red-600'"
          :disabled="starting"
          @click="handleStart(d)"
        >
          {{ d === 1 ? '🌱 简单' : d === 2 ? '🔥 中等' : '🌋 困难' }}
        </button>
      </div>
      <p class="text-xs text-ink-faint">推荐从「简单」开始热身</p>
    </div>

    <!-- 进行中 -->
    <template v-if="stage === 'playing' || stage === 'feedback'">
      <div class="mb-3 flex items-center gap-2">
        <div class="flex-1">
          <div class="mb-1 flex justify-between text-xs font-semibold text-ink-soft">
            <span>{{ answeredCount }}/{{ totalCount }}</span>
            <span>倒计时 {{ remainSec }}s</span>
          </div>
          <div class="h-2.5 overflow-hidden rounded-full bg-slate-100">
            <div
              class="h-full rounded-full transition-all duration-1000"
              :class="remainSec <= 10 ? 'bg-red-400' : 'bg-gradient-to-r from-brand-500 to-coral-400'"
              :style="{ width: (remainSec / durationSec) * 100 + '%' }"
            />
          </div>
        </div>
      </div>

      <div class="card p-5">
        <!-- 题目 -->
        <p class="min-h-[3rem] whitespace-pre-wrap break-words text-lg font-bold leading-relaxed text-slate-800" v-html="renderMath(currentQuestion?.content ?? '')"></p>

        <!-- 选项 -->
        <div v-if="isChoice" class="mt-4 grid grid-cols-1 gap-2">
          <button
            v-for="opt in choiceOptions"
            :key="opt.key"
            type="button"
            class="flex items-center gap-2 rounded-xl border-2 border-slate-200 bg-white px-3 py-2.5 text-left font-medium text-slate-700 transition active:scale-[0.98]"
            :disabled="stage === 'feedback' || submitting"
            @click="handleOption(opt.key)"
          >
            <span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-sm font-extrabold text-brand-600">{{ opt.key }}</span>
            <span class="whitespace-pre-wrap break-words" v-html="renderMath(opt.text)"></span>
          </button>
        </div>

        <!-- 判断题 -->
        <div v-else-if="isJudge" class="mt-4 grid grid-cols-2 gap-2">
          <button
            type="button"
            class="rounded-xl border-2 border-emerald-200 bg-emerald-50 py-3 text-base font-bold text-emerald-600 transition active:scale-[0.98]"
            :disabled="stage === 'feedback' || submitting"
            @click="handleOption('√')"
          >√ 正确</button>
          <button
            type="button"
            class="rounded-xl border-2 border-red-200 bg-red-50 py-3 text-base font-bold text-red-500 transition active:scale-[0.98]"
            :disabled="stage === 'feedback' || submitting"
            @click="handleOption('×')"
          >× 错误</button>
        </div>

        <!-- 填空题 -->
        <div v-else class="mt-4 flex gap-2">
          <input
            v-model="currentAnswer"
            type="text"
            inputmode="numeric"
            class="min-w-0 flex-1 rounded-xl border-2 border-slate-200 bg-white px-3 py-2.5 text-lg font-bold text-slate-800 outline-none focus:border-brand-400"
            :disabled="stage === 'feedback' || submitting"
            @keyup.enter="handleSubmitInput"
          />
          <button
            type="button"
            class="rounded-xl bg-brand-500 px-4 py-2.5 text-sm font-bold text-white transition active:scale-95"
            :disabled="stage === 'feedback' || submitting || !currentAnswer.trim()"
            @click="handleSubmitInput"
          >提交</button>
        </div>
      </div>

      <!-- 反馈浮层 -->
      <div
        v-if="stage === 'feedback' && lastFeedback"
        class="mt-3 rounded-2xl px-4 py-3 text-center font-bold transition"
        :class="lastFeedback.ok ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-500'"
      >
        <template v-if="lastFeedback.ok">🎉 答对啦！连击 ×{{ lastFeedback.combo }} · +{{ lastFeedback.gain }} 分</template>
        <template v-else>😢 答错了，连击清零</template>
      </div>
    </template>

    <!-- 结算 -->
    <div v-if="stage === 'finished' && result" class="card space-y-4 p-6 text-center">
      <div class="text-4xl">🏁</div>
      <p class="text-lg font-extrabold text-ink">本轮得分</p>
      <div class="text-5xl font-black text-brand-600">{{ result.score }}</div>

      <div class="grid grid-cols-3 gap-2 text-sm">
        <div class="rounded-xl bg-slate-50 py-2">
          <p class="font-extrabold text-emerald-600">{{ result.correct_count }}</p>
          <p class="text-xs text-ink-faint">答对</p>
        </div>
        <div class="rounded-xl bg-slate-50 py-2">
          <p class="font-extrabold text-red-500">{{ result.wrong_count }}</p>
          <p class="text-xs text-ink-faint">答错</p>
        </div>
        <div class="rounded-xl bg-slate-50 py-2">
          <p class="font-extrabold text-amber-500">×{{ result.max_combo }}</p>
          <p class="text-xs text-ink-faint">最高连击</p>
        </div>
      </div>

      <!-- 新徽章 -->
      <div v-if="result.new_badges.length" class="rounded-2xl border border-amber-200 bg-amber-50 p-3">
        <p class="text-sm font-extrabold text-amber-600">🏅 解锁新徽章</p>
        <div class="mt-2 flex flex-wrap justify-center gap-2">
          <span
            v-for="b in result.new_badges"
            :key="b.badge_id"
            class="rounded-full bg-white px-3 py-1 text-sm font-bold text-amber-600 shadow-soft"
          >{{ b.icon_url || '🏅' }} {{ b.display_name }}</span>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-2">
        <button type="button" class="btn-ghost" @click="goHome">返回大厅</button>
        <button type="button" class="btn-primary" @click="handleStart(1)">再来一局</button>
      </div>
    </div>
  </div>
</template>
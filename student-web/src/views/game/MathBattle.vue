<script setup lang="ts">
/**
 * 回合制战斗（方向六 · 第二期）。
 *
 * 规则（对齐后端 game_service.submit_battle_answer）：
 *   - 玩家 HP 100，怪物 10 波（wave 1-10，wave5 精英、wave10 Boss）；
 *   - 答对 = 击杀当前怪物 → 进入下一波（后端下发 next_monster / next_question）；
 *   - 答错 = 怪物反击，玩家按怪物 atk 扣血，同一题目重新作答；
 *   - 玩家 HP ≤ 0 或通关 10 波 → 结算（battle/finish），展示得分与新徽章。
 *
 * 前端约束：
 *   - snake_case 入参（C10）；
 *   - 提交期间防重复点击；答对/答错各播一段战斗反馈再进入下一步。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  finishMathBattle,
  startMathBattle,
  submitBattleAnswer,
  type BattleFinishResult,
  type GameQuestion,
  type MonsterInfo,
} from '@/api/game'
import { renderMath } from '@/utils/renderMath'
import { toMessage } from '@/api/http'

const router = useRouter()

type Stage = 'idle' | 'playing' | 'feedback' | 'finished'

const stage = ref<Stage>('idle')
const starting = ref(false)
const error = ref('')

// ---- 对局状态 ----
const sessionId = ref('')
/** 后端判题返回的玩家血量（权威值）。 */
const playerHp = ref(100)
const monster = ref<MonsterInfo | null>(null)
const question = ref<GameQuestion | null>(null)
const wave = ref(1)
const currentAnswer = ref('')
const submitting = ref(false)
const difficulty = ref(1)

// ---- 战斗反馈 ----
const lastResult = ref<{ ok: boolean; hit: number } | null>(null)

// ---- 结算 ----
const result = ref<BattleFinishResult | null>(null)

let feedbackTimer: ReturnType<typeof setTimeout> | null = null

/** 选择题选项（归一化 [{key,text}]）。 */
const choiceOptions = computed<Array<{ key: string; text: string }>>(() => {
  const raw = question.value?.options
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
  const t = question.value?.type ?? ''
  return t.includes('选择') || choiceOptions.value.length > 0
})
const isJudge = computed<boolean>(() => (question.value?.type ?? '').includes('判断'))

const playerHpPct = computed<number>(() => Math.max(0, Math.min(100, playerHp.value)))
const monsterHpPct = computed<number>(() => {
  if (!monster.value) return 0
  return Math.max(0, Math.min(100, monster.value.hp))
})

const rarityLabel = computed<Record<string, string>>(() => ({
  normal: '普通',
  elite: '精英',
  boss: 'Boss',
}))

const rarityStyle = computed<Record<string, string>>(() => ({
  normal: 'border-slate-200 bg-slate-50 text-slate-600',
  elite: 'border-amber-300 bg-amber-50 text-amber-600',
  boss: 'border-red-300 bg-red-50 text-red-600',
}))

function clearFeedbackTimer(): void {
  if (feedbackTimer) {
    clearTimeout(feedbackTimer)
    feedbackTimer = null
  }
}

async function handleStart(d: number): Promise<void> {
  if (starting.value) return
  starting.value = true
  error.value = ''
  clearFeedbackTimer()
  try {
    const data = await startMathBattle({ difficulty: d })
    difficulty.value = d
    sessionId.value = data.game_session_id
    playerHp.value = data.player_hp
    monster.value = data.monster
    question.value = data.question
    wave.value = data.monster.wave
    currentAnswer.value = ''
    lastResult.value = null
    result.value = null
    stage.value = 'playing'
  } catch (e) {
    error.value = toMessage(e, '开局失败，请稍后再试')
  } finally {
    starting.value = false
  }
}

async function submit(answer: string): Promise<void> {
  const q = question.value
  if (!q || stage.value !== 'playing' || submitting.value) return
  submitting.value = true
  try {
    const res = await submitBattleAnswer({
      game_session_id: sessionId.value,
      question_id: q.id,
      student_answer: answer,
    })

    if (res.game_over) {
      // 直接进入结算（无下一波）
      playerHp.value = Math.max(res.player_hp, 0)
      lastResult.value = { ok: res.is_correct, hit: monster.value?.atk ?? 0 }
      stage.value = 'feedback'
      clearFeedbackTimer()
      feedbackTimer = setTimeout(() => {
        void doFinish()
      }, 1200)
      return
    }

    if (res.is_correct && res.next_monster && res.next_question) {
      // 击杀 → 播反馈 → 进入下一波
      playerHp.value = res.player_hp
      lastResult.value = { ok: true, hit: monster.value?.hp ?? 0 }
      stage.value = 'feedback'
      clearFeedbackTimer()
      const nextMonster = res.next_monster
      const nextQuestion = res.next_question
      feedbackTimer = setTimeout(() => {
        monster.value = nextMonster
        question.value = nextQuestion
        wave.value = nextMonster.wave ?? res.wave + 1
        currentAnswer.value = ''
        lastResult.value = null
        stage.value = 'playing'
      }, 1100)
      return
    }

    // 答错 → 扣血、同一题重答
    playerHp.value = res.player_hp
    lastResult.value = { ok: false, hit: monster.value?.atk ?? 0 }
    stage.value = 'feedback'
    clearFeedbackTimer()
    feedbackTimer = setTimeout(() => {
      currentAnswer.value = ''
      lastResult.value = null
      stage.value = 'playing'
    }, 1000)
  } catch (e) {
    error.value = toMessage(e, '提交失败，请重试')
    submitting.value = false
  }
}

async function doFinish(): Promise<void> {
  if (stage.value === 'finished') return
  try {
    result.value = await finishMathBattle(sessionId.value)
    stage.value = 'finished'
  } catch (e) {
    error.value = toMessage(e, '结算失败，稍后重试')
    stage.value = 'idle'
  }
}

function goHome(): void {
  clearFeedbackTimer()
  void router.push({ name: 'game-hub' })
}

onMounted(() => {})
onBeforeUnmount(() => {
  clearFeedbackTimer()
})
</script>

<template>
  <div class="mx-auto max-w-xl p-4">
    <div class="mb-3 flex items-center justify-between">
      <button type="button" class="btn-ghost px-3 py-1.5 text-sm" @click="goHome">← 返回大厅</button>
      <h1 class="text-base font-extrabold text-ink">⚔️ 回合战斗</h1>
      <span class="w-16" />
    </div>

    <p v-if="error" class="mb-3 rounded-xl bg-red-50 px-3 py-2 text-sm font-medium text-red-500">{{ error }}</p>

    <!-- 开局 -->
    <div v-if="stage === 'idle'" class="card space-y-4 p-6 text-center">
      <div class="text-4xl">⚔️</div>
      <div>
        <p class="text-base font-extrabold text-ink">答对攻击，答错挨打！</p>
        <p class="mt-1 text-sm text-ink-soft">共 10 波，第 5 波精英、第 10 波 Boss，通关拿大奖。</p>
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
      <p class="text-xs text-ink-faint">答错后同一题可重新作答，直到血条归零</p>
    </div>

    <!-- 战斗中 / 反馈 -->
    <template v-if="stage === 'playing' || stage === 'feedback'">
      <!-- 波次信息 -->
      <div class="mb-3 flex items-center justify-between text-sm font-semibold">
        <span class="chip">第 {{ wave }} / 10 波</span>
        <span class="chip" :class="monster ? rarityStyle[monster.rarity] ?? rarityStyle.normal : ''">
          {{ monster ? rarityLabel[monster.rarity] ?? monster.rarity : '' }} · 攻击 {{ monster?.atk }}
        </span>
      </div>

      <!-- 玩家 vs 怪物 HP -->
      <div class="card space-y-3">
        <div>
          <div class="mb-1 flex justify-between text-xs font-bold text-sky-500">
            <span>🛡️ 玩家 HP</span>
            <span>{{ playerHp }} / 100</span>
          </div>
          <div class="h-3 overflow-hidden rounded-full bg-slate-100">
            <div
              class="h-full rounded-full bg-gradient-to-r from-sky-400 to-blue-500 transition-all duration-500"
              :style="{ width: playerHpPct + '%' }"
            />
          </div>
        </div>
        <div v-if="monster">
          <div class="mb-1 flex justify-between text-xs font-bold text-red-500">
            <span>👾 {{ monster.name }} <span class="font-normal text-slate-400">x{{ monster.hp }}</span></span>
            <span>HP {{ monster.hp }}</span>
          </div>
          <div class="h-3 overflow-hidden rounded-full bg-slate-100">
            <div
              class="h-full rounded-full bg-gradient-to-r from-rose-400 to-red-500 transition-all duration-500"
              :style="{ width: monsterHpPct + '%' }"
            />
          </div>
        </div>
      </div>

      <!-- 战斗反馈动画 -->
      <div
        v-if="stage === 'feedback' && lastResult"
        class="mt-3 rounded-2xl px-4 py-3 text-center font-extrabold transition animate-pop"
        :class="lastResult.ok ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-500'"
      >
        <template v-if="lastResult.ok">💥 击败 {{ monster?.name }}！</template>
        <template v-else>😵 受到 {{ lastResult.hit }} 点伤害！</template>
      </div>

      <!-- 题目卡片 -->
      <div class="card mt-3 p-5">
        <p
          class="min-h-[3rem] whitespace-pre-wrap break-words text-lg font-bold leading-relaxed text-slate-800"
          v-html="renderMath(question?.content ?? '')"
        ></p>

        <div v-if="isChoice" class="mt-4 grid grid-cols-1 gap-2">
          <button
            v-for="opt in choiceOptions"
            :key="opt.key"
            type="button"
            class="flex items-center gap-2 rounded-xl border-2 border-slate-200 bg-white px-3 py-2.5 text-left font-medium text-slate-700 transition active:scale-[0.98]"
            :disabled="stage === 'feedback' || submitting"
            @click="submit(opt.key)"
          >
            <span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-sm font-extrabold text-brand-600">{{ opt.key }}</span>
            <span class="whitespace-pre-wrap break-words" v-html="renderMath(opt.text)"></span>
          </button>
        </div>

        <div v-else-if="isJudge" class="mt-4 grid grid-cols-2 gap-2">
          <button
            type="button"
            class="rounded-xl border-2 border-emerald-200 bg-emerald-50 py-3 text-base font-bold text-emerald-600 transition active:scale-[0.98]"
            :disabled="stage === 'feedback' || submitting"
            @click="submit('√')"
          >√ 正确</button>
          <button
            type="button"
            class="rounded-xl border-2 border-red-200 bg-red-50 py-3 text-base font-bold text-red-500 transition active:scale-[0.98]"
            :disabled="stage === 'feedback' || submitting"
            @click="submit('×')"
          >× 错误</button>
        </div>

        <div v-else class="mt-4 flex gap-2">
          <input
            v-model="currentAnswer"
            type="text"
            inputmode="numeric"
            class="min-w-0 flex-1 rounded-xl border-2 border-slate-200 bg-white px-3 py-2.5 text-lg font-bold text-slate-800 outline-none focus:border-brand-400"
            :disabled="stage === 'feedback' || submitting"
            @keyup.enter="submit(currentAnswer.trim())"
          />
          <button
            type="button"
            class="rounded-xl bg-brand-500 px-4 py-2.5 text-sm font-bold text-white transition active:scale-95"
            :disabled="stage === 'feedback' || submitting || !currentAnswer.trim()"
            @click="submit(currentAnswer.trim())"
          >攻击</button>
        </div>
      </div>
    </template>

    <!-- 结算 -->
    <div v-if="stage === 'finished' && result" class="card space-y-4 p-6 text-center">
      <div class="text-4xl">{{ result.best_wave >= 10 ? '👑' : '🏁' }}</div>
      <p class="text-lg font-extrabold text-ink">
        {{ result.best_wave >= 10 ? '通关成功！' : '战斗结束' }}
      </p>
      <div class="text-5xl font-black text-brand-600">{{ result.score }}</div>
      <p class="text-sm text-ink-faint">本轮得分</p>

      <div class="grid grid-cols-3 gap-2 text-sm">
        <div class="rounded-xl bg-slate-50 py-2">
          <p class="font-extrabold text-amber-500">{{ result.best_wave }}</p>
          <p class="text-xs text-ink-faint">最高波次</p>
        </div>
        <div class="rounded-xl bg-slate-50 py-2">
          <p class="font-extrabold text-emerald-600">{{ result.correct_count }}</p>
          <p class="text-xs text-ink-faint">答对</p>
        </div>
        <div class="rounded-xl bg-slate-50 py-2">
          <p class="font-extrabold text-red-500">{{ result.wrong_count }}</p>
          <p class="text-xs text-ink-faint">答错</p>
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
        <button type="button" class="btn-primary" @click="handleStart(difficulty)">再战一局</button>
      </div>
    </div>
  </div>
</template>
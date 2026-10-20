<script setup lang="ts">
/**
 * 家长端：孩子详情页。
 *
 * Tabs：概览 / 错题本 / 练习记录 / 掌握度 / 权益
 * 分别调用 family.ts 里的 summary / mistakes / attempts / mastery / entitlement。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  getChildSummary,
  getChildMistakes,
  getChildAttempts,
  getChildEntitlement,
  type ChildMistake,
  type ChildAttempt,
  type ChildMastery,
  type ChildEntitlement,
} from '@/api/family'
import { getLearningDna, toChildMasteryList } from '@/api/dna'
import { toMessage } from '@/api/http'

const route = useRoute()
const studentId = computed(() => route.params.sid as string)

const tab = ref<'summary' | 'mistakes' | 'attempts' | 'mastery' | 'entitlement'>('summary')
const loading = ref(false)
const error = ref('')

/** 「不限」判定（内测期全免费不限额，2026-10-20 口径）。 */
function isUnlimited(limit: number | null | undefined): boolean {
  if (limit === null || limit === undefined) return false
  return limit <= 0 || limit >= 9999
}

const summary = ref<Record<string, unknown> | null>(null)
const mistakes = ref<ChildMistake[]>([])
const attempts = ref<ChildAttempt[]>([])
const mastery = ref<ChildMastery[]>([])
const entitlement = ref<ChildEntitlement | null>(null)

async function loadSummary() {
  summary.value = await getChildSummary(studentId.value)
}

async function loadMistakes() {
  const res = await getChildMistakes(studentId.value)
  mistakes.value = res.items
}

async function loadAttempts() {
  const res = await getChildAttempts(studentId.value)
  attempts.value = res.items
}

/**
 * 掌握度 Tab：数据源为 DNA-005 聚合接口（埋点实时驱动，恒定最新），
 * mastery_summary 经 toChildMasteryList 适配为 ChildMastery[]（0~1 分数渲染口径不变）。
 */
async function loadMastery() {
  const dna = await getLearningDna(studentId.value)
  mastery.value = toChildMasteryList(dna)
}

async function loadEntitlement() {
  entitlement.value = await getChildEntitlement(studentId.value)
}

async function loadTab() {
  loading.value = true
  error.value = ''
  try {
    switch (tab.value) {
      case 'summary': await loadSummary(); break
      case 'mistakes': await loadMistakes(); break
      case 'attempts': await loadAttempts(); break
      case 'mastery': await loadMastery(); break
      case 'entitlement': await loadEntitlement(); break
    }
  } catch (err) {
    error.value = toMessage(err, '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadTab()
})

function switchTab(t: typeof tab.value) {
  tab.value = t
  loadTab()
}

const tabs = [
  { key: 'summary', label: '概览' },
  { key: 'mistakes', label: '错题' },
  { key: 'attempts', label: '练习' },
  { key: 'mastery', label: '掌握度' },
  { key: 'entitlement', label: '权益' },
] as const
</script>

<template>
  <div class="min-h-screen bg-slate-50 pb-8">
    <!-- 头部 -->
    <div class="bg-gradient-to-br from-brand-500 to-brand-600 px-5 py-6 text-white">
      <h1 class="text-xl font-extrabold">孩子学习档案</h1>
      <p class="mt-1 text-sm text-white/80">实时追踪学习进展</p>
    </div>

    <!-- Tab 栏 -->
    <div class="sticky top-0 z-10 bg-white shadow-sm">
      <div class="flex overflow-x-auto">
        <button
          v-for="t in tabs"
          :key="t.key"
          type="button"
          class="shrink-0 border-b-2 px-4 py-3 text-sm font-bold transition"
          :class="tab === t.key ? 'border-brand-500 text-brand-600' : 'border-transparent text-slate-500'"
          @click="switchTab(t.key)"
        >
          {{ t.label }}
        </button>
      </div>
    </div>

    <!-- 加载 / 错误 -->
    <div v-if="loading" class="py-8 text-center text-sm text-slate-400">加载中…</div>
    <div v-if="error" class="mx-4 mt-4 rounded-2xl bg-red-50 p-3 text-center text-sm text-red-500">{{ error }}</div>

    <!-- 概览 -->
    <div v-if="tab === 'summary' && summary" class="space-y-3 px-4 pt-4">
      <div class="rounded-2xl bg-white p-4 shadow-soft">
        <h3 class="text-sm font-bold text-ink">本周数据</h3>
        <pre class="mt-2 overflow-x-auto text-xs text-slate-600">{{ JSON.stringify(summary, null, 2) }}</pre>
      </div>
    </div>

    <!-- 错题 -->
    <div v-if="tab === 'mistakes'" class="space-y-3 px-4 pt-4">
      <div
        v-for="m in mistakes"
        :key="m.id"
        class="rounded-2xl bg-white p-4 shadow-soft"
      >
        <p class="text-sm font-bold text-ink">{{ m.stem || '未知题目' }}</p>
        <div class="mt-2 flex flex-wrap gap-2 text-xs">
          <span class="rounded bg-red-50 px-2 py-1 font-semibold text-red-500">{{ m.errorType || '未知错因' }}</span>
          <span v-if="m.solved" class="rounded bg-mint-50 px-2 py-1 font-semibold text-mint-600">已解决</span>
          <span v-else class="rounded bg-amber-50 px-2 py-1 font-semibold text-amber-600">未解决</span>
        </div>
      </div>
      <div v-if="mistakes.length === 0 && !loading" class="py-8 text-center text-sm text-slate-400">暂无错题</div>
    </div>

    <!-- 练习 -->
    <div v-if="tab === 'attempts'" class="space-y-3 px-4 pt-4">
      <div
        v-for="a in attempts"
        :key="a.id"
        class="rounded-2xl bg-white p-4 shadow-soft"
      >
        <p class="text-sm font-bold text-ink">{{ a.stem || '未知题目' }}</p>
        <div class="mt-2 flex gap-2 text-xs">
          <span
            class="rounded px-2 py-1 font-semibold"
            :class="a.isCorrect ? 'bg-mint-50 text-mint-600' : 'bg-red-50 text-red-500'"
          >
            {{ a.isCorrect ? '正确' : '错误' }}
          </span>
          <span v-if="a.durationSec" class="rounded bg-slate-50 px-2 py-1 text-slate-500">{{ a.durationSec }}秒</span>
        </div>
      </div>
      <div v-if="attempts.length === 0 && !loading" class="py-8 text-center text-sm text-slate-400">暂无练习记录</div>
    </div>

    <!-- 掌握度 -->
    <div v-if="tab === 'mastery'" class="space-y-3 px-4 pt-4">
      <div
        v-for="m in mastery"
        :key="m.kpId"
        class="rounded-2xl bg-white p-4 shadow-soft"
      >
        <div class="flex items-center justify-between">
          <span class="text-sm font-bold text-ink">{{ m.kpName || m.kpId }}</span>
          <span
            class="rounded px-2 py-0.5 text-[10px] font-bold"
            :class="m.levelInt >= 3 ? 'bg-mint-100 text-mint-700' : m.levelInt >= 2 ? 'bg-brand-50 text-brand-600' : 'bg-red-50 text-red-500'"
          >
            {{ m.level }}
          </span>
        </div>
        <div class="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
          <div
            class="h-full rounded-full transition-all"
            :class="m.levelInt >= 3 ? 'bg-mint-400' : m.levelInt >= 2 ? 'bg-brand-400' : 'bg-red-400'"
            :style="{ width: Math.min(100, m.score) + '%' }"
          />
        </div>
        <p class="mt-1 text-xs text-slate-400">练习 {{ m.attempts }} 次 · 得分 {{ m.score }}</p>
      </div>
      <div v-if="mastery.length === 0 && !loading" class="py-8 text-center text-sm text-slate-400">暂无掌握度数据</div>
    </div>

    <!-- 权益 -->
    <div v-if="tab === 'entitlement' && entitlement" class="px-4 pt-4">
      <div class="rounded-2xl bg-white p-5 shadow-soft">
        <div class="flex items-center justify-between">
          <h3 class="text-lg font-extrabold text-ink">{{ entitlement.planName }}</h3>
          <span
            class="rounded-full px-3 py-1 text-xs font-bold"
            :class="entitlement.plan === 'free' ? 'bg-slate-100 text-slate-500' : 'bg-brand-100 text-brand-600'"
          >
            {{ entitlement.plan }}
          </span>
        </div>
        <div class="mt-4 space-y-3 text-sm">
          <div class="flex justify-between">
            <span class="text-slate-500">AI 对话今日用量</span>
            <span class="font-semibold text-ink">{{ entitlement.aiSessionsToday }} / {{ isUnlimited(entitlement.dailyAiLimit) ? '不限' : entitlement.dailyAiLimit }}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-slate-500">练习今日用量</span>
            <span class="font-semibold text-ink">{{ entitlement.aiAttemptsToday }}</span>
          </div>
          <div v-if="entitlement.expiresAt" class="flex justify-between">
            <span class="text-slate-500">有效期至</span>
            <span class="font-semibold text-ink">{{ entitlement.expiresAt.split('T')[0] }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

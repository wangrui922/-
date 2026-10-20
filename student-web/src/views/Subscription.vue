<script setup lang="ts">
/**
 * 订阅/权益页面。
 *
 * 展示计划卡片 + 当前权益状态 + 支付入口。
 * 游戏化视觉：渐变卡片、对勾徽章、进度条。
 */
import { computed, onMounted, ref } from 'vue'

import { getPlans, getEntitlement, createCheckout, type Plan, type Entitlement } from '@/api/billing'
import { toMessage } from '@/api/http'

const plans = ref<Plan[]>([])
const entitlement = ref<Entitlement | null>(null)
const loading = ref(false)
const error = ref('')
const checkoutLoading = ref<string | null>(null)
const intervalMode = ref<'month' | 'year'>('month')

/**
 * 内测期开关（2026-10-20 定）：全免费不限额，隐藏付费订阅入口。
 * 商业化重启时置为 false 即可恢复「立即订阅」按钮。
 */
const BETA_FREE_ONLY = true

const currentPlanSlug = computed(() => entitlement.value?.plan ?? 'free')

/**
 * 「不限」判定（内测期全免费不限额，2026-10-20 口径）。
 * 后端统一用 -1 表示无限；历史套餐用 9999/99999 表达近似无限，一并归一。
 */
function isUnlimited(limit: number | null | undefined): boolean {
  if (limit === null || limit === undefined) return false
  return limit <= 0 || limit >= 9999
}

function limitLabel(limit: number | null | undefined, unit: string): string {
  if (isUnlimited(limit)) return '不限'
  return `每日 ${limit} ${unit}`
}

function priceLabel(plan: Plan): string {
  const price = intervalMode.value === 'year' ? plan.priceYearly : plan.priceMonthly
  if (price <= 0) return '免费'
  return `¥${(price / 100).toFixed(0)}${intervalMode.value === 'year' ? '/年' : '/月'}`
}

function isCurrentPlan(plan: Plan): boolean {
  return currentPlanSlug.value === plan.slug
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [p, e] = await Promise.all([getPlans(), getEntitlement()])
    // 内测期只展示 free 档：basic/pro 是尚未开放的商业化档位，
    // 展示给内测用户会造成"要付费才能用 AI"的误解（2026-10-20 口径）。
    plans.value = BETA_FREE_ONLY ? p.filter((item) => item.slug === 'free') : p
    entitlement.value = e
  } catch (err) {
    error.value = toMessage(err, '加载失败')
  } finally {
    loading.value = false
  }
}

async function handleCheckout(plan: Plan) {
  if (plan.slug === 'free' || plan.slug === 'enterprise') return
  checkoutLoading.value = plan.slug
  try {
    const result = await createCheckout(plan.slug, intervalMode.value)
    // 跳转支付链接（模拟 / 真实支付页）
    window.open(result.checkoutUrl, '_blank')
  } catch (err) {
    error.value = toMessage(err, '创建订单失败')
  } finally {
    checkoutLoading.value = null
  }
}

const aiUsagePercent = computed(() => {
  if (!entitlement.value) return 0
  if (isUnlimited(entitlement.value.dailyAiLimit)) return 0
  const limit = Math.max(1, entitlement.value.dailyAiLimit)
  return Math.min(100, Math.round((entitlement.value.aiSessionsToday / limit) * 100))
})

const practiceUsagePercent = computed(() => {
  if (!entitlement.value) return 0
  if (isUnlimited(entitlement.value.practiceDailyLimit)) return 0
  const limit = Math.max(1, entitlement.value.practiceDailyLimit)
  return Math.min(100, Math.round((entitlement.value.aiAttemptsToday / limit) * 100))
})

onMounted(loadData)
</script>

<template>
  <div class="min-h-screen bg-slate-50 pb-8">
    <!-- 头部权益概览 -->
    <section class="relative overflow-hidden bg-gradient-to-br from-brand-500 to-brand-600 px-5 pt-6 pb-8 text-white">
      <div class="pointer-events-none absolute -right-8 -top-8 h-32 w-32 rounded-full bg-white/10" />
      <div class="pointer-events-none absolute -bottom-6 -left-6 h-24 w-24 rounded-full bg-white/10" />

      <div class="relative">
        <h1 class="text-xl font-extrabold">我的会员</h1>
        <p class="mt-1 text-sm text-white/80">
          当前：{{ entitlement?.planName ?? '-' }}
          <span v-if="entitlement?.expiresAt" class="ml-1 text-xs opacity-80">
            ({{ entitlement.isActive ? '有效至 ' + entitlement.expiresAt.split('T')[0] : '已过期' }})
          </span>
          <span v-else-if="BETA_FREE_ONLY" class="ml-1 text-xs opacity-80">(内测期永久有效)</span>
        </p>

        <!-- 用量条 -->
        <div class="mt-4 space-y-3">
          <div>
            <div class="flex justify-between text-xs text-white/80">
              <span>AI 对话</span>
              <span>{{ entitlement?.aiSessionsToday ?? 0 }} / {{ isUnlimited(entitlement?.dailyAiLimit) ? '不限' : (entitlement?.dailyAiLimit ?? '-') }}</span>
            </div>
            <div class="mt-1 h-2 overflow-hidden rounded-full bg-white/20">
              <div class="h-full rounded-full bg-white transition-all" :style="{ width: aiUsagePercent + '%' }" />
            </div>
          </div>
          <div>
            <div class="flex justify-between text-xs text-white/80">
              <span>今日练习</span>
              <span>{{ entitlement?.aiAttemptsToday ?? 0 }} / {{ isUnlimited(entitlement?.practiceDailyLimit) ? '不限' : (entitlement?.practiceDailyLimit ?? '-') }}</span>
            </div>
            <div class="mt-1 h-2 overflow-hidden rounded-full bg-white/20">
              <div class="h-full rounded-full bg-mint-300 transition-all" :style="{ width: practiceUsagePercent + '%' }" />
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 周期切换（内测期无付费周期，隐藏） -->
    <div v-if="!BETA_FREE_ONLY" class="mx-4 -mt-4 rounded-2xl bg-white p-1 shadow-soft">
      <div class="flex rounded-xl bg-slate-100 p-1">
        <button
          type="button"
          class="flex-1 rounded-lg py-2 text-sm font-bold transition"
          :class="intervalMode === 'month' ? 'bg-white text-brand-600 shadow-sm' : 'text-slate-500'"
          @click="intervalMode = 'month'"
        >
          月付
        </button>
        <button
          type="button"
          class="flex-1 rounded-lg py-2 text-sm font-bold transition"
          :class="intervalMode === 'year' ? 'bg-white text-brand-600 shadow-sm' : 'text-slate-500'"
          @click="intervalMode = 'year'"
        >
          年付
          <span class="ml-1 rounded bg-mint-100 px-1.5 py-0.5 text-[10px] text-mint-700">省20%</span>
        </button>
      </div>
    </div>

    <!-- 内测期说明横幅 -->
    <div v-if="BETA_FREE_ONLY" class="mx-4 -mt-4 rounded-2xl bg-mint-50 p-4 shadow-soft">
      <p class="text-sm font-bold text-mint-700">内测期全部功能免费开放</p>
      <p class="mt-1 text-xs leading-relaxed text-mint-600">
        AI 讲解、拍照解题、练习与错题本均不限次数、不限额度。正式商用后会推出付费套餐，届时会提前通知。
      </p>
    </div>

    <!-- 计划卡片 -->
    <section class="mt-4 space-y-3 px-4">
      <div
        v-for="plan in plans"
        :key="plan.slug"
        class="relative overflow-hidden rounded-2xl border-2 bg-white p-5 transition active:scale-[0.98]"
        :class="isCurrentPlan(plan) ? 'border-brand-400 shadow-md' : 'border-slate-100'"
      >
        <!-- 当前标识 -->
        <div
          v-if="isCurrentPlan(plan)"
          class="absolute right-0 top-0 rounded-bl-xl bg-brand-500 px-3 py-1 text-xs font-bold text-white"
        >
          当前方案
        </div>

        <div class="flex items-start justify-between">
          <div>
            <h3 class="text-lg font-extrabold text-ink">{{ plan.name }}</h3>
            <p class="mt-0.5 text-xs text-slate-500">{{ plan.description }}</p>
          </div>
          <div class="text-right">
            <p class="text-xl font-extrabold text-brand-600">{{ priceLabel(plan) }}</p>
          </div>
        </div>

        <!-- 权益清单 -->
        <ul class="mt-3 space-y-2 text-sm">
          <li class="flex items-center gap-2">
            <span class="flex h-5 w-5 items-center justify-center rounded-full text-xs"
                  :class="isUnlimited(plan.aiDailyLimit) ? 'bg-mint-100 text-mint-600' : 'bg-brand-50 text-brand-500'">✓</span>
            <span class="text-ink">AI 对话：{{ limitLabel(plan.aiDailyLimit, '次') }}</span>
          </li>
          <li class="flex items-center gap-2">
            <span class="flex h-5 w-5 items-center justify-center rounded-full text-xs"
                  :class="isUnlimited(plan.practiceDailyLimit) ? 'bg-mint-100 text-mint-600' : 'bg-brand-50 text-brand-500'">✓</span>
            <span class="text-ink">练习：{{ limitLabel(plan.practiceDailyLimit, '道') }}</span>
          </li>
          <li class="flex items-center gap-2">
            <span class="flex h-5 w-5 items-center justify-center rounded-full bg-brand-50 text-xs text-brand-500">✓</span>
            <span class="text-ink">错题本：{{ isUnlimited(plan.maxMistakeStorage) ? '不限' : `${plan.maxMistakeStorage} 道` }}</span>
          </li>
          <li v-if="plan.hasAdvancedAnalytics" class="flex items-center gap-2">
            <span class="flex h-5 w-5 items-center justify-center rounded-full bg-mint-100 text-xs text-mint-600">✓</span>
            <span class="text-ink">高级学习分析</span>
          </li>
          <li v-if="plan.hasFamilySharing" class="flex items-center gap-2">
            <span class="flex h-5 w-5 items-center justify-center rounded-full bg-mint-100 text-xs text-mint-600">✓</span>
            <span class="text-ink">家庭共享</span>
          </li>
          <li v-if="plan.hasOcrPremium" class="flex items-center gap-2">
            <span class="flex h-5 w-5 items-center justify-center rounded-full bg-mint-100 text-xs text-mint-600">✓</span>
            <span class="text-ink">高级 OCR 识别</span>
          </li>
        </ul>

        <!-- 按钮：内测期全免费不限额，付费入口隐藏（2026-10-20 口径） -->
        <button
          v-if="BETA_FREE_ONLY && !isCurrentPlan(plan) && plan.slug !== 'free' && plan.slug !== 'enterprise'"
          type="button"
          class="mt-4 w-full rounded-xl bg-mint-50 py-2.5 text-sm font-bold text-mint-600"
          disabled
        >
          内测期间免费开放
        </button>
        <button
          v-else-if="!BETA_FREE_ONLY && !isCurrentPlan(plan) && plan.slug !== 'free' && plan.slug !== 'enterprise'"
          type="button"
          class="mt-4 w-full rounded-xl py-2.5 text-sm font-bold transition"
          :class="checkoutLoading === plan.slug
            ? 'bg-slate-200 text-slate-500'
            : 'bg-brand-500 text-white shadow-soft active:bg-brand-600'"
          :disabled="checkoutLoading === plan.slug"
          @click="handleCheckout(plan)"
        >
          {{ checkoutLoading === plan.slug ? '创建订单中…' : '立即订阅' }}
        </button>
        <button
          v-else-if="plan.slug === 'enterprise'"
          type="button"
          class="mt-4 w-full rounded-xl bg-slate-100 py-2.5 text-sm font-bold text-slate-500"
          disabled
        >
          联系商务
        </button>
      </div>
    </section>

    <!-- 加载/错误 -->
    <div v-if="loading" class="mt-8 text-center text-sm text-slate-400">加载中…</div>
    <div v-if="error" class="mx-4 mt-4 rounded-2xl bg-red-50 p-3 text-center text-sm text-red-500">{{ error }}</div>
  </div>
</template>

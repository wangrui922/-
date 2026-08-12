<script setup lang="ts">
/**
 * 登录页：手机号 + 验证码。
 *
 * 契约（backend/app/api/auth.py）：
 *   POST /api/auth/code   { phone }        -> { sent, devCode }
 *   POST /api/auth/login  { phone, code }  -> { access, refresh, accessToken,
 *                                               refreshToken, tokenType, expiresIn,
 *                                               isNewUser, user: { userId, studentId, ... } }
 *
 * 验证码为随机 6 位数字（5min 过期）；dev 环境 /api/auth/code 回带 devCode 自动填充。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { post, toMessage } from '@/api/http'
import { login } from '@/stores/auth'

interface UserBrief {
  userId: string
  studentId: string | null
  phone: string
  nickname: string | null
  role: string
}

interface LoginResult {
  access: string
  refresh: string
  accessToken?: string
  refreshToken?: string
  tokenType?: string
  expiresIn?: number
  isNewUser?: boolean
  user?: UserBrief | null
}

interface SendCodeResult {
  sent: boolean
  devCode?: string | null
}

const router = useRouter()
const route = useRoute()

const phone = ref<string>('')
const code = ref<string>('') // 验证码（dev 环境自动填充）
const loading = ref<boolean>(false)
const sending = ref<boolean>(false)
const errorMsg = ref<string>('')
const tipMsg = ref<string>('')

const phoneValid = computed<boolean>(() => /^1[3-9]\d{9}$/.test(phone.value.trim()))
const canSubmit = computed<boolean>(
  () => phoneValid.value && code.value.trim().length > 0 && !loading.value,
)

/** 下发验证码（随机 6 位，5min 过期；dev 环境回带 devCode 自动填充）。 */
async function handleSendCode(): Promise<void> {
  if (!phoneValid.value || sending.value) return
  sending.value = true
  errorMsg.value = ''
  tipMsg.value = ''
  try {
    const data = await post<SendCodeResult>('/auth/code', { phone: phone.value.trim() })
    if (data.devCode) {
      code.value = data.devCode
      tipMsg.value = `开发环境验证码：${data.devCode}`
    } else {
      tipMsg.value = '验证码已发送，请查收短信'
    }
  } catch (error) {
    errorMsg.value = toMessage(error, '验证码发送失败')
  } finally {
    sending.value = false
  }
}

/** 登录并跳转（默认去 AI 老师页，带 redirect 时回跳原页）。 */
async function handleLogin(): Promise<void> {
  if (!canSubmit.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await post<LoginResult>('/auth/login', {
      phone: phone.value.trim(),
      code: code.value.trim(),
    })
    const token = data.access || data.accessToken || ''
    if (!token) throw new Error('登录返回缺少令牌')

    login(token, {
      refresh: data.refresh || data.refreshToken,
      studentId: data.user?.studentId ?? undefined,
    })

    const redirect = route.query.redirect
    await router.replace(typeof redirect === 'string' && redirect ? redirect : '/dashboard')
  } catch (error) {
    errorMsg.value = toMessage(error, '登录失败，请重试')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen w-full flex-col justify-center px-6 py-10">
    <header class="mb-8 text-center">
      <div
        class="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-500 text-2xl font-bold text-white"
      >
        AI
      </div>
      <h1 class="text-xl font-bold text-slate-800">AI 数学教练</h1>
      <p class="mt-1 text-sm text-slate-500">不直接给答案，陪你一步步想明白</p>
    </header>

    <form class="space-y-4" @submit.prevent="handleLogin">
      <div>
        <label class="mb-1.5 block text-sm font-medium text-slate-600" for="phone">手机号</label>
        <input
          id="phone"
          v-model="phone"
          type="tel"
          inputmode="numeric"
          maxlength="11"
          placeholder="请输入 11 位手机号"
          class="h-12 w-full rounded-xl border border-slate-200 bg-slate-50 px-4 text-base outline-none focus:border-brand-500 focus:bg-white"
        />
      </div>

      <div>
        <label class="mb-1.5 block text-sm font-medium text-slate-600" for="code">验证码</label>
        <div class="flex gap-2">
          <input
            id="code"
            v-model="code"
            type="text"
            inputmode="numeric"
            maxlength="6"
            placeholder="请输入验证码"
            class="h-12 min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50 px-4 text-base outline-none focus:border-brand-500 focus:bg-white"
          />
          <button
            type="button"
            class="btn-ghost h-12 shrink-0"
            :disabled="!phoneValid || sending"
            @click="handleSendCode"
          >
            {{ sending ? '发送中' : '获取验证码' }}
          </button>
        </div>
      </div>

      <p v-if="tipMsg" class="text-sm text-brand-600">{{ tipMsg }}</p>
      <p v-if="errorMsg" class="text-sm text-red-500">{{ errorMsg }}</p>

      <button type="submit" class="btn-primary h-12 w-full text-base" :disabled="!canSubmit">
        {{ loading ? '登录中…' : '登录' }}
      </button>
    </form>

    <p class="mt-6 text-center text-xs text-slate-400">
      未注册的手机号将自动创建账号
    </p>
  </div>
</template>

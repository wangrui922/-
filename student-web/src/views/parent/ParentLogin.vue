<script setup lang="ts">
/**
 * 家长登录页（手机号 + 验证码）。
 *
 * 复用学生端 Login.vue 的交互逻辑，但 role=parent，登录后跳 /parent/children。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { sendParentCode, parentLogin } from '@/api/family'
import { toMessage } from '@/api/http'
import { login } from '@/stores/auth'

const router = useRouter()

const phone = ref('')
const code = ref('')
const loading = ref(false)
const sending = ref(false)
const errorMsg = ref('')
const tipMsg = ref('')

const phoneValid = computed(() => /^1[3-9]\d{9}$/.test(phone.value.trim()))
const canSubmit = computed(() => phoneValid.value && code.value.trim().length > 0 && !loading.value)

async function handleSendCode() {
  if (!phoneValid.value) {
    errorMsg.value = '请输入正确的手机号'
    return
  }
  sending.value = true
  errorMsg.value = ''
  try {
    const res = await sendParentCode(phone.value.trim())
    if (res.devCode) {
      tipMsg.value = `开发验证码：${res.devCode}`
    } else {
      tipMsg.value = '验证码已发送，请注意查收'
    }
  } catch (err) {
    errorMsg.value = toMessage(err, '发送失败')
  } finally {
    sending.value = false
  }
}

async function handleLogin() {
  if (!canSubmit.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const res = (await parentLogin(phone.value.trim(), code.value.trim())) as Record<string, unknown>
    const token = (res.accessToken || res.access || '') as string
    if (!token) {
      errorMsg.value = '登录异常，未返回令牌'
      return
    }
    const userObj = res.user as Record<string, unknown> | undefined
    login(token, {
      refresh: (res.refreshToken || res.refresh || '') as string,
      studentId: (userObj?.studentId as string | undefined) || undefined,
    })
    void router.replace({ name: 'parent-children' })
  } catch (err) {
    errorMsg.value = toMessage(err, '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-brand-50 to-white px-6">
    <div class="w-full max-w-sm">
      <div class="mb-8 text-center">
        <h1 class="text-2xl font-extrabold text-ink">家长入口</h1>
        <p class="mt-1 text-sm text-slate-500">绑定孩子，查看学习进展</p>
      </div>

      <div class="space-y-4 rounded-3xl bg-white p-6 shadow-soft">
        <div>
          <label class="mb-1 block text-xs font-semibold text-slate-500">手机号</label>
          <input
            v-model="phone"
            type="tel"
            maxlength="11"
            placeholder="请输入手机号"
            class="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-brand-400 focus:bg-white focus:ring-2 focus:ring-brand-100"
            @keyup.enter="handleLogin"
          >
        </div>

        <div>
          <label class="mb-1 block text-xs font-semibold text-slate-500">验证码</label>
          <div class="flex gap-2">
            <input
              v-model="code"
              type="text"
              maxlength="6"
              placeholder="6 位验证码"
              class="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm outline-none focus:border-brand-400 focus:bg-white focus:ring-2 focus:ring-brand-100"
              @keyup.enter="handleLogin"
            >
            <button
              type="button"
              class="shrink-0 rounded-xl bg-brand-100 px-4 py-2 text-sm font-bold text-brand-600 transition active:bg-brand-200 disabled:opacity-50"
              :disabled="sending || !phoneValid"
              @click="handleSendCode"
            >
              {{ sending ? '发送中…' : '获取验证码' }}
            </button>
          </div>
        </div>

        <button
          type="button"
          class="w-full rounded-xl bg-brand-500 py-3 text-sm font-bold text-white shadow-soft transition active:bg-brand-600 disabled:bg-slate-200 disabled:text-slate-400"
          :disabled="!canSubmit"
          @click="handleLogin"
        >
          {{ loading ? '登录中…' : '登录 / 注册' }}
        </button>

        <p v-if="tipMsg" class="text-center text-xs text-brand-600">{{ tipMsg }}</p>
        <p v-if="errorMsg" class="text-center text-xs text-red-500">{{ errorMsg }}</p>
      </div>

      <p class="mt-6 text-center text-xs text-slate-400">
        首次登录将自动注册家长账号
      </p>
    </div>
  </div>
</template>

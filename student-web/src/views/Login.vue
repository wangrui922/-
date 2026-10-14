<script setup lang="ts">
/**
 * 登录页：邀请制激活码优先，手机号验证码作为备用入口。
 *
 * 视觉升级（卡通插画精简版）：
 *  - 中心大幅 Q 版 3D 小玥老师欢迎图（tutor-welcome.png）
 *  - 封面上方 4 个 SVG 卡通装饰：星星/算式气泡
 *  - 梦幻渐变背景 + 玻璃拟态表单
 *
 * 契约（backend/app/api/auth.py）：
 *   POST /api/auth/activation/activate { code, nickname, pin } -> { access, refresh, ... }
 *   POST /api/auth/code                 { phone }               -> { sent, devCode }
 *   POST /api/auth/login                { phone, code }         -> { access, refresh, ... }
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { post, toMessage } from '@/api/http'
import { login } from '@/stores/auth'
import tutorWelcome from '@/assets/tutor-welcome.png'

interface UserBrief {
  userId: string
  studentId: string | null
  phone: string | null
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
const code = ref<string>('')
const activationCode = ref<string>('')
const nickname = ref<string>('')
const pin = ref<string>('')
const mode = ref<'activation' | 'phone'>('activation')
const loading = ref<boolean>(false)
const sending = ref<boolean>(false)
const errorMsg = ref<string>('')
const tipMsg = ref<string>('')

const phoneValid = computed<boolean>(() => /^1[3-9]\d{9}$/.test(phone.value.trim()))
const phoneCanSubmit = computed<boolean>(
  () => phoneValid.value && code.value.trim().length > 0 && !loading.value,
)
const activationCodeValid = computed<boolean>(() => /^YY(?:-[A-Z0-9]{4}){4}$/.test(activationCode.value))
const activationCanSubmit = computed<boolean>(
  () => activationCodeValid.value && /^\d{6}$/.test(pin.value) && !loading.value,
)

function formatActivationCode(): void {
  const compact = activationCode.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 18)
  const prefix = compact.slice(0, 2)
  const groups = Array.from({ length: 4 }, (_, index) => compact.slice(2 + index * 4, 6 + index * 4))
    .filter(Boolean)
  activationCode.value = [prefix, ...groups].filter(Boolean).join('-')
}

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

async function finishLogin(data: LoginResult): Promise<void> {
  const token = data.access || data.accessToken || ''
  if (!token) throw new Error('登录返回缺少令牌')

  login(token, {
    refresh: data.refresh || data.refreshToken,
    studentId: data.user?.studentId ?? undefined,
  })

  const redirect = route.query.redirect
  await router.replace(typeof redirect === 'string' && redirect ? redirect : '/dashboard')
}

async function handlePhoneLogin(): Promise<void> {
  if (!phoneCanSubmit.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await post<LoginResult>('/auth/login', {
      phone: phone.value.trim(),
      code: code.value.trim(),
    })
    await finishLogin(data)
  } catch (error) {
    errorMsg.value = toMessage(error, '登录失败，请重试')
  } finally {
    loading.value = false
  }
}

async function handleActivationLogin(): Promise<void> {
  if (!activationCanSubmit.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await post<LoginResult>('/auth/activation/activate', {
      code: activationCode.value,
      nickname: nickname.value.trim() || undefined,
      pin: pin.value,
    })
    await finishLogin(data)
  } catch (error) {
    errorMsg.value = toMessage(error, '暂时进不去，请检查激活码和个人密码')
  } finally {
    loading.value = false
  }
}

async function handleSubmit(): Promise<void> {
  if (mode.value === 'activation') {
    await handleActivationLogin()
    return
  }
  await handlePhoneLogin()
}
</script>

<template>
  <div class="relative flex min-h-screen w-full flex-col overflow-hidden bg-gradient-to-br from-rose-50 via-fuchsia-50 to-violet-100">
    <!-- 背景光斑 -->
    <div class="pointer-events-none absolute -left-16 -top-10 h-56 w-56 rounded-full bg-brand-200/50 blur-3xl" />
    <div class="pointer-events-none absolute -right-12 top-40 h-56 w-56 rounded-full bg-coral-400/30 blur-3xl" />
    <div class="pointer-events-none absolute bottom-32 left-1/4 h-48 w-48 rounded-full bg-mint-400/30 blur-3xl" />
    <div class="pointer-events-none absolute right-1/4 bottom-1/3 h-40 w-40 rounded-full bg-amber-300/30 blur-3xl" />

    <!-- 卡通装饰：仅在封面上方 4 个图标，不超过 4 个 -->
    <!-- 左上：黄色星星 -->
    <div class="pointer-events-none absolute left-3 top-16 animate-bounce-soft drop-shadow-md" style="animation-delay: 0s">
      <svg width="40" height="40" viewBox="0 0 44 44" xmlns="http://www.w3.org/2000/svg">
        <path d="M22 3l5.4 11 12 1.7-8.7 8.5 2 11.8L22 30 11.3 36l2-11.8-8.7-8.5 12-1.7z" fill="#FFC24B" stroke="#E89A1F" stroke-width="1.5" stroke-linejoin="round"/>
      </svg>
    </div>

    <!-- 右上：珊瑚色算式气泡 2+2=4 -->
    <div class="pointer-events-none absolute right-2 top-20 animate-float drop-shadow-md" style="animation-delay: 0.4s">
      <svg width="68" height="38" viewBox="0 0 72 40" xmlns="http://www.w3.org/2000/svg">
        <ellipse cx="36" cy="20" rx="34" ry="16" fill="#FF8A65" stroke="#F4511E" stroke-width="1.5"/>
        <text x="36" y="26" text-anchor="middle" font-size="15" font-weight="800" fill="white" font-family="system-ui">2+2=4</text>
      </svg>
    </div>

    <!-- 左中偏上（封面左侧）：紫色小星星 -->
    <div class="pointer-events-none absolute left-2 top-[200px] animate-bounce-soft drop-shadow-md" style="animation-delay: 0.9s">
      <svg width="30" height="30" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
        <path d="M16 2l3.9 8 8.6 1.2-6.2 6.1 1.5 8.4L16 22l-7.8 3.7 1.5-8.4-6.2-6.1 8.6-1.2z" fill="#9B72FF" stroke="#7C5CFC" stroke-width="1.4" stroke-linejoin="round"/>
      </svg>
    </div>

    <!-- 右中偏上（封面右侧）：紫色算式气泡 3+1=4 -->
    <div class="pointer-events-none absolute right-1 top-[210px] animate-bounce-soft drop-shadow-md" style="animation-delay: 1.4s">
      <svg width="64" height="36" viewBox="0 0 72 40" xmlns="http://www.w3.org/2000/svg">
        <ellipse cx="36" cy="20" rx="34" ry="16" fill="#9B72FF" stroke="#7C5CFC" stroke-width="1.5"/>
        <text x="36" y="26" text-anchor="middle" font-size="14" font-weight="800" fill="white" font-family="system-ui">3+1=4</text>
      </svg>
    </div>

    <div class="relative flex flex-1 flex-col justify-center px-7 py-10">
      <header class="mb-7 text-center">
        <!-- 大幅 Q 版 3D 小玥老师欢迎图 -->
        <div class="relative mx-auto h-56 w-56 overflow-hidden rounded-[2rem] border-4 border-white shadow-float animate-float">
          <div class="absolute inset-0 rounded-[2rem] bg-gradient-to-br from-amber-200 via-white to-brand-100" />
          <img
            :src="tutorWelcome"
            alt="小玥老师"
            class="relative block h-full w-full object-contain"
          />
          <!-- 欢迎光环（粉紫光晕动画） -->
          <div class="pointer-events-none absolute -inset-3 rounded-[2.4rem] border-2 border-white/40 animate-pulse-ring" />
        </div>
        <h1 class="mt-5 text-2xl font-extrabold title-gradient">小玥老师 · AI 数学教练</h1>
        <p class="mt-2 text-sm font-medium text-ink-soft">不直接给答案，陪你一步步想明白 ✨</p>
      </header>

      <form class="space-y-4" @submit.prevent="handleSubmit">
        <div class="grid grid-cols-2 rounded-xl bg-white/60 p-1 shadow-card" role="tablist" aria-label="登录方式">
          <button
            type="button"
            class="h-10 rounded-lg text-sm font-bold transition"
            :class="mode === 'activation' ? 'bg-brand-500 text-white shadow-sm' : 'text-ink-soft'"
            role="tab"
            :aria-selected="mode === 'activation'"
            @click="mode = 'activation'"
          >
            激活码进入
          </button>
          <button
            type="button"
            class="h-10 rounded-lg text-sm font-bold transition"
            :class="mode === 'phone' ? 'bg-brand-500 text-white shadow-sm' : 'text-ink-soft'"
            role="tab"
            :aria-selected="mode === 'phone'"
            @click="mode = 'phone'"
          >
            手机号登录
          </button>
        </div>

        <template v-if="mode === 'activation'">
          <div>
            <label class="mb-1.5 block text-sm font-semibold text-ink-soft" for="activation-code">激活码</label>
            <input
              id="activation-code"
              v-model="activationCode"
              type="text"
              inputmode="text"
              autocapitalize="characters"
              autocomplete="off"
              maxlength="22"
              placeholder="YY-ABCD-EFGH-IJKL-MNOP"
              class="h-12 w-full rounded-2xl border border-white/70 bg-white/80 px-4 font-mono text-base font-bold tracking-wide text-ink shadow-card outline-none backdrop-blur transition focus:border-brand-400 focus:bg-white"
              @input="formatActivationCode"
            />
          </div>

          <div>
            <label class="mb-1.5 block text-sm font-semibold text-ink-soft" for="nickname">怎么称呼你</label>
            <input
              id="nickname"
              v-model="nickname"
              type="text"
              maxlength="50"
              placeholder="第一次用时填写，以后不用再填"
              class="h-12 w-full rounded-2xl border border-white/70 bg-white/80 px-4 text-base font-medium text-ink shadow-card outline-none backdrop-blur transition focus:border-brand-400 focus:bg-white"
            />
          </div>

          <div>
            <label class="mb-1.5 block text-sm font-semibold text-ink-soft" for="activation-pin">6 位个人密码</label>
            <input
              id="activation-pin"
              v-model="pin"
              type="password"
              inputmode="numeric"
              autocomplete="current-password"
              maxlength="6"
              placeholder="第一次设置，以后用它回来"
              class="h-12 w-full rounded-2xl border border-white/70 bg-white/80 px-4 text-base font-medium text-ink shadow-card outline-none backdrop-blur transition focus:border-brand-400 focus:bg-white"
            />
          </div>
        </template>

        <template v-else>
          <div>
          <label class="mb-1.5 block text-sm font-semibold text-ink-soft" for="phone">手机号</label>
          <input
            id="phone"
            v-model="phone"
            type="tel"
            inputmode="numeric"
            maxlength="11"
            placeholder="请输入 11 位手机号"
            class="h-12 w-full rounded-2xl border border-white/70 bg-white/80 px-4 text-base font-medium text-ink shadow-card outline-none backdrop-blur transition focus:border-brand-400 focus:bg-white"
          />
          </div>

          <div>
          <label class="mb-1.5 block text-sm font-semibold text-ink-soft" for="code">验证码</label>
          <div class="flex gap-2">
            <input
              id="code"
              v-model="code"
              type="text"
              inputmode="numeric"
              maxlength="6"
              placeholder="请输入验证码"
              class="h-12 min-w-0 flex-1 rounded-2xl border border-white/70 bg-white/80 px-4 text-base font-medium text-ink shadow-card outline-none backdrop-blur transition focus:border-brand-400 focus:bg-white"
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
        </template>

        <p v-if="tipMsg" class="rounded-2xl bg-white/70 px-4 py-2 text-center text-sm font-semibold text-brand-600 shadow-card backdrop-blur">
          🎁 {{ tipMsg }}
        </p>
        <p v-if="errorMsg" class="rounded-2xl bg-red-50 px-4 py-2 text-center text-sm font-medium text-red-500">
          {{ errorMsg }}
        </p>

        <button type="submit" class="btn-primary h-12 w-full text-base" :disabled="mode === 'activation' ? !activationCanSubmit : !phoneCanSubmit">
          {{ loading ? '进入中…' : '进入学习 →' }}
        </button>
      </form>

      <p class="mt-6 text-center text-xs font-medium text-ink-faint">
        激活码仅限受邀同学使用，请妥善保管个人密码
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 监护人同意弹窗（V6.5 合规闸门 · 前端引导）。
 *
 * 仿 components/report/ShareLinkDialog.vue 的范式：fixed inset-0 z-50
 * 遮罩 + fade transition + props.open / emit('close')（项目无统一 modal
 * 组件，各弹窗各自仿这一范式）。
 *
 * 两个拉起来源：
 *   1. stores/consent.ts 预查发现未同意（checkStatus → required → openDialog）；
 *   2. api/http.ts 响应拦截兜底：练习类 POST 返 403 且话术含「监护人同意」
 *      （深链 / 游戏页等绕过预查的入口开练时被接住）。
 *
 * 铁律：关闭 ≠ 同意。点 ✕ / 点遮罩只是关掉引导，服务端闸门仍然生效，
 * 下次开练照旧 403、照旧弹窗。
 */
import { ref, watch } from 'vue'

import { consentState, submitAndGrant } from '@/stores/consent'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

/** 监护人手机号（仅用于同意留痕）。 */
const phone = ref<string>('')

// 每次打开时重置输入与上一条失败文案，避免残留信息造成误解
watch(
  () => props.open,
  (isOpen: boolean) => {
    if (isOpen) {
      phone.value = ''
      consentState.error = ''
    }
  },
)

/** 提交同意：手机号长度前后端口径一致（5-20），非法就地提示不打请求。 */
async function handleSubmit(): Promise<void> {
  await submitAndGrant(phone.value)
  // 成败由 store 收口：成功 → granted 且 dialogOpen=false，弹窗自动收起；
  // 失败 → consentState.error 有文案，弹窗保持打开可重试。
}
</script>

<template>
  <transition name="fade">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-end justify-center bg-black/40 px-4 pb-6 pt-12"
      @click.self="emit('close')"
    >
      <div class="w-full max-w-app rounded-2xl bg-white p-5 shadow-xl">
        <header class="mb-3 flex items-center justify-between">
          <h3 class="text-base font-bold text-slate-800">监护人同意</h3>
          <button type="button" class="text-slate-400" aria-label="关闭" @click="emit('close')">
            ✕
          </button>
        </header>

        <p class="mb-3 text-sm leading-relaxed text-slate-500">
          使用练习功能需要监护人同意。请监护人填写手机号并确认同意，手机号仅用于同意留痕。
        </p>

        <input
          v-model="phone"
          type="tel"
          inputmode="tel"
          maxlength="20"
          placeholder="监护人手机号"
          aria-label="监护人手机号"
          class="w-full min-w-0 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600"
        />

        <p
          v-if="consentState.error"
          class="mt-3 rounded-xl bg-red-50 px-3 py-2 text-sm leading-relaxed text-red-500"
        >
          {{ consentState.error }}
        </p>

        <button
          type="button"
          class="btn-primary mt-4 w-full"
          :disabled="consentState.submitting || phone.trim().length === 0"
          @click="handleSubmit"
        >
          {{ consentState.submitting ? '提交中…' : '已阅读并同意' }}
        </button>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

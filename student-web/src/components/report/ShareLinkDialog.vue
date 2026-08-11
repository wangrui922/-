<script setup lang="ts">
/**
 * 家长周报分享弹窗（Epic6 · T6）。
 *
 * 生成周报后弹出，展示可直接转发给家长的链接，一键复制。
 * 重新生成不会换 token，因此链接稳定，可反复复制。
 */
import { computed, ref, watch } from 'vue'

import type { GenerateReportData } from '@/types/report'

const props = defineProps<{
  open: boolean
  data: GenerateReportData | null
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const copied = ref(false)
const url = computed<string>(() => props.data?.share_url || '')

// 每次弹窗打开时重置「已复制」状态
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) copied.value = false
  },
)

async function handleCopy(): Promise<void> {
  if (!url.value) return
  try {
    await navigator.clipboard.writeText(url.value)
    copied.value = true
    window.setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch {
    // 部分浏览器不支持 clipboard，降级为提示手动复制
    copied.value = false
  }
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
          <h3 class="text-base font-bold text-slate-800">家长周报已生成</h3>
          <button type="button" class="text-slate-400" aria-label="关闭" @click="emit('close')">
            ✕
          </button>
        </header>

        <p class="mb-3 text-sm text-slate-500">
          把下面这条链接发给孩子家长，家长打开就能看本周学习小结（不需要登录）。
        </p>

        <div class="flex items-stretch gap-2">
          <input
            :value="url"
            readonly
            class="min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600"
          />
          <button type="button" class="btn-primary shrink-0 px-4" @click="handleCopy">
            {{ copied ? '已复制' : '复制' }}
          </button>
        </div>

        <p v-if="props.data?.cached" class="mt-3 text-xs text-slate-400">
          已存在本周的周报，直接复用了上次的链接。
        </p>

        <button type="button" class="btn-ghost mt-4 w-full" @click="emit('close')">
          完成
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

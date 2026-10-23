<script setup lang="ts">
/**
 * 监护人同意指引条（V6.5 合规闸门 · B02 体验收尾 · 孩子端）。
 *
 * 挂在 StudentLayout（全部孩子端页面常驻），据 stores/consent.ts 的
 * 三态展示，让孩子**随时看得见**自己卡在哪：
 *   required  「需要监护人同意才能练习」+ 一键唤起同意流程（openDialog）
 *   stale     「监护人规则已更新，需重新确认」+ 重新同意入口（同一弹窗）
 *   granted / unknown  不渲染（unknown 时还没查到，不误显）
 *
 * 与 GuardianConsentDialog.vue 的分工：dialog 是「当场完成同意」的模态，
 * 本指引条是「持久告知状态」的横幅——用户关掉 dialog 后，条子还在，
 * 不会关掉引导就忘了为什么练不了。
 *
 * 挂载范围仅孩子端（StudentLayout）：家长专区（/parent/*）不查学生同意态，
 * 不会误显（见 router 守卫对 isParentArea 的处理）。
 */
import { consentState, openDialog } from '@/stores/consent'

/** 是否处于「需要行动」的两态（required / stale 才渲染）。 */
const needsAction = (status: string): boolean => status === 'required' || status === 'stale'
</script>

<template>
  <section
    v-if="needsAction(consentState.status)"
    class="mx-3 mt-2 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3"
    role="status"
    aria-live="polite"
  >
    <div class="flex items-start gap-3">
      <span class="mt-0.5 shrink-0 text-lg" aria-hidden="true">🛡️</span>
      <div class="min-w-0 flex-1">
        <p class="text-sm font-bold text-amber-900">
          {{
            consentState.status === 'stale'
              ? '监护人规则已更新，需重新确认'
              : '需要监护人同意才能练习'
          }}
        </p>
        <p class="mt-1 text-xs leading-relaxed text-amber-700">
          {{
            consentState.status === 'stale'
              ? '监护人同意了新的使用规则后才能继续练习，请监护人再次填写手机号并确认。'
              : '练习功能需要监护人同意。请监护人填写手机号并确认同意，手机号仅用于同意留痕。'
          }}
        </p>
        <button
          type="button"
          class="mt-2 inline-flex h-8 items-center rounded-full bg-amber-500 px-4 text-xs font-bold text-white shadow-soft transition active:scale-95"
          @click="openDialog"
        >
          {{ consentState.status === 'stale' ? '重新确认' : '去获得监护人同意' }}
        </button>
      </div>
    </div>
  </section>
</template>

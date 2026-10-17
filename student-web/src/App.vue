<script setup lang="ts">
/** 根组件：移动端画布容器 + 路由出口 + 全局监护人同意弹窗。背景由 body 全局渐变承载。 */
import GuardianConsentDialog from '@/components/GuardianConsentDialog.vue'
import { closeDialog, consentState } from '@/stores/consent'
</script>

<template>
  <div class="min-h-screen w-full overflow-x-hidden">
    <div class="mx-auto min-h-screen w-full max-w-app">
      <router-view />
    </div>
    <!-- 监护人同意全局兜底弹窗：预查未同意 / 403 门控话术命中时拉起（V6.5） -->
    <GuardianConsentDialog :open="consentState.dialogOpen" @close="closeDialog" />
  </div>
</template>

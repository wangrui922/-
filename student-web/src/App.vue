<script setup lang="ts">
/** 根组件：只做移动端画布容器 + 路由出口。 */
</script>

<template>
  <div class="min-h-screen w-full overflow-x-hidden bg-slate-100">
    <div class="mx-auto min-h-screen w-full max-w-app bg-white shadow-sm">
      <router-view />
    </div>
  </div>
</template>

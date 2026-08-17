<script setup lang="ts">
/** 根组件：移动端画布容器 + 路由出口。背景由 body 全局渐变承载。 */
</script>

<template>
  <div class="min-h-screen w-full overflow-x-hidden">
    <div class="mx-auto min-h-screen w-full max-w-app">
      <router-view />
    </div>
  </div>
</template>

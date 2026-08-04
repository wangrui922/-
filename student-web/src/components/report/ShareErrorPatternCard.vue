<script setup lang="ts">
/**
 * 分享页 · 错因分布卡片（Epic6 · T7 · 卡片三）。
 *
 * 直接复用 T04 的 ErrorPatternChart（其 props 与后端 error_patterns 同构），
 * 零改造。薄弱计算口径与学习画像看板完全一致。
 */
import ErrorPatternChart from '@/components/profile/ErrorPatternChart.vue'
import type { ErrorPatternData } from '@/types/profile'

defineProps<{
  data: ErrorPatternData | null
}>()
</script>

<template>
  <ErrorPatternChart :data="data" :loading="false" :error="''" />
</template>

<script setup lang="ts">
/**
 * 小玥老师头像：AI 老师的统一形象（复用「玥玥」IP 思考姿势）。
 *
 * 全局唯一真源——所有 AI 老师出现的位置都用它，避免形象分裂。
 * 改 IP 形象只动这一处（替换 src/assets 里的图 + 这里）。
 *
 * 视觉：透明底图 + 暖冷渐变背景卡（不管父容器是什么背景都有清晰轮廓）。
 */
import tutorImg from '@/assets/tutor-yueyue.png'

withDefaults(defineProps<{ size?: number }>(), { size: 32 })
</script>

<template>
  <span
    class="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full border-2 border-white shadow-sm"
    :class="[
      'bg-[linear-gradient(135deg,#fff1e6_0%,#eaf2ff_100%)]',
    ]"
    :style="{ width: size + 'px', height: size + 'px' }"
  >
    <img
      :src="tutorImg"
      alt="小玥老师"
      class="block h-full w-full object-cover"
    />
  </span>
</template>

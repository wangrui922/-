<script setup lang="ts">
/**
 * 小玥老师头像：AI 老师的统一形象（复用「玥玥」IP 思考姿势）。
 *
 * 全局唯一真源——所有 AI 老师出现的位置都用它，避免形象分裂。
 * 改 IP 形象只动这一处（替换 src/assets 里的图 + 这里）。
 *
 * 视觉：
 *  - 柔和小圆角矩形（不用圆形 mask）→ 全身图不被切
 *  - object-contain + 留白 → 图能完整呈现
 *  - 渐变背景 + 白边 → 任何底色下都有清晰轮廓
 */
import tutorImg from '@/assets/tutor-yueyue-v2.png'

withDefaults(defineProps<{ size?: number }>(), { size: 40 })
</script>

<template>
  <span
    class="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-2xl border-2 border-white shadow-soft bg-gradient-to-br from-amber-50 to-sky-50"
    :style="{ width: size + 'px', height: size + 'px' }"
  >
    <img
      :src="tutorImg"
      alt="小玥老师"
      class="block h-full w-full object-contain p-0.5"
    />
  </span>
</template>

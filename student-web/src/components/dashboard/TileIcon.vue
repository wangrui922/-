<script setup lang="ts">
/**
 * 学习画像首页 4 个趣味 tile 的「全息科技风」SVG 图标。
 *
 * 设计原则（与底部 Tab 线性图标、全局霓虹基调统一）：
 *  - 全部用「线框 + 发光描边」呈现全息科技感，不再用拟物色块。
 *  - 每个 icon 的描边色 = 所在 tile 的主色（品牌四色），发光 drop-shadow 跟随 currentColor。
 *  - 内置轻量微动画（twinkle / SMIL / radar-spin），无需额外 JS。
 *  - 错题库图标刻意与错题本页的宝石 SVG 同形，强化「宝藏」心智。
 *
 * props:
 *  - kind:  'practice' | 'ai' | 'mistakes' | 'dashboard'
 */
defineProps<{ kind: 'practice' | 'ai' | 'mistakes' | 'dashboard' }>()
</script>

<template>
  <!-- ① 去练习：全息六边形「关卡芯片」+ 启动三角 -->
  <svg
    v-if="kind === 'practice'"
    class="h-7 w-7"
    style="color: #7c5cfc; filter: drop-shadow(0 0 3px currentColor)"
    viewBox="0 0 32 32"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linejoin="round"
    stroke-linecap="round"
  >
    <path d="M16 4.5 L25.5 10.5 L25.5 21.5 L16 27.5 L6.5 21.5 L6.5 10.5 Z" />
    <path d="M16 4.5 L16 27.5 M6.5 10.5 L25.5 21.5 M25.5 10.5 L6.5 21.5" opacity="0.4" stroke-width="1" />
    <path d="M13.5 12 L13.5 20 L22 16 Z" fill="#FFFFFF" stroke="none" opacity="0.92" />
    <g class="animate-twinkle" style="color: #ffd27a; filter: drop-shadow(0 0 3px currentColor)">
      <path d="M26 5 L27 7.5 L29.5 8.5 L27 9.5 L26 12 L25 9.5 L22.5 8.5 L25 7.5 Z" fill="currentColor" stroke="none" />
    </g>
  </svg>

  <!-- ② 问小玥老师：对话泡 + 神经网络节点（AI 思考中） -->
  <svg
    v-else-if="kind === 'ai'"
    class="h-7 w-7"
    style="color: #ff7043; filter: drop-shadow(0 0 3px currentColor)"
    viewBox="0 0 32 32"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linejoin="round"
    stroke-linecap="round"
  >
    <path d="M7 8 Q7 5 10 5 L22 5 Q25 5 25 8 L25 19 Q25 22 22 22 L14 22 L10 26 L11 22 L10 22 Q7 22 7 19 Z" />
    <line x1="12" y1="12" x2="18" y2="10" stroke-width="1.1" opacity="0.7" />
    <line x1="18" y1="10" x2="16" y2="17" stroke-width="1.1" opacity="0.7" />
    <line x1="12" y1="12" x2="16" y2="17" stroke-width="1.1" opacity="0.7" />
    <circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none" />
    <circle cx="18" cy="10" r="1.7" fill="currentColor" stroke="none">
      <animate attributeName="cy" values="10;8.5;10" dur="1s" repeatCount="indefinite" />
      <animate attributeName="r" values="1.7;1.4;1.7" dur="1s" repeatCount="indefinite" />
    </circle>
    <circle cx="16" cy="17" r="1.7" fill="currentColor" stroke="none">
      <animate attributeName="cy" values="17;15.5;17" dur="1s" begin="0.3s" repeatCount="indefinite" />
      <animate attributeName="r" values="1.7;1.4;1.7" dur="1s" begin="0.3s" repeatCount="indefinite" />
    </circle>
  </svg>

  <!-- ③ 错题宝藏：全息宝石线框（与错题本页宝石同形）+ 闪烁 -->
  <svg
    v-else-if="kind === 'mistakes'"
    class="h-7 w-7"
    style="color: #ffb01f; filter: drop-shadow(0 0 3px currentColor)"
    viewBox="0 0 32 32"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linejoin="round"
    stroke-linecap="round"
  >
    <path d="M16 5 L23 11 L20 19 L16 25 L12 19 L9 11 Z" />
    <path d="M9 11 L23 11 L16 14 Z" />
    <path d="M9 11 L12 19 L16 25 L16 14 Z" />
    <path d="M23 11 L20 19 L16 25 L16 14 Z" />
    <path d="M16 14 L12 19 L16 25 L20 19 Z" />
    <path d="M14 8 L16 5 L16 13 Z" fill="#FFFFFF" opacity="0.4" stroke="none" />
    <circle cx="6" cy="9" r="1.3" fill="currentColor" stroke="none" class="animate-twinkle" />
    <circle cx="26" cy="8" r="1.5" fill="currentColor" stroke="none" class="animate-twinkle" style="animation-delay: 0.5s" />
    <circle cx="25" cy="22" r="1.1" fill="currentColor" stroke="none" class="animate-twinkle" style="animation-delay: 1s" />
  </svg>

  <!-- ④ 学习画像：霓虹雷达扫描盘 + 旋转指针 + 探测点 -->
  <svg
    v-else-if="kind === 'dashboard'"
    class="h-7 w-7"
    style="color: #1fbf85; filter: drop-shadow(0 0 3px currentColor)"
    viewBox="0 0 32 32"
    fill="none"
    stroke="currentColor"
    stroke-width="1.5"
    stroke-linejoin="round"
    stroke-linecap="round"
  >
    <circle cx="16" cy="16" r="12" opacity="0.55" stroke-width="1" />
    <circle cx="16" cy="16" r="8" opacity="0.6" stroke-width="1" />
    <circle cx="16" cy="16" r="4" opacity="0.7" stroke-width="1" />
    <line x1="16" y1="3" x2="16" y2="29" opacity="0.4" stroke-width="0.8" />
    <line x1="3" y1="16" x2="29" y2="16" opacity="0.4" stroke-width="0.8" />
    <g class="radar-scan" style="transform-origin: 16px 16px">
      <path d="M16 16 L26 6 A11 11 0 0 0 22 5 Z" fill="currentColor" opacity="0.35" stroke="none" />
      <line x1="16" y1="16" x2="25" y2="7" stroke-width="1.3" />
    </g>
    <circle cx="16" cy="16" r="2" fill="currentColor" stroke="none" />
    <circle cx="22" cy="10" r="1.4" fill="currentColor" stroke="none" class="animate-twinkle" />
    <circle cx="9" cy="22" r="1.4" fill="currentColor" stroke="none" class="animate-twinkle" style="animation-delay: 0.7s" />
  </svg>
</template>

<style scoped>
/* 雷达扫描：4s 一周，比 tailwind 默认 spin 慢 4 倍，更适合"探索/不打扰"语义 */
.radar-scan {
  animation: radar-spin 4s linear infinite;
}
@keyframes radar-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>

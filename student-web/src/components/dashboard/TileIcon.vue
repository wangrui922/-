<script setup lang="ts">
/**
 * 学习画像首页 4 个趣味 tile 的拟物化彩色 SVG 图标。
 *
 * 设计原则：
 *  - 每个 icon 颜色 / 主题与所在 tile 的主色一致，强化品牌色彩认知。
 *  - 多层 SVG（主体+高光+装饰），呈现「立体感」而非单调线条。
 *  - 内置轻量微动画（twinkle / SMIL / CSS spin），无需额外 JS。
 *
 * props:
 *  - kind:  'practice' | 'ai' | 'mistakes' | 'dashboard'
 *
 * 体量：每个 SVG 约 1KB，远小于 PNG。
 */
defineProps<{ kind: 'practice' | 'ai' | 'mistakes' | 'dashboard' }>()
</script>

<template>
  <!-- ① 去练习：立体魔法书 + 金星 -->
  <svg v-if="kind === 'practice'" class="h-7 w-7 drop-shadow-[0_2px_3px_rgba(0,0,0,0.18)]" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
    <!-- 落地阴影 -->
    <ellipse cx="16" cy="26" rx="10" ry="1.6" fill="rgba(0,0,0,0.18)" />
    <!-- 书本主体（两页微微翻开） -->
    <path d="M5 11 Q5 9 7 9 L15 10 L15 22 L7 23 Q5 23 5 21 Z" fill="#FFE3A8" stroke="#E89A1F" stroke-width="1" stroke-linejoin="round" />
    <path d="M27 11 Q27 9 25 9 L17 10 L17 22 L25 23 Q27 23 27 21 Z" fill="#FFD27A" stroke="#E89A1F" stroke-width="1" stroke-linejoin="round" />
    <!-- 书脊 -->
    <line x1="16" y1="10" x2="16" y2="22" stroke="#E89A1F" stroke-width="0.8" />
    <!-- 紫色算式行 -->
    <line x1="8" y1="13" x2="13" y2="13" stroke="#7C5CFC" stroke-width="1.4" stroke-linecap="round" />
    <line x1="8" y1="16" x2="13" y2="16" stroke="#7C5CFC" stroke-width="1.4" stroke-linecap="round" />
    <line x1="8" y1="19" x2="11" y2="19" stroke="#7C5CFC" stroke-width="1.4" stroke-linecap="round" />
    <line x1="19" y1="13" x2="24" y2="13" stroke="#7C5CFC" stroke-width="1.4" stroke-linecap="round" />
    <line x1="19" y1="16" x2="24" y2="16" stroke="#7C5CFC" stroke-width="1.4" stroke-linecap="round" />
    <line x1="20" y1="19" x2="24" y2="19" stroke="#7C5CFC" stroke-width="1.4" stroke-linecap="round" />
    <!-- 右上闪烁金星 -->
    <g class="animate-twinkle" style="transform-origin: 23px 6px;">
      <path d="M23 2 L24.1 5.1 L27 5.4 L24.7 7.5 L25.4 10.5 L23 8.7 L20.6 10.5 L21.3 7.5 L19 5.4 L21.9 5.1 Z" fill="#FFC24B" stroke="#E89A1F" stroke-width="0.6" stroke-linejoin="round" />
    </g>
    <!-- 左上小白光 -->
    <circle cx="6" cy="6" r="1.4" fill="white" opacity="0.85" />
    <circle cx="27" cy="22" r="1" fill="white" opacity="0.75" />
  </svg>

  <!-- ② 问小玥老师：对话气泡 + 3 跳动点（AI 思考中） -->
  <svg v-else-if="kind === 'ai'" class="h-7 w-7 drop-shadow-[0_2px_3px_rgba(0,0,0,0.18)]" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
    <!-- 对话气泡主体 -->
    <path
      d="M4 9 Q4 5 8 5 L24 5 Q28 5 28 9 L28 19 Q28 23 24 23 L15 23 L11 27 L12 23 L8 23 Q4 23 4 19 Z"
      fill="white" stroke="#FF7043" stroke-width="1.5" stroke-linejoin="round"
    />
    <!-- 三个跳动点：错落延迟，类「思考中」动画 -->
    <circle cx="11" cy="14" r="1.7" fill="#FF7043">
      <animate attributeName="cy" values="14;11;14" dur="1s" repeatCount="indefinite" />
      <animate attributeName="r" values="1.7;1.4;1.7" dur="1s" repeatCount="indefinite" />
    </circle>
    <circle cx="16" cy="14" r="1.7" fill="#FF7043">
      <animate attributeName="cy" values="14;11;14" dur="1s" begin="0.2s" repeatCount="indefinite" />
      <animate attributeName="r" values="1.7;1.4;1.7" dur="1s" begin="0.2s" repeatCount="indefinite" />
    </circle>
    <circle cx="21" cy="14" r="1.7" fill="#FF7043">
      <animate attributeName="cy" values="14;11;14" dur="1s" begin="0.4s" repeatCount="indefinite" />
      <animate attributeName="r" values="1.7;1.4;1.7" dur="1s" begin="0.4s" repeatCount="indefinite" />
    </circle>
    <!-- 旁侧小气泡表示 AI「回应」 -->
    <circle cx="27" cy="6" r="1.8" fill="#FFD27A" stroke="#E89A1F" stroke-width="0.8" class="animate-twinkle" />
  </svg>

  <!-- ③ 错题宝藏：立体钻石 + 高光 + 周围闪烁 -->
  <svg v-else-if="kind === 'mistakes'" class="h-7 w-7 drop-shadow-[0_2px_3px_rgba(0,0,0,0.18)]" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
    <!-- 主阴影 -->
    <ellipse cx="16" cy="26" rx="6" ry="1.4" fill="rgba(0,0,0,0.18)" />
    <!-- 顶面（亮） -->
    <path d="M9 11 L16 6 L23 11 L16 14 Z" fill="#FFE89A" stroke="#E89A1F" stroke-width="1.2" stroke-linejoin="round" />
    <!-- 左侧面（深） -->
    <path d="M9 11 L12 19 L16 25 L16 14 Z" fill="#FFB347" stroke="#E89A1F" stroke-width="1.2" stroke-linejoin="round" />
    <!-- 右侧面（中） -->
    <path d="M23 11 L20 19 L16 25 L16 14 Z" fill="#FF9F1C" stroke="#E89A1F" stroke-width="1.2" stroke-linejoin="round" />
    <!-- 中央面（最深，立体感） -->
    <path d="M16 14 L12 19 L16 25 L20 19 Z" fill="#FFD27A" stroke="#E89A1F" stroke-width="1.2" stroke-linejoin="round" />
    <!-- 高光带 -->
    <path d="M14 8 L16 6 L16 13 Z" fill="white" opacity="0.55" />
    <line x1="13" y1="13" x2="13" y2="18" stroke="white" stroke-width="0.6" opacity="0.45" />
    <!-- 周围闪烁 3 颗 -->
    <circle cx="6" cy="9" r="1.4" fill="#FFD27A" class="animate-twinkle" />
    <circle cx="26" cy="7" r="1.6" fill="#FFD27A" class="animate-twinkle" style="animation-delay: 0.5s" />
    <circle cx="25" cy="22" r="1.1" fill="#FFD27A" class="animate-twinkle" style="animation-delay: 1s" />
    <circle cx="5" cy="23" r="1" fill="#FFD27A" class="animate-twinkle" style="animation-delay: 1.4s" />
  </svg>

  <!-- ④ 学习画像：雷达扫描盘 + 旋转指针 + 探测点 -->
  <svg v-else-if="kind === 'dashboard'" class="h-7 w-7 drop-shadow-[0_2px_3px_rgba(0,0,0,0.18)]" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
    <!-- 同心圆 3 圈 -->
    <circle cx="16" cy="16" r="12" fill="none" stroke="white" stroke-width="0.7" opacity="0.5" />
    <circle cx="16" cy="16" r="8" fill="none" stroke="white" stroke-width="0.7" opacity="0.6" />
    <circle cx="16" cy="16" r="4" fill="none" stroke="white" stroke-width="0.7" opacity="0.7" />
    <!-- 十字 -->
    <line x1="16" y1="3" x2="16" y2="29" stroke="white" stroke-width="0.5" opacity="0.45" />
    <line x1="3" y1="16" x2="29" y2="16" stroke="white" stroke-width="0.5" opacity="0.45" />
    <!-- 扫描扇形 + 指针（4s 一圈慢转，干净不打扰） -->
    <g class="radar-scan" style="transform-origin: 16px 16px;">
      <path d="M16 16 L26 6 A11 11 0 0 0 22 5 Z" fill="#5FE0B0" opacity="0.5" />
      <line x1="16" y1="16" x2="25" y2="7" stroke="#5FE0B0" stroke-width="1.4" stroke-linecap="round" />
    </g>
    <!-- 中央指示点 -->
    <circle cx="16" cy="16" r="2.2" fill="white" stroke="#4DD0A0" stroke-width="1.2" />
    <!-- 两个探测闪烁点 -->
    <circle cx="22" cy="10" r="1.5" fill="#FFD27A" class="animate-twinkle" />
    <circle cx="9" cy="22" r="1.5" fill="#FFD27A" class="animate-twinkle" style="animation-delay: 0.7s" />
  </svg>
</template>

<style scoped>
/* 雷达扫描：4s 一周，比 tailwind 默认 spin 慢 4 倍，更适合"探索/不打扰"语义 */
.radar-scan {
  animation: radar-spin 4s linear infinite;
}
@keyframes radar-spin {
  to { transform: rotate(360deg); }
}
</style>

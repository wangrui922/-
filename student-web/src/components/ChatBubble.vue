<script setup lang="ts">
/** 对话气泡：AI 靠左（白底），学生靠右（蓝底）。 */
import { computed } from 'vue'

import { renderMath } from '@/utils/renderMath'

interface Props {
  /** 说话人 */
  role: 'user' | 'ai'
  /** 气泡文本 */
  content: string
  /** 学生拍照的题目缩略图（仅 user 角色，base64 dataUrl）。点开看大图。 */
  image?: string
  /** 是否显示「思考中」省略号动画 */
  pending?: boolean
}

const props = withDefaults(defineProps<Props>(), { pending: false, image: '' })

const isUser = computed<boolean>(() => props.role === 'user')

/** 任意方向都经 renderMath 渲染：文本段转义防 XSS，LaTeX 段交给 KaTeX。
 *  小学内容本就不含 LaTeX，渲染后仍是纯文本，不影响可读性。 */
const displayContent = computed<string>(() => renderMath(props.content))

/** 点击图片看大图 */
function openImage(): void {
  if (props.image) window.open(props.image, '_blank')
}
</script>

<template>
  <div class="flex w-full" :class="isUser ? 'justify-end' : 'justify-start'">
    <div class="flex max-w-[85%] items-end gap-2" :class="isUser ? 'flex-row-reverse' : 'flex-row'">
      <div
        class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold"
        :class="isUser ? 'bg-brand-100 text-brand-600' : 'bg-brand-500 text-white'"
      >
        {{ isUser ? '我' : 'AI' }}
      </div>

      <div
        class="whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed shadow-sm"
        :class="
          isUser
            ? 'rounded-br-sm bg-brand-500 text-white'
            : 'rounded-bl-sm border border-slate-100 bg-white text-slate-800'
        "
      >
        <span v-if="pending" class="inline-flex gap-1 py-1">
          <i class="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400" style="animation-delay: 0ms" />
          <i class="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400" style="animation-delay: 150ms" />
          <i class="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400" style="animation-delay: 300ms" />
        </span>
        <template v-else>
          <img
            v-if="image"
            :src="image"
            alt="题目照片"
            class="mb-1.5 block max-h-[260px] max-w-[220px] cursor-pointer rounded-lg object-cover hover:opacity-90"
            @click="openImage"
          />
          <span class="block" v-html="displayContent"></span>
        </template>
      </div>
    </div>
  </div>
</template>

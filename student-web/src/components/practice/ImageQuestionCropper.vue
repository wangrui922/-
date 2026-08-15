<script setup lang="ts">
/**
 * 拍题选题意组件（作业帮式「框选某道」体验，纯前端实现）。
 *
 * 用法：父组件拍照拿到原图 dataURL 后，把 src 传进来，组件显示原图 +
 * 可拖拽/缩放的矩形选区；用户框住要讲的那一道，点「下一步」前端用 canvas
 * 把选区裁成单题子图 emit 出去。AI 收到的就是干净的「单题」，不会把整页混讲。
 *
 * - confirm(dataUrl)：裁切后的单题图（默认 JPEG 0.92）
 * - whole()：不框选，直接用整张图（兜底）
 * - cancel()：放弃本次选图
 *
 * 坐标用 pointer 事件 + delta 法，兼容鼠标与触摸；不依赖绝对定位换算，
 * 因此外层滚动容器也安全。
 */
import { onMounted, ref } from 'vue'

const props = defineProps<{ src: string }>()
const emit = defineEmits<{
  (e: 'confirm', dataUrl: string): void
  (e: 'whole'): void
  (e: 'cancel'): void
}>()

const wrap = ref<HTMLDivElement | null>(null)
const imgEl = ref<HTMLImageElement | null>(null)
const box = ref({ w: 0, h: 0 })
const natural = ref({ w: 0, h: 0 })
const sel = ref({ x: 0, y: 0, w: 0, h: 0 })

type DragMode = 'move' | 'resize' | null
let mode: DragMode = null
let startX = 0
let startY = 0
let startSel = { x: 0, y: 0, w: 0, h: 0 }

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

function setup(): void {
  const im = imgEl.value
  const w = wrap.value
  if (!im || !w) return
  natural.value = { w: im.naturalWidth, h: im.naturalHeight }
  const r = w.getBoundingClientRect()
  box.value = { w: r.width, h: r.height }
  // 默认框一道横题：宽占 80%、高占 22%、垂直居中
  const sw = r.width * 0.8
  const sh = Math.max(r.height * 0.22, 48)
  sel.value = { x: (r.width - sw) / 2, y: (r.height - sh) / 2, w: sw, h: sh }
}

onMounted(() => {
  const im = imgEl.value
  if (im && im.complete) setup()
})

function onImgLoad(): void {
  setup()
}

function onDown(e: PointerEvent, m: DragMode): void {
  mode = m
  startX = e.clientX
  startY = e.clientY
  startSel = { ...sel.value }
  ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
}

function onMove(e: PointerEvent): void {
  if (!mode) return
  const dx = e.clientX - startX
  const dy = e.clientY - startY
  const b = box.value
  if (mode === 'move') {
    const w = startSel.w
    const h = startSel.h
    sel.value.x = clamp(startSel.x + dx, 0, b.w - w)
    sel.value.y = clamp(startSel.y + dy, 0, b.h - h)
  } else if (mode === 'resize') {
    sel.value.w = clamp(startSel.w + dx, 40, b.w - startSel.x)
    sel.value.h = clamp(startSel.h + dy, 40, b.h - startSel.y)
  }
}

function onUp(): void {
  mode = null
}

function crop(): string {
  const im = imgEl.value
  if (!im) return props.src
  const b = box.value
  const n = natural.value
  if (!b.w || !b.h) return props.src
  const sx = n.w / b.w
  const sy = n.h / b.h
  const s = sel.value
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(s.w * sx))
  canvas.height = Math.max(1, Math.round(s.h * sy))
  const ctx = canvas.getContext('2d')
  if (!ctx) return props.src
  ctx.drawImage(
    im,
    s.x * sx,
    s.y * sy,
    s.w * sx,
    s.h * sy,
    0,
    0,
    canvas.width,
    canvas.height,
  )
  return canvas.toDataURL('image/jpeg', 0.92)
}

function confirm(): void {
  emit('confirm', crop())
}
function whole(): void {
  emit('whole')
}
function cancel(): void {
  emit('cancel')
}
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <!-- 图片 + 选区 -->
    <div class="min-h-0 flex-1 overflow-auto bg-slate-900/90 p-3">
      <div ref="wrap" class="relative mx-auto w-full select-none" style="touch-action: none">
        <img
          ref="imgEl"
          :src="src"
          alt="题目原图"
          class="block w-full h-auto"
          draggable="false"
          @load="onImgLoad"
        />
        <!-- 暗化遮罩：用超大扩散阴影压住未选区域 -->
        <div
          class="absolute cursor-move rounded-md border-2 border-brand-600"
          :style="{
            left: sel.x + 'px',
            top: sel.y + 'px',
            width: sel.w + 'px',
            height: sel.h + 'px',
            boxShadow: '0 0 0 4000px rgba(15,23,42,0.55)',
          }"
          @pointerdown="onDown($event, 'move')"
          @pointermove="onMove"
          @pointerup="onUp"
          @pointercancel="onUp"
        >
          <span
            class="absolute -top-6 left-0 rounded bg-brand-600 px-2 py-0.5 text-xs font-medium text-white"
            >框住要讲的那道题</span
          >
          <!-- 右下角缩放手柄 -->
          <span
            class="absolute -bottom-1.5 -right-1.5 h-4 w-4 rounded-sm border-2 border-white bg-brand-600"
            @pointerdown.stop="onDown($event, 'resize')"
            @pointermove="onMove"
            @pointerup.stop="onUp"
            @pointercancel.stop="onUp"
          />
        </div>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="flex shrink-0 gap-2 border-t border-slate-100 bg-white px-4 py-3">
      <button type="button" class="btn-ghost shrink-0 px-3" @click="cancel">取消</button>
      <button type="button" class="btn-ghost shrink-0 px-3" @click="whole">整张图</button>
      <button type="button" class="btn-primary flex-1" @click="confirm">下一步</button>
    </div>
  </div>
</template>

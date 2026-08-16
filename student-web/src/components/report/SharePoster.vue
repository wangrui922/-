<script setup lang="ts">
/**
 * 周报分享图（朋友圈长图，原生 SVG → PNG，零第三方依赖）。
 *
 * 设计：用纯 SVG 画出一张竖向海报，家长点「保存图片」时把 SVG 序列化后
 * 绘制到 canvas 导出 PNG。这样不依赖 html2canvas，避免本机 npm 不可用的问题，
 * 且导出结果像素清晰（2x）。手机端长按生成的 <img> 即可存图发朋友圈。
 *
 * 海报内容：品牌头部 → 核心数据（正确率/学习天数/做题/做对/新增错题/攻克/辅导）
 * → 本周亮点 → 学情雷达（轴数 3~10 才画，否则退化为「覆盖 X/Y 能力点」）
 * → 还需多练的知识点 → 错因分布 → 这周末可以这样做 → 页脚。
 */
import { computed, ref } from 'vue'

import {
  ERROR_CATEGORY_COLOR,
  type ErrorCategory,
  type RadarData,
} from '@/types/profile'
import type { SharedReportData } from '@/types/report'

const props = defineProps<{
  report: SharedReportData
}>()

const FONT = "'PingFang SC','Microsoft YaHei','Hiragino Sans GB',sans-serif"
const W = 750
const P = 48
const INNER = W - P * 2
const HDR = 200

const BRAND = '#1f63d6'
const INK = '#1e293b'
const SUB = '#64748b'
const MUTE = '#94a3b8'
const LINE = '#e2e8f0'
const LEVEL_COLOR: Record<number, string> = {
  0: '#ef4444',
  1: '#f59e0b',
  2: '#3b82f6',
  3: '#22c55e',
}

function esc(s: unknown): string {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 按字符数折行（中文无空格，按字断行最稳）。 */
function wrap(str: string, maxChars: number): string[] {
  const s = String(str ?? '')
  if (!s) return ['']
  const lines: string[] = []
  let cur = ''
  for (const ch of s) {
    cur += ch
    if ([...cur].length >= maxChars) {
      lines.push(cur)
      cur = ''
    }
  }
  if (cur) lines.push(cur)
  return lines
}

function text(
  x: number,
  y: number,
  str: string,
  size: number,
  fill: string,
  weight = 400,
  anchor: 'start' | 'middle' | 'end' = 'start',
): string {
  return `<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}" font-family="${FONT}">${esc(str)}</text>`
}

/** 多行文本块，返回 svg 片段与占高。 */
function textBlock(
  x: number,
  y: number,
  str: string,
  size: number,
  fill: string,
  weight: number,
  lineHeight: number,
  maxChars: number,
  anchor: 'start' | 'middle' | 'end' = 'start',
): { svg: string; h: number } {
  const lines = wrap(str, maxChars)
  const tspans = lines
    .map((ln, i) => `<tspan x="${x}" dy="${i === 0 ? 0 : lineHeight}">${esc(ln)}</tspan>`)
    .join('')
  return {
    svg: `<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}" font-family="${FONT}">${tspans}</text>`,
    h: lines.length * lineHeight,
  }
}

function sectionTitle(y: number, title: string): string {
  return (
    `<rect x="${P}" y="${y - 24}" width="8" height="28" rx="2" fill="${BRAND}"/>` +
    text(P + 18, y, title, 30, INK, 700)
  )
}

function divider(y: number): string {
  return `<line x1="${P}" y1="${y}" x2="${P + INNER}" y2="${y}" stroke="${LINE}" stroke-width="1"/>`
}

/** 学情雷达（仅 3~10 轴时绘制，避免过密）。 */
function radarSvg(
  cx: number,
  cy: number,
  R: number,
  axes: RadarData['axes'],
  overall: number,
): string {
  const n = axes.length
  if (n < 3) return ''
  const pt = (i: number, pct: number): [number, number] => {
    const ang = ((-90 + (i * 360) / n) * Math.PI) / 180
    const r = (pct / 100) * R
    return [cx + r * Math.cos(ang), cy + r * Math.sin(ang)]
  }
  const rings = [25, 50, 75, 100]
    .map((lv) =>
      axes
        .map((_a, i) => pt(i, lv).map((v) => v.toFixed(1)).join(','))
        .join(' '),
    )
    .map((r) => `<polygon points="${r}" fill="none" stroke="#eef2f7" stroke-width="1"/>`)
    .join('')
  const axisLines = axes
    .map((_a, i) => {
      const p = pt(i, 100)
      return `<line x1="${cx}" y1="${cy}" x2="${p[0].toFixed(1)}" y2="${p[1].toFixed(1)}" stroke="#e2e8f0" stroke-width="1"/>`
    })
    .join('')
  const poly = axes.map((a, i) => pt(i, a.score).map((v) => v.toFixed(1)).join(',')).join(' ')
  const verts = axes
    .map((a, i) => {
      const p = pt(i, a.score)
      const c = !a.practiced ? '#cbd5e1' : LEVEL_COLOR[a.level] ?? '#ef4444'
      return `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4" fill="${c}"/>`
    })
    .join('')
  // labels
  const labelSvg = axes
    .map((a, i) => {
      const ang = ((-90 + (i * 360) / n) * Math.PI) / 180
      const r = R + 24
      const x = cx + r * Math.cos(ang)
      const y = cy + r * Math.sin(ang)
      const cos = Math.cos(ang)
      const anchor = cos > 0.15 ? 'start' : cos < -0.15 ? 'end' : 'middle'
      const dy = Math.sin(ang) > 0.3 ? 10 : Math.sin(ang) < -0.3 ? -4 : 4
      return `<text x="${x.toFixed(1)}" y="${(y + dy).toFixed(1)}" text-anchor="${anchor}" font-size="16" fill="${a.practiced ? '#475569' : '#cbd5e1'}" font-family="${FONT}">${esc(a.kp_name)}</text>`
    })
    .join('')
  const center =
    text(cx, cy - 4, String(overall), 30, INK, 800, 'middle') +
    text(cx, cy + 18, '整体掌握度', 15, MUTE, 400, 'middle')
  return rings + axisLines + `<polygon points="${poly}" fill="rgba(59,130,246,0.18)" stroke="#3b82f6" stroke-width="2" stroke-linejoin="round"/>` + verts + labelSvg + center
}

function periodText(r: SharedReportData): string {
  const p = r.period
  if (!p || !p.start) return ''
  return p.end ? `${p.start} ~ ${p.end}` : `${p.start} 起`
}
function gradeText(r: SharedReportData): string {
  const g = r.student.grade
  return g ? `${g} 年级` : ''
}

/** 薄弱点 delta 文案 + 配色。 */
function deltaInfo(delta: number | null): { text: string; color: string } {
  if (delta === null || delta === undefined) return { text: '新出现', color: MUTE }
  if (delta > 0) return { text: `比上周 +${Math.round(delta * 100)}%`, color: '#22c55e' }
  if (delta < 0) return { text: `比上周 ${Math.round(delta * 100)}%`, color: '#f59e0b' }
  return { text: '和上周持平', color: MUTE }
}

const poster = computed<{ svg: string; height: number }>(() => {
  const r = props.report
  const parts: string[] = []

  // —— 头部品牌区 ——
  parts.push(`<rect x="0" y="0" width="${W}" height="${HDR}" fill="${BRAND}"/>`)
  parts.push(text(P, 76, 'AI 数学教练 · 本周学习小结', 22, '#ffffff', 600))
  const nameLines = wrap(`${r.student.display_name} 的本周学习小结`, 12)
  nameLines.forEach((ln, i) => parts.push(text(P, 128 + 46 * i, ln, 38, '#ffffff', 800)))
  const sub = [periodText(r), gradeText(r)].filter(Boolean).join(' · ')
  parts.push(text(P, 182, sub, 24, '#dbeafe', 400))

  let y = HDR + 52

  // —— 核心数据 ——
  parts.push(text(P, y, '本周正确率', 24, SUB, 400))
  const cr = r.engagement.correct_rate
  parts.push(text(P, y + 66, cr === null ? '暂无' : `${Math.round(cr * 100)}%`, 66, BRAND, 800))
  parts.push(
    text(P, y + 110, `学习 ${r.engagement.study_days} 天 · 做题 ${r.engagement.attempt_count} 道`, 26, '#475569', 400),
  )
  y += 152
  const tiles = [
    { v: r.engagement.correct_count, l: '做对' },
    { v: r.engagement.new_mistake_count, l: '新增错题' },
    { v: r.engagement.solved_mistake_count, l: '攻克' },
    { v: r.engagement.tutor_session_count, l: '辅导' },
  ]
  const tileW = (INNER - 3 * 16) / 4
  tiles.forEach((t, i) => {
    const x = P + i * (tileW + 16)
    parts.push(text(x + tileW / 2, y, String(t.v), 34, INK, 700, 'middle'))
    parts.push(text(x + tileW / 2, y + 34, t.l, 22, MUTE, 400, 'middle'))
  })
  y += 84
  parts.push(divider(y))
  y += 46

  // —— 本周亮点 ——
  parts.push(sectionTitle(y, '本周亮点'))
  y += 42
  if (r.narrative.headline) {
    const h = textBlock(P, y, r.narrative.headline, 28, BRAND, 700, 40, 18)
    parts.push(h.svg)
    y += h.h
  }
  if (r.narrative.overview_text) {
    const o = textBlock(P, y, r.narrative.overview_text, 24, '#475569', 400, 38, 21)
    parts.push(o.svg)
    y += o.h
  }
  y += 18
  parts.push(divider(y))
  y += 46

  // —— 学情雷达 / 掌握度摘要 ——
  const radar = r.radar
  const axes = radar?.axes ?? []
  const n = axes.length
  if (radar && n >= 3 && n <= 10) {
    parts.push(sectionTitle(y, '学情能力雷达图'))
    y += 30
    const cx = P + INNER / 2
    const cy = y + 130
    const R = 110
    parts.push(radarSvg(cx, cy, R, axes, Math.round(radar.summary.overall_score)))
    y = cy + R + 36
    parts.push(text(P + INNER / 2, y, `已覆盖 ${radar.summary.practiced_kp} / ${radar.summary.total_kp} 个能力点`, 22, MUTE, 400, 'middle'))
    y += 44
  } else if (radar) {
    parts.push(sectionTitle(y, '学习掌握度'))
    y += 44
    parts.push(
      text(P + INNER / 2, y, `已练习 ${radar.summary.practiced_kp} / ${radar.summary.total_kp} 个能力点`, 26, '#475569', 600, 'middle'),
    )
    parts.push(
      text(P + INNER / 2, y + 40, `整体掌握度 ${Math.round(radar.summary.overall_score)}`, 26, BRAND, 800, 'middle'),
    )
    y += 72
  }
  parts.push(divider(y))
  y += 46

  // —— 还需多练的知识点 ——
  const weak = (r.weak_points ?? []).slice(0, 3)
  if (weak.length) {
    parts.push(sectionTitle(y, '还需要多练的知识点'))
    y += 46
    weak.forEach((w) => {
      const lvl = Math.max(0, Math.min(3, Number(w.level) || 0))
      const pct = Math.round(w.score * 100)
      parts.push(text(P, y, w.kp_name, 26, INK, 600))
      parts.push(text(P + INNER, y, `${pct}%`, 26, LEVEL_COLOR[lvl], 700, 'end'))
      y += 32
      const barY = y
      const barH = 14
      parts.push(`<rect x="${P}" y="${barY}" width="${INNER}" height="${barH}" rx="7" fill="#eef2f7"/>`)
      parts.push(
        `<rect x="${P}" y="${barY}" width="${Math.max(8, (INNER * pct) / 100)}" height="${barH}" rx="7" fill="${LEVEL_COLOR[lvl]}"/>`,
      )
      y += barH + 8
      const d = deltaInfo(w.delta)
      parts.push(text(P, y, d.text, 20, d.color, 400))
      y += 32
    })
    y += 0
    parts.push(divider(y))
    y += 46
  }

  // —— 错因分布 ——
  const ep = r.error_patterns
  const pats = (ep?.error_patterns ?? []).filter((p) => p.count > 0)
  if (pats.length && ep) {
    parts.push(sectionTitle(y, `错因分布 · 近${ep.range_days}天 ${ep.total_mistakes}道`))
    y += 46
    const maxC = Math.max(...pats.map((p) => p.count))
    const barMax = 300
    pats.slice(0, 5).forEach((p) => {
      const color = ERROR_CATEGORY_COLOR[p.category as ErrorCategory] || '#94a3b8'
      parts.push(`<circle cx="${P + 8}" cy="${y - 6}" r="7" fill="${color}"/>`)
      parts.push(text(P + 26, y, p.label, 24, INK, 500))
      parts.push(text(P + INNER, y, `${p.count}次 · ${Math.round(p.ratio * 100)}%`, 22, MUTE, 400, 'end'))
      y += 28
      const ratio = maxC > 0 ? p.count / maxC : 0
      parts.push(`<rect x="${P + 26}" y="${y}" width="${barMax}" height="10" rx="5" fill="#eef2f7"/>`)
      parts.push(`<rect x="${P + 26}" y="${y}" width="${Math.max(6, barMax * ratio)}" height="10" rx="5" fill="${color}"/>`)
      y += 32
    })
    y += 6
    parts.push(divider(y))
    y += 46
  }

  // —— 这周末可以这样做 ——
  const adv = (r.narrative.advice_items ?? []).slice(0, 2)
  if (adv.length) {
    parts.push(sectionTitle(y, '这周末可以这样做'))
    y += 44
    adv.forEach((a, i) => {
      parts.push(text(P, y, String(i + 1), 24, BRAND, 700))
      const b = textBlock(P + 36, y, a, 24, '#475569', 400, 38, 18)
      parts.push(b.svg)
      y += b.h + 14
    })
    y += 6
  }

  // —— 页脚 ——
  y += 18
  parts.push(divider(y))
  y += 38
  parts.push(text(P + INNER / 2, y, '由 AI 数学教练自动生成 · 仅供家长参考', 20, MUTE, 400, 'middle'))
  if (r.generated_at) {
    y += 30
    parts.push(text(P + INNER / 2, y, `生成于 ${r.generated_at}`, 18, '#cbd5e1', 400, 'middle'))
  }
  y += 52

  const height = Math.round(y)
  parts.unshift(`<rect x="0" y="0" width="${W}" height="${height}" fill="#ffffff"/>`)
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${height}" viewBox="0 0 ${W} ${height}">${parts.join('')}</svg>`
  return { svg, height }
})

const wrapRef = ref<HTMLElement | null>(null)
const resultUrl = ref('')
const generating = ref(false)
const errMsg = ref('')

async function saveImage(): Promise<void> {
  const svgEl = wrapRef.value?.querySelector('svg')
  if (!svgEl) {
    errMsg.value = '生成失败，请重试'
    return
  }
  generating.value = true
  errMsg.value = ''
  try {
    const xml = new XMLSerializer().serializeToString(svgEl)
    const url = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml)
    const img = new Image()
    img.onload = () => {
      const scale = 2
      const canvas = document.createElement('canvas')
      canvas.width = W * scale
      canvas.height = poster.value.height * scale
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        errMsg.value = '当前浏览器不支持保存'
        generating.value = false
        return
      }
      ctx.scale(scale, scale)
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, W, poster.value.height)
      ctx.drawImage(img, 0, 0, W, poster.value.height)
      resultUrl.value = canvas.toDataURL('image/png')
      generating.value = false
    }
    img.onerror = () => {
      errMsg.value = '图片生成失败，请重试'
      generating.value = false
    }
    img.src = url
  } catch {
    errMsg.value = '生成失败，请重试'
    generating.value = false
  }
}

function reset(): void {
  resultUrl.value = ''
  errMsg.value = ''
}
</script>

<template>
  <div class="w-full">
    <!-- 预览态：展示 SVG 海报 + 保存按钮 -->
    <div v-if="!resultUrl">
      <div
        ref="wrapRef"
        class="mx-auto w-full overflow-hidden rounded-2xl bg-white shadow-sm"
        v-html="poster.svg"
      />
      <p class="mt-3 text-center text-xs text-slate-400">
        点击「保存图片」后，长按图片即可存到相册，发朋友圈
      </p>
      <button
        class="mt-3 w-full rounded-xl bg-brand-600 py-3 text-center text-base font-bold text-white active:opacity-90 disabled:opacity-60"
        :disabled="generating"
        @click="saveImage"
      >
        {{ generating ? '正在生成…' : '保存图片' }}
      </button>
      <p v-if="errMsg" class="mt-2 text-center text-sm text-red-500">{{ errMsg }}</p>
    </div>

    <!-- 结果态：展示 PNG，供长按保存 / 下载 -->
    <div v-else>
      <img :src="resultUrl" alt="学习小结分享图" class="mx-auto w-full rounded-2xl bg-white shadow-sm" />
      <p class="mt-3 text-center text-sm font-medium text-slate-600">
        长按上方图片 → 保存到相册，就能发朋友圈啦
      </p>
      <div class="mt-3 flex gap-3">
        <a
          :href="resultUrl"
          download="学习小结.png"
          class="flex-1 rounded-xl bg-brand-600 py-3 text-center text-base font-bold text-white active:opacity-90"
        >
          下载图片
        </a>
        <button
          class="flex-1 rounded-xl bg-slate-100 py-3 text-center text-base font-bold text-slate-600 active:opacity-90"
          @click="reset"
        >
          返回
        </button>
      </div>
    </div>
  </div>
</template>

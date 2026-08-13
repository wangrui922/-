/**
 * LaTeX 渲染工具（#66 · 公式好看、可复制）。
 *
 * 设计要点：
 * - 输入可能混排普通文本与 `$...$`（行内）/ `$$...$$`（块级）LaTeX；
 * - 数学段交给 KaTeX 渲染（`throwOnError:false`，公式写错也只降级不崩）；
 * - 文本段**一律 HTML 转义**，这是 v-html 前必需的防线，杜绝 XSS；
 * - KaTeX 通过 index.html 的 CDN `<script defer>` 注入到 `window.katex`，
 *   加载慢时 `renderMath` 自动降级为「转义后的原始公式」，不白屏。
 *
 * 用法：在模板里 `v-html="renderMath(text)"`，父容器加 `whitespace-pre-wrap`
 * 即可让换行与 KaTeX 正常共存。
 */

interface KatexGlobal {
  renderToString(
    tex: string,
    opts?: { displayMode?: boolean; throwOnError?: boolean },
  ): string
}

declare global {
  interface Window {
    katex?: KatexGlobal
  }
}

/** 文本段 HTML 转义（防 XSS，必须对所有非数学内容做）。 */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// 仅在浏览器环境才有 window；SSR/测试环境不存在时直接走降级分支
const hasWindow = typeof window !== 'undefined'
const katex = hasWindow ? window.katex : undefined

const MATH_SPLIT_RE = /(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g

/** 把含 LaTeX 的文本渲染成可 v-html 的安全 HTML 串。空输入返回空串。 */
export function renderMath(src: string | null | undefined): string {
  if (!src) return ''

  return String(src)
    .split(MATH_SPLIT_RE)
    .map((seg) => {
      if (!seg) return ''

      const block = /^\$\$([\s\S]+?)\$\$$/.exec(seg)
      if (block) {
        const tex = block[1]
        try {
          return katex
            ? katex.renderToString(tex, { displayMode: true, throwOnError: false })
            : `<div class="math-fallback">$$${escapeHtml(tex)}$$</div>`
        } catch {
          return `<div class="math-fallback">$$${escapeHtml(tex)}$$</div>`
        }
      }

      const inline = /^\$([^$\n]+?)\$$/.exec(seg)
      if (inline) {
        const tex = inline[1]
        try {
          return katex
            ? katex.renderToString(tex, { displayMode: false, throwOnError: false })
            : `<span class="math-fallback">$${escapeHtml(tex)}$</span>`
        } catch {
          return `<span class="math-fallback">$${escapeHtml(tex)}$</span>`
        }
      }

      return escapeHtml(seg)
    })
    .join('')
}

export default renderMath

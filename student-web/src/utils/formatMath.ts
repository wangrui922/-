/**
 * 数学公式纯文本化（小学阶段友好版）。
 *
 * 背景：DeepSeek/方舟 出题或引用图片 OCR 时，经常把 $\frac{5}{13}$、\sqrt{3}、
 * x^2 这种 LaTeX 源码原文输出，小学生根本看不懂。后端 prompt 已经约束禁用
 * LaTeX，本文件是**双保险**——万一老模型或历史回复串里还有遗留 LaTeX，
 * 前端展示前先过一遍，肉眼看着都得是直接能看懂的纯文本。
 *
 * 处理优先级（从外到内）：
 *   1. 整体 $...$ 边界去掉（保留中间内容）
 *   2. \frac{a}{b} / \dfrac / \tfrac → a/b（小学生最常见、能看懂）
 *   3. \sqrt{x} → √x 或 根号 x
 *   4. x^{2} / x^2 → x²
 *   5. x_{1} / x_1 → x₁
 *   6. \times / \cdot → ×
 *   7. \div → ÷
 *   8. \geq / \leq / \neq → ≥ ≤ ≠
 *   9. ^{\circ} / \degree → °
 *
 * 转换后会留下极少量残留（深度括号嵌套），但对孩子来说"看见数字"就够了。
 */

/** 替换单条 LaTeX 风格的公式（含 $...$ 边界）。 */
function stripMathDelimiters(input: string): string {
  // 先去掉整段 $...$ 的边界（行内 + 整段都处理）
  let out = input.replace(/\$+/g, '')
  return out
}

/** 把 \frac{a}{b} / \dfrac{a}{b} / \tfrac{a}{b} 转成 a/b。 */
function replaceFrac(input: string): string {
  // \dfrac{a}{b}、\tfrac{a}{b} 先归一为 \frac{a}{b}
  let out = input.replace(/\\d?frac\{([^{}]+)\}\{([^{}]+)\}/g, '$1/$2')
  // 重复几次，处理嵌套（虽然小学场景基本不会嵌套）
  for (let i = 0; i < 3; i += 1) {
    const next = out.replace(/\\d?frac\{([^{}]+)\}\{([^{}]+)\}/g, '$1/$2')
    if (next === out) break
    out = next
  }
  return out
}

/** 把 x^{2} / x^2 转成 x²；x_{1} / x_1 转成 x₁。 */
function replaceSupSub(input: string): string {
  // 上标 ^{2} → unicode 上标
  const supMap: Record<string, string> = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
    '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾',
    'n': 'ⁿ',
  }
  // 下标 _{1} → unicode 下标
  const subMap: Record<string, string> = {
    '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
    '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
    '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎',
    'i': 'ᵢ', 'j': 'ⱼ',
  }
  let out = input
  // ^{...} → 整组；^{2}、^{10}、^{12} 都行
  out = out.replace(/\^\{([^{}]+)\}/g, (_, body: string) =>
    body.split('').map(ch => supMap[ch] ?? ch).join(''),
  )
  out = out.replace(/\^([0-9n])/g, (_, ch: string) => supMap[ch] ?? ch)
  // _{...} → 整组
  out = out.replace(/_\{([^{}]+)\}/g, (_, body: string) =>
    body.split('').map(ch => subMap[ch] ?? ch).join(''),
  )
  out = out.replace(/_([0-9ij])/g, (_, ch: string) => subMap[ch] ?? ch)
  return out
}

/** 通用替换：根号 / 乘除 / 不等 / 角度 / 一些常见命令。 */
function replaceCommon(input: string): string {
  let out = input
  // \sqrt{x} / \sqrt[n]{x} → √x
  out = out.replace(/\\sqrt\{([^{}]+)\}/g, '√$1')
  // \times / \cdot → ×
  out = out.replace(/\\times/g, '×')
  out = out.replace(/\\cdot/g, '×')
  // \div → ÷
  out = out.replace(/\\div/g, '÷')
  // \pm → ±
  out = out.replace(/\\pm/g, '±')
  // 不等
  out = out.replace(/\\geq|\\ge/g, '≥')
  out = out.replace(/\\leq|\\le/g, '≤')
  out = out.replace(/\\neq|\\ne/g, '≠')
  out = out.replace(/\\approx/g, '≈')
  out = out.replace(/\\sim/g, '~')
  // 角度
  out = out.replace(/\^\{?\\?circ\}?/g, '°')
  out = out.replace(/\\degree/g, '°')
  // 一些可能漏的转义
  out = out.replace(/\\!/g, '')
  out = out.replace(/\\\,/g, ' ')
  out = out.replace(/\\\\/g, '\\\\')
  return out
}

/**
 * 把任何含 LaTeX 的数学文本转成小学生看得懂的纯文本。
 * 多次幂等调用。
 */
export function formatMath(input: string): string {
  if (!input) return ''
  let out = input
  out = stripMathDelimiters(out)
  out = replaceFrac(out)
  out = replaceCommon(out)
  out = replaceSupSub(out)
  // 处理掉残留的单 $ 符号（极端情况，老 prompt 真的写出了 $5/13$ 这种）
  out = out.replace(/\$/g, '')
  return out.trim()
}

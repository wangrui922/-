// Tailwind mint 色阶守卫（2026-10-23 修复回归）
//
// 背景：tailwind.config.js 的 mint 只定义了 400/500/600，但 src 里
// 实际用到了 50/100/200/300/700。Tailwind JIT 对未定义色阶静默不生成
// 样式——border-mint-300、bg-mint-50 等类名写在模板里却不生效，
// 没有任何报错，视觉bug直到上线才被发现。
//
// 本测试：src 里用到的每个 mint-* 档位，config 里必须定义了对应色值。

import { test } from 'node:test'
import assert from 'node:assert'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name.startsWith('.')) continue
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) out.push(...walk(p))
    else if (/\.(vue|ts|js|tsx)$/.test(name)) out.push(p)
  }
  return out
}

const files = walk(join(root, 'src'))
const used = new Set()
const shadeRe = /mint-(\d+)/g
for (const f of files) {
  const text = readFileSync(f, 'utf8')
  for (const m of text.matchAll(shadeRe)) used.add(Number(m[1]))
}

const config = readFileSync(join(root, 'tailwind.config.js'), 'utf8')
// 取 mint: { ... } 块内的档位
const mintBlock = config.match(/mint:\s*\{([^}]*)\}/)
assert.ok(mintBlock, 'tailwind.config.js 应定义 mint 色阶')
const defined = new Set(
  [...mintBlock[1].matchAll(/(\d+):/g)].map((m) => Number(m[1])),
)

test('src 用到的每个 mint 档位都在 config 里定义', () => {
  assert.ok(used.size > 0, '应能在 src 里找到 mint-* 用法')
  const missing = [...used].filter((s) => !defined.has(s)).sort((a, b) => a - b)
  assert.deepEqual(missing, [], `mint 色阶缺档：${missing.join(', ')}（用了但没定义 = 样式静默失效）`)
})

test('mint 主色锚点不变（400/500/600 与品牌色一致）', () => {
  // 400/500/600 是原有锚点，补档不许顺手改主色值
  assert.match(config, /400:\s*'#34D9A0'/)
  assert.match(config, /500:\s*'#1FBF85'/)
  assert.match(config, /600:\s*'#0FA06B'/)
})

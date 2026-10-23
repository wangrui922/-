/**
 * 监护人同意体验收尾测试（V6.5 合规闸门 · 前端接线锁）。
 *
 * 沿用 tests/practice-filter.test.mjs 的源码断言范式（student-web 无
 * 单测框架，node --test 直接跑）：把「弹窗已挂载 / 兜底已接 / 预查已配 /
 * 三态机完整 / 关闭≠同意」这些隐性接线变成显式断言，重构改断即红。
 *
 * 与后端 tests/test_consent_gate.py::test_frontend_backend_contract_strings
 * 的分工：那边锁跨端**常量契约**（话术关键词 / 版本号），这边锁前端**装配
 * 接线**（组件挂载、拦截器分支、路由守卫、store 动作面）。
 *
 * B02 三态展示收尾（家长端卡片 / 孩子端指引条）：三态判定抽成纯函数
 * （utils/consent-gate.ts）后直接 import 测，**不在测试里挂载组件**；
 * 视图侧只锁静态接线（数据源、文案、按钮、并行加载、静默降级）。
 */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  deriveChildConsentState,
  formatConsentDate,
} from '../src/utils/consent-gate.ts'

const appVue = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
const httpTs = await readFile(new URL('../src/api/http.ts', import.meta.url), 'utf8')
const complianceTs = await readFile(
  new URL('../src/api/compliance.ts', import.meta.url),
  'utf8',
)
const consentTs = await readFile(
  new URL('../src/stores/consent.ts', import.meta.url),
  'utf8',
)
const routerTs = await readFile(new URL('../src/router/index.ts', import.meta.url), 'utf8')
const practiceVue = await readFile(
  new URL('../src/views/Practice.vue', import.meta.url),
  'utf8',
)
const dialogVue = await readFile(
  new URL('../src/components/GuardianConsentDialog.vue', import.meta.url),
  'utf8',
)
const childDetailVue = await readFile(
  new URL('../src/views/parent/ChildDetail.vue', import.meta.url),
  'utf8',
)

test('App.vue mounts the global consent dialog bound to store state', () => {
  assert.match(
    appVue,
    /import GuardianConsentDialog from '@\/components\/GuardianConsentDialog\.vue'/,
  )
  // 开合由 store 收口：预查未同意 / 403 兜底都是置 dialogOpen=true
  assert.match(
    appVue,
    /<GuardianConsentDialog :open="consentState\.dialogOpen" @close="closeDialog" \/>/,
  )
})

test('http.ts opens the consent dialog on 403 gate denials', () => {
  // 拦截器从 store 引入 openDialog
  assert.match(httpTs, /import \{ openDialog \} from '@\/stores\/consent'/)
  // 403 且话术命中 CONSENT_GATE_HINT → 拉弹窗（深链 / 游戏页兜底）
  assert.match(
    httpTs,
    /if \(\s*code === 403 &&[\s\S]*?includes\(CONSENT_GATE_HINT\)[\s\S]*?\) \{\s*openDialog\(\)/,
  )
  // 兜底只弹窗、不改拒绝路径：调用方照旧收到 ApiError
  assert.match(httpTs, /return Promise\.reject\(new ApiError\(code, msg\)\)/)
})

test('consent api layer pins the gate version and speaks camelCase', () => {
  // 版本常量与后端 system_config.DEFAULTS['CONSENT_VERSION'] 对齐（v1）
  assert.match(complianceTs, /export const CONSENT_VERSION = 'v1'/)
  // GET 显式带 snake_case query（FastAPI query 参数不吃 body 的 camelCase alias）
  assert.match(complianceTs, /consent_version: CONSENT_VERSION/)
  // POST body 字段 camelCase（对齐后端 GuardianConsentIn 的 alias_generator）
  assert.match(complianceTs, /guardianPhone: string/)
  assert.match(complianceTs, /consentVersion: string/)
  assert.match(complianceTs, /agreed: boolean/)
})

test('consent store exposes the three-state machine and phone bounds', () => {
  // 三态：unknown 不误弹 / granted 放行 / required 主动弹
  assert.match(consentTs, /'unknown' \| 'granted' \| 'required'/)
  // 动作面齐全（预查 / 提交 / 开关 / 重置）
  for (const name of [
    'checkStatus',
    'submitAndGrant',
    'openDialog',
    'closeDialog',
    'resetConsent',
  ]) {
    assert.match(
      consentTs,
      new RegExp(`export (async )?function ${name}\\(`),
      `stores/consent.ts 缺少导出 ${name}`,
    )
  }
  // 手机号边界 5-20，与后端 GuardianConsentIn.guardian_phone 的 min/max 对齐
  assert.match(consentTs, /PHONE_MIN_LEN = 5/)
  assert.match(consentTs, /PHONE_MAX_LEN = 20/)
  // 版本常量从 api/compliance 引入，store 不另存会漂移的副本
  assert.match(
    consentTs,
    /import \{[^}]*\bCONSENT_VERSION\b[^}]*\} from '@\/api\/compliance'/,
  )
})

test('router prechecks consent and resets it on account switch', () => {
  assert.match(
    routerTs,
    /import \{ checkStatus, consentState, resetConsent \} from '@\/stores\/consent'/,
  )
  // 未登录先清同意态：换账号登录后重新预查，不继承上个账号的结论
  assert.match(routerTs, /if \(!isParentArea\) resetConsent\(\)/)
  // 已登录 + unknown → 异步预查（不阻塞导航）
  assert.match(
    routerTs,
    /if \(!isPublic && !isParentArea && consentState\.status === 'unknown'\) \{[\s\S]*?void checkStatus\(\)/,
  )
})

test('Practice.vue bootstraps the consent check before deep-link practice', () => {
  assert.match(practiceVue, /import \{ checkStatus, consentState \} from '@\/stores\/consent'/)
  // 深链自动开练前兜底预查：unknown 时先查再放行
  assert.match(
    practiceVue,
    /if \(consentState\.status === 'unknown'\) \{[\s\S]*?await checkStatus\(\)/,
  )
})

test('consent dialog keeps close distinct from granting', () => {
  // 提交走 store 的 submitAndGrant（成败由 store 收口）
  assert.match(
    dialogVue,
    /import \{ consentState, submitAndGrant \} from '@\/stores\/consent'/,
  )
  // ✕ 与点遮罩都只发 close，不提交同意——服务端闸门该拦还是拦
  assert.match(dialogVue, /@click="emit\('close'\)"/)
  assert.match(dialogVue, /@click\.self="emit\('close'\)"/)
})

/* ---------- B02 三态展示：家长端卡片（纯函数判定 + 视图接线锁） ---------- */

test('parent three-state decision is a pure function over the child payload', () => {
  // granted：服务端按当前版本权威判定放行（前端不拿 consentVersion 猜）
  assert.equal(
    deriveChildConsentState({
      granted: true,
      agreedAt: '2026-01-02T03:04:05+00:00',
      consentVersion: 'v1',
      currentVersion: 'v1',
    }),
    'granted',
  )
  // 未同意 + 有旧版本记录 → stale（监护人规则更新过，旧同意作废）
  assert.equal(
    deriveChildConsentState({
      granted: false,
      agreedAt: '2026-01-02T03:04:05+00:00',
      consentVersion: 'v0',
      currentVersion: 'v1',
    }),
    'stale',
  )
  // 未同意 + 从无记录 → required
  assert.equal(
    deriveChildConsentState({
      granted: false,
      agreedAt: null,
      consentVersion: null,
      currentVersion: 'v1',
    }),
    'required',
  )
})

test('parent card formats agreedAt as a plain date and never leaks Invalid Date', () => {
  assert.equal(formatConsentDate('2026-01-02T03:04:05+00:00'), '2026-01-02')
  assert.equal(formatConsentDate('2026-01-02'), '2026-01-02')
  // 空值 / 畸形输入一律回落「—」，绝不把 Invalid Date 漏到 UI 上
  assert.equal(formatConsentDate(null), '—')
  assert.equal(formatConsentDate(undefined), '—')
  assert.equal(formatConsentDate(''), '—')
  assert.equal(formatConsentDate('not-a-date'), '—')
})

test('parent ChildDetail.vue wires the three-state card above the weekly card', () => {
  // 数据源与三态派生：判定走纯函数，模板不重复猜版本
  assert.match(childDetailVue, /import \{[^}]*getChildConsent[^}]*\} from '@\/api\/family'/)
  assert.match(
    childDetailVue,
    /import \{[^}]*deriveChildConsentState[^}]*\} from '@\/utils\/consent-gate'/,
  )
  assert.match(childDetailVue, /deriveChildConsentState\(consent\.value\)/)
  assert.match(childDetailVue, /import \{ openDialog \} from '@\/stores\/consent'/)
  // 三态文案（B02 口径，家长口语）
  assert.equal(childDetailVue.includes('✓ 监护人已同意'), true)
  assert.equal(
    childDetailVue.includes(
      '同意版本 {{ consent.consentVersion }} · 同意时间 {{ formatConsentDate(consent.agreedAt) }}',
    ),
    true,
  )
  assert.equal(childDetailVue.includes('⚠ 监护人规则已更新，需重新确认'), true)
  assert.equal(childDetailVue.includes('⚪ 监护人尚未同意'), true)
  assert.equal(childDetailVue.includes('孩子需要您同意后才能开始练习'), true)
  // 行动按钮：「去重新确认」/「去同意」都唤起全局同意弹窗（openDialog），共两处
  assert.equal(childDetailVue.includes('去重新确认'), true)
  assert.equal(childDetailVue.includes('去同意'), true)
  assert.equal((childDetailVue.match(/@click="openDialog"/g) ?? []).length, 2)
  // 与概览数据并行加载：onMounted 里 loadTab 与 loadConsent 都触发，互不 await
  assert.match(
    childDetailVue,
    /onMounted\(\(\) => \{[\s\S]*?loadTab\(\)[\s\S]*?void loadConsent\(\)/,
  )
  // 卡片在「本周数据」上方（概览 Tab 内）——只截取 summary 区块比对，
  // 避开文件顶部注释里同样出现「本周数据」的干扰
  const summarySection = childDetailVue.slice(
    childDetailVue.indexOf(`v-if="tab === 'summary'"`),
    childDetailVue.indexOf(`v-if="tab === 'mistakes'"`),
  )
  assert.ok(summarySection.includes('✓ 监护人已同意'))
  assert.ok(
    summarySection.indexOf('✓ 监护人已同意') < summarySection.indexOf('本周数据'),
  )
  // 失败静默降级：只显示「状态获取失败」小字，不走 error / toMessage 弹错
  assert.equal(childDetailVue.includes('状态获取失败'), true)
  assert.match(
    childDetailVue,
    /catch \{[\s\S]*?consent\.value = null[\s\S]*?consentFailed\.value = true/,
  )
})

test('student-side consent banner covers the practice entry without a second bar', () => {
  // 孩子端提示条由 GuardianConsentBanner（挂 StudentLayout，全孩子端页面常驻）承担，
  // Practice.vue 不再挂第二条——否则练习页会出现双条重复。这里锁住这个分工：
  // Practice.vue 只有预查接线，不渲染自己的同意条。
  assert.match(practiceVue, /import \{ checkStatus, consentState \} from '@\/stores\/consent'/)
  assert.equal(practiceVue.includes('GuardianConsentBanner'), false)
  assert.equal(practiceVue.includes('需要监护人同意才能练习'), false)
})

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  deriveChildConsentState,
  deriveConsentGateState,
  formatConsentDate,
} from '../src/utils/consent-gate.ts'

const read = (relPath) =>
  readFile(new URL(`../src/${relPath}`, import.meta.url), 'utf8')

/* ---------- 纯函数：三态推导 ---------- */

test('deriveConsentGateState: never agreed -> required', () => {
  assert.equal(
    deriveConsentGateState({ agreed: false, agreedAt: null, consentVersion: null }, 'v1'),
    'required',
  )
})

test('deriveConsentGateState: current version agreed -> granted', () => {
  assert.equal(
    deriveConsentGateState(
      { agreed: true, agreedAt: '2026-01-02T03:04:05+00:00', consentVersion: 'v1' },
      'v1',
    ),
    'granted',
  )
})

test('deriveConsentGateState: stale version agreed -> stale', () => {
  assert.equal(
    deriveConsentGateState(
      { agreed: true, agreedAt: '2026-01-02T03:04:05+00:00', consentVersion: 'v0' },
      'v1',
    ),
    'stale',
  )
})

test('deriveChildConsentState: server granted verdict wins', () => {
  assert.equal(
    deriveChildConsentState({
      granted: true,
      agreedAt: '2026-01-02T03:04:05+00:00',
      consentVersion: 'v1',
      currentVersion: 'v1',
    }),
    'granted',
  )
})

test('deriveChildConsentState: not granted with old record -> stale, without -> required', () => {
  assert.equal(
    deriveChildConsentState({
      granted: false,
      agreedAt: '2026-01-02T03:04:05+00:00',
      consentVersion: 'v0',
      currentVersion: 'v1',
    }),
    'stale',
  )
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

test('formatConsentDate: ISO8601 -> YYYY-MM-DD, junk -> dash', () => {
  assert.equal(formatConsentDate('2026-01-02T03:04:05+00:00'), '2026-01-02')
  assert.equal(formatConsentDate('2026-01-02'), '2026-01-02')
  assert.equal(formatConsentDate(null), '—')
  assert.equal(formatConsentDate(undefined), '—')
  assert.equal(formatConsentDate(''), '—')
  assert.equal(formatConsentDate('not-a-date'), '—')
})

/* ---------- 静态断言：store / api / 组件接线 ---------- */

test('consent store implements the three-state check', async () => {
  const src = await read('stores/consent.ts')
  // 三态类型：required 之外新增 stale
  assert.match(src, /export type ConsentStatus = 'unknown' \| 'granted' \| 'required' \| 'stale'/)
  // 二查（不限版本）区分「从未同意 / 版本失效」，复用纯函数推导
  assert.match(src, /import \{[^}]*getLatestConsent[^}]*\} from '@\/api\/compliance'/)
  assert.match(src, /import \{[^}]*deriveConsentGateState[^}]*\} from '@\/utils\/consent-gate'/)
  assert.match(src, /deriveConsentGateState\(latest, CONSENT_VERSION\)/)
  // 详情字段进状态机并在重置时清掉（防止跨账号残留）
  assert.match(src, /agreedAt: string \| null/)
  assert.match(src, /consentVersion: string \| null/)
  assert.match(src, /function resetConsent\(\): void \{[\s\S]*?consentState\.agreedAt = null/)
  assert.match(src, /function resetConsent\(\): void \{[\s\S]*?consentState\.consentVersion = null/)
})

test('compliance api adds version-less latest query', async () => {
  const src = await read('api/compliance.ts')
  assert.match(
    src,
    /export function getLatestConsent\(\): Promise<ConsentStatusData> \{\s*return get<ConsentStatusData>\('\/compliance\/guardian-consent'\)/,
  )
  // 版本化查询（门控同口径）保持不变
  assert.match(src, /consent_version: CONSENT_VERSION/)
})

test('family api exposes child consent status query', async () => {
  const src = await read('api/family.ts')
  assert.match(src, /export interface ChildConsentStatus \{/)
  assert.match(src, /export function getChildConsent\(studentId: string\): Promise<ChildConsentStatus>/)
  assert.match(src, /\/family\/children\/\$\{studentId\}\/consent/)
})

test('student banner shows required and stale guidance with one-click entry', async () => {
  const src = await read('components/GuardianConsentBanner.vue')
  assert.equal(src.includes('需要监护人同意才能练习'), true)
  assert.equal(src.includes('监护人规则已更新，需重新确认'), true)
  assert.equal(src.includes('去获得监护人同意'), true)
  assert.equal(src.includes('重新确认'), true)
  // 一键唤起同意流程：走 store 的 openDialog（不另造弹窗状态）
  assert.match(src, /import \{ consentState, openDialog \} from '@\/stores\/consent'/)
  assert.match(src, /@click="openDialog"/)
})

test('student layout mounts the consent banner', async () => {
  const src = await read('layouts/StudentLayout.vue')
  assert.match(src, /import GuardianConsentBanner from '@\/components\/GuardianConsentBanner\.vue'/)
  assert.equal(src.includes('<GuardianConsentBanner />'), true)
})

test('parent child detail renders the three-state consent card', async () => {
  const src = await read('views/parent/ChildDetail.vue')
  // 三态文案与 B02 任务口径逐字对齐（寇豆码按 team-lead spec 定稿）：
  //   已同意    ✓ 监护人已同意 + 同意版本 vX · 同意时间 YYYY-MM-DD
  //   版本失效  ⚠ 监护人规则已更新，需重新确认 + 上次/当前版本小字
  //   未同意    ⚪ 监护人尚未同意 + 孩子需要您同意后才能开始练习
  assert.equal(src.includes('✓ 监护人已同意'), true)
  assert.equal(
    src.includes(
      '同意版本 {{ consent.consentVersion }} · 同意时间 {{ formatConsentDate(consent.agreedAt) }}',
    ),
    true,
  )
  assert.equal(src.includes('⚠ 监护人规则已更新，需重新确认'), true)
  assert.equal(src.includes('⚪ 监护人尚未同意'), true)
  assert.equal(src.includes('孩子需要您同意后才能开始练习'), true)
  // 两个行动按钮（去重新确认 / 去同意）都走 store 的 openDialog，复用全局同意弹窗
  assert.match(src, /import \{ openDialog \} from '@\/stores\/consent'/)
  assert.equal((src.match(/@click="openDialog"/g) ?? []).length, 2)
  // 数据源与三态派生
  assert.match(src, /import \{[^}]*getChildConsent[^}]*\} from '@\/api\/family'/)
  assert.match(src, /import \{[^}]*deriveChildConsentState[^}]*\} from '@\/utils\/consent-gate'/)
  assert.match(src, /deriveChildConsentState\(consent\.value\)/)
})

test('consent gate hint in http.ts stays aligned with backend denial message', async () => {
  // 与后端 test_consent_gate.py::test_frontend_backend_contract_strings 互为双保险：
  // 403 兜底关键词必须仍是后端话术「使用练习功能需要监护人同意」的子串
  const src = await read('api/http.ts')
  const match = src.match(/CONSENT_GATE_HINT\s*=\s*'([^']+)'/)
  assert.ok(match, 'http.ts 应定义 CONSENT_GATE_HINT 常量')
  assert.equal('使用练习功能需要监护人同意'.includes(match[1]), true)
})

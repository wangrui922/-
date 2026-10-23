/**
 * 监护人同意三态推导（V6.5 合规闸门 · B02 体验收尾）。
 *
 * 纯函数、零依赖：**不 import 任何 ``@/`` 模块**（本文件被
 * ``node --test`` 直接 import 做单测，见 tests/guardian-consent-state.test.mjs），
 * 也被 stores/consent.ts（孩子端）与 views/parent/ChildDetail.vue（家长端）复用，
 * 保证双端「三态」判定口径只有这一份实现。
 *
 * 三态定义（与服务端 ``deps.require_guardian_consent`` 门控口径对齐）：
 *   granted  当前规则版本已同意 → 放行练习；
 *   required 从未同意（没有任何同意记录）；
 *   stale    有同意记录但版本过期（CONSENT_VERSION 变更后旧同意作废），
 *            需按新规则重新同意——服务端门控同样会 403 拦下。
 */

/** 同意三态（UI 展示口径）。 */
export type ConsentGateState = 'granted' | 'required' | 'stale'

/** 同意事实：GET /compliance/guardian-consent 出参的最小字段集。 */
export interface ConsentFact {
  /** 是否已同意（查询口径内的） */
  agreed: boolean
  /** 同意时间（ISO8601 UTC）；从未同意为 null */
  agreedAt: string | null
  /** 同意对应的规则版本；从未同意为 null */
  consentVersion: string | null
}

/** 家长端孩子同意接口出参（camelCase，对齐后端 family.py 的 consent 端点）。 */
export interface ChildConsentFact {
  /** 服务端按「当前版本」判定过的同意结论（权威，前端直接信） */
  granted: boolean
  /** 同意时间（ISO8601 UTC）；从未同意为 null */
  agreedAt: string | null
  /** 同意记录对应的规则版本；从未同意为 null（版本失效时为旧版本号） */
  consentVersion: string | null
  /** 服务端当前规则版本（CONSENT_VERSION） */
  currentVersion: string
}

/**
 * 由「最近一条同意记录 + 当前规则版本」推导三态（孩子端口径）。
 *
 * 调用方应先用**带当前版本**的查询确认 ``agreed``（门控同口径）；
 * 未同意时再用不限版本的查询拿到最近一条记录传进来——此时
 * ``agreed=true`` 即意味着「有记录但版本过期」（极少见的并发 race 下
 * 也可能拿到当前版本，函数会正确返回 granted）。
 *
 * @param fact 最近一条同意记录（不限版本查询的结果）
 * @param currentVersion 当前规则版本（前端 ``CONSENT_VERSION`` 常量）
 */
export function deriveConsentGateState(
  fact: ConsentFact,
  currentVersion: string,
): ConsentGateState {
  if (!fact.agreed) return 'required'
  return fact.consentVersion === currentVersion ? 'granted' : 'stale'
}

/**
 * 家长端出参 → 三态。
 *
 * 服务端已经按当前版本判定好 ``granted``（权威口径，避免前端再拿
 * ``consentVersion`` 猜一遍），这里只负责区分两种未同意态：
 * 有旧版本记录（``consentVersion`` 非空）即「版本失效」，否则「从未同意」。
 */
export function deriveChildConsentState(fact: ChildConsentFact): ConsentGateState {
  if (fact.granted) return 'granted'
  return fact.consentVersion ? 'stale' : 'required'
}

/**
 * ISO8601 同意时间 → ``YYYY-MM-DD``（双端统一展示口径）。
 *
 * 直接截取日期部分（UTC 存储口径，与 ChildDetail 里 expiresAt 的
 * ``split('T')[0]`` 既有做法一致）；空值 / 畸形输入回落 ``'—'``，
 * 绝不把 ``Invalid Date`` 漏到 UI 上。
 */
export function formatConsentDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const trimmed = iso.trim()
  if (!trimmed) return '—'
  const datePart = trimmed.slice(0, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(datePart)) return '—'
  if (Number.isNaN(Date.parse(trimmed))) return '—'
  return datePart
}

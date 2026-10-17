/**
 * 合规接口封装（V6.5 监护人同意门控 · T13 留痕后的前端联调层）。
 *
 * 对应后端 `backend/app/api/compliance.py` 的两个端点：
 *   GET  /api/compliance/guardian-consent  查询当前账号的同意状态
 *   POST /api/compliance/guardian-consent  提交监护人同意（幂等）
 *
 * ⚠️ 命名硬约束（与 api/practice.ts 相反，别搞混）：
 *   compliance 的 schemas 继承 `schemas/common.BaseSchema` 的 camelCase
 *   alias_generator（见 backend/app/schemas/compliance.py 的
 *   GuardianConsentIn），出入参一律 **camelCase**：guardianPhone /
 *   consentVersion / agreed——这里**不能**转 snake_case，否则字段对不上。
 *
 * 响应外壳 { code, msg, data } 已由 api/http.ts 拦截器拆封，本层拿到、
 * 返回的都直接是 data；code !== 0 会抛 ApiError。
 */
import { get, post } from '@/api/http'

/**
 * 当前同意的规则版本。
 *
 * 与后端 system_config 的 CONSENT_VERSION 默认值一致；服务端门控按
 * 「当前版本」查同意记录，规则修订换版本后旧同意一律失效——前端提交
 * 与预查共用这一个常量，避免两处版本号漂移。
 */
export const CONSENT_VERSION = 'v1'

/** GET /compliance/guardian-consent 出参（data 部分，camelCase）。 */
export interface ConsentStatusData {
  /** 当前版本是否已同意 */
  agreed: boolean
  /** 同意时间（ISO8601 UTC）；从未同意为 null */
  agreedAt: string | null
  /** 同意对应的规则版本；从未同意为 null */
  consentVersion: string | null
}

/** POST /compliance/guardian-consent 入参（camelCase，对齐 GuardianConsentIn）。 */
export interface SubmitConsentBody {
  /** 监护人手机号（同意留痕备查，5-20 位） */
  guardianPhone: string
  /** 同意的规则版本号 */
  consentVersion: string
  /** 是否同意；仅 true 会被后端记录，false 按 400 拒绝 */
  agreed: boolean
}

/**
 * 查询当前账号的监护人同意状态。
 *
 * 显式带 `consent_version=v1`（GET 的 query 参数名是 snake_case——
 * FastAPI query 参数不吃 body 那套 camelCase alias）：与服务端门控
 * `require_guardian_consent` 按「当前版本」查询的口径逐字对齐，
 * 老版本的同意记录在这里就算 agreed=false，不会误放行。
 *
 * @returns { agreed, agreedAt, consentVersion }；从未同意时三者分别为 false / null / null
 */
export function getConsentStatus(): Promise<ConsentStatusData> {
  return get<ConsentStatusData>('/compliance/guardian-consent', {
    consent_version: CONSENT_VERSION,
  })
}

/**
 * 提交一次监护人同意（后端幂等：同账号同版本重复提交返回已有记录，不报错）。
 *
 * @param body camelCase 入参；agreed 仅 true 会被记录
 * @returns 落库后的同意记录（恒为已同意态）
 */
export function submitConsent(body: SubmitConsentBody): Promise<ConsentStatusData> {
  return post<ConsentStatusData>('/compliance/guardian-consent', body)
}

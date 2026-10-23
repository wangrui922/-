/**
 * 监护人同意态存储（V6.5 合规闸门 · T13 前端门控 · B02 三态体验收尾）。
 *
 * 仿 stores/practice.ts 的组织风格：Vue reactive + 导出动作函数
 * （**不引 pinia**——package.json 里没有 pinia，全项目 store 均为这一风格）。
 *
 * 四态（前端侧）：
 *   unknown   还没查到（首屏 / 查询失败时保持此态，**不误弹** dialog）
 *   granted   当前版本已同意 → 放行练习
 *   required  从未同意 → 主动弹 dialog 引导提交
 *   stale     有同意记录但版本过期（CONSENT_VERSION 变更后旧同意作废）
 *             → 同样弹 dialog，但对外展示「规则已更新，需重新确认」
 *
 * 定位（设计 §边界）：前端门控只是体验优化（提前弹、少撞墙）；真正的闸
 * 在服务端（CONSENT_GATE_ENABLED 开启后，练习类 POST 未同意返 403）。
 * 深链 / 游戏页等绕过预查的入口，由 api/http.ts 响应拦截的 403 兜底
 * 拉起本 store 的弹窗，前端判断不作为唯一拦截点。
 */
import { reactive } from 'vue'

import { toMessage } from '@/api/http'
import {
  CONSENT_VERSION,
  getConsentStatus,
  getLatestConsent,
  submitConsent,
  type ConsentStatusData,
} from '@/api/compliance'
import { deriveConsentGateState } from '@/utils/consent-gate'

/**
 * 同意态四态。
 *
 * ``stale`` 与 ``required`` 对门控是同一后果（都要重新同意），分开只为
 * 展示话术不同：孩子端指引条 / 家长端卡片据态取文案。
 */
export type ConsentStatus = 'unknown' | 'granted' | 'required' | 'stale'

/** 监护人同意全局状态。 */
export interface ConsentState {
  status: ConsentStatus
  /** 同意引导弹窗是否打开 */
  dialogOpen: boolean
  /** 同意提交中（防重复点击） */
  submitting: boolean
  /** 最近一次提交 / 校验失败的用户可读文案 */
  error: string
  /** 最近一条同意时间（ISO8601 UTC）；从未同意为 null（三态展示用） */
  agreedAt: string | null
  /** 最近一条同意记录的规则版本；从未同意为 null（版本失效时为旧版本号） */
  consentVersion: string | null
}

/** 供组件模板直接读取的响应式同意态。 */
export const consentState = reactive<ConsentState>({
  status: 'unknown',
  dialogOpen: false,
  submitting: false,
  error: '',
  agreedAt: null,
  consentVersion: null,
})

/** 手机号长度边界（与后端 GuardianConsentIn.guardian_phone 的 min/max_length 对齐）。 */
const PHONE_MIN_LEN = 5
const PHONE_MAX_LEN = 20

/** checkStatus 的在途 Promise：并发调用复用同一个请求，避免重复查。 */
let statusInFlight: Promise<ConsentStatus> | null = null

/** 把一次查询结果里的「同意时间 / 版本」收进全局态（展示字段，与 status 解耦）。 */
function applyDetail(data: ConsentStatusData): void {
  consentState.agreedAt = data.agreedAt
  consentState.consentVersion = data.consentVersion
}

/**
 * 预查同意状态（GET /api/compliance/guardian-consent）。
 *
 * 两步查（仅在未同意时走第二步）：
 *   1. 带当前版本查（门控同口径）：已同意 → granted，顺手收掉弹窗；
 *   2. 未同意 → 再查最近一条记录（不限版本），据
 *      ``deriveConsentGateState`` 区分 required（从未同意）与
 *      stale（旧版本同意失效）——两者都主动弹 dialog，少撞墙。
 *
 * 降级口径：
 *   - 第一步失败（网络 / 5xx，ApiError 抛出）→ 保持 unknown，**不弹**
 *     dialog——避免把一次故障误报成「需要同意」；此时真门的服务端
 *     403 兜底（http.ts 拦截器）会接住，不影响最终拦截效果；
 *   - 第二步失败 → 第一步的结论（当前版本未同意）仍是事实，按
 *     required 处理并弹窗，不因二查故障把已知结论吞掉。
 *
 * @returns 查询后的同意态（首查失败时为 unknown）
 */
export function checkStatus(): Promise<ConsentStatus> {
  if (statusInFlight) return statusInFlight
  statusInFlight = (async (): Promise<ConsentStatus> => {
    try {
      const current = await getConsentStatus()
      if (current.agreed) {
        applyDetail(current)
        consentState.status = 'granted'
        consentState.dialogOpen = false
      } else {
        let latest: ConsentStatusData = current
        try {
          latest = await getLatestConsent()
        } catch {
          // 二查失败：current 即「当前版本未同意」的事实，按 required 展示
        }
        applyDetail(latest)
        const state = deriveConsentGateState(latest, CONSENT_VERSION)
        consentState.status = state
        if (state === 'granted') {
          // 并发 race：两步之间用户刚完成同意——以最新事实为准，不弹窗
          consentState.dialogOpen = false
        } else {
          openDialog()
        }
      }
    } catch {
      // 首查失败：保持 unknown，不误弹（见函数注释的降级说明）
    } finally {
      statusInFlight = null
    }
    return consentState.status
  })()
  return statusInFlight
}

/**
 * 提交监护人同意并放行（POST /api/compliance/guardian-consent，幂等）。
 *
 * 手机号长度在前端先按后端口径（5-20）校验，非法就不打请求；
 * 成功 → granted（详情取落库回包）并关 dialog；失败 → error 留文案，
 * dialog 保持打开可重试。
 *
 * @param phone 监护人手机号（仅用于同意留痕）
 * @returns 是否同意成功
 */
export async function submitAndGrant(phone: string): Promise<boolean> {
  if (consentState.submitting) return false

  const trimmed = phone.trim()
  if (trimmed.length < PHONE_MIN_LEN || trimmed.length > PHONE_MAX_LEN) {
    consentState.error = `请填写监护人手机号（${PHONE_MIN_LEN}-${PHONE_MAX_LEN} 位）`
    return false
  }

  consentState.submitting = true
  consentState.error = ''

  try {
    const data = await submitConsent({
      guardianPhone: trimmed,
      consentVersion: CONSENT_VERSION,
      agreed: true,
    })
    applyDetail(data)
    consentState.status = 'granted'
    consentState.dialogOpen = false
    return true
  } catch (error) {
    consentState.error = toMessage(error, '提交失败了，再试一次')
    return false
  } finally {
    consentState.submitting = false
  }
}

/** 打开同意引导弹窗（预查未同意 / http.ts 的 403 兜底时调用）。 */
export function openDialog(): void {
  consentState.dialogOpen = true
}

/**
 * 关闭弹窗。
 *
 * 注意：关闭 ≠ 同意——服务端闸门该拦还是拦，下次开练仍会 403 被接住。
 */
export function closeDialog(): void {
  consentState.dialogOpen = false
}

/**
 * 清空同意态（退出登录 / 跳登录时由路由守卫调用）。
 *
 * 不这么做的话：账号 A 的 granted / required 结论会被账号 B 继承，
 * 而预查只在 unknown 时触发，B 将带着 A 的结论到处走。
 */
export function resetConsent(): void {
  consentState.status = 'unknown'
  consentState.dialogOpen = false
  consentState.submitting = false
  consentState.error = ''
  consentState.agreedAt = null
  consentState.consentVersion = null
}

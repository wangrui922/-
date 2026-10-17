/**
 * 监护人同意态存储（V6.5 合规闸门 · T13 前端门控）。
 *
 * 仿 stores/practice.ts 的组织风格：Vue reactive + 导出动作函数
 * （**不引 pinia**——package.json 里没有 pinia，全项目 store 均为这一风格）。
 *
 * 三态：
 *   unknown   还没查到（首屏 / 查询失败时保持此态，**不误弹** dialog）
 *   granted   当前版本已同意 → 放行练习
 *   required  未同意 → 主动弹 dialog 引导提交
 *
 * 定位（设计 §边界）：前端门控只是体验优化（提前弹、少撞墙）；真正的闸
 * 在服务端（CONSENT_GATE_ENABLED 开启后，练习类 POST 未同意返 403）。
 * 深链 / 游戏页等绕过预查的入口，由 api/http.ts 响应拦截的 403 兜底
 * 拉起本 store 的弹窗，前端判断不作为唯一拦截点。
 */
import { reactive } from 'vue'

import { toMessage } from '@/api/http'
import { CONSENT_VERSION, getConsentStatus, submitConsent } from '@/api/compliance'

/** 同意态三态。 */
export type ConsentStatus = 'unknown' | 'granted' | 'required'

/** 监护人同意全局状态。 */
export interface ConsentState {
  status: ConsentStatus
  /** 同意引导弹窗是否打开 */
  dialogOpen: boolean
  /** 同意提交中（防重复点击） */
  submitting: boolean
  /** 最近一次提交 / 校验失败的用户可读文案 */
  error: string
}

/** 供组件模板直接读取的响应式同意态。 */
export const consentState = reactive<ConsentState>({
  status: 'unknown',
  dialogOpen: false,
  submitting: false,
  error: '',
})

/** 手机号长度边界（与后端 GuardianConsentIn.guardian_phone 的 min/max_length 对齐）。 */
const PHONE_MIN_LEN = 5
const PHONE_MAX_LEN = 20

/** checkStatus 的在途 Promise：并发调用复用同一个请求，避免重复查。 */
let statusInFlight: Promise<ConsentStatus> | null = null

/**
 * 预查同意状态（GET /api/compliance/guardian-consent）。
 *
 * - 后端 200 且已同意 → granted（顺手收掉可能开着的弹窗）；
 * - 后端 200 且未同意 → required 并**主动弹** dialog（提前弹，少撞墙）；
 * - 请求失败（网络 / 5xx，ApiError 抛出）→ 保持 unknown，**不弹** dialog
 *   ——避免把一次故障误报成「需要同意」；此时真门的服务端 403 兜底
 *   （http.ts 拦截器）会接住，不影响最终拦截效果。
 *
 * @returns 查询后的同意态（失败时为 unknown）
 */
export function checkStatus(): Promise<ConsentStatus> {
  if (statusInFlight) return statusInFlight
  statusInFlight = (async (): Promise<ConsentStatus> => {
    try {
      const data = await getConsentStatus()
      if (data.agreed) {
        consentState.status = 'granted'
        consentState.dialogOpen = false
      } else {
        consentState.status = 'required'
        openDialog()
      }
    } catch {
      // 查失败：保持 unknown，不误弹（见函数注释的降级说明）
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
 * 成功 → granted 并关 dialog；失败 → error 留文案，dialog 保持打开可重试。
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
    await submitConsent({
      guardianPhone: trimmed,
      consentVersion: CONSENT_VERSION,
      agreed: true,
    })
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
}

/**
 * DNA-005 学习 DNA 聚合接口封装（GET /api/students/{id}/learning-dna）。
 *
 * 与 T04 画像接口（profile.ts）的区别：
 *   - T04 读 student_mastery 物化缓存（profile_service 主动重算后落表）；
 *   - DNA-005 由埋点链路实时驱动（record_learning_event → update_mastery_from_event
 *     同事务 upsert student_knowledge_mastery），应答即最新，无缓存概念。
 *
 * 本模块同时提供两个适配函数，把聚合的 mastery_summary 转成既有展示契约：
 *   - toMasteryData      -> 学生端 MasteryData（DuMate 知识星图 / 掌握度面板 / HUD）
 *   - toChildMasteryList -> 家长端 ChildMastery[]
 * 字段口径与 backend/app/services/student_dna_service.py 的 get_learning_dna 一一对应。
 */
import { get } from '@/api/http'
import type { ChildMastery } from '@/api/family'
import type { MasteryData, MasteryItem, MasteryLevel } from '@/types/profile'

/** DNA-005 mastery_summary 单条（含 V6.5 增补展示字段）。 */
export interface DnaMasterySummaryItem {
  knowledge_point_id: string
  /** 知识点名称（关联 knowledge_points，缺失为空串） */
  kp_name: string
  /** 掌握度 0~1 */
  mastery: number
  /** 置信度 0~1 */
  confidence: number
  /** 0 未入门 / 1 薄弱 / 2 基本掌握 / 3 熟练（T04 同源口径） */
  level: MasteryLevel
  /** 错误率 0~1 */
  error_rate: number
  practice_count: number
  correct_count: number
  wrong_count: number
  last_practice_at: string | null
  last_correct_at: string | null
  last_wrong_at: string | null
  updated_at: string | null
}

/** DNA-005 最近学习事件（当前展示面暂不消费）。 */
export interface DnaRecentEvent {
  event_id: string
  event_type: string
  knowledge_point_id: string
  kp_name: string
  is_correct: boolean
  created_at: string
}

/** GET /api/students/{student_id}/learning-dna 的 data 载荷。 */
export interface LearningDnaData {
  student_id: string
  profile: Record<string, unknown>
  mastery_summary: DnaMasterySummaryItem[]
  recent_events: DnaRecentEvent[]
}

/** 掌握度等级 → 中文标签（与后端 family_service._MASTERY_LABEL 保持一致）。 */
export const MASTERY_LABEL: Record<MasteryLevel, string> = {
  0: '未入门',
  1: '薄弱',
  2: '基本掌握',
  3: '熟练',
}

/**
 * 拉取学习 DNA 聚合（本人 / 绑定家长可见，实时驱动）。
 *
 * @param studentId 学生 ID
 */
export function getLearningDna(studentId: string): Promise<LearningDnaData> {
  return get<LearningDnaData>(`/students/${studentId}/learning-dna`)
}

/**
 * DNA mastery_summary → 学生端 MasteryData（T04 兼容契约）。
 *
 * 语义对齐说明：
 *   - score = mastery（0~1，与 T04 mastery_score 同量纲，无需缩放）
 *   - level 由后端按 T04 同源 grade_level 阈值给出
 *   - effective_attempts：DNA 无时效衰减语义，以累计练习次数充当有效样本量
 *   - mode 恒为 'fresh'（DNA 应答即实时，无缓存概念）
 *   - 按 level 升序（最弱在前），与 T04 看板排序一致
 */
export function toMasteryData(dna: LearningDnaData): MasteryData {
  const mastery: MasteryItem[] = dna.mastery_summary
    .map((m) => ({
      kp_id: m.knowledge_point_id,
      kp_name: m.kp_name,
      score: m.mastery,
      level: m.level,
      attempts: m.practice_count,
      correct_count: m.correct_count,
      error_rate: m.error_rate,
      effective_attempts: m.practice_count,
      last_practiced_at: m.last_practice_at,
    }))
    .sort((a, b) => a.level - b.level)

  const level_count: Record<string, number> = { '0': 0, '1': 0, '2': 0, '3': 0 }
  for (const it of mastery) {
    level_count[String(it.level)] += 1
  }

  return {
    student_id: dna.student_id,
    mastery,
    summary: { total_kp: mastery.length, level_count },
    computed_at: null,
    mode: 'fresh',
  }
}

/**
 * DNA mastery_summary → 家长端 ChildMastery[]。
 *
 * score 保持 0~1（与 family 接口 / 家长端模板渲染口径一致）；
 * level 中文文案与 family 的 _MASTERY_LABEL 一致。
 */
export function toChildMasteryList(dna: LearningDnaData): ChildMastery[] {
  return dna.mastery_summary.map((m) => ({
    kpId: m.knowledge_point_id,
    kpName: m.kp_name || null,
    score: m.mastery,
    level: MASTERY_LABEL[m.level] ?? '未知',
    levelInt: m.level,
    attempts: m.practice_count,
    updatedAt: m.updated_at ?? null,
  }))
}
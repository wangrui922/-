/**
 * 引导对话面板的共享类型（T1 · 前端地基）。
 *
 * `GuideChatPanel` 被两条业务线复用：
 *   - **题库练习**（`views/Practice.vue`）：老师带我想一想；
 *   - **AI 老师**（`views/AiTeacher.vue`）：拍题辅导 + 练习题作答。
 *
 * 两边的消息结构完全一致，所以类型抽到这里做**单一真源**，
 * `stores/practice.ts` 只做转发导出以保持旧 import 路径不破。
 */
import type { TutorMode } from '@/api/chat'

/** 引导面板里的一条对话。 */
export interface GuideMessage {
  role: 'student' | 'teacher'
  text: string
}

/**
 * 输入框上方的一个快捷按钮。
 *
 * 点击 = 把 `text` 当作孩子说的话发出去，并**显式带上** `mode`——
 * 这是「前端显式指定意图」通道，优先级高于后端的自动识别，
 * 保证孩子点了「不会做，给我讲」就一定进讲解模式，不会被规则误判回引导。
 */
export interface QuickAction {
  /** 按钮上显示的文字，如「不会做，给我讲」 */
  label: string
  /** 点击后实际发给老师的话术 */
  text: string
  /** 显式指定的响应模式；不给则交由后端自动识别 */
  mode?: TutorMode
}

export type { TutorMode }

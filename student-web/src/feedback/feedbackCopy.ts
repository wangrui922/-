// 情感化反馈文案库 v1
// 设计原则（创意总监定调）：
//  1. 做对 → 具体夸（点出孩子用的方法/努力），不用干巴巴的"正确"
//  2. 做错 → 不打击，先接住情绪，再给"一小步"引导，绝不说"你错了"
//  3. 全程孩子语气，不居高临下说教，多用"我们/你/一起"
//  4. 同场景多版本轮换，避免机械重复
// 调用方应按 user_id + 场景 做去重轮换，避免连续出现同一条。

export interface CopyLine {
  text: string
  /** 可选：强调关键词，前端可高亮 */
  highlight?: string
}

/** 做对：按"夸的角度"分组，随机取一条 */
export const CORRECT: Record<string, CopyLine[]> = {
  // 夸方法（点出具体思路）
  method: [
    { text: '这步走得特别稳！你先{m}再算，思路很清楚 ✨', highlight: '通分' },
    { text: '漂亮！你抓住了这个题的关键——{m}，一点没绕弯路 🌟', highlight: '单位统一' },
    { text: '你刚才那一下换算做得很利落，这就是学霸的感觉 👍' },
  ],
  // 夸努力 / 坚持
  effort: [
    { text: '这道题有点绕，但你没急，慢慢算对了，稳！🔥' },
    { text: '我看到你刚才停了一下又想通了，这种定力很厉害 👏' },
    { text: '连续做对啦，你的手感越来越好了，继续保持 💪' },
  ],
  // 夸进步（对比上次）
  progress: [
    { text: '比上次那道同类题顺多了，你真的记住方法了 🎉' },
    { text: '上次这里你会卡一下，这次直接过，进步看得见 ⭐' },
  ],
}

/** 做错：按"错因类型"分组（接 judge_engine 的错因判定） */
export const WRONG: Record<string, CopyLine[]> = {
  // 差一点点（接近正确）
  nearMiss: [
    { text: '差一点点就够到啦，别急～我们再看这一小步 👇' },
    { text: '思路对了一大半！就最后一个小弯，我陪你拐过去 🤝' },
  ],
  // 概念不清
  concept: [
    { text: '不是你算错，是这个小知识点还没站稳。来，我画给你看 👇' },
    { text: '这里有个小陷阱，很多同学都会踩。我们一起把它填平 🛠️' },
  ],
  // 粗心 / 看错
  careless: [
    { text: '会的！就是手快了一点。下次读完题先停一秒，胜利就是你的 😉' },
    { text: '这道题你会做，是眼睛先跑了。我们慢慢看清楚再下手 👀' },
  ],
  // 计算错
  compute: [
    { text: '方法全对，就是最后一步算瓢了。来，重新算这一下，很稳的 🧮' },
    { text: '思路满分！就是笔头滑了一下。算完再回头瞄一眼就对啦 ✅' },
  ],
}

/** 卡关 / 想放弃：温柔拉回 */
export const STUCK: CopyLine[] = [
  { text: '这关确实有点硬，换我也得想一会儿。要不我们先歇 2 分钟？🍬' },
  { text: '你不是不会，是还没找到那把钥匙。我帮你递一把，试试看 🔑' },
  { text: '卡住很正常呀，每个厉害的人都有卡住的时候。我们不赶，慢慢来 🌈' },
]

/** 连续打卡（streak） */
export const STREAK: CopyLine[] = [
  { text: '连续 {n} 天啦！你比昨天的自己更厉害一点点 🔥' },
  { text: '第 {n} 天打卡，这个小习惯正在偷偷帮你变强 💡' },
  { text: '今天也来了，这就是坚持的样子，给你点个大大的赞 👍' },
]

/** 解锁成就 / 通关 */
export const UNLOCK: CopyLine[] = [
  { text: '🏅 成就到手！「{name}」被你拿下了，去成就墙看看～' },
  { text: '这关通了！你给星图又点亮一颗 ⭐，继续往前冲 🚀' },
]

/** 提示（hint）语气 */
export const HINT: CopyLine[] = [
  { text: '不着急给答案，先给你一个小提示：{tip} 💡' },
  { text: '试试从这个角度想想：{tip} 👀' },
]

/**
 * 按场景取一条文案（带简单轮换，避免连续重复）。
 * @param pool 文案分组（对象或数组）
 * @param key  对象分组时的键
 * @param last 上一次取到的文本，用于去重
 */
export function pickCopy(
  pool: Record<string, CopyLine[]> | CopyLine[],
  key?: string,
  last?: string,
): CopyLine {
  const list: CopyLine[] = Array.isArray(pool)
    ? pool
    : pool[key as string] ?? []
  if (list.length === 0) return { text: '' }
  let cand = list[Math.floor(Math.random() * list.length)]
  // 同场景有多条时，尽量不连续重复上一条
  if (list.length > 1 && last) {
    let guard = 0
    while (cand.text === last && guard < 5) {
      cand = list[Math.floor(Math.random() * list.length)]
      guard++
    }
  }
  return cand
}

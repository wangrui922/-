# AI 老师「更像真人老师」机制升级 · 增量 PRD

- 文档版本：v1.0（2026-08-10）
- 作者：产品经理 许清楚
- 状态：待架构评审
- 影响范围：`backend/app/ai/*`、`backend/app/api/{chat,exercise}.py`、`student-web/src/views/AiTeacher.vue`
- 前置文档：本文件为**增量 PRD**，不覆盖已上线的题库练习（`/api/practice/*`）主流程

---

## 1. 背景与目标

### 1.1 现状痛点 A：错题辅导后的练习题「直接给答案」

用户原话：

> 错题辅导完了，需要出两道相似的练习题，这两道相似的练习题也需要像辅导的过程一样，需要一步一步引导，最后算出答案，练习题如果在作答的过程中有错误，就需要把这道题的原型错题收集到错题库里。现在这个功能没有练习题是直接就给答案，根本看不到学生究竟是怎么做出来的，以及错在什么地方，以后也没办法通过做题中的错误得出学生的薄弱环节。

**代码级证据**：

| 位置 | 现状 |
|---|---|
| `AiTeacher.vue:142-158` `handleGenExercise()` | 调 `genExercise()` 后**只做** `exercises.value = result.exercises`，直接渲染列表 |
| `AiTeacher.vue:204-209` | 用 `<QuestionCard>` 静态渲染，无任何对话入口 |
| `QuestionCard.vue:57-67` | 一个「先自己做，做完看答案」按钮 → `showAnswer = true` → **直接展示 `question.a` 标准答案** |
| `api/exercise.py:9` | 注释明写「生成结果**不落题库**」；`orchestrator.generate_exercise():200` 亦标注「**不落库**」 |

**结论**：练习题目前是「一张卡片 + 一个展开答案按钮」的**纯静态展示**。没有作答输入框、没有逐步引导、没有判对错、没有错题回写。用户描述完全属实。

### 1.2 现状痛点 B：练习/辅导对话死板，不按学生意思走（本轮新增，更基础）

用户原话：

> 做练习题的时候，我把我的需求发给它，结果他还是按他的思路来提问，而不能按我的意思为我一一对答，显得很死板，对面的不是老师，而是设置好的模板，比较死板，帮我把这个问题解决掉。

**用户截图场景**（百数表填数题）：

| 轮次 | 学生说 | AI 实际回复 | 应有回复 |
|---|---|---|---|
| 1 | 我不知道，你把这道题给我解释下吧 | 还记得同一行相邻两个数右边比左边大几吗？ | 好，我从头讲一遍：百数表每行从左到右 +1…… |
| 2 | 我不会做，你给我把整道题解答下 | （继续苏格拉底式追问） | 完整演示一遍解题过程 |

**代码级证据（三重锁死，这是核心根因）**：

1. **提示词层锁死** — `ai/prompts/tutor.py:24` 【铁律（永远不许破）】第 1 条：
   > 「第一轮绝不直接给答案，也不直接写解题步骤。」

   同文件第 25 行铁律 2：「一次只说一件事……不把讲解、公式、步骤、总结一次性倒给孩子。」
   `prompts/practice_guide.py:35-39` 铁律 5 更狠：「**不公布答案。即使孩子问你答案**，也用问题引导他自己走到结果。」

2. **代码闸门层锁死** — `ai/answer_guard.py`
   `generate_guarded_reply()` 用正则扫描模型回复，命中 `答案[:：=]`、`最终答案`、`所以 x=5`、`解得 y=` 等即判定「泄漏」，**最多重生成 2 次**，仍命中就丢弃整条回复、返回写死的兜底话术：
   > `FALLBACK_REPLY = "你先跟我说说，这道题你读下来，卡在哪一步了？"`

   → 这就是用户感到「对面不是老师，而是设置好的模板」的直接来源：AI 就算想讲解，也会被代码层拦截并替换成固定模板句。

3. **状态机层锁死** — `ai/state_machine.py`
   仅 5 个状态：`intro / guiding / practicing / summarizing / closed`。**没有任何「讲解 / 演示」状态**。
   且 `is_strict_guard():63-68` 对 `intro` 和 `guiding` 一律返回 `True`（最严格）。错题辅导全程都在 `guiding`，因此**全程严格禁答**。

4. **零意图识别** — 全量 grep `intent` / `意图` / `DEMO` / `不会做` 在 `backend/app/` 下**无任何业务代码命中**。
   `orchestrator.continue_session():144-183` 把学生原话直接塞给 `tutor.next_question()`，中间**没有任何分支判断**。

### 1.3 共同根源

> **AI 老师是一台「只有一个挡位」的硬编码状态机：Socratic 引导挡。**
> 缺失两块能力 ——(a) **学生意图识别**；(b) **响应模式切换**。
> 于是无论学生说「我在想」还是「我不会，你讲一遍」，系统给出的都是同一种行为。

痛点 A 与痛点 B 是**同一根线**：练习题之所以只能「要么不给、要么全给」，正是因为缺少中间的对话模式调度层。

### 1.4 业务目标

| 目标 | 衡量口径 |
|---|---|
| G1 让 AI 识别学生意图并切换响应模式 | 「我不会/你讲一遍」类输入触发 DEMO 模式准确率 ≥ 90% |
| G2 让错题辅导后的练习题变成对话式逐步引导 | 练习题作答过程可回溯步数 ≥ 2 步；不再一键直出答案 |
| G3 打通「练习出错 → 错题本 → 学习画像」闭环 | 练习判错后错题入库成功率 100%，可按 `kp_id` 聚合出薄弱点 |

---

## 2. 用户故事

### US-A 错题辅导后的对话化练习

- **US-A1**：作为学生，错题辅导完点「再出两道」后，我希望进入**对话式练习**，而不是看到一张能直接展开答案的卡片。
- **US-A2**：作为学生，我逐步输入我的思路，AI 每一步都给我反馈（对了 / 再想想 / 方向偏了），而不是等我全做完才说话。
- **US-A3**：作为学生，我给出最终答案后，AI 明确告诉我对还是错，并说明错在哪一步。
- **US-A4**：作为家长/老师，学生练习题做错时，这道题的**原型错题**要自动进错题本，并带上诊断信息，以便日后分析薄弱环节。

### US-B 灵活响应学生意图（核心新增）

- **US-B1**：作为学生，我说「不会做」/「给我讲一遍」/「帮我解题」时，AI 应切到**讲解模式**，完整演示思路，而不是继续反问我。
- **US-B2**：作为学生，我说「给点提示」时，AI 给**思路提示**而非完整答案。
- **US-B3**：作为学生，我聊偏了或卡太久时，AI 主动把我拉回主线。
- **US-B4**：作为学生，我说「我直接告诉你答案：XX」时，AI 应**接收并评判对错**，而不是继续追问引导。

---

## 3. 需求池

### P0（必须做）

| 编号 | 关联 | 需求 | 验收标准 |
|---|---|---|---|
| **P0-1** | US-B1/B2/B4 | **AI 意图识别 + 模式切换**：在 `continue_session` 生成回复前，先识别学生消息意图，决定本轮响应模式 | 输入「我不会，你给我讲一遍」→ 返回完整解题演示，非追问句 |
| **P0-2** | US-B1 | **放开 `answer_guard` 在 DEMO 模式下的拦截**：`is_strict_guard()` 需支持 DEMO/JUDGE 模式返回 `False` | DEMO 模式下含「答案是 X」的回复不被替换为 `FALLBACK_REPLY` |
| **P0-3** | US-A1 | **「再出两道」改为进入对话式练习**：练习题不再默认可展开答案 | 点击后进入对话面板，`QuestionCard` 的「看答案」入口在未作答前不可用 |
| **P0-4** | US-A2/A3 | **练习题逐步作答 + 每步评判**：复用现有 `practice_guide` 能力，学生可多轮输入过程 | 至少支持 3 轮来回；最终答案有明确对/错结论 |
| **P0-5** | US-A4 | **判错即入错题本**：练习题判错后写入 `mistakes` | 错题本能查到该题，`error_type` 归入五类枚举之一 |

> ⚠️ **P0-5 的硬阻塞（必须先解）**：`learning_service.record_mistake()` 需要一个**存在于 `questions` 表**的 `question_id`（有外键），但 `/api/exercise/gen` 生成的练习题**当前完全不落库**（`api/exercise.py:9`、`orchestrator.py:200` 均明确标注）。
> → 因此 P0-5 隐含一个前置改动：**AI 生成的练习题必须先持久化为 `Question` 记录**（或为 AI 练习题设计独立的错题入库路径）。请架构师优先裁决此项。

### P1（应该做）

| 编号 | 关联 | 需求 |
|---|---|---|
| P1-1 | US-B3 | AI 检测偏题/卡顿信号（连续 2 轮无进展）自动引导回主线 |
| P1-2 | US-A2 | 每步评判的视觉反馈（✓ 对 / ⚠ 再想想 / ✗ 方向偏了） |
| P1-3 | US-B1/B2 | 输入框旁提供快捷意图按钮：「不会做，给我讲」「给点提示」「看演示」 |
| P1-4 | US-A1 | 练习题状态指示器：第 1/2 题、当前处于「作答中/评判中/已完成」 |

### P2（可以做）

| 编号 | 需求 |
|---|---|
| P2-1 | 错题诊断摘要（学生原始步骤 + 错因分类）存入错题本 |
| P2-2 | 学习画像从练习错题入库反推薄弱点（沿用 `profile_service` 现有路径） |
| P2-3 | DEMO 模式按学生年级动态调整讲解详细度 |

---

## 4. UI / 交互草案

### 4.1 `AiTeacher.vue` 改造

```
┌──────────────────────────────────┐
│ [模式指示器] 🎓 引导中 / 📖 讲解中 / ✓ 评判中 │  ← 新增，顶部常驻
├──────────────────────────────────┤
│                                  │
│   ChatBubble 消息流（沿用）        │
│                                  │
│   ── 练习题区（改造）──            │
│   [ 第 1 题 · 修复练习 ]           │
│   题面……                          │
│   ┌ 对话式作答区（新增）┐          │
│   │ 学生：我先算 3×4=12  │          │
│   │ 老师：✓ 这步对了…    │          │
│   └────────────────┘          │
├──────────────────────────────────┤
│ [不会做，给我讲][给点提示][看演示]  │  ← 新增快捷意图按钮（输入框上方）
│ ┌──────────────────────────┐ │
│ │ 说说你是怎么想的…           │ │
│ └──────────────────────────┘ │
│ [拍照] [换一题] [    回答    ]    │
└──────────────────────────────────┘
```

### 4.2 ⭐ 强烈建议：复用 `PracticeGuideDrawer.vue`

调研发现 `student-web/src/components/practice/PracticeGuideDrawer.vue` **已经实现了完整的对话式练习面板**：
学生输入框、多轮消息流、loading 态、`askFinalConfirm` 时的「是的 / 我再想想」快捷回复按钮。

它当前只服务于题库练习（`Practice.vue`），**未接入错题辅导的练习流**。

> **建议**：US-A 不要从零造轮子，直接把 `PracticeGuideDrawer` 抽为通用组件接入 `AiTeacher.vue`。可显著降低 P0-3/P0-4 工作量。

### 4.3 练习题状态机

```
INIT → 出题中 → 对话作答态 ⇄ 评判态 → 结论态 → 下一题 / 完成
                    ↑                    ↓
                    └──── 判错 → 错题入库 ─┘
```

---

## 5. 改动点（接口契约概要，交架构师展开）

### 5.1 后端

**新增：意图识别层**

- 位置建议：`backend/app/ai/intent.py`（新建）
- 输入：学生本轮消息 + 最近 N 轮上下文
- 输出：`mode` 枚举

| mode | 含义 | answer_guard 严格度 | 触发信号示例 |
|---|---|---|---|
| `GUIDE` | 引导（现状，默认） | strict = True | 学生在描述思路 |
| `HINT` | 提示 | strict = True | 「给点提示」「提示一下」 |
| `DEMO` | 演示讲解 | **strict = False** | 「我不会」「你讲一遍」「给我解答下」 |
| `JUDGE` | 评判 | strict = False | 「答案是 X」「我算出来是 X」 |

**改造清单**

| 文件 | 改动 |
|---|---|
| `ai/state_machine.py` | 扩展模式枚举；`is_strict_guard()` 增加 DEMO/JUDGE → `False`；为各 mode 增加 `_STATE_PROMPTS` 后缀 |
| `ai/prompts/tutor.py` | 新增【意图识别与模式切换】段；铁律 1 由「第一轮绝不给答案」改为「**GUIDE 模式下**第一轮不给答案；学生明确要求讲解时切 DEMO 模式完整演示」。⚠️ 此文件头部注明「任何改动都会直接影响教学效果，改前请先评审并升 `VERSION`」，当前 `VERSION = 1.1.0` → 需升版 |
| `ai/prompts/practice_guide.py` | 同上，铁律 5「即使孩子问你答案也不公布」需增加 DEMO 例外。当前 `VERSION = 1.1.0` → 需升版 |
| `ai/answer_guard.py` | `generate_guarded_reply()` 增加 `mode` 参数透传严格度 |
| `ai/agents/tutor.py` | `next_question()` 增加 `mode` 参数（现仅有 `state`/`current_error`） |
| `ai/orchestrator.py` | `continue_session()` 在调 `tutor.next_question()` 前插入意图识别；将 mode 落库到 `ai_sessions` |
| `api/chat.py` | `POST /chat/{sid}/message` 响应体增加 `mode` 字段供前端渲染指示器；可选支持前端显式传 `mode`（快捷按钮） |
| `api/exercise.py` + `orchestrator.generate_exercise()` | 解除「不落库」限制（见 P0-5 阻塞项） |

### 5.2 前端

| 文件 | 改动 |
|---|---|
| `views/AiTeacher.vue` | 加模式指示器；加快捷意图按钮；`handleGenExercise` 改为进入对话式练习 |
| `components/QuestionCard.vue` | 「先自己做，做完看答案」入口收敛：未完成对话前不可展开 `a` |
| `components/practice/PracticeGuideDrawer.vue` | 抽为通用组件，供 `AiTeacher.vue` 复用 |
| `api/chat.ts` | 补 `mode` 字段类型；新增练习对话接口 |

### 5.3 数据

- `ai_sessions`：建议增加 `current_mode` 字段（已有 `session_state` / `step` / `state_updated_at`）
- `mistakes`：P2-1 若要落诊断，建议增加 `dialog_diagnosis`(text)、`student_steps`(jsonb)
- `error_type` **必须**沿用 `core/error_categories.py` 五类枚举：`READ / METHOD / CALC / KNOWLEDGE / CHECK`（该文件是「错因分类唯一真相源」，禁止另起炉灶）

---

## 6. 与学习画像 / 错题本的连接

- 练习判错 → `learning_service.record_mistake()` 入 `mistakes`（**幂等**：同一 `(student_id, question_id)` 存在未攻克记录时不重复建档）
- 错题的 `question.kp_id` 非空 → `profile_service.invalidate_cache_for_question()` 失效掌握度缓存
- 学习画像懒计算时按 `kp_id` 聚合出 `weak_points`
- 参考现成实现：`practice_service.finish_session():772-828`（题库练习的错题入本 + 缓存失效已跑通，AI 练习题可直接对齐这套口径）

---

## 7. 待确认问题（≤ 5 条）

1. **【最高优先级】** AI 生成的练习题是否落 `questions` 表？不落则 `record_mistake` 的外键无法满足，P0-5 无法实现。备选：(a) 落库为 `source='ai_gen'` 的题目；(b) 为 AI 练习题设计不依赖 `question_id` 的错题记录路径。
2. **意图识别实现方式**：关键词正则（快、省 token、易误判）/ 独立轻量 LLM 调用（准、+1 次网络往返）/ 让 tutor 模型自己在回复里输出 `mode` 字段（省调用、但受 `answer_guard` 影响）。
3. **DEMO 模式后是否允许「回切」**：讲解完整题后，是否自动回到 GUIDE 模式再出一道同类题验证理解？
4. **错题入库时机**：判定错**即时**入库，还是两道练习题全部结束后**统一**入库？（题库练习现走 `finish_session` 统一入库）
5. **快捷意图按钮位置**：输入框上方（醒目、占竖屏空间）还是折叠在「+」里（省空间、发现率低）？

---

## 附录：关键代码定位速查

| 关注点 | 文件:行 |
|---|---|
| 「第一轮绝不给答案」铁律 | `backend/app/ai/prompts/tutor.py:24` |
| 「即使孩子问你答案也不公布」铁律 | `backend/app/ai/prompts/practice_guide.py:39` |
| 答案泄漏正则闸门 | `backend/app/ai/answer_guard.py:28-45` |
| 死板兜底话术来源 | `backend/app/ai/answer_guard.py:58`、`ai/agents/tutor.py:25` |
| 严格度判定（无 DEMO 挡） | `backend/app/ai/state_machine.py:63-68` |
| 多轮对话主流程（无意图识别） | `backend/app/ai/orchestrator.py:144-183` |
| 练习题直出答案 | `student-web/src/components/QuestionCard.vue:57-67` |
| 练习题静态渲染 | `student-web/src/views/AiTeacher.vue:142-158, 204-209` |
| 可复用的对话面板 | `student-web/src/components/practice/PracticeGuideDrawer.vue` |
| 错题入本参考实现 | `backend/app/services/practice_service.py:772-790` |
| 错因五类枚举唯一真相源 | `backend/app/core/error_categories.py:23-48` |

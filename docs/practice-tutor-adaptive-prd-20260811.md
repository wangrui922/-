# AI 老师「按错因定向推题」· 简单增量 PRD

- 文档版本：v1.0（2026-08-11）
- 作者：产品经理 许清楚
- 状态：待架构评审
- 类型：**简单增量 PRD**（仅描述本次变更，不重写整体 PRD）
- 上游文档：`docs/practice-tutor-prd-20260810.md`、`docs/practice-tutor-design-20260810.md`（2026-08-10 已落地的对话式引导 + 意图识别 + 错题自动入库）
- 影响范围：`backend/app/ai/orchestrator.py`（`generate_exercise` 注入弱项）、`backend/app/services/learning_service.py`（新增弱项画像聚合，**纯本地 SQL**）、`backend/app/api/exercise.py`（`/exercise/gen` 响应增量）、`src/views/AiTeacher.vue`（薄弱点标签展示）、`src/api/chat.ts`（`Exercise` 类型增量）
- 借鉴点（主理人已锁定）：DeepTutor 的「掌握度门槛 + 自适应出题」方法论 → 落地为本次主题 **「按错因定向推题」**

---

## 0. 本次变更定位（为什么做、现状证据）

### 0.1 现状缺陷：练习题是「泛泛相似」，没有「哪里弱、练哪里」

现有 `/exercise/gen` 出题链路（`backend/app/ai/orchestrator.py:266-274`）：

```python
analysis = _pick_analysis(messages)                       # 取【当前这道错题】的诊断 JSON
kp = analysis.get("knowledge") or DEFAULT_KP
error_type = analysis.get("error") or DEFAULT_ERROR_TYPE  # ← 用的是「当前错题」的错因
difficulty = analysis.get("difficulty") or DEFAULT_DIFFICULTY
exercises = exercise.gen_exercises(error_type=error_type, kp=kp, difficulty=difficulty)
```

**结论**：生成的 2 道练习题，其 drill 目标 = **当前这道题的诊断错因**，与「学生历史上反复犯的薄弱错因」无关。孩子总犯「计算方法错 METHOD」，系统却仍按当前错题的错因随机出相似题，**未形成自适应闭环**。这正是用户描述的「泛泛的相似」。

### 0.2 目标升级：把弱项画像注入出题，并让薄弱点可见

- **出题侧**：生成练习题前，先按 `student_id` 聚合其错题库的 `error_type` 分布，取主导弱项，把该弱项错因注入 `gen_exercises()` 的提示词，让 LLM 生成的相似题**专门 drill 这个弱项**（知识点仍锚定当前错题，主题不跑偏）。
- **展示侧**：在 `/ai` 页面练习区**显式展示薄弱点标签**（如「这道题在练你常错的：计算方法」），让学生和家长看得见「为什么给我出这题」。

### 0.3 既有能力可直接复用（本次「几乎免费」的关键）

闭环的**写**路径已存在：`judge_and_record()` → `learning_service.record_mistake(error_type=...)`，错题判错即把**五类机器码**错因写入 `mistakes.error_type`（经 `_safe_error_code` 收敛，见 `ai_exercise_service.py:400-410`）。本次只需在 `generate_exercise()` **读回**这份画像 —— `gen_exercises(error_type=...)` 本就接受 `error_type` 参数，改动主要是「算一个更好的 error_type + 调提示词让其真 drill 该类 + 把弱项带回响应用于打标签」。

---

## 1. 产品目标

**一句话**：把 `/ai` 的练习题从「泛泛相似」升级为「按学生薄弱错因定向 drill」的自适应推题，并让薄弱点对学生和家长可见、可解释。

| 目标 | 衡量口径 |
|---|---|
| G1 弱项注入准确率 | 注入 `gen_exercises` 提示词的弱项错因，与生成题实际 drill 的错因类别一致率 ≥ 90%（人工/模型抽检 20 题） |
| G2 薄弱点标签可见率 | `/exercise/gen` 返回题 100% 携带 `weakTags`；前端练习卡片/对话区渲染率 100%（埋点/回归校验） |
| G3 兜底可用性 | 画像为空（新学生 / 无历史错题）时 100% 走「按当前错题知识点出相似题」兜底，不 500、不阻断出题意图 |

---

## 2. 用户故事

- **US-1（学生·看到薄弱点标签）**：作为学生，我点「再出两道」后，希望每道练习题卡片上能看到「这道题在练你常错的：计算方法」，这样我知道为什么给我出这题、练得有针对性，而不是又来一堆随机题。
- **US-2（系统·按弱项出题）**：作为系统，当某学生的错题库里「计算方法错 METHOD」反复出现时，希望 AI 老师出的相似练习题**专门 drill 计算方法**，而非沿用当前错题的错因随缘出。
- **US-3（家长·看懂推题逻辑）**：作为家长，我希望在 `/ai` 页（或后续家长端）看到孩子当前的薄弱错因标签（如「最近常犯：读题错」），并能理解「为什么这题针对这个弱点」，从而在家配合辅导。
- **US-4（闭环·错题反哺画像）**：作为学生，当我练错被判错入库后，希望这次错因进入我的薄弱画像，使**下次出题更精准**——形成「练错 → 入库 → 画像更新 → 下次更准」的自适应闭环。

---

## 3. 需求池（P0 / P1 / P2）

### P0（必须做）

| 编号 | 关联 | 需求 | 验收标准 |
|---|---|---|---|
| **P0-1** | US-2、US-4 | **薄弱错因画像计算**：新增 `get_weak_error_profile(db, student_id)`，从 `mistakes` 表按 `error_type` 聚合（**聚合时 `WHERE error_type IN (READ,METHOD,CALC,KNOWLEDGE,CHECK)` 过滤**，规避 TD-3 的 `"练习错题"` 脏值），取 Top-N 主导弱项，返回机器码 + 中文标签。**纯本地 SQL GROUP BY，零外网、零迁移** | 对同一学生 `SELECT error_type, COUNT(*) FROM mistakes WHERE student_id=? AND error_type IN (五类) GROUP BY error_type` 结果与函数输出一致；Top-N 取值见待确认 Q2 |
| **P0-2** | US-2 | **出题提示词注入弱项**：在 `orchestrator.generate_exercise()` 中，先算画像，再把主导弱项错因与当前错题的 `kp` 组合注入 `gen_exercises(error_type=weak_code, kp=current_kp, difficulty=...)`；`agents/exercise.py` 的提示词增加一句「**专门针对学生的薄弱错因 X 出题，让他在本题知识点的变式中反复踩/练这个坑**」 | 抽检 20 题，生成题实际考查错因与注入弱项一致率 ≥ 90%（G1）；kp 仍锚定当前错题、主题不跑偏 |
| **P0-3** | US-1、US-3 | **/ai 页面薄弱点标签展示**：`/exercise/gen` 响应新增 `weakTags`（机器码+中文）与 `weakSource`（`profile`/`fallback`）；`AiTeacher.vue` 在练习卡片/引导对话区上方渲染标签 | 有历史错题时 100% 渲染标签（G2）；画像为空时显示兜底文案且不报错（G3） |

### P1（应该做）

| 编号 | 关联 | 需求 |
|---|---|---|
| **P1-1** | US-2、US-4 | **弱项时间衰减/加权**：近期错题权重更高（按 `mistakes.created_at` 加权聚合，如指数衰减），让「最近常犯」优先于「很久以前犯过」 |
| **P1-2** | US-2、G3 | **弱项为空兜底**：画像为空（新学生 / 无合规错题）时，沿用现有 `analysis.get("error")` + `DEFAULT_ERROR_TYPE` 路径按当前错题出相似题，保证功能不中断 |
| **P1-3** | US-1、US-3 | **标签可解释**：点击薄弱点标签弹出一句简短说明「为什么给我出这题」（如「你最近 3 次练习都栽在计算方法上，这题专门帮你练这个」） |

### P2（可以做）

| 编号 | 需求 |
|---|---|
| **P2-1** | **薄弱点可视化小图**：在 `/ai` 页或 Profile 页用雷达/条形图展示五类错因分布（复用 `error_categories` 五类维度） |
| **P2-2** | **家长端弱项报告**：周期性（章节/周）输出孩子薄弱错因报告，附「针对建议」 |

---

## 4. UI 草案（薄弱点标签 · 文字/ASCII 描述）

**位置**：练习区最上方新增一条「本次薄弱点」横幅；练习卡片标题下再补一句「这题在练你常错的：X」做二次强调。以下基于 2026-08-10 已落地的 `AiTeacher.vue` 布局追加（不重画整体）：

```
┌──────────────────────────────────────────────┐
│ [🎓 引导中]                          顶部模式指示器 │
├──────────────────────────────────────────────┤
│  ChatBubble 对话流（延续上文辅导）                │
│                                                │
│  ╔══════ 本次练习针对你的薄弱点 ════════════╗    │
│  ║  🎯 计算方法      读题错        (弱项标签)  ║    │  ← 新增：weakTags 横幅
│  ╚═════════════════════════════════════════╝   │
│                                                │
│  [ 第 1 题 · 修复练习 ]                          │
│  📌 这道题在练你常错的：计算方法                  │  ← 新增：卡片内强调
│  题面……                                        │
│  ┌──────────── 对话式作答区 ──────────┐         │
│  │ 学生：我先算 3×4=12                  │         │
│  │ 老师：✓ 这步对了，接下来……          │         │
│  └────────────────────────────────────┘         │
├──────────────────────────────────────────────┤
│ [不会做，给我讲][给点提示][看演示]                │  ← 既有快捷意图按钮
│ ┌────────────────────────────────────┐         │
│ │ 说说你是怎么想的…                     │         │
│ └────────────────────────────────────┘         │
│ [拍照][换一题][回答]                             │
└──────────────────────────────────────────────┘
```

**标签视觉**：用 `error_categories.ERROR_LABELS` 的中文（如「计算方法」「读题错」）渲染为圆角 chip；多弱项横向排列，超宽换行。文案**不在前端硬编码**，由后端 `weakTags` 直接给（`{code, label}`），`label` 走 `error_label(code)` 唯一真相源。

**数据契约增量**（`/exercise/gen` 响应 `data` 新增，全部可选 + 默认，老客户端不崩）：

```jsonc
{
  "exercises": [ { "questionId": "...", "q": "...", "a": "...", /* 既有字段 */ } ],
  "weakTags":  [ { "code": "METHOD", "label": "方法错" }, { "code": "READ", "label": "读题错" } ],
  "weakSource": "profile"   // "profile"=来自历史画像；"fallback"=画像为空走兜底
}
```

---

## 5. 待确认问题（主理人据此拍板）

1. **【最高优先级】薄弱画像数据源范围**：全部错题库（`mistakes` where `student_id`，含库题练习 + AI 练习错题）/ 仅 AI 练习错题（`source_doc='ai_gen'` 关联题）/ 仅库题练习错题？推荐「**全部错题库**」最全面，但须注意 TD-3：`practice_service` 写入的 `error_type="练习错题"` 不合规、无法归入五类，会污染聚合 → 需在聚合时 `WHERE error_type IN (五类机器码)` 过滤，或先修 TD-3。请主理人拍板数据源 + 是否顺手修 TD-3。
2. **Top-N 取几个主导弱项？** 默认取 1 个 dominant（激进、最聚焦）还是 Top-2/3（覆盖多重薄弱）？DeepTutor 通常取主导 1–2 个。推荐 **Top-2**。
3. **弱项标签文案口径**：固定复用 `error_categories.ERROR_LABELS`（METHOD→「方法错」等，机器码唯一真相源，前端走 `error_label()` 不硬编码）/ 还是展示更口语化版本（「计算方法」「读题不仔细」）？推荐**复用 ERROR_LABELS**，零新增枚举、零外网。
4. **标签展示位置**：练习卡片标题下方（每题独立）/ 对话引导区上方（整批一道横幅）/ 两者都有？影响前端布局与「可见率」验收口径（G2）。
5. **弱项注入出题的「强度」**：弱项错因**完全替换**当前错题的 `error_type`（纯 drill 弱项，可能偏离当前题知识点）/ 还是「在当前错题知识点（kp）下、**额外偏向**弱项」（稳，主题不跑偏）？推荐后者：`kp` 取当前错题、`error_type` 取弱项，组合为「在 X 知识点下专门练你常错的 Y」。

---

## 附录 A：与既有约束的对齐（确保可落地）

| 约束 | 本次设计如何满足 |
|---|---|
| **零迁移红线**（不加表/列/CHECK） | 弱项画像 = 对既有 `mistakes.error_type` 做本地 `GROUP BY`，**不新增任何表/列/CHECK**；仅给 `/exercise/gen` 响应**加可选字段**（符合 design §7.6「新字段可选 + 默认」）。 |
| **五类机器码唯一真相源** | 聚合只认 `error_categories` 的 `READ/METHOD/CALC/KNOWLEDGE/CHECK`；标签走 `error_label()`；**禁止另起错因枚举**（对应 design §7.2）。 |
| **国内环境 / 不依赖外网** | 画像计算是**纯本地 SQL**，零 LLM 调用；`gen_exercises` 本身已调模型生成，注入一句弱项提示**不新增任何外网往返**。 |
| **技术栈为 Vue3**（非 React） | 前端改动落在 `src/views/AiTeacher.vue` 与 `src/api/chat.ts` 的 `Exercise` 类型，遵循既有 Vue3/Tailwind/Pinia 结构。 |
| **提示词是核心教学资产** | 仅给 `agents/exercise.py` 的练习生成提示词**加一句**弱项指令（加法），不动既有铁律；如需升 `VERSION` 按 design §7.3 评审流程走。 |
| **事务纪律** | 画像查询为只读 `SELECT`，挂在 `generate_exercise` 现有 `db` 上，不额外 `commit`；不影响既有 `judge_and_record` 的写路径。 |

## 附录 B：关键技术定位速查（本次改动落点）

| 关注点 | 文件:行（以 2026-08-11 代码为准，实施以函数名定位） |
|---|---|
| 出题链路取「当前错题错因」（待改造） | `backend/app/ai/orchestrator.py:266-274` `generate_exercise()` |
| 错题写入（既有闭环写路径） | `backend/app/services/ai_exercise_service.py:653` `record_mistake(error_type=...)` |
| 错因归一与中文标签（唯一真相源） | `backend/app/core/error_categories.py` `ERROR_LABELS` / `error_label()` / `ERROR_CATEGORIES` |
| 画像聚合建议落点（新增函数） | `backend/app/services/learning_service.py`（与 `record_mistake` 同模块，复用 `Mistake` 模型） |
| 出题响应契约（待加 `weakTags`） | `backend/app/api/exercise.py:80-102` `gen_exercise()` |
| 前端标签渲染落点 | `src/views/AiTeacher.vue`（练习卡片 / 引导对话区）、`src/api/chat.ts` `Exercise` 类型 |

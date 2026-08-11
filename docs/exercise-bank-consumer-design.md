# 题库消费层 · 架构设计 + 任务分解（MVP = A 最小闭环）

> 作者：架构师 高见远（Gao） · 团队 `software-tiku-consumer`
> 输入：PRD `docs/exercise-bank-consumer-prd.md`（Alice 交付；§4.4.1 描述 L1 池口径，**代码唯一真相源是 `judge_engine.is_l1_safe()`**）
> 主理人已拍板决策（**无条件遵循**）：Q1=最小闭环 / Q2=A+B 抽题池 / Q3=A 分级解锁答案 / Q4=复用 `LearningTask` 不建表 / Q5=补 `duration_sec` / Q6~Q8 按 PRD 默认。
> 配套图：`docs/class-diagram.mermaid`（类图）、`docs/sequence-diagram.mermaid`（时序图）。
> **已对齐实现终审口径**：L1 安全池的**唯一真相源是 `backend/app/services/judge_engine.py::is_l1_safe()`**（四道闸门见 §①）。
> ⚠️ **本文档只承诺口径（规则），不承诺池规模（数值）**——规模是规则的派生值，每次入库都会变；当前规模请跑 `backend/tests/test_practice.py::TestL1PoolInvariantOnRealBank` 现场查看。
> 其余终审口径：端点名 `/session` `/answer` `/finish` `/reveal` `/guide`；R-04b reveal 审计、C11/C12 guide no-leak。

---

## ① 实现方案 + 框架选型

### 核心难点与对策

| 难点 | 对策 |
|------|------|
| **L1 规则判分必须零 AI、可解析、绝不误判学生** | 纯函数 `judge_engine`，进池过 **四道闸门**（见下），判断题按 **唯一真相源 token 集** 精确匹配。**复合多小问题**（如 `(1)C (2)B`）一旦落入 L1 会把「第 2 小问答对的孩子判错」——这是 MVP 锁 L1 的全部意义（G2：L1 判分准确率 100%）所在，故铁律 C2 必须剔除全部复合题。 |
| **抽题池 A+B 自动放开** | `draw_questions` 默认 `kp_verified=1 AND kp_id IS NOT NULL`（**可归因子池**）；带 `edition` 等筛选只能命中新题时**两级回退**：①可归因子池+排除最近 7 天 → ②全 L1 安全池（含不可归因题）→ 结果页诚实披露 `unmapped_count`。两个池子的规模都由 `is_l1_safe()` 运行期派生，不写死。 |
| **判分池锁 L1，且必须安全** | MVP 判分仅在 **L1 安全池**内抽题。该池由 `judge_engine.is_l1_safe()` 四道闸门唯一定义（见下），并由不变式测试保证「**进池 ⟺ 可判分**」双向成立。 |
| **AI 调用同步阻塞** | Q3 主按钮「让老师带我想一想」调 `practice_guide.py` 经 `ark_respond`（同步），async 路由 `run_in_threadpool` 包裹（复用 chat/exercise 范式）。L1 判分本身**不涉及 AI**，纯本地计算。 |
| **零新增基础设施** | 复用 `LearningTask`(items JSON)、`attempts`(唯一真相源)、`Mistake` 错题本、`profile_service._VERIFIED_KP_EXPR`、`invalidate_cache_for_question`。**零 DDL 改动**（见 §③）。 |

### L1 安全池口径（唯一真相源：`judge_engine.is_l1_safe()`）

> 🔑 **本节定义的是规则，不是数字。** 池规模是规则的**派生值**，题库每入一批新题必然变化。
> 全项目唯一真相源：`backend/app/services/judge_engine.py::is_l1_safe(q_type, options, correct_answer) -> bool`。
> 抽题层（`practice_service._candidate_rows`）与不变式测试（`tests/test_practice.py::TestL1PoolInvariantOnRealBank`）**都只调这一个函数**，不得在别处复写口径。

**四道闸门（全过才准入 L1，选择题与判断题一视同仁）：**

| # | 闸门 | 判据 | 实现 |
|---|------|------|------|
| 1 | **题型受支持** | `q_type ∈ SUPPORTED_TYPES`，即 `选择` / `判断` | `is_l1_judgeable()` |
| 2 | **标准答案非空** | `correct_answer` 去空白后不为空串 | `_clean_text()` |
| 3 | **非复合多小问**（🔴 铁律 C2） | 答案里 `(n)` / `（n）` 小问编号（半/全角数字皆算，正则 `[(（]\s*[1-9１-９][0-9０-９]*\s*[)）]`）出现次数 **< `COMPOUND_MARK_THRESHOLD`(=2)** | `is_compound()` |
| 4 | **标准答案可被 L1 唯一解析** | **选择**：`option_keys(options)` 非空，**且** `extract_choice_key(answer, allowed=keys)` 非 `None`（能唯一抽出一个**落在本题选项键位内**的键位；抽到多个不同键位判为歧义 → 不准入）<br>**判断**：`normalize_judge_value(answer)` 非 `None`（能唯一归一成布尔；多符号并存判为歧义 → 不准入） | `_standard_answer_resolvable()` |

**闸门 4 是根治漂移的关键设计**：它**刻意复用判分层同一组解析器**（`extract_choice_key` / `normalize_judge_value`），把「能不能进池」与「能不能判分」焊死在同一段代码上——解析规则一改，两边同时改，不可能再对不上。

由此得到一条**可证明的不变式（进池 ⟺ 可判分）**：

```
is_l1_safe(q_type, options, ans) is True
  ⟺  judge(q_type, options, ans, ans) 返回 level=1 且 is_correct=True
```

正向保证孩子不会抽到「注定只能自评」的题；反向保证被踢出池的题**绝不会**被 L1 误判为对。两个方向都由 `TestL1PoolInvariantOnRealBank` 在真实库上全量验证。

**闸门 4 实际拦下的两类脏数据**（举例说明规则，不作计数承诺）：① 标准答案指向**不存在的选项键位**（答案 `'C'` 但选项只有 A/B）；② docx 导入把**解析文本串进了答案格**（`'A 提示：在直角三角形中…'`）。这类题孩子无论怎么选都判不出对错，必须踢出。

> **与旧版「五闸门 C1~C5」表的对应关系**：实现把旧表收敛为上述四道——旧 `C1 options 合法` 与 `C4 字母可抽` 合并进**闸门 4**（用「键位落在本题选项白名单内」替代两条独立启发式，更准）；旧 `C3 答案非污染 len(correct_answer) ≤ 40` 这条**长度阈值已废弃**（会误杀长选项正文，也拦不住短脏数据，其意图已被闸门 4 完整覆盖）；旧 `C5 基础` 中的 `status='active'` 下沉到抽题层 SQL 粗筛，答案非空即**闸门 2**；`C2 非复合` 编号保留（代码 docstring 亦称「铁律 C2」）。

**判断题 token 集**（`normalize_judge_value` 唯一真相源）：真 `√ ✔ ✓ 对 正确 对的 是 T true Y yes 1` ／ 假 `× ✗ ✘ x 错 错误 错的 否 不对 F false N no 0`。单符号夹在其它字符里（如 `答案：√`）时，**有且仅有一个**符号才判，多符号（如 `(1)× ×1 (2)√`）一律判为歧义交 L4。

**规模怎么查**：`cd backend && pytest tests/test_practice.py::TestL1PoolInvariantOnRealBank -v`。⚠️ 历史上这个数字被人肉重算过四轮（595 → 591 → 589 → 594），每轮都对不上——根因不是谁算错，而是把「数据的当前事实」当成「设计契约」写进了文档。**不要再把任何池规模写进本文档、代码 docstring 或测试断言。**

### 框架选型（零新增基础设施优先）

- **后端**：FastAPI + SQLAlchemy（PG/SQLite 双方言，无 Alembic，无 `ON CONFLICT`）。L1 判分/抽题/结算为同步纯逻辑；仅 guide 端点用 `run_in_threadpool`。
- **前端**：Vue3 + Vite + TS + Tailwind，**不引入 pinia**，用 `reactive()` 模块式 store。
- **AI 复用**：`practice_judge.py` / `practice_guide.py` 的 `PROMPT`+`VERSION`+`PLACEHOLDERS` 直接 import（L2/L3 才用 judge；MVP 只用 guide）。
- **图表复用**：结果页 `PracticeResult` 复用 `DonutChart`/`MasteryBar`。
- **结论**：预期 **零新增第三方依赖**。

---

## ② 文件列表（🆕 新建 / ✏️ 改 / 归属任务）

### 后端

| 文件 | 状态 | 归属任务 |
|------|------|----------|
| `backend/app/services/learning_service.py` | ✏️ 给 `record_attempt` 增加 `duration_sec` 入参并写入（列已存在，仅补传参） | B1 |
| `backend/app/services/practice_service.py` | 🆕 抽题/判分/结算/看答案/引导 全部业务逻辑 | B2、B3 |
| `backend/app/services/judge_engine.py` | 🆕 L1 规则判分纯函数（`OptionItem`/`JudgeOutcome`/`judge`/`is_l1_safe`/`is_compound`），**并作为 L1 安全池口径的唯一真相源**（四道闸门） | B2 |
| `backend/app/api/practice.py` | 🆕 5 端点（命名见 §③）+ reveal 审计埋点（R-04b）+ guide no-leak 校验（C11/C12） | B4 |
| `backend/app/api/__init__.py` | ✏️ 追加 `include_router(practice.router)`（router 前缀 `/practice`） | B4 |
| `backend/app/schemas/practice.py` | 🆕 请求体（继承 `pydantic.BaseModel`，**snake_case**，禁 `BaseSchema`） | B4 |
| `backend/tests/test_practice.py` | 🆕 抽题/判分/结算/幂等/L4 兜底 + **V3 全库不变式 `TestL1PoolInvariantOnRealBank`（进池 ⟺ 可判分，双向断言，零硬编码数字）** + **V3b C2 复合题反例 `(1)C (2)B` 既不进池、判分也必走 L4** | B4 |

### 前端

| 文件 | 状态 | 归属任务 |
|------|------|----------|
| `student-web/src/router/index.ts` | ✏️ 增加 `/practice` 路由（**不加** `meta.public`） | F1 |
| `student-web/src/layouts/StudentLayout.vue` | ✏️ 左导航增加「练习」项 | F1 |
| `student-web/src/views/Dashboard.vue` | ✏️ `handlePractice()` 由跳 `ai-teacher` 改为跳 `/practice` | F1 |
| `student-web/src/stores/practice.ts` | 🆕 `reactive()` 模块式练习状态 | F2 |
| `student-web/src/api/practice.ts` | 🆕 5 端点封装（`{code,msg,data}` 拆封） | F2 |
| `student-web/src/views/PracticeView.vue` | 🆕 练习主页面（编排筛选/题卡/反馈/抽屉/结果） | F3 |
| `student-web/src/components/practice/BankQuestionCard.vue` | 🆕 题库题卡（**不复用** `QuestionCard.vue`，其 props 绑 `Exercise`） | F3 |
| `student-web/src/components/practice/PracticeFilterCard.vue` | 🆕 筛选卡（grade/section/edition/kp） | F3 |
| `student-web/src/components/practice/AnswerFeedbackBar.vue` | 🆕 单题反馈条（对/错 + 看答案 + 引导按钮，前端 `duration_sec` 计时并 `>600` 截断） | F3 |
| `student-web/src/components/practice/PracticeGuideDrawer.vue` | 🆕 AI 引导抽屉（复用 `practice_guide` 文案；首轮展示前过 no-leak 校验） | F4 |
| `student-web/src/components/practice/PracticeResult.vue` | 🆕 结果页（复用 `DonutChart`/`MasteryBar`） | F4 |

> **零 DDL 改动确认**：`Question`/`Answer`/`Attempt`/`Mistake`/`LearningTask` 模型全部沿用，不新增列/表/迁移。`Attempt.duration_sec` 列**已存在**（learning.py:107），仅 `record_attempt` 当前未接收该参数 → B1 补传参。`reveal` 审计以**结构化日志埋点**实现（R-04b），不新增表，守零 DDL 硬约束。

---

## ③ 数据结构与接口（ORM 零改动 + 接口契约 JSON）

### ORM 层（零改动复用，仅标注关键字段）

```mermaid
classDiagram
    class LearningTask {
        +UUID id
        +UUID student_id
        +str task_type   %% 'practice' 复用 daily/review/exam 之外的值
        +JSON items      %% [{question_id, status, is_correct?}]
        +str status      %% pending/doing/done
        +datetime generated_at
        +datetime finished_at
    }
    class Attempt {
        +UUID id
        +UUID student_id
        +UUID question_id
        +UUID session_id
        +Text selected_answer
        +bool is_correct
        +int duration_sec   %% 已存在，MVP 写入
        +str client_event_id %% 唯一键，幂等
    }
    class Mistake {
        +UUID id
        +UUID student_id
        +UUID question_id
        +str error_type
        +bool solved
    }
    class Question {
        +UUID id
        +str type_          %% 选择/判断/填空...
        +JSON options       %% dict|list|None 双形态
        +bool kp_verified
        +UUID kp_id
        +int grade
        +str edition
        +str section
    }
    class Answer {
        +UUID question_id
        +Text correct_answer
        +Text analysis
    }
```

### 判分引擎 `judge_engine`（🆕 纯函数 + L1 池口径唯一真相源）

```python
@dataclass(frozen=True)
class OptionItem:
    key: str          # 'A'/'B'/... 归一化键位（恒大写单字母）
    text: str

@dataclass(frozen=True)
class JudgeOutcome:
    is_correct: bool  # level=4 时恒为 False（接口契约）
    level: int        # 1=L1规则 4=L4兜底(脏数据/不可解析)
    reason: str

# ---- 判分 ----
def judge(q_type, options, correct_answer, selected_answer) -> JudgeOutcome:
    """L1 规则判分；解析失败/歧义/复合一律 level=4 兜底，绝不猜对错。"""

def extract_choice_key(raw, *, allowed=None) -> Optional[str]:
    """从文本抽唯一选项键位；抽到多个不同键位 → None（歧义交 L4）。"""

def normalize_judge_value(raw) -> Optional[bool]:
    """判断题表述归一成布尔；多符号并存 → None（歧义交 L4）。"""

# ---- L1 安全池口径（唯一真相源，见 §① 四道闸门）----
def is_l1_safe(q_type, options, correct_answer) -> bool:
    """四道闸门全过才准入：①题型受支持 ②标准答案非空 ③非复合多小问
    ④标准答案可被判分层唯一解析。保证「进池 ⟺ 可判分」。
    ⚠️ 唯一真相源，抽题层与不变式测试都只调本函数；不承诺池规模数值。"""

def is_compound(raw) -> bool:
    """(n)/（n） 小问编号出现 ≥ COMPOUND_MARK_THRESHOLD(2) 次 → True（铁律 C2，必踢）。"""

def _standard_answer_resolvable(kind, options, answer) -> bool:
    """闸门 4 私有实现：复用 extract_choice_key / normalize_judge_value，
    把「进池」与「可判分」焊死在同一段解析代码上。"""
```

### 接口契约（5 端点，router 前缀 `/practice`，响应外壳一律 `app.core.response.ok()`，snake_case）

**接口1 建会话/抽题** `POST /api/practice/session`
```json
// 请求
{ "grade": 3, "edition": "青岛版", "section": null, "kp_id": null, "count": 10 }
// 响应 data
{ "session_id": "uuid", "questions": [
    { "question_id":"uuid", "type":"选择", "stem":"...", "options":[{"key":"A","text":"..."}], "difficulty":3 }
  ],
  "pool_size": <int>, "unmapped_count": <int>, "auto_relaxed": true }
// ⚠️ pool_size / unmapped_count 是**运行期由 is_l1_safe() 实时派生的计数**，
//    不是契约常量。前端只做展示与诚实披露，禁止对具体数值做断言或硬编码。
```

**接口2 判分** `POST /api/practice/answer`
```json
// 请求
{ "session_id":"uuid", "question_id":"uuid",
  "selected_answer":"B", "duration_sec":42, "client_event_id":"c-xxx" }
// 响应 data
{ "is_correct": true, "level": 1, "reason": "选项B与标准答案一致" }
// 脏数据兜底: { "is_correct": false, "level": 4, "reason": "答案无法自动判定，已记录" }
```

**接口3 结算** `POST /api/practice/finish`
```json
// 请求 { "session_id":"uuid" }
// 响应 data
{ "session_id":"uuid", "total":10, "correct":7, "wrong":3,
  "mistakes_created":3, "mastery_refreshed": true }
```

**接口4 看答案（Q3 二次确认 + R-04b 审计）** `POST /api/practice/reveal`
```json
// 请求 { "session_id":"uuid", "question_id":"uuid" }
// 响应 data { "correct_answer":"B", "analysis":"..." }
// 副作用：结构化审计日志（不新增表）记录 {student_id, session_id, question_id, reveal_seq, ts}
// 仅对做错的题返回；做对的题前端不展示该入口
```

**接口5 引导（Q3 主按钮，C11/C12 no-leak）** `POST /api/practice/guide`
```json
// 请求 { "session_id":"uuid", "question_id":"uuid", "work":"孩子当前作答" }
// 响应 data { "reply":"...", "ask_final_confirm": false }
// 实现：practice_guide.PROMPT 注入 {{QUESTION}}{{WORK}} → ark_respond（run_in_threadpool）
// 守卫：返回前过 leaks_answer(reply, q_type, options, correct_answer)，命中(返回 True)则脱敏/重生成（V7：抽检 20 轮 0 泄题）
```

> **Schema 约定（硬）**：新接口请求/响应体均直接继承 `pydantic.BaseModel`（snake_case，参考 `schemas/profile.py`），**禁止**继承 `schemas/common.BaseSchema`（camelCase alias 会把 `kp_id` 变 `kpId`）。响应由 `app.core.response.ok(data)` 包裹。

---

## ④ 程序调用流程（时序图）

见 `docs/sequence-diagram.mermaid`（含 建会话/抽题 session → 判分 answer → 看答案 reveal(含审计) → 结算 finish → 引导 guide(含 no-leak) 五条链路）。要点：

1. **session** → `draw_questions` 两级回退抽题（默认可归因子池，必要时放开到全 L1 安全池）→ 落 `LearningTask(items=[{question_id,status:"pending"}])` → 返回题面（options 已归一化为 `[{key,text}]`）。两级池均为 SQL 粗筛 + `is_l1_safe()` Python 复核，规模运行期派生。
2. **answer** → 取 `Question.options`/`Answer.correct_answer` → `judge_engine.judge()`（复合题与不可解析题已在抽题阶段被 `is_l1_safe()` 挡掉，此处必落 L1；万一落 L4 说明不变式被破坏）→ `learning_service.record_attempt(is_correct, duration_sec, client_event_id)`（幂等）→ 回填 `items[i].is_correct`。
3. **reveal** → 仅做错题可点，二次确认后返回 `correct_answer`+`analysis`，**并写审计日志**（R-04b）。
4. **finish** → 遍历错题 `record_mistake`（幂等）→ 对有 `kp_id` 的题 `profile_service.invalidate_cache_for_question` → `LearningTask.status='done'` → 返回统计。
5. **guide** → `practice_guide.PROMPT` 注入 → `ark_respond`（同步，run_in_threadpool）→ **no-leak 校验** → 返回 `reply`+`ask_final_confirm`。

---

## ⑤ 任务列表（后端 / 前端分开，P0 = MVP，P1 = L2/L3 分批）

### 后端（MVP 仅 P0）

| ID | 任务 | 源文件 | 依赖 | 优先级 |
|----|------|--------|------|--------|
| **B1** | 落库补 `duration_sec`：`record_attempt` 增加 `duration_sec: Optional[int]` 入参并写入 `Attempt.duration_sec`（列已存在，仅补传参）；`>600` 截断在**前端**完成，后端 `min(d,600)` 兜底。 | `learning_service.py` | — | P0 |
| **B2** | L1 判分引擎：`judge_engine.py`（`OptionItem`/`JudgeOutcome`/`judge`/`extract_choice_key`/`normalize_judge_value`）+ **L1 池口径唯一真相源 `is_l1_safe()`/`is_compound()`/`_standard_answer_resolvable()`（四道闸门，见 §①）**；脏数据与歧义一律兜底 L4。**禁止在本模块任何 docstring 里写死池规模。** | `judge_engine.py` | — | P0 |
| **B3** | 练习业务：`practice_service.py`（`draw_questions` 两级回退 + `create_session`/`submit_answer`/`reveal_answer`/`finish_session`/`guide_question` + 私有 `_load_session`/`_patch_item`/`_recent_question_ids`/`_clamp_duration`），复用 `learning_service`/`Mistake`/`profile_service.invalidate_cache_for_question`。 | `practice_service.py` | B1,B2 | P0 |
| **B4** | API 收口：`practice.py`（5 端点 `/session` `/answer` `/finish` `/reveal` `/guide`，guide 用 `run_in_threadpool`；reveal 审计埋点 R-04b；guide no-leak 校验 C11/C12）+ `schemas/practice.py`（snake_case）+ `api/__init__.py` 注册 + `tests/test_practice.py`（**V3 全库不变式 `TestL1PoolInvariantOnRealBank`：进池 ⟺ 可判分，双向断言、零硬编码数字** + **V3b C2 反例 `(1)C (2)B` 既不进池、判分也必走 L4** + 幂等 + L4 兜底）。 | `practice.py`,`schemas/practice.py`,`api/__init__.py`,`tests/test_practice.py` | B3 | P0 |
| **P1-B** | L2/L3 AI 判分（填空/应用/计算）：接入 `practice_judge.py`，分批实现，不改 MVP 闭环。 | （后续） | B4 | P1 |

### 前端（MVP 仅 P0）

| ID | 任务 | 源文件 | 依赖 | 优先级 |
|----|------|--------|------|--------|
| **F1** | 路由与导航：`router/index.ts` 加 `/practice`（不加 `meta.public`）；`StudentLayout.vue` 加「练习」导航项；`Dashboard.handlePractice()` 改跳 `/practice`。 | `router/index.ts`,`StudentLayout.vue`,`Dashboard.vue` | — | P0 |
| **F2** | 状态与请求层：`stores/practice.ts`（`reactive()` 模块）+ `api/practice.ts`（5 端点封装，拆 `{code,msg,data}`）。 | `stores/practice.ts`,`api/practice.ts` | F1 | P0 |
| **F3** | 核心交互：`PracticeView.vue` + `BankQuestionCard.vue`（题卡，不复用 `QuestionCard`）+ `PracticeFilterCard.vue`（筛选）+ `AnswerFeedbackBar.vue`（对/错 + 看答案灰色入口 + 引导主按钮，前端 `duration_sec` 计时并 `>600` 截断）。 | `PracticeView.vue`,`BankQuestionCard.vue`,`PracticeFilterCard.vue`,`AnswerFeedbackBar.vue` | F2 | P0 |
| **F4** | 引导与结果：`PracticeGuideDrawer.vue`（调 `/guide`，首轮过 no-leak 校验，复用 `practice_guide` 文案）+ `PracticeResult.vue`（调 `/finish` 后展示，复用 `DonutChart`/`MasteryBar`）。 | `PracticeGuideDrawer.vue`,`PracticeResult.vue` | F3 | P0 |
| **P1-F** | L2/L3 判分前端：AI 反馈/过程点评展示组件，随后端 P1-B 落地。 | （后续） | F4 | P1 |

> MVP 验收口径：B1~B4 + F1~F4 全绿即「抽题→练习→L1 判分→attempts 落库→错题自动入本→结果页→报告吸收」闭环跑通。L2/L3 标 P1 不在 MVP。**V3/V3b 锁死的是不变式而非数字**：V3 断言「进池 ⟺ 可判分」双向成立，V3b 断言复合题反例既不进池、判分也走 L4——口径一破立刻红，入库新题不会误报。

---

## ⑥ 依赖包清单（预期零新增）

- 后端：无新增（FastAPI / SQLAlchemy / pydantic / 现有 `ark_respond` 已在位）。
- 前端：`student-web/package.json` 现有 `axios`/`vue`/`vue-router` 已覆盖，**零新依赖**。
- 测试：`pytest` + 现有 `conftest.py` 内存 SQLite 夹具，无新增。

---

## ⑦ 共享知识（C1–C12 落地细则）

- **C1 跨方言**：禁用 `date()`/`DATE_TRUNC`/`strftime`；upsert 先 `SELECT` 后 `UPDATE/INSERT`；主键 Python 侧 `uuid.uuid4()`。`draw_questions`/`finish_session` 全用标准 SQL，PG/SQLite 一致。
- **C2 幂等**：所有写操作以 `client_event_id` 唯一键去重；`record_attempt`/`record_mistake` 已内置并发回查。前端每题生成稳定 `client_event_id`，重发不双写。
- **C3 真相源**：`attempts` 是唯一作答真相源；`LearningTask.items` 仅存逐题状态快照，**不**反向计算正确率。报告/掌握度直接读 `attempts`。
- **C4 错题入本**：仅 `is_correct=false` 的题在 `finish` 时 `record_mistake`；同题未攻克不重复建档（幂等）。
- **C5 答案分级（Q3）**：做错**默认不给答案**；主按钮「让老师带我想一想」→ `/guide`（AI）；灰色次级「看参考答案」→ `/reveal`，**二次确认**弹窗后返回 `correct_answer`+`analysis`。
- **C6 抽题池（Q2）**：默认 `kp_verified=1 AND kp_id IS NOT NULL`（**可归因子池**）；带 `edition` 等筛选只能命中新题时**自动放开**到全 L1 安全池（含**不可归因题**），结果页披露 `unmapped_count`，不静默降级。两级池的规模均为 `is_l1_safe()` 的运行期派生值，**任何一层都不得硬编码**。
- **C7 判分锁 L1**：仅在 L1 安全池内抽题判分，池口径 = `judge_engine.is_l1_safe()` 四道闸门（§①）。**铁律 C2：复合多小问（`(n)`/`（n）` 编号 ≥2 次）必踢，选择题与判断题一视同仁**——否则 `(1)C (2)B` 这类题里第 2 小问答对的孩子会被误判错。判断题按 `normalize_judge_value()` 的 token 集精确匹配，多符号并存判为歧义。脏数据与歧义一律兜底 L4，标记「未自动判定」而非误判为对/错。**本条只锁规则，不锁数字。**
- **C8 duration_sec（Q5）**：前端单题计时，`>600s` 截断为 600；后端 `record_attempt` 接收并 `min(d,600)` 兜底写入。**⚠️ 周报 `report_service.count_engagement` 有 K3 硬约束：不依据 `duration_sec` 估算学习时长**（仅供数据分析），MVP 不改周报此行为。
- **C9 复用不新建**：`LearningTask` 复用；`practice_judge`/`practice_guide` 仅 import `PROMPT`+`VERSION`+`PLACEHOLDERS` 零改；图表复用 `DonutChart`/`MasteryBar`。
- **C10 响应格式**：全站 `{code,msg,data}`；成功 `code=0`，失败抛 `ApiError`（HTTP 与 `code` 同步）；新接口响应由 `app.core.response.ok()` 包裹，Schema 用 `pydantic.BaseModel`（snake_case）。
- **C11 guide no-leak（V7）**：`/guide` 返回前必过 `judge_engine.leaks_answer(reply, q_type, options, correct_answer)`（⚠️ 语义是「**是否泄题**」，返回 `True` 即命中，需脱敏/重生成）；验收抽检 20 轮 0 条泄题。
- **C12 reveal 审计（R-04b）**：`/reveal` 必须记日志（结构化审计埋点，**不新增表**），记录 `{student_id, session_id, question_id, reveal_seq, ts}`——这是判断「孩子是否直接抄答案」及评估 Q3 分级解锁是否有效的唯一信号源。

---

## ⑧ 待明确事项（均已收尾，仅留运营口径）

1. **L1 池口径已定版、规模不定版**：口径 = `judge_engine.is_l1_safe()` 四道闸门（§①），V3/V3b 以不变式锁死；**规模是派生值，本文档不承诺**，需要时跑 `TestL1PoolInvariantOnRealBank` 现场查。历史上把规模写进文档导致四轮返工（595→591→589→594），此坑已封。
2. **新题不进掌握度**（不可归因题 `kp_id` 全空）：`finish` 时对无 `kp_id` 的题跳过 `invalidate_cache_for_question`（无 `kp_id` 可失效）。是否可接受「练习了但掌握度不体现」已由 Q2 默认 A+B 覆盖，此处仅明示副作用。
3. **`LearningTask.task_type` 扩展 `'practice'`**：无 DDL，仅应用层约定，需确认枚举是否放行。
4. **`/guide` AI 配额口径**：走 `ark_respond` 计配额，是否单独限流（不影响实现）。
5. **reveal 审计存储形态**：本期以结构化日志埋点实现（守零 DDL）；若后续需持久化查询，单独立表属 P1/独立迁移，不在 MVP。

---

## 文末声明

### 锁定的 PRD 默认选择
- Q1 最小闭环（不作 L2/L3）、Q2 A+B 抽题池（默认可归因子池 + 自动放开到全 L1 安全池）、Q3 A 分级解锁答案、Q4 复用 `LearningTask` 不建表、Q5 补 `duration_sec`、Q6~Q8 按 PRD 默认、C1–C10 硬约束全数采纳；**L1 池唯一真相源已由「PRD §4.4.1 规则表」升级为代码内的 `judge_engine.is_l1_safe()`**（文档描述规则，代码定义规则，测试验证规则）。

### 与初版设计不同的修订
1. 🔑 **L1 池规模由「硬编码常量」改为「规则派生值」（本次修订，根治口径漂移）**：初版及其后续修订都把池规模当结论写进文档，结果被人肉重算了四轮（595 → 591 → 589 → 594）且每轮都对不上。根因不是算错，而是**把「数据的当前事实」当成「设计契约」来写**——题库每入一批新题，这个数字必然变，文档必然过时。现改为：文档只描述**口径**（四道闸门，属设计契约，见 §①），唯一真相源下沉到 `judge_engine.is_l1_safe()`，规模由 `TestL1PoolInvariantOnRealBank` 现场派生，**文档、代码 docstring、测试断言三处都不再出现任何池规模数字**。
2. **闸门口径由「选择题五闸门 C1~C5」收敛为「四道闸门」**：`options 合法` + `字母可抽` 合并为「标准答案可被 L1 唯一解析」（键位白名单校验），`len(correct_answer) ≤ 40` 长度阈值废弃，`status='active'` 下沉到抽题层 SQL 粗筛；铁律 C2「非复合多小问」编号保留。详见 §① 的对应关系说明。
3. **端点命名对齐 PRD §4.5**：`/practice/draw`→`/practice/session`、`/practice/submit`→`/practice/answer`，其余 `/finish` `/reveal` `/guide` 不变（共 5 个写接口）。
4. **新增 R-04b reveal 审计埋点、C11/C12 guide no-leak 校验**：reveal 记结构化日志（不新增表）、guide 返回前过答案泄露校验（V7）。
5. **V3/V3b 验收由「数字断言」改为「不变式断言」**：V3 = 全库验证「进池 ⟺ 可判分」双向等价（正向：池内每题喂标准答案必得 `level=1 & is_correct=True`；反向：池外的题绝不会被 L1 判分）；V3b = 复合题反例 `(1)C (2)B` 既不进池、判分时也必走 L4。**规则一改立刻红，入库新题不会误报。**
6. `record_attempt` 补 `duration_sec` 入参、`finish` 对无 `kp_id` 新题跳过缓存失效、新接口 Schema 用 `pydantic.BaseModel`（snake_case）等前述决策不变。

### 需老板再拍板的遗留项（仅运营/约定口径，不影响实现）
- 新题（不可归因题，`kp_id` 全空）练习后不进掌握度/周报是否可接受（§⑧.2）。
- `LearningTask.task_type` 是否扩展 `'practice'`（§⑧.3）。
- `/guide` 配额口径（§⑧.4）；reveal 审计持久化形态（§⑧.5，守零 DDL 本期用日志）。

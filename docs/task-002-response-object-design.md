# TASK-002 学生回答结构化 · 设计文档（V1 设计稿，不动代码）

> 状态：**设计待评审**（team-lead 确认后进入第二步落地；当前工作区压着 Benchmark 同事未提交 WIP，三层未提交叠加风险过大，落地时点由 lead 统一通知）。
> 作者：software-engineer-17 · 任务 #31
> 上游输入：任务书（#31）+ 规划 §37/§38/§40（lead 已做前期 synthesis，直接作为设计边界）+ 现状代码勘察。

---

## 1. 背景与目标

「学生回答结构化」是 003 决策引擎与 006 六维掌握度的**共同地基**：目前学生这一轮说的话只以纯文本落在 `ai_messages.content`（orchestrator.continue_session L696），下游要判「学生答了什么、对没对、卡在哪」只能重新解析自然语言或再调一次 LLM。

本设计定义 V1 的结构化对象（response object）：在学生消息落库时，**顺带**产出一份规则级结构化 JSON，落 `ai_messages` 新增列；`content` 原文天然成为 `raw_response`，保留可重分析能力。

设计边界（来自规划，逐条遵守）：

- 规划 §38 原话：「第一版不要过度数据库化，可以暂时合并为一个 JSON 字段保存 analysis/evidence/error/next_action」→ **V1 用合并 JSON 列，不建新表**（详见 §2 方案对比）。
- 规划 §40：「Response Analyzer 与 Tutor 分离」→ V1 先落地**规则/轻量 analyzer**（零 LLM 调用），LLM analyzer 作为明确演进位（§4.4），接口保持可互换。
- 任务书字段集：`response_type`（8 枚举）+ `answer`/`reasoning`/`evidence`/`error` + `confidence` + `step_state` 子对象；`raw_response` 保留可重分析。

---

## 2. 方案对比与推荐

| 维度 | A：`ai_messages` 增加结构化 JSON 列（推荐） | B：独立 `student_responses` 表族（规划 §37） |
|---|---|---|
| 新表/新迁移 | 零新表，仅一次 `ADD COLUMN` | 新表 + 索引 + 外键 + 双写链路 |
| 与 §38「不要过度数据库化」 | 完全贴合（原话即指此形态） | 违背 V1 从简原则 |
| raw 保留 | `content` 列即 raw_response，**零冗余** | 需在新表复制一份原文，双源一致性风险 |
| 老数据兼容 | 老消息列值为 NULL，读取方按「无结构化信号」降级，**零迁移数据** | 老数据永远缺失，需回填脚本 |
| 写入链路 | continue_session 已有 `db.add(AiMessage(...))`，多传一个字段 | 同一事务双写两表，失败一半即漂移 |
| 消费方（#33）成本 | 读消息时同行取列，零 JOIN | 每次多一次 JOIN/子查询 |
| 查询/聚合能力 | JSON 内查询弱（PG 可 JSONB 表达式 + GIN 索引，SQLite 弱） | 列式索引强 |
| 与既有范式一致性 | 与 `quality_traces.payload`（同事 Benchmark WIP）同款「事件/上下文进 JSON 列」范式 | 无先例 |

**推荐：A 方案作为 V1。** 核心理由：

1. 规划 §38 的原文表述就是 A 的形态（「暂时合并为一个 JSON 字段保存」），B 是 §37 的远期形态，V1 提前建表等于把演进成本前置；
2. `raw_response` 零成本——现有 `content` 列就是原文，这是 B 方案结构上给不了的（B 必须复制或跳表关联）；
3. 老数据零迁移——列可空，历史消息天然 NULL，读取方降级即可，不需要回填任务；
4. 写入面收敛——只在 continue_session/continue_session_async 已有的 `db.add(AiMessage)` 处多传一个参数，不新开写路径，不引入双写一致性风险；
5. 与同事 Benchmark 的 `quality_traces.payload`（JSONType 列）同一范式，团队认知一致。

**B 方案的演进位（何时升级、怎么升级）**：当 #33 进入需要「按 response_type / error.type 高频聚合统计」（如六维掌握度批量重算）时，从 JSON 列物化出新表——写一个一次性物化脚本（`INSERT INTO student_responses SELECT ... FROM ai_messages WHERE role='user'`，JSON 解析在 Python 侧做），物化期间双写，完成后切换消费方。**物化不删列、不删 content**，raw 永远可再物化。此演进不在 V1 范围，记录于此供 #33 排期参考。

---

## 3. 数据契约

### 3.1 列定义

`ai_messages` 新增一列（详见 §5 迁移）：

| 属性 | 值 |
|---|---|
| 列名 | `response_object` |
| 类型 | `JSONType()`（PG→JSONB，SQLite→TEXT + json 序列化，见 `app/models/base.py`） |
| 可空 | 是（`nullable=True`，`default=None`） |
| 适用范围 | **仅 `role='user'` 的学生消息**；assistant/system 消息恒为 NULL |
| DB 层约束 | 不加 CHECK（JSON 内部枚举由 analyzer 保证 + 应用层校验；跨方言 CHECK JSON 不可行，遵守 base.py 方言纪律） |

### 3.2 response_type 八枚举（唯一真相源：`app/ai/response_analyzer.py`）

| 枚举 | 语义 | 典型语料 |
|---|---|---|
| `FINAL_ANSWER` | 给出（自认为）最终的答案/结论 | 「我算出来是 x=2」「答案是 12」 |
| `REASONING` | 讲思路/过程，未给最终答案 | 「我先把 3 移到右边」「因为两边都要除以 2」 |
| `QUESTION` | 学生反问题目本身 | 「这道题求什么呀」「这个条件什么意思」 |
| `HINT_REQUEST` | 主动要提示/线索 | 「给点提示」「卡住了」「从哪开始啊」 |
| `I_DONT_KNOW` | 明确不会/不懂 | 「我不会」「完全看不懂」 |
| `PARTIAL_ANSWER` | 做了一部分，明示未完成 | 「我算到 2x=4 了，后面不会」 |
| `CORRECTION` | 修正自己或他人的说法 | 「不对不对，应该是」「我改一下」 |
| `UNCLEAR` | 兜底：无法归入以上任何一类 | 「……」、纯符号、空泛语气词 |

**判级优先级（规则版，先命中先返回；与 intent 的 mode 优先级相互独立，见 §3.4）：**

```
I_DONT_KNOW > HINT_REQUEST > QUESTION > CORRECTION > FINAL_ANSWER > PARTIAL_ANSWER > REASONING > UNCLEAR
```

设计说明：
- `I_DONT_KNOW` 最优先——「我不会」是最强的行为信号，且与 intent 的 DEMO 模式天然伴生（§3.4 映射表）；
- `CORRECTION` 先于 `FINAL_ANSWER`——「不对，应该是 5」内容是改口后的答案，但行为类型是修正，消费方（#33）对两类信号的运用方式不同（改口 = 元认知信号，权重高于普通给答案）；
- `UNCLEAR` 恒为兜底，保证 `response_type` **产出率 100%**（消费方永远拿得到合法枚举，不存在「没分类」状态）。

### 3.3 JSON Schema（数据契约）

```json
{
  "response_type": "FINAL_ANSWER",
  "answer": "x = 2",
  "reasoning": null,
  "evidence": ["2x + 3 = 7"],
  "error": null,
  "confidence": 0.8,
  "step_state": null
}
```

字段契约表（消费方只依赖下表语义；**任何字段都允许为 None/缺键**，缺键按 None 处理）：

| 字段 | 类型 | V1 产出规则 | 消费者 | 语义与边界 |
|---|---|---|---|---|
| `response_type` | str，八枚举之一 | **必出**（UNCLEAR 兜底） | 003 | 学生本轮回答的**内容分类**；不是教学策略，不替代 mode（§3.4） |
| `answer` | str \| null | 有且只有一个可解析数字/表达式时抽取（复用 `_sole_number` 语义：`7` vs `17` 不误判），否则 null | 006 | 学生给出的最终答案；**不是判对错结论**（判题归 sympy_judge，§4.3） |
| `reasoning` | str \| null | V1 不抽（恒 null），LLM analyzer 演进位填充 | 003 | 学生自述思路片段 |
| `evidence` | list[str] \| null | V1 仅抽文本中出现的等式/不等式片段（如 `2x + 3 = 7`），无则 null | 003 | 学生引用的题目条件，用于判断「有没有读对题」 |
| `error` | {type, detail} \| null | V1 仅学生**明示自报错因**时填：`{"type": "CALC", "detail": "忘了变号"}`；type 取五类机器码（READ/METHOD/CALC/KNOWLEDGE/CHECK，与 `error_categories.normalize_error` 对齐） | 006 | **权威判题不在 chat 链路**（§4.3 边界纪律），此处只承载学生自我报告 |
| `confidence` | float 0~1 \| null | 规则命中强度映射：多规则命中 0.8 / 单规则 0.6 / 兜底 UNCLEAR 0.3；**语义是「分类置信度」，不是「答案正确概率」** | 006 | 低置信度信号在掌握度更新中降权；与 quality_traces.severity 无任何关系 |
| `step_state` | {current_step, total_steps, status} \| null | V1 大部分为 null；仅强信号时填：`{"current_step": 1, "total_steps": null, "status": "in_progress"}`（status ∈ not_started/in_progress/stuck/done） | 003 | **学生自报**的步骤进度；与 `ai_sessions.session_state`（系统阶段）是两回事，消费方不得互相推导 |

`raw_response`：即 `ai_messages.content` 原文，**不复制进 JSON**（零冗余原则）；重分析时以 content 为输入重跑 analyzer。

### 3.4 与 intent 4 mode 的关系（并存，不互相取代）

两者服务**不同目的**，必须并存：

| | intent.mode | response_type |
|---|---|---|
| 回答的问题 | 老师**下一轮怎么教**（教学策略） | 学生**这一轮说了什么**（内容分类） |
| 取值 | GUIDE/HINT/DEMO/JUDGE（4） | 八枚举（§3.2） |
| 定稿处 | `app/ai/intent.py::detect_mode`（唯一定稿，铁律：下游不许再猜再改） | `app/ai/response_analyzer.py`（本设计新建，唯一定稿处） |
| 消费方 | tutor / state_machine / answer_guard / prompt | #33 决策引擎 / #006 掌握度 |
| 是否互推 | **禁止**：response_type 不得回写 mode；mode 不得推导 response_type | 同左 |

**软映射表**（仅用于观测/埋点对齐，**不进任何分支判断**）：

| response_type | 常见伴生 mode |
|---|---|
| FINAL_ANSWER / PARTIAL_ANSWER | JUDGE |
| REASONING | GUIDE |
| QUESTION | GUIDE / HINT |
| HINT_REQUEST | HINT |
| I_DONT_KNOW | DEMO |
| CORRECTION | GUIDE / JUDGE |
| UNCLEAR | GUIDE |

为什么必须并存：mode 的输入是「学生这句话+历史+override」，服务教学策略切换（还带冷却回切防锁死）；response_type 服务「学生答了什么」的事实记录与后续掌握度计算。一个学生说「给点提示吧，我卡在移项了」——mode=HINT（要教提示），response_type=HINT_REQUEST（内容是要提示）；若只用 mode，掌握度引擎永远不知道学生其实讲过半句思路。二者互相取代都会丢信息。

---

## 4. 产出机制：V1 规则/轻量 analyzer

### 4.1 模块与接口（第二步落地件）

新模块 `backend/app/ai/response_analyzer.py`，纪律照抄 `intent.py` 的模块头（零 DB、零网络、零新依赖、纯标准库 `re`，可离线单测）：

```python
def analyze_response(*, text: str, history: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """把学生这一轮原话结构化成 response object（§3.3 契约）。

    Args:
        text: 学生本轮原话（必填）。
        history: 会话历史 [{"role","content"}]，**不含**本轮；V1 仅预留
            （CORRECTION 的「修正上一轮」判定可能用到），当前不参与判断。

    Returns:
        符合 §3.3 契约的 dict；response_type 恒为合法枚举，其余字段可 None。
        **不抛异常**——任何脏输入都收敛为 UNCLEAR + 尽量抽取。

    Note:
        raw_response（content 列）保留可重分析：本实现为规则版，模型升级后
        可凭原文本重跑同签名接口（或换 LLM 实现）覆盖旧数据，签名不变。
    """
```

### 4.2 V1 实现要点（规则版）

1. `response_type`：自建轻量正则规则表（参考 intent 的 `_*_PATTERNS` 组织方式，模块级预编译），按 §3.2 优先级判级；**不 import intent 的私有正则**——两个模块各自独立演进，避免「改 mode 规则顺手打破回答分类」。
2. `answer` 抽取：复用现成件——
   - `app/services/ai_exercise_service._sole_number` 的「有且只有一个数字才抽」语义（ analyzer 内实现同款 `_NUM_RE` 或第二步落地时评估 import；注意分层纪律：`app.ai` 不反向依赖 `app.services`，故**建议复制这 15 行语义并在 docstring 注明来源**，或提升为 `app/ai/_num_utils.py` 共享——推荐后者，消除双份）；
   - 表达式场景（`x=2`、`2x+3=7` 片段）走轻量等式抽取（evidence 与 answer 共用一次扫描）。
3. `evidence`：扫等式/不等式片段（`_EQ_RE`），有则返回列表，无则 None。
4. `error`：仅「学生明示自报错因」正则（`我(算|写|看|移)(错|漏)了`、`忘记(变号|进位)` 等 → type 映射表 CALC/CHECK/READ），否则 None。**不猜错因**。
5. `confidence`：按命中规则数映射（§3.3 表），语义仅为分类置信度。
6. `step_state`：仅 `我(算|做|写)完了`→done、`卡(住|在)`→stuck、`第一步`→current_step=1 三档强信号，其余 None。

### 4.3 边界纪律（写进模块 docstring，落地时强制执行）

- **判对错的权威在 sympy_judge / answer_guard / judgeExercise 链**，chat 链路的 analyzer 不判对错、不产 verdict，只记录学生说了什么。两处判题口径漂移是本设计最大的腐烂风险，用纪律掐死。
- analyzer 失败（脏输入、正则异常）**不许拖垮辅导主流程**：内部 try 兜底返回 UNCLEAR 契约对象——与 `_emit_chat_dna_event` 的旁路容错同款姿势。
- 结构化列只写 `role='user'` 消息；assistant 消息恒 NULL（消费方按 role 过滤）。

### 4.4 LLM analyzer 演进位（规划 §40 完整版）

规划 §40 要求 Response Analyzer 与 Tutor 分离——V1 规则版已是**进程分离**（独立模块、独立调用位）；后续演进 = 同签名接口换 LLM 实现：

- 演进触发条件：规则版在 V1 内测数据上 UNCLEAR 占比 > 20%，或 #33 需要 reasoning 字段；
- 演进方式：`analyze_response` 内部按配置开关（照 `system_config` 灰度键先例，如 `RESPONSE_ANALYZER_LLM`，默认关）分流到 LLM 实现；LLM 版输出同一契约；
- **重分析能力**：raw 在 content 列永远在，LLM 版上线后可对历史消息批量重跑覆盖 `response_object`（脚本 + 幂等，按 message id UPDATE）。

---

## 5. 迁移方案

### 5.1 列定义（ORM，第二步落地件）

`app/models/ai.py::AiMessage` 增加（照 `quality_trace.py` 的 JSONType 列写法）：

```python
response_object: Mapped[Optional[Any]] = mapped_column(
    JSONType(),
    nullable=True,
    default=None,
    comment="学生回答结构化对象(JSON):response_type/answer/reasoning/evidence/"
            "error/confidence/step_state;仅user消息,老数据NULL,可凭content重分析",
)
```

### 5.2 SQLite ALTER ADD COLUMN 限制的处理

- SQLite `ALTER TABLE ... ADD COLUMN` **不支持 `IF NOT EXISTS`、不支持加 COMMENT**；
- 对 **NOT NULL 列必须带默认值**（`sync_schema.py` L54-56 有专门推导逻辑）——本列设计为 `nullable=True`，**天然绕开该限制**，直接 `ADD COLUMN response_object TEXT` 即可；
- 幂等策略：靠 `app/scripts/sync_schema.py` 的「先检查再执行」（L125-131）或手工 `PRAGMA table_info(ai_messages)` 预检，不依赖 SQL 层 IF NOT EXISTS。

### 5.3 迁移 SQL 双方言草稿（编号 024）

> ⚠️ 落地前提：当前 migrations 最大号是 **023**（同事 quality_traces，未提交 WIP）。第二步开工前先 `ls backend/migrations/` 确认 024 未被他人占用；若被占则顺延，并同步更新本文件。

**PG 版**（`backend/migrations/024_student_response_object.sql`）：

```sql
-- 024 · ai_messages 增加学生回答结构化列 response_object（TASK-002 V1）· PostgreSQL 方言
--
-- 设计：docs/task-002-response-object-design.md §5。A 方案（合并 JSON 列），
-- 不建新表；raw_response 即现有 content 列，不复制。
-- 幂等：PG 9.6+ 的 ADD COLUMN IF NOT EXISTS。
-- 执行：psql -U coach -h 127.0.0.1 -p 5433 -d coach -f backend/migrations/024_student_response_object.sql
-- SQLite 版本见同目录 024_student_response_object_sqlite.sql。

BEGIN;

ALTER TABLE ai_messages ADD COLUMN IF NOT EXISTS response_object JSONB;

COMMENT ON COLUMN ai_messages.response_object
    IS '学生回答结构化对象(JSON):response_type/answer/reasoning/evidence/error/confidence/step_state;仅user消息;老数据NULL;可凭content重分析';

COMMIT;
```

**SQLite 版**（`backend/migrations/024_student_response_object_sqlite.sql`）：

```sql
-- 024 · ai_messages 增加学生回答结构化列 response_object（TASK-002 V1）· SQLite 方言
--
-- 与 PG 版的差异（参照既有 023 约定）：
--   * JSONB → TEXT（JSON 序列化由 app/models/base.py::JSONType 负责）；
--   * SQLite 的 ALTER TABLE ADD COLUMN 无 IF NOT EXISTS、无 COMMENT ON——
--     幂等由 sync_schema.py「先检查再执行」或 PRAGMA table_info 预检保证；
--   * 列可空，无需 server_default（绕开 SQLite「NOT NULL 加列必须带默认值」限制）。
--
-- 幂等说明：重复执行前必须先确认列不存在，见上。
-- 开发/测试库由 Base.metadata.create_all 建出（模型已登记），
-- 本文件用于手动对齐或离线 SQLite 部署。

BEGIN TRANSACTION;

ALTER TABLE ai_messages ADD COLUMN response_object TEXT;

COMMIT;
```

### 5.4 冷母库同步步骤（顺序即纪律）

母版是唯一事实源（**`F:/错题教练/_wt/_master_dev.db`**——在 worktree 的父目录，不在 git 仓库内；`reset_dev_db.py::resolve_master` 按候选路径解析，勿凭相对路径猜）；`dev.db` 是母版的副本。**必须先改母版，再重建 dev.db，最后校验**——反序会让 dev.db 与母版漂移，下次 reset 把改动抹掉。

1. **备份母版**：一律用 sqlite3 backup API（**禁 `cp`**：WAL 未检查点时拷出来是 malformed 半成品），例如 `python -c "import sqlite3; s=sqlite3.connect('file:...?mode=ro',uri=True); d=sqlite3.connect(bak); s.backup(d)"`。母版被写坏没有第二次机会。
2. **改母版**（先预检再执行，幂等）：
   ```bash
   sqlite3 "F:/错题教练/_wt/_master_dev.db" "PRAGMA table_info(ai_messages);"   # 确认无 response_object 列
   sqlite3 "F:/错题教练/_wt/_master_dev.db" "ALTER TABLE ai_messages ADD COLUMN response_object TEXT;"
   ```
3. **重建 dev.db**：`cd backend && python scripts/reset_dev_db.py --ensure`（先体检，不合格就地重建；`--ensure` 是团队约定入口）。**注意**：`--ensure` 的内容指纹已含列结构哈希（024 事故后加固）——母版加了列而 dev.db 漏同步时指纹必不符、自动重建，不再需要人工跑 sync_schema 补列。
4. **模型↔库校验**：`cd backend && python app/scripts/sync_schema.py`（schema_guard 失败时的官方修复入口；此时应报「无缺列」）。
5. **守卫测试**：`python -m pytest tests/test_schema_guard.py -q` 必须通过。
6. **PG 环境**（如在跑）：psql 执行 024 PG 版。

---

## 6. 与 #33（003 决策引擎 / 006 六维掌握度）的接口约定

### 6.1 消费方读什么

推荐入口（第二步落地时随 analyzer 一并提供，放在 orchestrator 或 analyzer 模块，照 `_pick_analysis` 的回捞范式）：

```python
def latest_response_objects(messages: List[AiMessage], limit: int = 1) -> List[Optional[Dict[str, Any]]]:
    """按时间升序取最近 limit 条**学生消息**的结构化对象。

    - 只取 role='user'；assistant 消息恒 NULL，直接跳过；
    - 老消息（无该列 / 列值 NULL）→ 对应位置为 None，**不抛异常**；
    - 脏 JSON 文本 → JSONType.process_result_value 已回退原值，调用方按 None 处理。
    """
```

消费方字段依赖（**只依赖这些键，缺键按 None**，与 §3.3 契约表一致）：

| 消费者 | 读的字段 | 用途 |
|---|---|---|
| 003 决策引擎 | `response_type`、`evidence`、`step_state` | 「学生答的是什么类型/读对题没有/自报进度」——作为决策输入之一，**与 mode 并列输入，不替代** |
| 006 六维掌握度 | `answer`、`error.type`、`confidence` | 答案信号 + 自报错因 + 分类置信度降权；判对错仍走 sympy_judge 权威链 |

### 6.2 兼容性保证（#33 可直接消费的前提）

1. **老数据**：V1 上线前的历史 user 消息 `response_object` 为 NULL → `latest_response_objects` 返回 None 项 → #33 按「无结构化信号」走现有降级路径（与 `_pick_analysis` 返回 `{}` 同款宽容），不需要等回填；
2. **脏数据**：任何非法 JSON → 读出来是原文本 → 非 dict 按 None；
3. **枚举稳定**：八枚举只增不改（新增枚举 appended 在 UNCLEAR 之前、代码同步 `RESPONSE_TYPE_VALUES` 元组），#33 的匹配用「in 集合」而非 switch 全量。

---

## 7. 测试策略（第二步落地件）

沿用现有 chat 测试的 `mock_ark`/`FakeArk` 风格（`tests/t03_helpers.py` + conftest 内存库，**绝不设 DATABASE_URL**）。用例清单：

| # | 用例 | 断言要点 |
|---|---|---|
| 1 | `test_user_message_gets_response_object` | 「我算出来是 x = 2，对不对」→ user 消息列非空；`response_type=FINAL_ANSWER`；`answer` 含 `2` |
| 2 | `test_response_type_enum_coverage`（参数化 8 组语料） | 每组落对应枚举；UNCLEAR 组用「……」 |
| 3 | `test_missing_fields_are_none` | 「给点提示吧」→ `answer=None`、`evidence=None`、`error=None`；`response_type=HINT_REQUEST` 仍在 |
| 4 | `test_sole_number_guard` | 「7 和 17 差多少」→ `answer=None`（有且只有一个数字的语义，不误抽） |
| 5 | `test_old_messages_without_column_compat` | 直接 ORM 插一条 `response_object=None` 的旧 user 消息 → `latest_response_objects` 返回 None 项不炸；`_to_history` 正常 |
| 6 | `test_assistant_messages_not_structured` | 开场两条 assistant 消息列恒 NULL |
| 7 | `test_mode_unaffected_by_structuring` | 同一输入加结构化前后，`continue_session` 返回的 `mode`/`next_mode` 与现状逐字一致（回归铁律：结构化是旁路，不改教学策略） |
| 8 | `test_raw_content_preserved` | `content` 与学生原话逐字相等（重分析前提） |
| 9 | `test_dirty_json_tolerated` | 手工写入非法 JSON 文本 → 读出非 dict → 按 None 处理不炸 |
| 10 | `test_analyzer_unit`（纯函数，零 DB） | 空串、`history=None`、纯符号、超长输入 → 均返回合法契约对象，`response_type` 恒合法 |
| 11 | `test_analyzer_failure_tolerated` | monkeypatch  analyzer 内部抽取函数抛异常 → 主流程仍返回 reply，列落 UNCLEAR 契约对象（旁路容错） |
| 12 | `test_quota_and_gate_unaffected` | 与 #29 低置信度闸门、AI 配额链路的回归（同一文件顺带覆盖） |

预期增量：约 15-20 个用例（含参数化展开），全量基线只增不减。

---

## 8. 落地任务清单（第二步，等 lead 通知开工）

顺序即依赖；每步一个可验证产物。

1. **共享数字工具**（消除双份）：评估把 `_sole_number` 语义提升为 `app/ai/_num_utils.py`（或复制 15 行并注明来源，二选一，文档记录决策）→ 单测。
2. **analyzer 模块**：`app/ai/response_analyzer.py`（规则版，§4.2 要点 + §4.3 纪律写进 docstring）+ 纯函数单测（§7 #10/#11）。
3. **模型 + 迁移**：`AiMessage.response_object` 列 + `migrations/024_*` 双方言（§5.3 草稿落盘，开工前确认编号）。
4. **冷母库同步**：按 §5.4 六步执行（023/024 母版均已落地，路径按 §5.4 实际路径，勿再用 `<repo>/_wt/` 相对路径）。
5. **编排接线**：`continue_session` / `continue_session_async` 在 `db.add(AiMessage(role="user"))` 处带 `response_object=analyze_response(...)`（旁路容错：analyzer 异常不拖垮主流程）。
6. **回捞 helper**：`latest_response_objects`（§6.1 签名）+ 老数据/脏数据兼容测试（§7 #5/#9）。
7. **集成测试**：`tests/test_response_object.py`（§7 全清单）。
8. **全量自测**：`pytest -q`，确认基线只增不减、schema_guard 通过（若 #30 的 WIP 失败仍在，按既有口径隔离报告，不混入本任务结论）。
9. **报告**：改动文件 + 行号 + 测试输出，回传 team-lead，**不自行 commit**。

前端 V1 **无改动**（结构化列是内部地基，不出 API；与 quality_traces 同定位）。若未来要给「学生回答画像」类展示，另开任务。

---

## 9. 风险与未决问题（评审时请重点看这几条）

1. **母版撞车（已解除）**：#30 的 023 quality_traces 与本任务 024 均已按序进冷母版（023 先、024 后），且 023 迁移文件已按模型真相源重写收敛（母版当时 quality_traces 与 dev.db 存在列结构漂移：severity server default、created_at 类型、3 个 FK 索引缺失——已重建为 create_all 形态并给 reset 指纹加列结构哈希防复发）。后续任何加列迁移前，`--ensure` 会自动发现母版/dev 列漂移并重建。
2. **判题口径漂移（设计已防）**：chat 链路 analyzer 不判对错是纪律不是实现细节——评审需确认 #006 消费方同意「answer 只是抽取信号，对错以 sympy_judge 为准」。
3. **UNCLEAR 占比观测**：V1 上线后应按会话统计 UNCLEAR 占比（可走既有 DNA 埋点或临时 SQL），>20% 触发 §4.4 LLM 演进评估。**注意不要新增埋点任务**，先用 SQL 脚本离线看。
4. **`_sole_number` 共享方式**：提升共享 vs 复制+注明，两种都可行，倾向提升共享（消双份），但注意 `app.ai` 不依赖 `app.services` 的分层纪律——若提升只能放到 `app/ai/` 下或 `app/core/` 下。
5. **枚举扩张流程**：八枚举只增不改，新增需同步 `RESPONSE_TYPE_VALUES` + 本文档 §3.2 表 + analyzer 规则，三处一致。

---

## 附：本设计引用的现状代码锚点

| 锚点 | 位置 |
|---|---|
| user 消息落库（结构化列写入位） | `app/ai/orchestrator.py` L696 / L787 附近（continue_session / continue_session_async 的 `db.add(AiMessage(role="user"))`） |
| analysis 回捞范式（helper 参照） | `app/ai/orchestrator.py::_pick_analysis` L968 |
| intent mode 唯一定稿处（并存纪律参照） | `app/ai/intent.py`（模块头铁律 + `detect_mode`） |
| 「有且只有一个数字」抽取语义 | `app/services/ai_exercise_service.py::_sole_number` L775 |
| 答案归一化/片段抽取 | `app/services/sympy_judge.py::_normalize_answer` L417、`_extract_result_fragment` |
| 五类错因机器码 | `app/core/error_categories.py::normalize_error` |
| JSONType 跨方言列范式 | `app/models/base.py::JSONType` L92 + `app/models/quality_trace.py` payload 列 |
| 迁移双方言范式 | `backend/migrations/023_quality_traces*.sql` |
| ALTER ADD COLUMN 幂等机制 | `app/scripts/sync_schema.py` L54-56 / L125-131 |
| 冷母版定位与重建 | `backend/scripts/reset_dev_db.py::resolve_master` L94 / `--ensure` L344 |
| 测试夹具风格 | `backend/tests/t03_helpers.py`（FakeArk/patch_ark）+ `conftest.py`（内存库闸门） |

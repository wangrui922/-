# tutor-mcp 适配计划（侦察结论转开题）

- 日期：2026-10-20
- 作者：oss-scout（技术侦察）
- 状态：开题计划（等评审后排期；本轮只出计划，不装依赖、不改业务代码）
- 上游依据：《技术侦察简报》（GitHub 同类项目 5 维度核查，许可证逐个验证原文）
- 工作树：`F:/错题教练/_wt/integrate`（develop @ 0c27f64）

---

## ① 侦察结论回顾

| 项 | 值 |
|---|---|
| 仓库 | `ArnaudGuiovanna/tutor-mcp`（https://github.com/ArnaudGuiovanna/tutor-mcp） |
| 形态 | **Go 编写的 MCP server**（go.mod，需 Go 1.26.9 构建；stdio 本地 / HTTPS+OAuth 远程） |
| 定位 | 自适应学习引擎：LLM 负责"生成教学"，引擎负责"确定性教学法治理"（Generative teaching. Deterministic pedagogy.） |
| 成熟度 | **Alpha**（README 徽章 status-alpha），当前 v0.6.1；52★，pushed 2026-10-19（活跃但 API 不稳定） |
| 许可证 | **MIT**（LICENSE 原文已验证：可商用） |

**仓库结构与可复用模块定位**（本计划唯一借鉴来源，不部署其二进制）：

| 模块 | 文件 | 借鉴点 |
|---|---|---|
| 苏格拉底系统提示词 | `tools/prompt.go`（巨型 `systemPrompt` 常量） | 一次只问一个问题；不向学生解释算法推理；"让自己逐步过时"的 autonomy 目标；`motivation_brief` 一次只给一个激励角度；失败重构为努力/策略而非能力；情绪先接纳再重构；`pedagogical_contract` 四段结构（`llm_instruction` / `constraints` / `audit_rationale` / `learner_explanation`） |
| BKT 算法与阈值 | `algorithms/bkt.go`、`individual_bkt.go`、`bkt_info_gain.go`、`thresholds.go` | 掌握度贝叶斯更新；路由括号 **0.30 / 0.70 / 0.85**（N=3 次交互稳定化）；info-gain 选题（诊断期选信息增益最大的概念） |
| 错因锁定与门控 | `tools/misconceptions.go`、`tools/pedagogical_decision.go`（含 gate appendix） | `DEBUG_MISCONCEPTION` 活动锁定该概念至**连续 3 次交互未复现**错因；KST 先修门槛过滤候选概念 |
| 核心循环 | `tools/pedagogical_decision.go` + MCP 工具 `get_next_activity` → `record_interaction` | 选活动 → 教/练 → 记录 → 更新学习状态；决策可审计（pedagogical_snapshots） |
| 设计文档 | `docs/algorithms.md`、`docs/architecture.md`、`docs/mcp-tools.md` | 机制与边界的成文参考 |

**明确不借鉴**：`algorithms/fsrs.go`（Go 版 FSRS）——本项目复习调度已于 T04 统一到 py-fsrs 唯一内核，引入即"双内核"，纪律禁止。

## ② 集成点与边界（仓库文件路径 + 行号）

| 层 | 文件:行号 | 现状 | tutor-mcp 借鉴落点 |
|---|---|---|---|
| 引导主链 | `backend/app/ai/orchestrator.py:480-491` | 首轮引导 `tutor.next_question(session_messages, state=SESSION_STATE_INTRO)` | 不改编排签名；`pedagogical_contract` 作为 tutor agent 出参的**可选附加字段** |
| 话术层 | `backend/app/ai/agents/tutor.py:114`；`backend/app/ai/prompts/tutor.py`；`backend/app/ai/prompts/mathdial.py`（282 行） | `system = TUTOR_PROMPT + state_suffix + mode_suffix`；铁律"不直接给答案、一次只说一件事" | **P1：增强 prompts/tutor.py 与 mathdial.py 文案层**（autonomy 措辞、激励单角度规则、情绪先接纳、pedagogical_contract 四段模板） |
| 状态机 | `backend/app/ai/dialogue_state_machine.py`（327 行）；`backend/app/ai/state_machine.py`（253 行） | 引导阶段状态流转 | P3：新增"错因锁定"状态（3 次干净交互解锁）——**加法，不改既有状态枚举值** |
| 掌握度 | `backend/app/services/mastery_engine.py`（655 行） | 纯函数纪律（不 import ORM/Session/FastAPI/方舟）：`compute_mastery`（正确率×时效衰减×贝叶斯平滑）、`classify_error`（五类错因）、`rank_recommendations`；**当前无 BKT** | P2：BKT 更新 + 0.30/0.70/0.85 阈值路由作为**可选信号层**，经 `compute_mastery` 统一出口，feature flag 默认 off |
| 复习调度 | `backend/app/services/fsrs_scheduler.py`（738 行） | py-fsrs 唯一内核（T04 已统一，`SCHEDULER=fsrs_py` 灰度） | **禁止触碰**；tutor-mcp fsrs.go 仅作算法交叉验证参考 |
| 纯函数纪律 | `backend/app/engines/`（config/difficulty/priority/planner/generation/types） | 不碰 ORM/LLM | BKT 阈值函数若放 engines，必须维持同纪律（单测可覆盖） |

**「不双内核、不绕过封装」约束的落实方式**：

1. **不部署 Go sidecar**。理由：Alpha 稳定性风险；Go+SQLite/Postgres 与 PG/SQLite 双方言后端形成**双存储双写**；60 人内测属过度集成。若未来确需上线，只能以独立进程 MCP over stdio + **只读快照**方式接入，严禁双写。
2. **FSRS 唯一内核 py-fsrs**：不碰 `fsrs_scheduler.py` 调度逻辑；tutor-mcp 的 Go 实现仅作对照。
3. **BKT/阈值只作可选信号**：新增代码走 `mastery_engine` 纯函数层统一出口，`orchestrator` 不得直连；开关默认 off，与 `SCHEDULER_FSRS_ROLLOUT_RATIO` 灰度机制同构（按学生稳定哈希放量）。
4. **只借文案不借契约**：prompt 借鉴仅限文案层；`question/diagnose` agent 的 JSON 出餐契约不动，`chat.py` 的 API 响应结构不动。

## ③ 改造工作量分解（3-5 人日）+ PoC 范围

| 项 | 内容 | 人日 |
|---|---|---|
| P1 | prompts/tutor.py + mathdial.py 增强（autonomy/激励单角度/情绪接纳/pedagogical_contract 四段模板）+ 引导对话回归测试 | 1.5 |
| P2 | BKT 更新 + 0.30/0.70/0.85 阈值路由落 mastery_engine（纯函数 + 单测 + flag 默认 off） | 1.5 |
| P3 | dialogue_state_machine 错因锁定状态接线（3 次干净交互解锁） | 0.5-1 |
| P4 | PoC 最小验证 | 1 |
| **合计** | 全量 4.5-5；**可裁版 3 人日（只做 P1+P2）** | |

**PoC 范围（最小可验证路径，不引 Go 二进制）**：在 Python 侧复刻三件事——
1. `pedagogical_contract` 最小结构（`llm_instruction` / `constraints` / `learner_explanation`）作为 `tutor.next_question` 出参附加字段；
2. 阈值路由纯函数（如 `bkt_route_level(p) -> 弱/中/稳` 三档，对齐 0.30/0.70/0.85）；
3. 错因锁定状态迁移。
**验证标准**：`backend/tests/` 现有引导对话测试全绿（行为不回退）＋ 新增单测覆盖阈值路由与锁定迁移 ＋ 一条手工对话脚本走通「弱掌握 → 分步引导 → 错因锁定 → 解锁」闭环。

## ④ 引入决策建议 + 退路

**决策：引入（借鉴式）——借 prompt 设计 + BKT 阈值/错因锁定思路；不部署 sidecar、不引 Go 二进制、不碰 FSRS 内核。**

- **ROI 依据**：P1 直接强化产品核心卖点"苏格拉底分步引导"的话术质量（1.5 人日，零部署风险）；P2 给掌握度模块补一个内测期可观测的掌握度信号（纯函数、可卸）；P3 与现有错因体系（`app.core.error_categories` 五类错因）天然衔接。
- **退路**：P2/P3 整体可卸（flag 默认 off，摘除后 `compute_mastery` 行为与现在完全一致）；若内测数据量不足以观测 BKT 信号（60 人、每题交互次数少），仅保留 P1 成果，算法部分整体回退，沉没成本 ≤ 1.5 人日。
- **不引入的场景**：若评审认定内测期引导话术已够用，则本计划整体降级为"参考文档"，仅将 `tools/prompt.go` 摘录进 `docs/guide-prompt-optimization.md` 作后续路线。

**最大约束**：tutor-mcp 是 **Alpha 阶段 Go 项目**——其价值 100% 在"设计思想"而非"可运行代码"；任何"跑起来直接用"的冲动都会把双存储、双内核、Alpha API 漂移三类风险一次性引进后端，因此本计划从 scope 上就锁死"只借鉴、不部署"。

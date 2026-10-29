# C02 设计短文档 · 三层可检视学生模型（对接六维掌握度）

> 状态：草案（待评审）
> 来源：技术侦察 `HKUDS/DeepTutor`（Apache-2.0）的「三层可检视记忆 L1/L2/L3 + Memory Graph」思路借鉴，**仅借鉴架构思想，不引入其代码**（见 §8 许可证边界）
> 对接：C02 六维掌握度（当前状态：依赖内测数据回流，未开工）
> 原则：模型 = 唯一真相源；引擎 = 纯函数；阈值经 `adaptive_config` 注入；tutor prompt 为受保护资产

---

## 1. 动机与定位

DeepTutor 把"个性化"做成一个**文件化、可检视、可编辑**的三层记忆系统（L1 原始事件 / L2 精选事实 / L3 跨面综合），并用 Memory Graph 把 L3→L2→L1 的证据链显式连起来。

我们的 `错题教练` 当前缺的不是"数据"，而是**缺一个把已有数据沉淀成"可检视学生模型"的中间层**：

- L1 已有（`student_learning_events`），但写完就躺平，没人二次消费；
- L2 已有表结构（`student_knowledge_mastery`），但 5 个分维列（concept/procedure/independent/transfer/retention）**全为 NULL**，只有一个整体 `mastery` 被 `update_mastery_from_event` 填充；
- L3 完全缺失——错题模式、薄弱链路、学习趋势没有结构化沉淀，家长/学情报告每次现算且不可追溯。

本设计把 C02「六维掌握度」落实为 **L2 六维激活 + L3 综合层新建**，复用现有 `mastery_engine`（纯函数）、`adaptive_config`（阈值注入）、`cron_jobs.daily_sync`（每日重算钩子），不引入重型组件。

---

## 2. 现状盘点（基于 `_wt/integrate` develop）

| 层 | 现状 | 文件 / 表 | 备注 |
|---|---|---|---|
| L1 | ✅ 已落地 | `models/dna.py::StudentLearningEvent`；`services/student_dna_service.record_learning_event` | 8 类事件枚举硬校验；`meta` 自由 JSON 已承载 `hint_count`/`hint_used`/`transfer`/`independent` 标记 |
| L2-整体 | ✅ 已计算 | `student_knowledge_mastery.mastery` / `confidence` | `update_mastery_from_event` + `mastery_engine.compute_mastery` 双口径已存在 |
| L2-五维 | ⚠️ 列已建、值全 NULL | `concept_mastery`/`procedure_mastery`/`independent_mastery`/`transfer_mastery`/`retention_mastery` | 注释标记 "Sprint 3 启用" = 即本 C02 |
| L3 | ❌ 缺失 | — | 无综合层、无溯源边 |
| 引擎层 | ✅ 纯函数 | `engines/mastery_engine.py`（compute_mastery / classify_error / aggregate_error_patterns / detect_prerequisite_gaps） | 不 import ORM/Session；阈值从 `engines/config.py` 注入 |
| 配置层 | ✅ 现成 | `engines/config.py::CONFIG_SPECS`（`adaptive.*` 前缀） | 运营可在后台改阈值 |
| 调度钩子 | ✅ 现成 | `tasks/cron_jobs.py::daily_sync` → `profile_service._compute_and_store` | 每日每生重算掌握度，天然 L3 触发点 |

**结论**：地基 80% 已就绪，C02 的工程量集中在"L2 五维填充规则" + "L3 综合层"两块。

---

## 3. 三层模型定义

### L1 · 原始行为事件层（不变，明确为源）
- 表：`student_learning_events`（已存在）
- 角色：**唯一事实源**。所有 L2/L3 推导必须能回溯到这里的事件 ID。
- 本设计不改其结构，只要求埋点 `meta` 持续携带派生 L2 所需的标记（`independent` / `transfer` / `hint_count` / `hint_used` / `error.type`）。

### L2 · 知识点掌握层（六维 = 整体 + 五分维）
- 表：`student_knowledge_mastery`（已存在，激活 5 列）
- **六维口径（本设计权威定义）**：

| 维度 | 字段 | 0~1 含义 | 主要输入信号 |
|---|---|---|---|
| D0 整体掌握 | `mastery`（已有） | 综合掌握度 | `compute_mastery` 既有贝叶斯平滑 |
| D1 概念理解 | `concept_mastery` | 概念性正确率 | 错因**非**"概念混淆"类的正确率；概念题正确率 |
| D2 程序执行 | `procedure_mastery` | 步骤/流程正确率 | 流程题正确率；错因**非**"计算/步骤"类的占比 |
| D3 独立完成 | `independent_mastery` | 无提示独立做对占比 | `meta.independent==True` 且正确的事件 / 该类总事件 |
| D4 迁移应用 | `transfer_mastery` | 跨情境正确率 | `meta.transfer==True` 事件的正确率 |
| D5 保持留存 | `retention_mastery` | 复习后召回保持 | `review_logs` 复习召回率 / 半衰期衰减后 `mastery` 保持比 |

- 唯一约束 `(student_id, knowledge_point_id)` 不变；每维独立 0~1，**不做"六维平均"覆盖 `mastery`**（D0 仍是综合口径，六维是 D0 的分解视图，互不替代）。
- 样本不足降级：某维有效样本 < `adaptive.mastery.dim.min_samples` 时该维置 NULL（与现有 `grade_level` 的"样本不足降级"同语义），前端按"暂无数据"展示，不编造 0。

### L3 · 综合洞察层（新建）
- 新表：`student_mastery_synthesis`（见 §6）
- 角色：**跨知识点 / 跨时间的综合结论**，可检视、可编辑、可溯源。
- 三种综合类型（枚举 `synthesis_type`）：

| 类型 | 含义 | 主要输入 | 输出 payload 示例 |
|---|---|---|---|
| `ERROR_PATTERN` | 该生错因模式聚类 | L1 事件 meta.error.type + `error_categories` 五类 | 主导错因、Top3 薄弱知识点、样例事件 |
| `WEAK_CHAIN` | 薄弱链路（前置缺口） | L2 六维 + `knowledge_prerequisite` 边 | 应先补的前置知识点链、缺口强度 |
| `TREND` | 学习趋势 | L1/L2 随时间窗口 | 近 7/30 天掌握度斜率、活跃度、稳定性 |

- **溯源边（Memory Graph 借鉴点）**：每行 L3 必须记录 `ref_kp_ids`（它综合了哪些 L2 行）+ `ref_event_ids`（它消费了哪些 L1 事件）。这把 DeepTutor 的"L3→L2→L1 证据链"落为显式外键式字段，使综合结论可审计、可回滚。

---

## 4. 六维评分引擎（纯函数，新增 `engines/mastery_sixdim.py`）

- 不 import ORM / Session / FastAPI；吃 dataclass 吐 dataclass，可被 `tests/engines/test_mastery_sixdim.py` 纯单测覆盖。
- 输入：`Sequence[AttemptEvent]`（复用 `mastery_engine.AttemptEvent`）+ 该生 `review_logs` 抽取出的 `retention_events`；时间统一 UTC。
- 阈值全部经 `adaptive_config` 注入，新增键（示例）：

```
adaptive.mastery.dim.min_samples        = 3     # 单维最小有效样本，低于则 NULL
adaptive.mastery.dim.concept_weight     = 0.6   # 概念题在 D1 的权重
adaptive.mastery.dim.independent_bonus  = 0.12  # 独立做对相对提示做对的增量
adaptive.mastery.dim.retention_halflife = 14    # D5 半衰期（天）
adaptive.mastery.dim.retention_floor    = 0.4   # 复习召回低于此视为留存弱
```

- 输出：`SixDimMastery`（frozen dataclass，含 6 个 0~1 值与 `samples` 计数，供降级判定）。
- 与现有 `mastery_engine` 的关系：`compute_mastery` 继续算 D0；`mastery_sixdim` 在其之上拆 D1–D5，二者共用 `AttemptEvent` / `as_utc` / `grade_level`，**不重复造轮子**。
- 错因分类沿用 `app.core.error_categories.ERROR_CATEGORIES`（五类唯一真相源），不新造枚举。

---

## 5. L3 综合器（纯函数，新增 `engines/mastery_synthesis.py`）

- 输入：L1 事件聚合 + L2 六维结果（`Mapping[kp_id, SixDimMastery]`）+ 前置依赖边。
- 输出：`List[SynthesisResult]`（frozen dataclass：type / ref_kp_ids / ref_event_ids / payload / confidence）。
- `ERROR_PATTERN`：复用 `mastery_engine.aggregate_error_patterns` 聚合错因，挑 Top3 薄弱 KP（用 `compute_priority` 或 `detect_prerequisite_gaps` 的优先级口径）。
- `WEAK_CHAIN`：直接复用 `mastery_engine.detect_prerequisite_gaps`（已实现的 GKT 前置缺口检测），把结果规整为 synthesis 行。
- `TREND`：对 L2 随时间窗口做线性斜率（天数差由 `now` 注入，不读系统时钟，与 `engines` 既有约定一致）。
- **不碰 ORM**：引擎只算；写库由服务层 `consolidate_mastery` 负责（读 config → 查 ORM → 调引擎 → 回写）。

---

## 6. 数据模型变更

### 6.1 L2（无需新列）
`student_knowledge_mastery` 的 5 个分维列已存在。**只需激活填充逻辑**，无需迁移加列。

### 6.2 L3（新增表 + 迁移）
```sql
-- migrations/02x_student_mastery_synthesis.sql (SQLite) + PG 双写
CREATE TABLE student_mastery_synthesis (
    id                  UUID PRIMARY KEY,
    student_id          UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    synthesis_type      TEXT NOT NULL
                        CHECK (synthesis_type IN ('ERROR_PATTERN','WEAK_CHAIN','TREND')),
    ref_kp_ids          TEXT,                       -- JSON 数组：溯源 L2 行
    ref_event_ids       TEXT,                       -- JSON 数组：溯源 L1 事件
    payload             TEXT,                       -- JSON：综合结论
    confidence          REAL NOT NULL DEFAULT 0.0,  -- 0~1，随样本量增长
    created_at          TIMESTAMP,
    updated_at          TIMESTAMP,
    UNIQUE (student_id, synthesis_type)             -- 每类每生一行，upsert
);
CREATE INDEX idx_sms_student ON student_mastery_synthesis(student_id);
```
- **冷母库同步**：新表必须同步进 `_wt/_master_dev.db`（铁律 #4），且进 `mastery_synthesis` 的建表须走 `backend/scripts/schema_reconcile.py` 的对账（无 ORM 模型，raw SQL 维护，参照 `review_cards`/`review_logs` 既有范式）。

---

## 7. 接入点与调度

- **写入触发**：在 `tasks/cron_jobs.daily_sync` 的每生循环里，于 `_compute_and_store` 之后调用新增的 `consolidate_mastery(db, student_id)`（独立 try/except，失败仅 warning 不阻断主流程，沿用现有容错风格）。
- **读取出口**：`student_dna_service.get_learning_dna` 增加 `synthesis` 字段，把 L3 三类型一并返回；学情诊断报告（第二阶段 PRD）与家长端直接消费，避免重复现算。
- **实时性边界**：L3 走每日批量（与现有掌握度重算同频），不进请求热路径；L1→L2 仍为事件驱动实时（保持现有 `record_learning_event` 联动）。

---

## 8. 与既有工程铁律对齐

1. **engines = 纯函数**：六维评分与 L3 综合均在 `engines/` 下，不 import ORM/Session；阈值经 `adaptive_config` 注入（铁律 #5）。
2. **配置键唯一真相源**：新增阈值只在 `engines/config.py::CONFIG_SPECS` 登记，服务层只读不写（铁律 #5）。
3. **tutor prompt 受保护**：L2/L3 仅作为**讲题上下文输入**，不改 prompt 本体；prompt 升级仍走 VERSION + 评审（铁律 #21）。
4. **schema guard**：L3 新表进冷母库 + `schema_reconcile` 对账 + `test_schema_guard` 守卫（铁律 #3、#11）。
5. **守卫需变异测试自证**：L2 五维"样本不足降级为 NULL"、L3"溯源边缺失即拒写"这类守卫必须做变异测试证明能抓回归（铁律 #7）。
6. **N1 对称互比 + golden**：六维评分关键路径配手算 golden 真值（尤其 D3/D4 依赖 meta 标记的场景），防对称互比漏掉公共分支被改坏（铁律 #6）。
7. **许可证边界（DeepTutor 借鉴）**：Apache-2.0 商用友好；本项目为私有 SaaS 部署**不对外分发源码**，不触发 NOTICE/修改声明义务；**本设计零代码复用**（仅架构思想），无任何许可证负担。若未来抽用其某小模块，需保留 NOTICE + 许可证头。

---

## 9. 落地分阶段（建议）

| Phase | 内容 | 产物 | 依赖 |
|---|---|---|---|
| 0 | 确认六维口径与 `synthesis_type` 枚举；补 `CONFIG_SPECS` 阈值键 | 设计冻结 | 本评审 |
| 1 | `engines/mastery_sixdim.py` 六维评分纯函数 + `tests/engines/test_mastery_sixdim.py`（含 golden） | 引擎 + 单测 | Phase 0 |
| 2 | `engines/mastery_synthesis.py` + 迁移 `02x` + 冷母库同步 + `schema_reconcile` | L3 表 + 对账 | Phase 1 |
| 3 | `consolidate_mastery` 服务层 + `daily_sync` 钩子 + `get_learning_dna` 出口 | 端到端打通 | Phase 2 |
| 4 | 前端六维展示（雷达/条形）+ 学情报告接入 + 评测 harness（C04） | 可观测 | 内测数据回流 |

**关键前置**：C02 强依赖内测数据回流（当前 60 人内测进行中）。Phase 1–3 可在无真实数据时用合成事件单测先行；Phase 4 的"真实口径校准"需等内测样本。

---

## 10. 风险与不做项

- **不做**：引入 DeepTutor 的 GraphRAG/LightRAG/Obsidian/Kiwix 等重型检索；引入其 IM/图书/视频模块——与 K-12 数学讲题闭环无关，且体量 305MB+ 维护债过高。
- **不做**：用 LLM 现算 L3（成本高、不可复现）——L3 走纯规则聚合（错因聚类 + 前置缺口 + 趋势斜率），保证可检视、可回滚。
- **风险**：meta 标记（`independent`/`transfer`）依赖埋点质量；若内测埋点缺字段，D3/D4 会普遍 NULL。应对：Phase 0 先审计 `practice_service.submit_answer` 的埋点完整性（参照 `record_learning_event` 的 `mastery_meta` 双语义约定）。
- **风险**：L2 五维与现有 `mastery` 口径打架。应对：六维是 D0 的分解视图，不反向覆盖 D0；展示层明确"整体 vs 分维"。

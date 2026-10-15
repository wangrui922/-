# 教学决策引擎层设计约定（V6.5 Sprint 6）

> 适用范围：`backend/app/engines/**`。
> 首次落地任务：**T05**（骨架 + 难度引擎）；接续任务：**T06**（优先级 + 出题）、**T07**（学习计划）。
> 本文是这四个引擎的**唯一接线契约**，新增引擎前先读完。

---

## 1. 为什么要有 engines 层

V6.5 之前，项目里已经有三块「会算东西」的代码：

| 已有模块 | 职责 | 局限 |
| --- | --- | --- |
| `services/mastery_engine.py` | 掌握度 / 错因 / 推荐排序 | 只算**学生现在怎么样** |
| `services/student_dna_service.py` | DNA 画像、事件、掌握表读写 | 只管**存与聚数据** |
| `services/adaptive_service.py` | 自适应难度窗口调档（`(1,3,5)` 三档） | 只有**三档粗调**，不看掌握度、不输出区间 |

缺的是一层：**「知道这些之后，下一步该怎么教」**。所以 engines 层的定位是——

> **输入学生 DNA + 当前状态 + 运营阈值 → 输出教学决策**（教什么、出多难、出几道、怎么排期），
> 且这一层必须能在**没有数据库**的环境里跑测试。

---

## 2. 铁律：引擎是纯函数

以下四条一样都不能破，破了就失去可测性，也会让后续三个引擎变成「各写一个版本」的泥潭。

1. **禁止 ORM / Session**：`engines/types.py`、`engines/difficulty.py` 以及后续引擎模块，
   顶层不得 import `sqlalchemy`、`app.models.*`、`os`（含读 env）。
   由 `tests/engines/test_difficulty.py::test_engine_modules_have_no_orm_imports`
   用 AST 检查顶层 import，坏了 CI 会拦下来。
2. **禁止读配置 / 环境**：所有阈值由调用方以 `AdaptiveConfig` **注入**。
3. **同输入必同输出**：不得出现随机数、`datetime.now()`、进程级缓存、全局可变状态。
4. **异常兜底不抛**：脏入参在入参数据类的 `__post_init__` 里归一化
   （脏数字回落缺省值 / 夹紧区间），引擎本身不负责「参数校验」那是 API 层的活；
   引擎只保证**不会把出题主流程打挂**。

**唯一的例外（脏边界）**：`engines/config.py::read_adaptive_config(db)`。
它是全层唯一碰 DB 的函数，ORM 在**函数体内延迟 import**，所以
`import app.engines.config` 本身不会拉起 sqlalchemy。调用规则：

```python
config = read_adaptive_config(db)          # 服务层：一个请求只读一次
...多次调用纯引擎...                        # 之后都是纯计算
```

---

## 3. 目录与职责边界

```
backend/app/engines/
├─ __init__.py      四个引擎入口的统一导出（唯一对外门面）
├─ types.py         共享数据类 + 类型安全工具（clamp/to_float/snap_levels...）
├─ config.py        AdaptiveConfig 缺省值 + 解码器 + read_adaptive_config（脏边界）
├─ difficulty.py    【T05 已实现】难度引擎
├─ priority.py      【T06 已实现】优先级引擎
├─ generation.py    【T06 已实现】出题配额引擎
└─ planner.py       【T07 占位】学习计划引擎
```

| 引擎入口 | 输入 | 输出 | 职责 | 状态 |
| --- | --- | --- | --- | --- |
| `decide_difficulty` | `DifficultyInput` + `AdaptiveConfig` | `DifficultyBand` | 下一批题**出多难**（区间 `[lo,hi]`） | ✅ T05 |
| `rank_priorities` | `mastery_items` + `gaps` + `graph` + `results` + `AdaptiveConfig` + `now` + `top_n` | `List[PriorityItem]` | 先补**哪个知识点** | ✅ T06 |
| `plan_exercises` | `List[PriorityItem]` + `List[DifficultyBand]` + `total` (+ 可选 `plan_units`) | `List[ExercisePlan]` | 出**几道**、各档位怎么配 | ✅ T06 |
| `build_learning_plan` | `List[PriorityItem]` + `List[ExercisePlan]` + `days` | `List[PlanItem]` | 多天日程怎么排 | ⏳ T07 |

边界纪律：

* **引擎不做 IO**：不做 HTTP 调用（含方舟）、不写库、不发 MQ；
* **引擎不捞题**：真正去题库取题仍是 `services/practice_service.py` /
  `services/ai_exercise_service.py` 的活，引擎只给**配额与难度带**；
* **占位引擎调用即抛 `NotImplementedError`**（loud fail）。宁可报错，也不要
  返回一条谁也看不懂的默认排序——这一条由单测守着；
* **命名**：内部 / DB 用 `snake_case`，API 出口用 `camelCase`（各数据类的 `to_api()`）；
  变量名英文、注释中文，沿用项目既有风格。

---

## 4. adaptive_config：载体与用法

**载体：复用既有的 `system_config` 表**（`app.models.system_config.SystemConfig`），
不新建表、不加迁移（既省掉 PG/SQLite 双份 SQL，也让运营能在后台改阈值）。

**Key 规范**：一律 `adaptive.` 打头，段式命名，例如

```
adaptive.enabled                            # 总开关
adaptive.difficulty.enabled                 # 难度引擎开关
adaptive.difficulty.band_width              # 难度带半宽
adaptive.difficulty.confidence_width_gain   # 置信度补宽系数
adaptive.difficulty.inertia                 # 向当前难度回归的惯性
adaptive.difficulty.cold_start_samples      # 冷启动样本阈值
adaptive.difficulty.cold_start_width        # 冷启动探索带宽
adaptive.difficulty.error_rate_penalty      # 错误率惩罚系数
adaptive.difficulty.accuracy_target         # 期望近期正确率
adaptive.difficulty.accuracy_gain           # 正确率偏离增益
adaptive.difficulty.hint_high_offset        # 提示依赖 high 的偏置
adaptive.difficulty.hint_low_offset         # 提示依赖 low 的偏置
adaptive.difficulty.reason_shift            # 原因码位移阈值
adaptive.difficulty.min / .max / .default   # 难度坐标边界与默认值
adaptive.priority.weights                  # 四项权重（dict 或 JSON 字符串）
adaptive.priority.gap_divisor              # 前置缺口条数归一化除数
adaptive.priority.out_degree_divisor       # 图谱出度归一化除数
adaptive.priority.recent_window            # 近期练习窗口（天）
adaptive.priority.stale_after              # 陈旧阈值（天）
adaptive.priority.top_n                    # 缺省返回条数
adaptive.generation.default_total          # total 缺省时的批次题数
adaptive.generation.max_total              # 单批题数上限
adaptive.generation.max_knowledge_points   # 单批覆盖知识点上限
adaptive.generation.new_ratio              # 新题占比（决定策略标记）
```

**新增阈值的三步**（不要偷懒跳过任何一步）：

1. 在 `engines/config.py` 的 `AdaptiveConfig` 加字段（并在 `__post_init__` 里夹紧合法区间）；
2. 在 `CONFIG_SPECS` 追加一行 `ConfigSpec(key, attr, kind, default)`
   ——这里是**唯一真相源**，`CONFIG_KEYS` / `adaptive_config_to_rows()` / 读取都靠它；
3. 补一条「配置确实生效」的单测（照抄 `test_band_width_config_applies` 的写法）。

**优先级 discipline**：各引擎只读写**自己前缀**的字段（`difficulty_*` / `priority_*` /
`generation_*` / `plan_*`），不许跨前缀取值。

**降级**：`read_adaptive_config` 读库失败（表不存在、会话异常）一律记 warning 并回退缺省配置，
绝不上抛——配置读失败不能拖垮出题主流程（与既有 `_emit_dna_event` 旁路兜底同一思路）。

---

## 5. 难度引擎算法（T05）

### 5.1 输入输出

* 入参 `DifficultyInput`：`mastery / confidence / practice_count / error_rate /
  hint_dependency / current_difficulty / recent_accuracy / student_id / knowledge_point_id`
  （构造期已归一化到合法区间）；
* 出参 `DifficultyBand`：`lo / hi / target / levels / reason / cold_start / confidence`；
  `lo,hi,target` 是**连续难度坐标 1~5**，`levels` 是落在该区间内的离散档位子集
  （`(1,3,5)`，与 `questions.difficulty` 同口径），恒非空。

### 5.2 规则（R0~R5）

| 编号 | 规则 | 配置 |
| --- | --- | --- |
| R0 | 任一层开关关闭 → 难度带锁定为当前难度 `[cur, cur]`，原因码 `disabled` | `adaptive.enabled`、`difficulty.enabled` |
| R1 | **冷启动**（`practice_count < cold_start_samples`）：以当前难度为中心、取较宽的探索带 | `cold_start_samples`、`cold_start_width` |
| R2 | 正常路径：`anchor = min + mastery × (max-min)`；`center = anchor×(1-inertia) + current×inertia`；再叠加错误率惩罚、近期正确率偏离、提示依赖偏置 | `inertia`、`error_rate_penalty`、`accuracy_target`、`accuracy_gain`、`hint_*_offset` |
| R3 | **单侧钳制**：掌握度撑得起当前难度时不下调（惩罚再多也不低于 `current`），撑不住时不上调 | 无（结构性规则） |
| R4 | 带宽：`半宽 = band_width + (1-confidence) × confidence_width_gain`（数据越少越探索） | `band_width`、`confidence_width_gain` |
| R5 | 落纸：夹紧 `[min,max]`、两位小数、映射回离散档位；完全无交集时就近取一档 | `min`、`max` |

原因码：`cold_start` / `disabled` / `steady` / `push_up` / `pull_down`
（后三者按 `|target - current|` 与 `reason_shift` 比较判定）。

### 5.3 算例（缺省配置，当前难度 3）

| 场景 | mastery | 推导 | 结果 |
| --- | --- | --- | --- |
| 学霸 | 1.0 | anchor=5 → center=5×0.7+3×0.3=4.4 → 半宽 0.5 | `[3.9,4.9]`，levels `(5,)`，push_up |
| 学渣 | 0.0 + 错误率 1.0 | anchor=1 → center=1.6-0.8=0.8 → 夹到下界 | `[1.0,1.5]`，levels `(1,)`，pull_down |
| 中等 | 0.5 + 近期正确率 0.75 | anchor=3 → center=3.0 → 半宽 0.5 | `[2.5,3.5]`，levels `(3,)`，steady |
| 冷启动 | 任意 | 以 current=3 为中心、半宽 1.0 | `[2.0,4.0]`，levels `(3,)`，cold_start |
| 端到端样例 | kp-strong（0.86 / 错误率 0.08 / 提示依赖 high / 正确率 0.75） | anchor=4.44 → center=4.008-0.064-0.4=3.544 → 半宽 0.5+0.06×0.5=0.53 | `[3.01,4.07]`，target 3.54，levels `(3,)` |

### 5.4 怎么接进服务层（T06 照抄）

```python
from app.engines import read_adaptive_config
from app.engines.difficulty import decide_difficulty, difficulty_input_from_dna
from app.services.adaptive_service import get_current_difficulty
from app.services.student_dna_service import get_learning_dna

config = read_adaptive_config(db)                                  # 脏边界，只读一次
dna = get_learning_dna(db, student_id=student_id)
current = get_current_difficulty(db, student_id=student_id)
payload = difficulty_input_from_dna(dna, knowledge_point_id=kp_id, current_difficulty=current)
data = decide_difficulty(payload, config).to_api()                 # camelCase dict
return {"code": 0, "msg": "ok", "data": data}                      # 统一响应
```

`difficulty_input_from_dna` 是**纯函数**（只读写 dict），不指定 `knowledge_point_id`
时自动锁定**最薄弱**知识点（掌握度升序 → 置信度升序 → 练习次数升序）。

---

## 6. 优先级引擎算法（T06）

### 6.1 输入输出

```python
rank_priorities(
    mastery_items,                 # 掌握度条目（dict / 对象均可）
    gaps=None,                     # detect_prerequisite_gaps() 的结果
    graph=None,                    # {kp_id: 出度} 或前置边序列
    results=None,                  # T07 从 attempts 聚合的结果（本次仅留出入口）
    *,
    adaptive_config=None,          # AdaptiveConfig（priority_* 阈值）
    now=None,                      # 时间基准，用于「陈旧 / 近期」判定
    top_n=None,                    # None 时取 priority.top_n（缺省 5）
) -> List[PriorityItem]
```

* `mastery_items` 每项兼容多套键名：`knowledge_point_id`/`kp_id`/`id`、
  `mastery`/`mastery_score`/`score`、`confidence`/`conf`、`kp_name`/`name`、
  `last_practiced_at`/`last_practice_at`/`last_reviewed_at`；
* `graph` 支持映射（`{kp_id: 出度}`）与边序列（按 `pre_kp_id` 出现次数计出度）；
* `results` 本次只留出入口：`None` 时忽略；提供时其中的 `last_practiced_at`
  会覆盖条目上的时间戳参与陈旧/近期判定；
* 出参是按 `score` 降序的去重 `List[PriorityItem]`；
  `mastery_items` 为空（或 `None` / 脏容器）时返回 `[]`；
* 引擎**不读系统时钟**：`now=None` 时直接跳过时间维度。

### 6.2 缺省常量

```text
PRIORITY_WEIGHTS_DEFAULT = {"mastery":0.40,"confidence":0.25,"gap":0.20,"degree":0.15}
PRIORITY_GAP_DIVISOR=3.0  PRIORITY_OUTDEG_DIVISOR=5  PRIORITY_RECENT_WINDOW=7
PRIORITY_STALE_AFTER=14   PRIORITY_LOW_MASTERY=0.5   PRIORITY_LOW_CONFIDENCE=0.5
PRIORITY_TOP_N_DEFAULT=5
```

权重可由 `adaptive.priority.weights` 覆盖，**dict 与 JSON 字符串两种载体都要能处理**，
缺 key / 解析失败 / 类型不对一律回退缺省；除数被配置成 0 或负数时回退内置缺省
（除零保护）。

### 6.3 排序规则与依据

| 编号 | 规则 | 教学依据 | 配置 |
| --- | --- | --- | --- |
| R1 `gap` | 被点名为前置缺口的次数按 `min(count, gap_divisor)/gap_divisor` 加权 | 地基没补会让学生在后置知识上反复受挫 | `priority.gap_divisor`、权重 `gap` |
| R2 `mastery` | `(1-mastery)` 加权 | 有限时间先消除真实能力短板 | 权重 `mastery` |
| R3 `confidence` | `(1-confidence)` 加权 | 置信度低说明结论不可靠，需先做题补样本 | 权重 `confidence` |
| R4 `degree` | `min(degree, out_degree_divisor)/out_degree_divisor` 加权 | 枢纽基础点能解锁更多后续内容 | `priority.out_degree_divisor`、权重 `degree` |
| R5 `stale` | `days > stale_after` 时加 `(days-stale_after)/stale_after` | 遗忘曲线：久不练的能力会回退 | `priority.stale_after` |
| R6 `recent` | `days <= recent_window` 时乘 `(days+1)/(window+1)` 降权 | 用间隔练习代替同一知识点机械连刷 | `priority.recent_window` |
| R7 | 同 ID 去重；同分按掌握度、等级、ID 稳定排序 | 避免重复配额并保证同输入同输出 | 无（结构性规则） |

```text
score = (1-mastery)      × w.mastery
      + (1-confidence)   × w.confidence
      + gap_ratio        × w.gap
      + degree_ratio     × w.degree
      + stale_boost                       # 仅 days > stale_after
      # recent 时整体乘 (days+1)/(window+1)
score = clamp(score, 0, 1) 并保留 4 位小数
```

`reason` 使用中文模板（前置基础 / 很久没练 / 掌握度偏低 / 近期错峰 / 掌握不稳），
前端可直接展示；`to_api()` 保持
`knowledgePointId/kpName/score/mastery/level/reason` 六个 camelCase 键不变。

---

## 7. 出题编排引擎算法（T06）

### 7.1 职责边界

```python
plan_exercises(
    priorities,                    # List[PriorityItem]
    bands,                         # 与 priorities 按位置对应的 DifficultyBand
    *,
    total=None,                    # None/非正/脏值 → plan_units 题量之和 → 配置默认
    config=None,
    plan_units=None,               # 可选：调用方已拆好的计划单元
) -> List[ExercisePlan]
```

只负责：

1. 对优先级知识点去重并选前 N 个；
2. 决定每个知识点出几题（题数总和严格等于批次配额）；
3. 传递各知识点的离散难度档；
4. 标记批次策略 `new_first` / `review_first` / `balanced`（空队列兜底为
   `diagnostic_fallback`）。

它**从不查询题库**：`services/practice_service.py` 里已有 `draw_questions()` /
`_draw_with_relaxation()`（1–3 档逐级放宽）/ `_recent_question_ids()`（7 天回避窗口）
三件套，引擎再做一遍就是重复造轮子。因此引擎**既不接收也不返回题目 ID**——
`ExercisePlan` 维持 T05 冻结的四字段，`to_api()` 键集合恒为
`knowledgePointId/questionCount/difficultyLevels/strategy`（不改名、不删除、不追加）。

`plan_units` 是**可选且仅关键字**：省略时题量由引擎按优先级自行摊派，
老调用方零感知。

### 7.2 配额、档位与兜底规则

| 编号 | 规则 | 配置 |
| --- | --- | --- |
| G1 | `total` 为 `None`/非正/脏值时：先取 `plan_units` 各单元 `question_count` 之和，仍为 0 再取默认题数；超大值夹到批次上限 | `generation.default_total`、`generation.max_total` |
| G2 | 同知识点仅保留最高优先项；最多覆盖 N 个，且每点至少 1 题 | `generation.max_knowledge_points` |
| G3 | 剩余题量按非负 `score` 用最大余数法分配；全零分等比分配 | 无（结构性规则） |
| G4 | `bands` 短缺/脏值时用 `DifficultyBand()` 全档探索带 | 无（结构性规则） |
| G5 | `levels` 取不到（缺失/越界/脏值）时回退 `(3,)`，且档位去重后恒非空 | 无（结构性规则） |
| G6 | 优先级队列为空时返回 `__mixed_review__` 综合摸底计划 | `generation.default_total` |
| G7 | `new_count=round(total×new_ratio)`、`review_count=total-new_count`；新题多 → `new_first`，复习多 → `review_first`，相等 → `balanced` | `generation.new_ratio`（缺省 `PLAN_NEW_RATIO_DEFAULT=0.6`） |

G6 保证 `List[ExercisePlan]` 永不为空；每项 `question_count>=1`、
`difficulty_levels` 非空。「题数总和 = 批次配额」是硬不变式：即使 `plan_units`
给出的题量与批次配额不一致，引擎也会回退到自算的摊派值补齐。所有步骤无随机数，
同输入同输出。

### 7.3 服务层接缝（T07 落地，引擎层不再新增入口）

引擎层四个入口就此定型；「计划 → 具体题目」只在服务层补一个薄包装，禁止新增
`engines/next_question.py`：

```python
def draw_questions_by_plan(
    db: Session,
    *,
    student_id: Any,
    plans: Sequence[ExercisePlan],   # plan_exercises 出参，原样透传
    grade: Optional[int] = None,
    recent_days: int = 7,
) -> Dict[str, Any]:
    """逐 ExercisePlan 调用现有 _draw_with_relaxation 完成抽题。

    - exclude_ids = _recent_question_ids(db, student_id, days=recent_days)
    - difficulty_levels 逐档传给 _draw_with_relaxation 的 common['difficulty']
    - plan.strategy 原样写回出参；diagnostic_fallback 时 kp_id 置 None
    - 复用既有的「少给不凑数」与 relaxed_to_level / relaxation_steps 语义
    """
```

### 7.4 `plan_units` 单元字段约定（T07 组装数据时照此来）

`plan_units` 与 `priorities` **按位置一一对应**（第 i 个单元描述第 i 个优先级知识点）：

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `question_count` | 是 | `int` | 该知识点计划出几道。缺失 / 为 0 / 脏值 → 该单元回退引擎摊派值 |
| `levels` | 否 | `Sequence[int]` | 该单元的难度档（子集只能是 `1/3/5`）。缺失 / 越界 / 脏值 → 回退对应 `DifficultyBand` 的档位，仍取不到则回退 `(3,)` |

同义键名也认：`count` / `total` 等价于 `question_count`；
`difficulty_levels` / `difficulty` 等价于 `levels`。单元可以是 dict 也可以是带同名
属性的对象；`plan_units` 为空时等价「不传」，题量由引擎按优先级自行摊派。

---

## 8. 测试约定

* 位置：`backend/tests/engines/test_*.py`（纯单测，不依赖 DB fixture）；
* 每个引擎**至少 20 条**用例，必须覆盖：边界钳制、冷启动、极端掌握度、
  配置生效、脏入参兜底、幂等性；
* T06 额外约定：出参 camelCase 键集合要有专项断言；每个引擎模块都要有一条
  **AST 守卫**（顶层 import 不得出现 `sqlalchemy` / `app.models` / `os`）；
* 本机离线环境无 pytest 时，可直接 `python tests/engines/test_difficulty.py`
  ——测试文件末尾自带 runner，逐条执行本模块全部 `test_*` 函数并汇总
  （有 pytest 时它就是普通 pytest 模块，两种跑法都成立）。

---

## 9. 如何新增一个引擎

1. 在 `types.py` 加出参数据类（`frozen=True` + `to_api()` + `__post_init__` 兜底）；
2. 在 `config.py` 加**自己前缀**的阈值（字段 + `CONFIG_SPECS` 一行，见第 4 节三步）；
3. 新建 `engines/<name>.py`：只放纯函数入口 + 必要的私有计算函数，
   在 docstring 里写清「输入 / 输出契约」；
4. 在 `__init__.py` 导出入口（同时在 `__all__` 登记）；
5. 补 `backend/tests/engines/test_<name>.py`，并在本文第 3 节表格里把状态从 ⏳ 改成 ✅。

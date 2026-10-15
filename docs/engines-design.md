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
├─ priority.py      【T06 占位】优先级引擎
├─ generation.py    【T06 占位】出题配额引擎
└─ planner.py       【T07 占位】学习计划引擎
```

| 引擎入口 | 输入 | 输出 | 职责 | 状态 |
| --- | --- | --- | --- | --- |
| `decide_difficulty` | `DifficultyInput` + `AdaptiveConfig` | `DifficultyBand` | 下一批题**出多难**（区间 `[lo,hi]`） | ✅ T05 |
| `rank_priorities` | DNA + `AdaptiveConfig` + `limit` | `List[PriorityItem]` | 先补**哪个知识点** | ⏳ T06 |
| `plan_exercises` | `List[PriorityItem]` + `List[DifficultyBand]` + `total` | `List[ExercisePlan]` | 出**几道**、各档位怎么配 | ⏳ T06 |
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

## 6. 测试约定

* 位置：`backend/tests/engines/test_*.py`（纯单测，不依赖 DB fixture）；
* 每个引擎**至少 20 条**用例，必须覆盖：边界钳制、冷启动、极端掌握度、
  配置生效、脏入参兜底、幂等性；
* 本机离线环境无 pytest 时，可直接 `python tests/engines/test_difficulty.py`
  ——测试文件末尾自带 runner，逐条执行本模块全部 `test_*` 函数并汇总
  （有 pytest 时它就是普通 pytest 模块，两种跑法都成立）。

---

## 7. 如何新增一个引擎

1. 在 `types.py` 加出参数据类（`frozen=True` + `to_api()` + `__post_init__` 兜底）；
2. 在 `config.py` 加**自己前缀**的阈值（字段 + `CONFIG_SPECS` 一行，见第 4 节三步）；
3. 新建 `engines/<name>.py`：只放纯函数入口 + 必要的私有计算函数，
   在 docstring 里写清「输入 / 输出契约」；
4. 在 `__init__.py` 导出入口（同时在 `__all__` 登记）；
5. 补 `backend/tests/engines/test_<name>.py`，并在本文第 3 节表格里把状态从 ⏳ 改成 ✅。

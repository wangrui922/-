# 复习调度统一到 py-fsrs（双版本裁决 · 迁移与灰度手册）

> 适用范围：`backend/app/services/fsrs_scheduler.py`、`backend/app/services/review_scheduler.py`、
> `backend/app/core/config.py`
> 目标读者：后端开发 / 运维 / 内测负责人
> 结论一句话：**统一入口收敛到 `fsrs_scheduler`，内核可在 py-fsrs 与 SM-2 之间一键切换，
> 默认 `sm2`，回滚只改配置不改代码。**

---

## 1. 为什么要裁决（历史遗留）

仓库里长期并存两套复习调度：

| 模块 | 算法 | 状态 |
|---|---|---|
| `backend/app/services/fsrs_scheduler.py` | 自研 FSRS 语义适配（内部复用 SM-2 核心） | 真实算法层，被 `review_service` 直接调用 |
| `backend/app/services/review_scheduler.py` | SM-2（SuperMemo-2） | 被 `review_service`、`practice` 链路调用 |

带来的问题：

1. **间隔口径不一**：同一道题走不同链路，下次复习时间不同，学生体验不一致；
2. **维护成本翻倍**：任何排程调整都要改两处，且容易漏改；
3. **不可验证**：自研 FSRS 语义适配层没有权重文件与校准数据，FSRS-4.5 的 19 个权重
   无从落地，属于「可解释但没有社区验证」的实现。

项目要做 60 人免费内测，**复习是留存核心**，因此需要：

* 用**经过社区验证**的算法（[py-fsrs](https://github.com/open-spaced-repetition/py-fsrs)，
  MIT 许可，可商用，FSRS-4.5/5 官方 Python 实现）；
* **可一键回退**：一旦内测数据异常，改配置即可回到旧行为。

## 2. 裁决结论

| 项 | 结论 |
|---|---|
| 统一入口 | `app/services/fsrs_scheduler.py`（唯一裁决点） |
| 新内核 | `py-fsrs`（`fsrs.Scheduler`），lazy import，未安装自动降级 |
| 旧内核 | SM-2（保留，作为默认与降级兜底） |
| `review_scheduler.py` | **降级为兼容壳**：保留全部常量/数据结构/函数签名，调度主逻辑转发到统一入口 |
| 默认配置 | `SCHEDULER=sm2`（灰度基线，零风险） |
| 回滚方式 | 改配置回 `sm2`，**无需改代码、无需改数据** |

### 2.1 对外契约不变

`FSRSScheduler` / `Card` / `ReviewLog` 的方法名、参数、返回结构**一律不变**：

* `FSRSScheduler.review_card(card, rating, now) -> (Card, ReviewLog)`
* `FSRSScheduler.get_retrievability(card, now) -> float`
* `Card` / `ReviewLog` 的字段与默认值不变

既有测试 `tests/test_fsrs_scheduler.py`、`tests/test_review_service_fsrs.py`、
`tests/test_review_scheduler.py` 不感知内核切换（默认走 SM-2，行为与切换前完全一致）。

`FSRSScheduler.__init__` 新增**可选**参数 `student_id=None`（仅用于灰度裁决），
`FSRSScheduler()` 的既有写法保持可用。

### 2.2 数据口径：保证回滚无损

`review_cards.difficulty` 列在**两种内核下都存 SM-2 难度系数 EF（1.3~3.0）**，
**不落 py-fsrs 的 1~10 difficulty**。

* py-fsrs 的 difficulty 由 EF 线性可逆映射得到：
  `_ef_to_pyfsrs_difficulty()` / `_pyfsrs_difficulty_to_ef()` 互为逆运算；
* `_sm2_restore_ef()` 保留，作为「从旧 SM-2 状态回填」的唯一入口，
  两个内核都用它把库里的 `difficulty` 还原成难度。

因此：学生从 `fsrs_py` 切回 `sm2`，已存学习状态**不丢、不乱**，
这也是「改配置即回滚」能成立的前提。

---

## 3. 配置项（backend/app/core/config.py）

| 配置项 | 环境变量 | 取值 | 默认 | 说明 |
|---|---|---|---|---|
| `scheduler` | `SCHEDULER` | `fsrs_py` \| `sm2` | `sm2` | 调度内核；未知值一律归一化回落 `sm2` |
| `scheduler_fsrs_rollout_ratio` | `SCHEDULER_FSRS_ROLLOUT_RATIO` | `0.0`~`1.0` | `0.0` | py-fsrs 灰度放量比例（按学生稳定哈希） |

裁决逻辑（`fsrs_scheduler.resolve_scheduler_mode`）：

1. `scheduler` 归一化后不是 `fsrs_py` → `sm2`；
2. py-fsrs 未安装 → **静默降级 `sm2`**（绝不阻断应用启动，日志 `WARNING`）；
3. 灰度比例 `>= 1.0` → 全量 `fsrs_py`；
4. 灰度比例 `<= 0.0` 或拿不到 `student_id` → `sm2`；
5. 否则：`sha256(student_id) → [0,1)` 的稳定哈希，小于比例则走 `fsrs_py`。

> 稳定哈希保证：**同一学生每次都命中同一内核**，复习体验始终一致，
> 不会出现「今天间隔 6 天、明天间隔 3 天」的抖动。

### 3.1 依赖声明

`py-fsrs` 由并行任务 T03 独占写入 `backend/requirements.txt`：

```
py-fsrs>=5.0,<6
```

安装：

```bash
pip install "py-fsrs>=5.0,<6"
```

> 未安装时应用**照常启动**，复习链路走 SM-2，只在日志里打一条 WARNING。

---

## 4. 灰度放量步骤（60 人内测）

前置：确认后端已装 `py-fsrs>=5.0,<6`，且 `tests/test_fsrs_pyfsrs_parity.py` 的双跑
偏差用例**通过**（未安装时该用例 skip，不算通过）。

| 阶段 | 配置 | 观察期 | 观察指标 | 通过标准 |
|---|---|---|---|---|
| P0 基线 | `SCHEDULER=sm2` | — | — | 当前默认状态 |
| P1 小流量 | `SCHEDULER=fsrs_py`<br>`SCHEDULER_FSRS_ROLLOUT_RATIO=0.1` | ≥ 3 天 | 复习完成率、次日留存、平均间隔、错题复错率 | 与 P0 无显著劣化 |
| P2 半量 | `...RATIO=0.5` | ≥ 5 天 | 同上 + 学生反馈 | 同上 |
| P3 全量 | `...RATIO=1.0` | ≥ 7 天 | 同上 | 留存不降、平均间隔合理 |

操作（改 `.env` 后重启后端进程，配置是进程启动时读取的单例）：

```bash
# backend/.env
SCHEDULER=fsrs_py
SCHEDULER_FSRS_ROLLOUT_RATIO=0.1
```

**放量原则**：一次只动一个变量（先放量比例、不调算法参数）；
每阶段至少跨一个完整周末（复习行为受上学日影响明显）。

---

## 5. 回滚方案

| 场景 | 动作 | 影响 |
|---|---|---|
| 数据异常 / 学生反馈差 | `SCHEDULER=sm2`（或 `RATIO=0`）后重启 | 立即全量回到 SM-2 |
| py-fsrs 包损坏 / 版本冲突 | 无需动作（已自动降级）；或 `pip uninstall py-fsrs` | 无 |
| 只想让部分学生回退 | 调低 `SCHEDULER_FSRS_ROLLOUT_RATIO` | 哈希值落到区间外的学生先回退 |

**关键点：回滚不需要改代码、不需要迁移数据、不需要清缓存。**
因为 `difficulty` 列始终存 EF（见 §2.2），切回 SM-2 后学生看到的是连续的状态，
不会「回到解放前」。

回滚后需确认：

```bash
grep -c "py-fsrs 未安装\|降级为 SM-2" <后端日志>   # 降级是否发生过
```

---

## 6. 验证

### 6.1 双跑 parity 测试

`backend/tests/test_fsrs_pyfsrs_parity.py`：

* 固定种子 `20261001`，生成 1000 条 rating 流；
* 同一条流分别喂给 `_sm2_review_card` 与 `_pyfsrs_review_card`；
* 取每条流的最终 `scheduled_days` 求均值，
  偏差 = `|mean_pyfsrs - mean_sm2| / mean_sm2`，断言 **< 5%**；
* py-fsrs 未安装 → **skip**（不误报红灯）。

### 6.2 分支链路测试（不依赖 py-fsrs）

同文件内用注入桩（`sys.modules["fsrs"]`）覆盖：开关裁决、灰度放量比例与稳定性、
未知值回落、包缺失降级、运行期异常降级、EF 往返无损、difficulty 列语义。

### 6.3 偏差超标怎么办

**不要放宽 5% 阈值。** 按以下顺序校准（改的是 py-fsrs 的配置口径，
见 `fsrs_scheduler._build_pyfsrs_scheduler`）：

1. `learning_steps`：当前按 SM-2 同尺度设为 `1 天 / 6 天`，
   若首两次间隔偏短可调大；
2. `desired_retention`：当前 `0.9`，调低会拉长间隔、调高会缩短；
3. `maximum_interval`：当前 `365` 天，防止间隔膨胀到数十年；
4. 仍不收敛 → 说明 py-fsrs 与 SM-2 的增长曲线本质上不可对齐，
   保持 `SCHEDULER=sm2` 并把结论回填本文档。

---

## 7. 代码结构（改动后）

```
app/services/fsrs_scheduler.py      ← 统一入口（唯一裁决点）
    ├─ resolve_scheduler_mode(student_id)   开关 + 灰度裁决
    ├─ _sm2_review_card()                   内核 A：SM-2（默认/降级）
    ├─ _pyfsrs_review_card()                内核 B：py-fsrs（lazy import）
    ├─ _import_pyfsrs() / pyfsrs_available()/ reset_pyfsrs_cache()
    ├─ _ef_to_pyfsrs_difficulty() / _pyfsrs_difficulty_to_ef()   EF ↔ difficulty 可逆映射
    ├─ _sm2_restore_ef()                    旧 SM-2 状态回填（保留）
    ├─ FSRSScheduler                        对外契约类（签名不变）
    └─ review_review_state()                统一入口（SM-2 ReviewState 形态）

app/services/review_scheduler.py    ← 兼容壳
    ├─ _sm2_review_core()                   SM-2 内核本体（供统一入口回调）
    ├─ review()                             兼容壳：转发到 review_review_state
    └─ 常量 / ReviewState / ReviewPlan / is_due / select_due / quality_from_correct
                                            （纯函数，保留仅为兼容旧调用方）

app/services/review_service.py      ← 调用方（最小改动）
    └─ ReviewService._scheduler_for(student_id)  把学生 ID 透传给内核裁决

app/core/config.py                  ← 开关
    ├─ scheduler / scheduler_mode
    └─ scheduler_fsrs_rollout_ratio(_safe)
```

**新调用方一律走 `fsrs_scheduler`**；`review_scheduler` 只在旧链路里出现，
后续可随调用方改造逐步收敛删除。

---

## 8. 风险与已知限制

| 风险 | 等级 | 处置 |
|---|---|---|
| py-fsrs 未安装 / 装不上（内网机） | 低 | 自动降级 SM-2，应用照常运行 |
| py-fsrs 运行期异常 | 低 | 单次调度降级 SM-2 并告警，不阻塞复习 |
| 灰度切换导致同一学生间隔抖动 | 低 | 稳定哈希，同一学生恒定命中同一内核 |
| 两种内核间隔口径差异 | 中 | parity 测试守 5%；灰度放量分阶段观察 |
| 权重未针对本项目学生校准 | 中 | 先用 py-fsrs 默认权重；内测积累 review_logs 后可做参数优化（后续任务） |

> **许可证**：py-fsrs 为 MIT 许可，可商用，与本项目商业化计划无冲突。

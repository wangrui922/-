# 判题层升级：L3 数学等价判定（math_verify）

> 适用版本：V4.1+ ｜ 代码：`backend/app/services/sympy_judge.py`
> 依赖：`math-verify==0.6.*`（**可选依赖**，见「降级路径」）
> 开关：`JUDGE_L3=on|off`（默认 `on`）、`JUDGE_L3_TIMEOUT_MS=500`

## 1. 为什么要加这一层

升级前判题只有两层：

| 层 | 实现 | 能力 |
| --- | --- | --- |
| L1 | 字符串归一（`judge_engine`） | 选择/判断题的键位、布尔比对 |
| L0 | SymPy（`sympy_judge.judge`） | 纯计算类题目的符号级精确判分 |

遇到**等价但字面不同**的写法会误判成错：

* `1/2` vs `0.5`（分数 / 小数）
* `x+1` vs `1+x`（交换律）
* `\frac{1}{2}` vs `0.5`（LaTeX / 纯文本）
* `50%` vs `0.5`（百分号）
* `5cm` vs `5`（学生按题面要求写了单位）

误判的直接后果是**错题归因和掌握度失真**：孩子写对了却被记进错题本，
「掌握度」模型拿到的是脏信号。判题正确性是商业化落地的质量底线。

## 2. 分层逻辑

`layered_judge(q_type, content, options, correct_answer, selected_answer)` 的执行顺序：

```
L0  SymPy 精确判     ──→ 判对？直接返回（level=0）
                        判错 / 判不出 ↓
L3  math_verify 等价判 ──→ 判定等价？返回判对（level=3）
                        不等价 / 不可用 ↓
                       L0 已有判错结论？原样返回（level=0）
                        否则 ↓
L1  规则引擎判        ──→ 选择/判断（level=1），其它题型 level=4 兜底
L2  LLM 语义判        ──→ 应用题/开放题（由 practice_service 在 L1 兜底后调用）
```

### ⚠️ L3 的核心约束：只救不杀

L3 **只能把结论改成「判对」，永远不能把「判对」改成「判错」**，也永远不会在
L1 规则引擎 / L2 LLM 之前给出判错结论。

原因：`math_verify` 说「不相等」并不等于孩子答错了——它可能是解析不了中文
夹杂的答案、可能是题型根本不适合数学判定。把「证明不了等价」当成「判错」
会绕过 L2 的 LLM 语义判，直接把开放题判死。所以 L3 只在**正向**（确认等价）
时才有产出，负向一律交回原路径。

## 3. 开关与降级路径

### 3.1 配置（pydantic-settings，不读裸 `os.environ`）

`backend/app/core/config.py`：

| 字段 | 环境变量 | 默认 | 说明 |
| --- | --- | --- | --- |
| `judge_l3` | `JUDGE_L3` | `"on"` | `off` 时**完全不进 L3**，回到原来的 L0 + L1 两层路径 |
| `judge_l3_timeout_ms` | `JUDGE_L3_TIMEOUT_MS` | `500` | 单题 L3 判定超时（毫秒），超时强制回退；允许 50~5000 |

`judge_l3` 兼容 `on/1/true/yes/开启` 与 `off/0/false/no/关闭`（大小写不敏感），
未识别的写法按默认 `on` 处理。派生属性 `settings.judge_l3_enabled: bool`。

### 3.2 五条降级路径（任一命中即等价于升级前的行为）

1. **开关关闭**：`JUDGE_L3=off` → `_l3_equivalent()` 直接返回 `None`；
2. **库未安装**：`math_verify` 是**函数内 lazy import**，捕获 `ImportError`
   → 整层跳过。**绝不让应用启动失败**；
3. **超时**：单题 parse+verify 超过 `judge_l3_timeout_ms` → 放弃该次判定；
4. **题型不符**：不在 `MATH_VERIFY_SUPPORTED_TYPES`（SymPy 题型 + `填空`/`填空题`）
   → 跳过，**保证选择/判断题仍由 L1 规则引擎判定**；
5. **解析异常**：`math_verify` 内部抛任何异常 → 捕获并跳过。

### 3.3 超时为什么用守护线程

| 方案 | 不用它的原因 |
| --- | --- |
| `signal.alarm` / `SIGALRM` | Windows 没有 `SIGALRM`，跨平台不可用 |
| `asyncio.wait_for` | `layered_judge()` 是**同步**函数，可能被 FastAPI 线程池执行，拿不到 running loop |
| `concurrent.futures.ThreadPoolExecutor` | 解释器退出时 `atexit` 会 **join** 工作线程，卡死的判定会拖住进程退出 |
| ✅ `threading.Thread(daemon=True)` + `join(timeout)` | 不阻塞事件循环、不拖住进程退出；超时后线程被放弃（结果丢弃） |

实现见 `sympy_judge._run_with_timeout(func, timeout_sec) -> (completed, value, error)`。

## 4. L3 内部的两处归一（自己可控，不依赖 math_verify 的行为）

1. **百分号**：`50%` → `(50/100)`。math_verify 对 `%` 的处理不确定，
   这里自己归一，保证「百分号 vs 小数」结果稳定可预期。
2. **单位剥离**（`_strip_unit_pair`，三条规则）：

   | 场景 | 处理 | 理由 |
   | --- | --- | --- |
   | 单侧带单位（`5cm` vs `5`） | 剥掉那一侧 | 孩子按题面要求写单位，不该判错 |
   | 两侧同单位（`5CM` vs `5cm`） | 一起剥 | 单位大小写不该影响判定 |
   | 两侧不同单位（`5cm` vs `5m`） | **一个都不剥** | 否则被抹成 `5` vs `5` 误判成对 |

   单位白名单见 `_UNIT_TOKENS`（长词在前：`平方米` 先于 `米`）。

## 5. 已知边界（务必知悉）

| 边界 | 表现 | 处理方式 |
| --- | --- | --- |
| **中文题干** | math_verify 面向英文/LaTeX 竞赛题语料，对「小明有 3 个苹果」这类中文题干抽取不稳定 | L3 只比较**答案**不比题干；中文夹杂答案解析失败时返回 `None` → 安全回落 L1/L2 |
| **单位** | 只有白名单内的单字符/双字符单位参与剥离，且必须满足「单侧 / 同单位」条件 | 未覆盖的单位不剥离 → 不判对也不判错，回落原路径（宁可不判，不误判） |
| **量纲换算** | `5cm` vs `0.05m`、`1小时` vs `60分钟` 判不出等价 | 明确不支持，回落原路径 |
| **大小写变量** | `X+1` vs `x+1` 判为**不等价**（数学上 X 与 x 确为不同符号） | 产品口径：变量大小写敏感；只有**单位**的大小写做了归一 |
| **`2x` 这类隐式乘法** | math_verify 的纯文本抽取可能解析失败 | L0（SymPy）已能处理，L0 判对即返回，不进 L3 |
| **选择题 / 判断题** | 完全不进 L3 | 由 L1 规则引擎负责（见 `MATH_VERIFY_SUPPORTED_TYPES`） |
| **首次导入开销** | `import math_verify` 是一次性成本（不在 500ms 超时内计量） | 之后走 `sys.modules` 缓存 |

## 6. 依赖与安装

`backend/requirements.txt`（每个包都标注了「未安装时的降级策略」）：

```txt
sympy==1.13.*        # L0 层已在用，此前漏登记，补锁避免线上裸奔
math-verify==0.6.*   # L3 层；未安装 → L3 整层跳过，行为与升级前一致
py-fsrs>=5.0,<6      # T04 复习调度预留；当前 fsrs_scheduler 不 import，未装无影响
pytest-cov==5.*      # 仅测试期使用，缺失不影响应用启动
```

## 7. 测试

`backend/tests/test_sympy_judge_equiv.py`（49 条参数化用例 + 路由/降级/超时/开关用例）：

* `EQUIV_CASES_L0`（26 条）：L0 已能覆盖的等价形式，**装没装 math_verify 都必须判对**；
* `EQUIV_CASES_L3`（11 条）：只有 L3 能救回的形式（百分号 / 单位 / 单位大小写），
  **math_verify 未安装时整组 skip**（`requires_math_verify` 标记，不 fail）；
* `NOT_EQUIV_CASES`（12 条）：不等价必须判错（含 `5cm` vs `5m` 这类单位错）。

用例来源覆盖：`1/2`=`0.5`、`0.5`=`1/2`、`x+1`=`1+x`、`2x`=`2*x`、
`\frac{1}{2}`=`0.5`、百分号与小数、带单位、全角/半角括号、多余空格、
单位大小写、正负号位置、分数与除法式、`3.0`=`3`。

## 8. 对外契约（未变更）

`layered_judge()` 与 `judge()` 的**函数签名与返回结构完全未变**，仍返回
`JudgeOutcome(is_correct, level, reason, audit_reason)`。新增的取值只有
`level=3`（`LEVEL_MATH_VERIFY`）。

`reason` 仍然遵守 no-leak 铁律：**任何分支都不含标准答案**（含 L3 分支）；
答案只进 `audit_reason`（只进日志/审计，绝不进 `to_dict()` 与 API 响应体）。

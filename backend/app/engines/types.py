# -*- coding: utf-8 -*-
"""V6.5 教学决策引擎层（Sprint 6 · T05）共享数据类与类型工具。

设计约定（详见 ``docs/engines-design.md``）：

* **纯层**：本模块**只依赖标准库**，禁止 import ``sqlalchemy`` / ``Session`` /
  ``os.environ`` / 配置文件——引擎层必须能被纯单测直接 import；
* **不可变优先**：所有数据类 ``@dataclass(frozen=True)``，入参在读的时候就做
  归一化（``__post_init__`` 里用 ``object.__setattr__``），使下游算法拿到的字段
  一定落在合法区间，**异常入参兜底而不是抛异常**（引擎不得把请求打挂）；
* **camelCase 出口**：所有数据类都提供 ``to_api()``，产出 camelCase ``dict``，
  API 层可直接塞进 ``{code,msg,data}`` 的 ``data``；
* **职责边界**：四个引擎（difficulty / priority / generation / planner）共用
  这里的数据类，避免各自定义同名字段导致接线时互相转来转去。

当前实现进度：
    * ``DifficultyInput`` / ``DifficultyBand`` —— T05 已实现（``engines.difficulty``）；
    * ``PriorityItem`` / ``ExercisePlan`` —— 占位，T06 实现；
    * ``PlanItem`` —— 占位，T07 实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

# ============================================================ 难度坐标系常量
#: 离散难度档位，与 ``questions.difficulty`` 口径严格一致
#: （故意不 import ``app.services.adaptive_service.DIFFICULTIES``——那边依赖 ORM，
#:  引擎层一旦依赖就把「纯函数」破功；此处为坐标ソース，口径变更时两处一起改）
DIFFICULTY_LEVELS: Tuple[int, ...] = (1, 3, 5)

#: 连续难度坐标下界 / 上界（1~5，与离散档位同量程，便于换算）
DIFFICULTY_MIN: float = 1.0
DIFFICULTY_MAX: float = 5.0
#: 缺省主难度
DEFAULT_DIFFICULTY: float = 3.0

# ============================================================ 提示依赖枚举
#: 与 ``app.models.dna.HINT_DEPENDENCY_VALUES`` 同口径（此处不 import ORM）
HINT_DEPENDENCY_HIGH: str = "high"
HINT_DEPENDENCY_MEDIUM: str = "medium"
HINT_DEPENDENCY_LOW: str = "low"
HINT_DEPENDENCY_VALUES: Tuple[str, ...] = (
    HINT_DEPENDENCY_HIGH,
    HINT_DEPENDENCY_MEDIUM,
    HINT_DEPENDENCY_LOW,
)

#: 难度带端点比较容差（浮点误差 1e-9 级，避免边界档位被误判为「不在带内」）
_LEVEL_EPSILON: float = 1e-9

_TRUE_TEXTS: frozenset = frozenset({"1", "true", "yes", "y", "on"})
_FALSE_TEXTS: frozenset = frozenset({"0", "false", "no", "n", "off"})


# ============================================================ 类型安全工具
def to_float(value: Any, default: float = 0.0) -> float:
    """把任意入参安全转成 float（不可转换 / NaN / inf 一律回退 ``default``）。

    Args:
        value: 任意入参（``None``、字符串、布尔、越界数值都可能出现）。
        default: 兜底值。

    Returns:
        合法有限浮点数。
    """
    if isinstance(value, bool):
        return float(int(value))
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)
    if result != result or result in (float("inf"), float("-inf")):
        # NaN / ±inf 一律视作脏数据
        return float(default)
    return result


def to_int(value: Any, default: int = 0) -> int:
    """把任意入参安全转成 int（先按 float 解析，非法回退 ``default``）。"""
    return int(to_float(value, float(default)))


def to_bool(value: Any, default: bool = False) -> bool:
    """把任意入参安全转成 bool。

    支持 ``True/False``、``1/0``、``"true"/"false"/"yes"/"no"/"on"/"off"``
    （大小写不敏感）；无法识别时回退 ``default``。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_TEXTS:
        return True
    if text in _FALSE_TEXTS:
        return False
    return bool(default)


def clamp(value: float, low: float, high: float) -> float:
    """把数值夹紧到 ``[low, high]``；``low > high`` 时按 ``[high, low]`` 处理。"""
    lower, upper = (low, high) if low <= high else (high, low)
    result = to_float(value, lower)
    return min(upper, max(lower, result))


def round2(value: float) -> float:
    """保留两位小数，并把 ``-0.0`` 归一成 ``0.0``（避免 API 出脏值）。"""
    result = round(to_float(value, 0.0), 2)
    return 0.0 if result == 0 else result


def nearest_level(value: float) -> int:
    """取离散档位里距离 ``value`` 最近的一档；距离相同时取较小档位。"""
    center = to_float(value, DEFAULT_DIFFICULTY)
    return min(DIFFICULTY_LEVELS, key=lambda level: (abs(float(level) - center), level))


def snap_levels(lo: float, hi: float) -> Tuple[int, ...]:
    """把连续难度带映射回落在其范围内的离散档位。

    Args:
        lo: 难度带下界（连续坐标 1~5）。
        hi: 难度带上界（连续坐标 1~5）。

    Returns:
        落在带内的档位元组（按 ``(1, 3, 5)`` 原序）；一个都不落则在带中心
        就近取一档——保证**永远至少有一档**，抽题层不必再处理空列表。
    """
    lower = to_float(lo, DIFFICULTY_MIN)
    upper = to_float(hi, DIFFICULTY_MAX)
    if lower > upper:
        lower, upper = upper, lower

    levels = tuple(
        int(level)
        for level in DIFFICULTY_LEVELS
        if lower - _LEVEL_EPSILON <= level <= upper + _LEVEL_EPSILON
    )
    if levels:
        return levels
    return (nearest_level((lower + upper) / 2.0),)


def _clean_text(value: Any, default: str = "") -> str:
    """把任意入参转成去空白的字符串；不可转换回退 ``default``。"""
    if value is None:
        return default
    try:
        text = str(value).strip()
    except Exception:  # noqa: BLE001 - __str__ 可能抛，按缺省处理
        return default
    return text or default


# ============================================================ 难度引擎数据类
@dataclass(frozen=True)
class DifficultyInput:
    """难度引擎入参（由学生 DNA 汇总而来，**已归一化**）。

    构造期即完成兜底归一化（非法值回落默认值，不抛异常）：

        * ``mastery`` / ``confidence`` / ``error_rate`` / ``recent_accuracy``
          ∈ ``[0, 1]``，非数字回退默认值；
        * ``current_difficulty`` 数值型会夹紧到 ``[DIFFICULTY_MIN, DIFFICULTY_MAX]``，
          不可解析（``None`` / 非数字串）才回退 ``DEFAULT_DIFFICULTY``；
        * ``practice_count`` ≥ 0；
        * ``hint_dependency`` 只取 high/medium/low，其余（含 ``None``）按 medium。

    Attributes:
        mastery: 目标知识点掌握度（0~1）。
        confidence: 掌握度置信度（0~1，样本越少越低）。
        practice_count: 目标知识点累计练习次数（冷启动判定用）。
        error_rate: 目标知识点错误率（0~1）。
        hint_dependency: 提示依赖 high/medium/low。
        current_difficulty: 学生当前主难度（连续坐标或离散档位均可）。
        recent_accuracy: 最近事件窗口正确率（0~1）；``None`` 表示无窗口数据。
        student_id: 学生 ID（纯日志/追踪用，不参与计算）。
        knowledge_point_id: 目标知识点 ID（同上）。
    """

    mastery: float = 0.0
    confidence: float = 0.0
    practice_count: int = 0
    error_rate: float = 0.0
    hint_dependency: str = HINT_DEPENDENCY_MEDIUM
    current_difficulty: float = DEFAULT_DIFFICULTY
    recent_accuracy: Optional[float] = None
    student_id: str = ""
    knowledge_point_id: Optional[str] = None

    def __post_init__(self) -> None:
        """入参归一化（frozen 下用 ``object.__setattr__`` 写字段）。"""
        object.__setattr__(self, "mastery", clamp(self.mastery, 0.0, 1.0))
        object.__setattr__(self, "confidence", clamp(self.confidence, 0.0, 1.0))
        object.__setattr__(self, "error_rate", clamp(self.error_rate, 0.0, 1.0))
        object.__setattr__(self, "practice_count", max(0, to_int(self.practice_count, 0)))

        hint = _clean_text(self.hint_dependency, HINT_DEPENDENCY_MEDIUM).lower()
        if hint not in HINT_DEPENDENCY_VALUES:
            hint = HINT_DEPENDENCY_MEDIUM
        object.__setattr__(self, "hint_dependency", hint)

        current = to_float(self.current_difficulty, DEFAULT_DIFFICULTY)
        object.__setattr__(
            self, "current_difficulty", clamp(current, DIFFICULTY_MIN, DIFFICULTY_MAX)
        )

        if self.recent_accuracy is None:
            object.__setattr__(self, "recent_accuracy", None)
        else:
            accuracy = to_float(self.recent_accuracy, 0.0)
            object.__setattr__(self, "recent_accuracy", clamp(accuracy, 0.0, 1.0))

        object.__setattr__(self, "student_id", _clean_text(self.student_id, ""))
        kp_id = self.knowledge_point_id
        object.__setattr__(
            self, "knowledge_point_id", None if kp_id is None else (_clean_text(kp_id, "") or None)
        )


@dataclass(frozen=True)
class DifficultyBand:
    """难度引擎出参：目标难度带 ``[lo, hi]``（连续坐标 1~5）。

    Attributes:
        lo: 难度带下界（已夹紧到 ``[DIFFICULTY_MIN, DIFFICULTY_MAX]``）。
        hi: 难度带上界（同上，恒满足 ``hi >= lo``）。
        target: 推荐中心难度（= 夹紧前的推荐值再夹紧）。
        levels: 落在带内的离散档位子集，恒非空。
        reason: 决策原因码，见 ``engines.difficulty.REASON_*``。
        cold_start: 是否走了冷启动分支（样本不足）。
        confidence: 回带入参置信度，便于前端提示「数据较少」。
    """

    lo: float = DIFFICULTY_MIN
    hi: float = DIFFICULTY_MAX
    target: float = DEFAULT_DIFFICULTY
    levels: Tuple[int, ...] = DIFFICULTY_LEVELS
    reason: str = "steady"
    cold_start: bool = False
    confidence: float = 0.0

    def __post_init__(self) -> None:
        """保证出参自洽：``lo <= hi``、``levels`` 非空、``confidence`` 合法。"""
        lower = clamp(self.lo, DIFFICULTY_MIN, DIFFICULTY_MAX)
        upper = clamp(self.hi, DIFFICULTY_MIN, DIFFICULTY_MAX)
        if lower > upper:
            lower, upper = upper, lower
        object.__setattr__(self, "lo", lower)
        object.__setattr__(self, "hi", upper)
        object.__setattr__(
            self, "target", clamp(self.target, DIFFICULTY_MIN, DIFFICULTY_MAX)
        )
        levels = tuple(int(level) for level in self.levels)
        if not levels:
            levels = (nearest_level(self.target),)
        object.__setattr__(self, "levels", levels)
        object.__setattr__(self, "confidence", clamp(self.confidence, 0.0, 1.0))

    def to_api(self) -> Dict[str, Any]:
        """转成 camelCase dict（API 层可直接作为 ``data`` 返回）。"""
        return {
            "lo": round2(self.lo),
            "hi": round2(self.hi),
            "target": round2(self.target),
            "levels": [int(level) for level in self.levels],
            "reason": _clean_text(self.reason, "steady"),
            "coldStart": bool(self.cold_start),
            "confidence": round2(self.confidence),
        }


# ============================================================ 优先级 / 出题 / 计划
@dataclass(frozen=True)
class PriorityItem:
    """优先级引擎出参条目（T06 实现，本 Sprint 仅占位数据类）。

    Attributes:
        knowledge_point_id: 知识点 ID。
        kp_name: 知识点名称。
        score: 优先级得分（越大越该先学）。
        mastery: 掌握度（0~1）。
        level: 掌握度档位 0~3。
        reason: 中文理由（家长端/学生端可直接展示）。
    """

    knowledge_point_id: str = ""
    kp_name: str = ""
    score: float = 0.0
    mastery: float = 0.0
    level: int = 0
    reason: str = ""

    def __post_init__(self) -> None:
        """数值兜底：``mastery`` ∈ ``[0,1]``、``level`` ∈ ``[0,3]``。"""
        object.__setattr__(self, "mastery", clamp(self.mastery, 0.0, 1.0))
        object.__setattr__(self, "level", int(clamp(to_float(self.level, 0.0), 0.0, 3.0)))

    def to_api(self) -> Dict[str, Any]:
        """转成 camelCase dict。"""
        return {
            "knowledgePointId": _clean_text(self.knowledge_point_id, ""),
            "kpName": _clean_text(self.kp_name, ""),
            "score": round2(self.score),
            "mastery": round2(self.mastery),
            "level": int(self.level),
            "reason": _clean_text(self.reason, ""),
        }


@dataclass(frozen=True)
class ExercisePlan:
    """出题引擎出参（T06 实现，本 Sprint 仅占位数据类）。

    Attributes:
        knowledge_point_id: 目标知识点 ID。
        question_count: 计划题目数量。
        difficulty_levels: 计划覆盖的离散难度档位组合。
        strategy: 出题策略标记（如 ``review_first`` / ``gap_first``）。
    """

    knowledge_point_id: str = ""
    question_count: int = 0
    difficulty_levels: Tuple[int, ...] = ()
    strategy: str = ""

    def __post_init__(self) -> None:
        """兜底：题数非负、档位限制在合法集合内。"""
        object.__setattr__(self, "question_count", max(0, to_int(self.question_count, 0)))
        levels = tuple(
            int(level) for level in self.difficulty_levels if int(level) in DIFFICULTY_LEVELS
        )
        object.__setattr__(self, "difficulty_levels", levels or DIFFICULTY_LEVELS[:1])

    def to_api(self) -> Dict[str, Any]:
        """转成 camelCase dict。"""
        return {
            "knowledgePointId": _clean_text(self.knowledge_point_id, ""),
            "questionCount": int(self.question_count),
            "difficultyLevels": [int(level) for level in self.difficulty_levels],
            "strategy": _clean_text(self.strategy, ""),
        }


@dataclass(frozen=True)
class PlanItem:
    """学习计划引擎出参条目（T07 实现，本 Sprint 仅占位数据类）。

    Attributes:
        knowledge_point_id: 知识点 ID。
        kp_name: 知识点名称。
        day_index: 计划第几天（1 起算）。
        minutes: 建议投入分钟数。
        difficulty_lo: 当日建议难度下界。
        difficulty_hi: 当日建议难度上界。
        goal: 当日目标描述。
    """

    knowledge_point_id: str = ""
    kp_name: str = ""
    day_index: int = 1
    minutes: int = 0
    difficulty_lo: float = DIFFICULTY_MIN
    difficulty_hi: float = DIFFICULTY_MAX
    goal: str = ""

    def __post_init__(self) -> None:
        """兜底：天数/分钟非负，难度带自洽且落在坐标范围内。"""
        object.__setattr__(self, "day_index", max(1, to_int(self.day_index, 1)))
        object.__setattr__(self, "minutes", max(0, to_int(self.minutes, 0)))
        lower = clamp(self.difficulty_lo, DIFFICULTY_MIN, DIFFICULTY_MAX)
        upper = clamp(self.difficulty_hi, DIFFICULTY_MIN, DIFFICULTY_MAX)
        if lower > upper:
            lower, upper = upper, lower
        object.__setattr__(self, "difficulty_lo", lower)
        object.__setattr__(self, "difficulty_hi", upper)

    def to_api(self) -> Dict[str, Any]:
        """转成 camelCase dict。"""
        return {
            "knowledgePointId": _clean_text(self.knowledge_point_id, ""),
            "kpName": _clean_text(self.kp_name, ""),
            "dayIndex": int(self.day_index),
            "minutes": int(self.minutes),
            "difficultyLo": round2(self.difficulty_lo),
            "difficultyHi": round2(self.difficulty_hi),
            "goal": _clean_text(self.goal, ""),
        }


#: 掌握度行的基础字段（``get_learning_dna().mastery_summary`` 的每项所必需的键），
#: 供 T06 适配器按同一口径取数，避免各引擎各写一份 key 名兜底。
MASTERY_ROW_REQUIRED_KEYS: Tuple[str, ...] = (
    "knowledge_point_id",
    "mastery",
    "confidence",
    "practice_count",
    "error_rate",
)


def mastery_rows(dna: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """从 ``get_learning_dna()`` 的结果里安全取出掌握度行列表。

    Args:
        dna: ``app.services.student_dna_service.get_learning_dna`` 的返回值。

    Returns:
        只包含 dict 行的列表；入参缺失/非 dict/None 一律返回空列表。
    """
    if not isinstance(dna, Mapping):
        return []
    rows = dna.get("mastery_summary")
    if not isinstance(rows, (list, tuple)):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


__all__ = [
    "DIFFICULTY_LEVELS",
    "DIFFICULTY_MIN",
    "DIFFICULTY_MAX",
    "DEFAULT_DIFFICULTY",
    "HINT_DEPENDENCY_HIGH",
    "HINT_DEPENDENCY_MEDIUM",
    "HINT_DEPENDENCY_LOW",
    "HINT_DEPENDENCY_VALUES",
    "MASTERY_ROW_REQUIRED_KEYS",
    "clamp",
    "to_float",
    "to_int",
    "to_bool",
    "round2",
    "nearest_level",
    "snap_levels",
    "mastery_rows",
    "DifficultyInput",
    "DifficultyBand",
    "PriorityItem",
    "ExercisePlan",
    "PlanItem",
]

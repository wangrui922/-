# -*- coding: utf-8 -*-
"""出题编排引擎：把优先级队列与难度带翻译成确定性的题量计划。

纯函数铁律
----------
引擎**只做配额与档位分配**，自己绝不查询题库：不 import ORM / Session、
不读 env 与配置文件，也不接收 / 返回题目 ID。真正去题库捞题、近 N 次回避与
逐级放宽都由 ``practice_service`` / ``ai_exercise_service`` 完成
（``docs/engines-design.md`` 第 7 节）——否则纯函数引擎会被迫依赖数据库。

``plan_units`` 是**可选、仅关键字**入参：省略时引擎按优先级自行摊派题量，
出参与 T05 冻结契约完全一致，老调用方零感知。

兜底策略
--------
* 队列为空：返回一条 ``__mixed_review__`` 综合摸底计划，绝不返回空列表；
* 难度带缺失 / 错位：该知识点使用 ``DifficultyBand()`` 的全档探索带；
* ``levels`` 取不到（缺失 / 越界 / 脏值）时回退 ``(3,)``；
* 总题数非法 / 非正：使用 ``generation_default_total``；过大则夹到
  ``generation_max_total``；``total=None`` 且传了 ``plan_units`` 时取各单元
  ``question_count`` 之和；
* 重复知识点：只保留队列中排名最前的一项，避免重复配额；
* 总题数少于候选知识点数：只覆盖最靠前的知识点，保证每个计划至少一题。

下游接缝（T07）
--------------
引擎层不新增入口，服务层用薄包装把计划翻译成题目：
``draw_questions_by_plan(db, student_id=..., plans=..., recent_days=7)`` 逐条
``ExercisePlan`` 复用现有 ``_draw_with_relaxation``，其中
``exclude_ids=_recent_question_ids(...)``、``difficulty_levels`` 逐档传入，
``strategy`` 原样写回出参；``diagnostic_fallback`` 时把 ``kp_id`` 置 ``None``
走综合题池。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.engines.config import AdaptiveConfig, DEFAULT_ADAPTIVE_CONFIG
from app.engines.types import (
    DEFAULT_DIFFICULTY,
    DIFFICULTY_LEVELS,
    DifficultyBand,
    ExercisePlan,
    PriorityItem,
    nearest_level,
    to_float,
    to_int,
)

#: 负责人任务号
OWNER_TASK: str = "T06"

#: 队列为空时的稳定兜底知识点 ID；服务层看到它应走「综合题池」。
FALLBACK_KNOWLEDGE_POINT_ID: str = "__mixed_review__"

#: 缺省新题占比（决定 ``new_first`` / ``review_first`` / ``balanced``）
PLAN_NEW_RATIO_DEFAULT: float = 0.6

#: 取不到难度档时的回退档位
FALLBACK_LEVEL: int = 3

#: 策略标记
STRATEGY_NEW_FIRST: str = "new_first"
STRATEGY_REVIEW_FIRST: str = "review_first"
STRATEGY_BALANCED: str = "balanced"
STRATEGY_DIAGNOSTIC_FALLBACK: str = "diagnostic_fallback"

#: ``plan_units`` 里题量字段的候选键名
_COUNT_KEYS: Tuple[str, ...] = ("question_count", "count", "total")
#: ``plan_units`` 里难度档字段的候选键名
_LEVEL_KEYS: Tuple[str, ...] = ("levels", "difficulty_levels", "difficulty")


# ============================================================ 入参归一化
def _text(value: Any, default: str = "") -> str:
    """把任意值安全转成去空白字符串；不可转换回退 ``default``。"""
    if value is None:
        return default
    try:
        text = str(value).strip()
    except Exception:  # noqa: BLE001 - 脏对象的 __str__ 可能抛异常
        return default
    return text or default


def _field(source: Any, names: Sequence[str], default: Any = None) -> Any:
    """同时兼容 dict 与对象：按顺序尝试键名 / 属性名。"""
    if isinstance(source, dict):
        for name in names:
            if name in source:
                return source.get(name)
        return default
    for name in names:
        try:
            if hasattr(source, name):
                return getattr(source, name)
        except Exception:  # noqa: BLE001 - 外部对象属性访问失败按缺失处理
            continue
    return default


def _as_sequence(value: Any) -> Tuple[Any, ...]:
    """把入参归一成元组；``None`` / 映射 / 字符串按单元素处理。"""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or isinstance(value, dict):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _priority_pairs(
    priorities: Any,
    bands: Any,
) -> List[Tuple[PriorityItem, DifficultyBand]]:
    """过滤脏元素并去重，同时保留难度带与原优先级位置的对应关系。"""
    raw_bands = bands if isinstance(bands, (list, tuple)) else ()
    result: List[Tuple[PriorityItem, DifficultyBand]] = []
    seen: Set[str] = set()
    for index, item in enumerate(_as_sequence(priorities)):
        if not isinstance(item, PriorityItem):
            continue
        kp_id = _text(item.knowledge_point_id)
        if not kp_id or kp_id in seen:
            continue
        seen.add(kp_id)
        raw_band = raw_bands[index] if index < len(raw_bands) else None
        band = raw_band if isinstance(raw_band, DifficultyBand) else DifficultyBand()
        result.append((item, band))
    return result


def _levels_for(band: Any) -> Tuple[int, ...]:
    """清洗难度档并保证至少一档；取不到合法档位时回退 ``(3,)``。

    Args:
        band: ``DifficultyBand``、带 ``levels`` / ``difficulty_levels`` 字段的
            dict / 对象，或直接给出档位序列。脏值一律回退 ``(FALLBACK_LEVEL,)``。
    """
    raw_levels: Sequence[Any]
    if isinstance(band, DifficultyBand):
        raw_levels = band.levels
    elif isinstance(band, (list, tuple, set, frozenset)):
        raw_levels = tuple(band)
    elif isinstance(band, (int, float)) and not isinstance(band, bool):
        raw_levels = (band,)
    elif band is None:
        raw_levels = ()
    elif isinstance(band, dict):
        raw_levels = _as_sequence(band.get("levels") or band.get("difficulty_levels") or ())
    else:
        raw_levels = _as_sequence(
            _field(band, _LEVEL_KEYS, ()) if _field(band, _LEVEL_KEYS) is not None else ()
        )

    levels = tuple(
        dict.fromkeys(
            int(level)
            for level in _as_sequence(raw_levels)
            if to_int(level, -1) in DIFFICULTY_LEVELS
        )
    )
    if levels:
        return levels
    if isinstance(band, DifficultyBand):
        return (nearest_level(to_float(band.target, DEFAULT_DIFFICULTY)),)
    return (FALLBACK_LEVEL,)


def _unit_counts(plan_units: Any) -> List[int]:
    """取 ``plan_units`` 里各单元的题量（脏值按 0 计）。"""
    counts: List[int] = []
    for unit in _as_sequence(plan_units):
        counts.append(max(0, to_int(_field(unit, _COUNT_KEYS), 0)))
    return counts


def _unit_levels(plan_units: Any) -> Dict[int, Tuple[int, ...]]:
    """取 ``plan_units`` 里各单元的难度档（按索引索引）。"""
    levels: Dict[int, Tuple[int, ...]] = {}
    for index, unit in enumerate(_as_sequence(plan_units)):
        raw = _field(unit, _LEVEL_KEYS)
        levels[index] = _levels_for(raw)
    return levels


def _safe_total(total: Any, settings: AdaptiveConfig, fallback: Optional[int] = None) -> int:
    """归一化本批题数，恒位于 ``[1, generation_max_total]``。

    Args:
        total: 调用方传入的题数；``None`` / 0 / 负数 / 脏值时：
            先取 ``fallback``（``plan_units`` 题量之和，可能仍为 0），
            再取配置默认题数。
        settings: 阈值配置。
        fallback: ``total`` 缺省时的第一优先级取值。
    """
    requested = fallback if total is None else to_int(total, 0)
    if requested is None or requested <= 0:
        requested = fallback if fallback and fallback > 0 else settings.generation_default_total
    if requested <= 0:
        requested = settings.generation_default_total
    upper = max(1, settings.generation_max_total)
    return min(requested, upper)


# ============================================================ 配额与策略
def _allocate(total: int, priorities: Sequence[PriorityItem]) -> List[int]:
    """按优先级权重做最大余数法配额，确定且总和严格等于 ``total``。

    每个入选知识点先拿一题，剩余题量按非负 ``score`` 比例分配；全部得分为 0 时
    等比分配。小数余数并列时沿优先级队列顺序分配。
    """
    size = len(priorities)
    if size <= 0:
        return []
    quotas = [1 for _ in range(size)]
    remaining = max(0, total - size)
    if remaining <= 0:
        return quotas

    weights = [max(0.0, to_float(item.score, 0.0)) for item in priorities]
    weight_sum = sum(weights)
    if weight_sum <= 0.0:
        weights = [1.0 for _ in priorities]
        weight_sum = float(size)

    raw_extras = [remaining * weight / weight_sum for weight in weights]
    floor_extras = [int(math.floor(value)) for value in raw_extras]
    quotas = [base + extra for base, extra in zip(quotas, floor_extras)]
    leftovers = remaining - sum(floor_extras)
    order = sorted(
        range(size),
        key=lambda index: (-(raw_extras[index] - floor_extras[index]), index),
    )
    for index in order[:leftovers]:
        quotas[index] += 1
    return quotas


def _strategy(total: int, settings: AdaptiveConfig) -> str:
    """按新题占比给出批次策略：新题多 → ``new_first``，反之 ``review_first``。

    ``new_count = round(total × new_ratio)``、``review_count = total - new_count``；
    相等时为 ``balanced``。``new_ratio`` 缺省 0.6，脏值会被夹紧到 ``[0, 1]``。
    """
    ratio = to_float(settings.generation_new_ratio, PLAN_NEW_RATIO_DEFAULT)
    ratio = min(1.0, max(0.0, ratio))
    new_count = int(round(total * ratio))
    review_count = max(0, total - new_count)
    if new_count > review_count:
        return STRATEGY_NEW_FIRST
    if review_count > new_count:
        return STRATEGY_REVIEW_FIRST
    return STRATEGY_BALANCED


# ============================================================ 入口
def plan_exercises(
    priorities: Sequence[PriorityItem],
    bands: Sequence[DifficultyBand],
    *,
    total: Optional[int] = None,
    config: Optional[AdaptiveConfig] = None,
    plan_units: Any = None,
) -> List[ExercisePlan]:
    """生成知识点题量与难度档计划（**纯函数**）。

    Args:
        priorities: 已按分数降序的知识点优先级队列；重复 ID 会去重。
        bands: 与优先级队列按位置对应的 T05 难度带。短于队列时用默认带补齐。
        total: 本批总题数；``None`` / 非正 / 脏值时：若传了 ``plan_units``
            则取其 ``question_count`` 之和，否则用配置默认题数。
        config: ``generation_*`` 阈值；非配置对象使用缺省配置。
        plan_units: **可选**。调用方（T07）已拆好的计划单元，每项至少带
            ``question_count``；有 ``levels`` / ``difficulty_levels`` 时用它
            覆盖对应难度带。省略时题量由引擎按优先级自行摊派。

    Returns:
        非空 ``ExercisePlan`` 列表；各项 ``question_count >= 1``、题数之和等于
        归一化后的批次题数，知识点不重复，``difficulty_levels`` 恒非空。
    """
    settings = config if isinstance(config, AdaptiveConfig) else DEFAULT_ADAPTIVE_CONFIG
    units = _as_sequence(plan_units)
    unit_counts = _unit_counts(units) if units else []
    safe_total = _safe_total(
        total, settings, fallback=sum(unit_counts) if unit_counts else None
    )
    priority_pairs = _priority_pairs(priorities, bands)
    clean_bands = [band for band in _as_sequence(bands) if isinstance(band, DifficultyBand)]
    strategy = _strategy(safe_total, settings)

    if not priority_pairs:
        fallback_band = clean_bands[0] if clean_bands else DifficultyBand()
        return [
            ExercisePlan(
                knowledge_point_id=FALLBACK_KNOWLEDGE_POINT_ID,
                question_count=safe_total,
                difficulty_levels=_levels_for(fallback_band),
                strategy=STRATEGY_DIAGNOSTIC_FALLBACK,
            )
        ]

    candidate_count = min(
        len(priority_pairs),
        settings.generation_max_knowledge_points,
        safe_total,
    )
    selected_pairs = priority_pairs[:candidate_count]
    selected = [item for item, _band in selected_pairs]

    if unit_counts:
        # 调用方已拆好配额：按单元题量覆盖，缺失/为 0 的单元回退引擎摊派值。
        quotas = _allocate(safe_total, selected)
        for index in range(min(len(quotas), len(unit_counts))):
            if unit_counts[index] > 0:
                quotas[index] = unit_counts[index]
        if sum(quotas) != safe_total:
            quotas = _allocate(safe_total, selected)
    else:
        quotas = _allocate(safe_total, selected)

    unit_levels = _unit_levels(units) if units else {}

    plans: List[ExercisePlan] = []
    for index, ((item, band), quota) in enumerate(zip(selected_pairs, quotas)):
        levels = unit_levels.get(index) or _levels_for(band)
        plans.append(
            ExercisePlan(
                knowledge_point_id=item.knowledge_point_id,
                question_count=quota,
                difficulty_levels=levels,
                strategy=strategy,
            )
        )
    return plans


__all__ = [
    "FALLBACK_KNOWLEDGE_POINT_ID",
    "FALLBACK_LEVEL",
    "OWNER_TASK",
    "PLAN_NEW_RATIO_DEFAULT",
    "STRATEGY_BALANCED",
    "STRATEGY_DIAGNOSTIC_FALLBACK",
    "STRATEGY_NEW_FIRST",
    "STRATEGY_REVIEW_FIRST",
    "plan_exercises",
]

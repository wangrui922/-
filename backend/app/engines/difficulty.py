# -*- coding: utf-8 -*-
"""难度引擎（V6.5 Sprint 6 · T05）：决定「下一批题该出多难」。

纯函数铁律
----------
* **零 ORM**：不 import ``sqlalchemy``、不吃 ``Session``、不读 env / 配置文件；
* **阈值全靠注入**：所有可调参数来自调用方传入的 ``AdaptiveConfig``；
* **同输入必同输出**：无随机数、无时间戳、无全局缓存；
* **异常兜底不抛**：脏入参在 ``DifficultyInput`` 构造期就被归一化，
  引擎不会把请求打挂（真正的参数校验归 API 层负责）。

决策规则（overview，详见 ``docs/engines-design.md``）
--------------------------------------------------
R0 总开关关闭 → 难度带锁定为当前难度（``[cur, cur]``），原因码 ``disabled``；
R1 冷启动（练习次数 < ``difficulty_cold_start_samples``）→ 以当前难度为中心，
   取较宽的探索带 ``difficulty_cold_start_width``，原因码 ``cold_start``；
R2 正常路径——
    ``anchor = min + mastery × (max - min)``（掌握度线性映射到难度坐标）；
    ``center = anchor × (1 - inertia) + current × inertia``（向历史难度回归的惯性）；
    再叠加三项修正：错误率惩罚、近期正确率偏离、提示依赖偏置；
R3 单侧钳制：掌握度已撑得起当前难度时不下调，撑不住时不上调
    （防止掌握度已满分却被惯性拉回低难度，也防止错误率一高就越级拔高）；
R4 带宽：``半宽 = band_width + (1 - confidence) × confidence_width_gain``
    ——数据越少、置信度越低，难度带越宽（多探索）；
R5 落纸：夹紧到 ``[min, max]`` 后按两位小数输出，并映射回离散档位子集。

典型接线（服务层伪代码）
------------------------
.. code-block:: python

    from app.engines import build_adaptive_config, read_adaptive_config
    from app.engines.difficulty import decide_difficulty, difficulty_input_from_dna

    config = read_adaptive_config(db)                                  # 脏边界，只调一次
    dna = get_learning_dna(db, student_id=sid)                         # 既有 DNA 服务
    current = get_current_difficulty(db, student_id=sid)               # 既有自适应服务
    payload = difficulty_input_from_dna(dna, current_difficulty=current)
    band = decide_difficulty(payload, config).to_api()                 # → camelCase dict
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.engines.config import AdaptiveConfig, DEFAULT_ADAPTIVE_CONFIG
from app.engines.types import (
    DEFAULT_DIFFICULTY,
    HINT_DEPENDENCY_HIGH,
    HINT_DEPENDENCY_LOW,
    DifficultyBand,
    DifficultyInput,
    clamp,
    round2,
    snap_levels,
    to_float,
)

# ============================================================ 原因码
#: 冷启动：样本不足，按探索带出题
REASON_COLD_START: str = "cold_start"
#: 自适应未开启：锁定当前难度
REASON_DISABLED: str = "disabled"
#: 维持：目标难度与当前难度基本持平
REASON_STEADY: str = "steady"
#: 上推：掌握度够好，可以往上出
REASON_PUSH_UP: str = "push_up"
#: 下拉：撑不住当前难度，往下出
REASON_PULL_DOWN: str = "pull_down"

#: 参与近期正确率统计的事件类型（与 ``models.dna`` 事件枚举一致）
CORRECT_EVENT_TYPE: str = "CORRECT"
ERROR_EVENT_TYPE: str = "ERROR"

#: ``difficulty_input_from_dna`` 里掌握度行的取整精度（排序去噪用）
_MASTERY_SORT_DIGITS: int = 4


# ============================================================ 入口（纯函数）
def decide_difficulty(
    input_obj: DifficultyInput,
    config: Optional[AdaptiveConfig] = None,
) -> DifficultyBand:
    """算出学生的目标难度带（引擎主入口，**纯函数**）。

    Args:
        input_obj: 已归一化的入参（``DifficultyInput``）。
        config: 阈值配置；``None`` 时用 ``DEFAULT_ADAPTIVE_CONFIG``。

    Returns:
        ``DifficultyBand``，含 ``[lo, hi]``、推荐中心 ``target``、落带档位
        ``levels``、原因码 ``reason``、是否冷启动 ``cold_start``。
    """
    settings: AdaptiveConfig = config or DEFAULT_ADAPTIVE_CONFIG
    payload: DifficultyInput = (
        input_obj if isinstance(input_obj, DifficultyInput) else DifficultyInput()
    )

    current: float = payload.current_difficulty
    lower_bound: float = settings.difficulty_min
    upper_bound: float = settings.difficulty_max

    # ---- R0 总开关：任一层级关闭 → 锁定当前难度 ----
    if not (settings.enabled and settings.difficulty_enabled):
        pinned = clamp(current, lower_bound, upper_bound)
        return DifficultyBand(
            lo=pinned,
            hi=pinned,
            target=pinned,
            levels=snap_levels(pinned, pinned),
            reason=REASON_DISABLED,
            cold_start=False,
            confidence=payload.confidence,
        )

    # ---- R1 冷启动 ----
    cold_start: bool = payload.practice_count < settings.difficulty_cold_start_samples
    if cold_start:
        center = clamp(current, lower_bound, upper_bound)
        half_width: float = settings.difficulty_cold_start_width
        reason: str = REASON_COLD_START
    else:
        # ---- R2 正常路径 ----
        anchor: float = _mastery_anchor(payload.mastery, lower_bound, upper_bound)
        damped = anchor * (1.0 - settings.difficulty_inertia)
        center = damped + current * settings.difficulty_inertia
        center -= payload.error_rate * settings.difficulty_error_rate_penalty
        if payload.recent_accuracy is not None:
            center += (
                payload.recent_accuracy - settings.difficulty_accuracy_target
            ) * settings.difficulty_accuracy_gain
        center += _hint_offset(payload.hint_dependency, settings)

        # ---- R3 单侧钳制 ----
        if anchor >= current:
            center = max(center, current)
        else:
            center = min(center, current)

        center = clamp(center, lower_bound, upper_bound)
        half_width = settings.difficulty_band_width + (1.0 - payload.confidence) * (
            settings.difficulty_confidence_width_gain
        )
        reason = _reason_of(center - current, settings.difficulty_reason_shift)

    target: float = clamp(center, lower_bound, upper_bound)
    lo: float = round2(clamp(target - half_width, lower_bound, upper_bound))
    hi: float = round2(clamp(target + half_width, lower_bound, upper_bound))

    return DifficultyBand(
        lo=lo,
        hi=hi,
        target=round2(target),
        levels=snap_levels(lo, hi),
        reason=reason,
        cold_start=cold_start,
        confidence=round2(payload.confidence),
    )


def decide_difficulty_payload(
    input_obj: DifficultyInput,
    config: Optional[AdaptiveConfig] = None,
) -> Dict[str, Any]:
    """``decide_difficulty`` 的 dict 出口（camelCase，可直接作为 API ``data``）。"""
    return decide_difficulty(input_obj, config).to_api()


# ============================================================ 内部计算
def _mastery_anchor(mastery: float, lower_bound: float, upper_bound: float) -> float:
    """掌握度 → 难度坐标锚点（线性映射 ``min + mastery × (max - min)``）。"""
    safe_mastery = clamp(mastery, 0.0, 1.0)
    span = upper_bound - lower_bound
    return lower_bound + safe_mastery * span


def _hint_offset(hint_dependency: str, settings: AdaptiveConfig) -> float:
    """提示依赖带来的难度偏置：依赖强 → 降难度，几乎不依赖 → 加一点难度。"""
    if hint_dependency == HINT_DEPENDENCY_HIGH:
        return settings.difficulty_hint_high_offset
    if hint_dependency == HINT_DEPENDENCY_LOW:
        return settings.difficulty_hint_low_offset
    return 0.0


def _reason_of(shift: float, threshold: float) -> str:
    """按中心相对当前难度的位移给出原因码。"""
    if shift >= threshold:
        return REASON_PUSH_UP
    if shift <= -threshold:
        return REASON_PULL_DOWN
    return REASON_STEADY


# ============================================================ DNA 适配（仍是纯函数）
def _recent_accuracy(events: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """由最近事件流算近期正确率 ``CORRECT / (CORRECT + ERROR)``。

    Args:
        events: ``get_learning_dna()`` 的 ``recent_events`` 列表。

    Returns:
        0~1 的正确率；窗口里没有任何判对错事件时返回 ``None``（不误导引擎）。
    """
    correct = 0
    judged = 0
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = event.get("event_type")
        if event_type == CORRECT_EVENT_TYPE:
            correct += 1
            judged += 1
        elif event_type == ERROR_EVENT_TYPE:
            judged += 1
    if judged <= 0:
        return None
    return correct / judged


def _pick_mastery_row(
    rows: Sequence[Mapping[str, Any]],
    knowledge_point_id: Optional[str] = None,
) -> Optional[Mapping[str, Any]]:
    """挑选参与计算的掌握度行。

    Args:
        rows: ``get_learning_dna().mastery_summary`` 的行列表。
        knowledge_point_id: 指定知识点；为空则自动取**最薄弱**的一行
            （掌握度升序 → 置信度升序 → 练习次数升序）。

    Returns:
        命中的行字典；没有可用行时返回 ``None``。
    """
    usable: List[Mapping[str, Any]] = [row for row in rows if isinstance(row, Mapping)]
    if not usable:
        return None

    if knowledge_point_id:
        target = str(knowledge_point_id)
        for row in usable:
            if str(row.get("knowledge_point_id") or "") == target:
                return row
        return None

    def sort_key(row: Mapping[str, Any]) -> Tuple[float, float, int]:
        """薄弱优先：掌握度低、置信度低、练得少的排前面。"""
        return (
            round(to_float(row.get("mastery"), 0.0), _MASTERY_SORT_DIGITS),
            round(to_float(row.get("confidence"), 0.0), _MASTERY_SORT_DIGITS),
            int(to_float(row.get("practice_count"), 0.0)),
        )

    return min(usable, key=sort_key)


def difficulty_input_from_dna(
    dna: Optional[Mapping[str, Any]],
    *,
    knowledge_point_id: Optional[str] = None,
    current_difficulty: float = DEFAULT_DIFFICULTY,
) -> DifficultyInput:
    """把 ``student_dna_service.get_learning_dna()`` 的结果适配成引擎入参。

    **纯函数**：只对 dict 做读取与筛选，不碰 DB；``dna`` 缺失或字段缺失时
    产出全零入参（走冷启动分支），不抛异常。

    Args:
        dna: ``get_learning_dna`` 返回的字典（``student_id`` / ``profile`` /
            ``mastery_summary`` / ``recent_events``）。
        knowledge_point_id: 指定知识点；为空则自动锁定最薄弱知识点。
        current_difficulty: 学生当前主难度（来自 ``adaptive_service``）。

    Returns:
        ``DifficultyInput``；无数据时为全零入参 + ``current_difficulty``。
    """
    source: Mapping[str, Any] = dna if isinstance(dna, Mapping) else {}

    profile = source.get("profile")
    profile_map: Mapping[str, Any] = profile if isinstance(profile, Mapping) else {}

    rows_raw = source.get("mastery_summary")
    rows: Sequence[Mapping[str, Any]] = (
        tuple(row for row in rows_raw if isinstance(row, Mapping))
        if isinstance(rows_raw, (list, tuple))
        else ()
    )

    events_raw = source.get("recent_events")
    events: Sequence[Mapping[str, Any]] = (
        tuple(event for event in events_raw if isinstance(event, Mapping))
        if isinstance(events_raw, (list, tuple))
        else ()
    )

    row = _pick_mastery_row(rows, knowledge_point_id)
    row_map: Mapping[str, Any] = row if isinstance(row, Mapping) else {}

    kp_value = row_map.get("knowledge_point_id")
    return DifficultyInput(
        mastery=to_float(row_map.get("mastery"), 0.0),
        confidence=to_float(row_map.get("confidence"), 0.0),
        practice_count=int(to_float(row_map.get("practice_count"), 0.0)),
        error_rate=to_float(row_map.get("error_rate"), 0.0),
        hint_dependency=profile_map.get("hint_dependency"),
        current_difficulty=current_difficulty,
        recent_accuracy=_recent_accuracy(events),
        student_id=source.get("student_id") or "",
        knowledge_point_id=None if kp_value is None else str(kp_value),
    )


__all__ = [
    "REASON_COLD_START",
    "REASON_DISABLED",
    "REASON_STEADY",
    "REASON_PUSH_UP",
    "REASON_PULL_DOWN",
    "CORRECT_EVENT_TYPE",
    "ERROR_EVENT_TYPE",
    "decide_difficulty",
    "decide_difficulty_payload",
    "difficulty_input_from_dna",
]

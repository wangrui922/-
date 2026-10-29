"""六维掌握度评分（纯函数引擎，C02 · Phase 1）。

硬约束（与 ``mastery_engine`` 同源）：
    * 本模块**不 import 任何 ORM / Session / FastAPI**——只吃 dataclass 吐
      dataclass，可被 ``tests/engines/test_mastery_sixdim.py`` 纯单测覆盖；
    * 阈值经 ``app.engines.config.MasteryDimConfig`` 注入（后台可调），不写死；
    * 不调用 LLM——D1/D2 错因折扣走 ``app.core.error_categories`` 的五类枚举。

六维口径（与 ``docs/design-c02-three-layer-student-model.md`` §3 对齐）：
    * D0 mastery     整体掌握度，复用 ``mastery_engine.compute_mastery`` 贝叶斯平滑；
    * D1 concept     概念理解：错因命中 ``concept_deficit``(KNOWLEDGE) 给 0 信用，
                      其余错误给 ``slip_credit`` 半分，正确给满分；
    * D2 procedure   程序执行：错因命中 ``procedure_deficits``(CHECK/CALC) 给 0 信用，
                      其余错误给半分，正确给满分；
    * D3 independent 独立完成：仅统计 ``independent`` 标记事件的正确率；
    * D4 transfer    迁移应用：仅统计 ``transfer`` 标记事件的正确率；
    * D5 retention   保持留存：仅统计复习召回事件的正确率（来自 FSRS review_logs）。

样本不足降级：某维有效样本（衰减加权）< ``min_samples`` 时该维返回 ``None``，
前端按「暂无数据」展示，不编造 0（与 ``grade_level`` 的样本不足降级同语义）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

from app.core.constants import MASTERY_CLAMP_HIGH, MASTERY_CLAMP_LOW
from app.core.error_categories import ERROR_CALC, ERROR_CHECK, ERROR_KNOWLEDGE
from app.engines.config import DEFAULT_MASTERY_DIM_CONFIG, MasteryDimConfig
from app.services.mastery_engine import AttemptEvent, as_utc, compute_mastery, decay_weight

_SECONDS_PER_DAY: float = 86400.0


# ==================================================================== 数据结构
@dataclass(frozen=True)
class SixDimEvent:
    """一条用于六维评分的练习事件（服务层由 ``student_learning_events`` +
    ``error_categories`` 分类 + 埋点 meta 组装而成）。

    Attributes:
        kp_id: 知识点 ID（字符串形态，引擎层不关心 UUID）。
        is_correct: 本次是否判对。
        occurred_at: 发生时间（UTC）。
        weight: 事件源可信度权重（缺省 1.0）。
        error_type: 错因机器码（``ERROR_CATEGORIES`` 之一），正确事件为 ``None``。
        independent: 是否「无提示 / 未看答案 / 非引导」独立做对。
        transfer: 是否迁移 / 跨情境题。
    """

    kp_id: str
    is_correct: bool
    occurred_at: datetime
    weight: float = 1.0
    error_type: Optional[str] = None
    independent: bool = False
    transfer: bool = False


@dataclass(frozen=True)
class RetentionEvent:
    """一条复习召回事件（来自 FSRS ``review_logs`` 抽取）。

    Attributes:
        kp_id: 知识点 ID。
        recalled_correctly: 本次复习是否回忆正确（决定留存信用）。
        occurred_at: 复习发生时间（UTC）。
        weight: 权重（缺省 1.0）。
    """

    kp_id: str
    recalled_correctly: bool
    occurred_at: datetime
    weight: float = 1.0


@dataclass(frozen=True)
class SixDimMastery:
    """单个知识点的六维掌握度结果。

    Attributes:
        kp_id: 知识点 ID。
        mastery: D0 整体掌握度 0~1。
        concept: D1 概念理解（样本不足为 ``None``）。
        procedure: D2 程序执行（样本不足为 ``None``）。
        independent: D3 独立完成（无标记事件为 ``None``）。
        transfer: D4 迁移应用（无标记事件为 ``None``）。
        retention: D5 保持留存（无复习事件为 ``None``）。
        samples: 该知识点总练习次数（D0 口径，供展示 / 降级判定）。
    """

    kp_id: str
    mastery: float = 0.0
    concept: Optional[float] = None
    procedure: Optional[float] = None
    independent: Optional[float] = None
    transfer: Optional[float] = None
    retention: Optional[float] = None
    samples: int = 0


# ==================================================================== 时间工具
#: 子分钟级时间差视为「同一时刻」（防止 now 比事件晚几毫秒导致衰减略小于 1，
#: 进而使 N 个事件的有效样本浮点略小于 N、恰好踩中 min_samples 阈值被误降级）。
_ONE_MINUTE_IN_DAYS: float = 1.0 / (24.0 * 60.0)


def _days_between(later: datetime, earlier: datetime) -> float:
    """``later - earlier`` 的天数差（负数 / 子分钟级差截断为 0）。

    事件发生在 ``now`` 之后（时钟抖动）或几乎同时（差 < 1 分钟）时，物理上
    应视为「新鲜」，衰减取 1.0，而不是 0.99999998 这种会被阈值误伤的极小值。
    """
    left = as_utc(later)
    right = as_utc(earlier)
    if left is None or right is None:
        return 0.0
    delta = (left - right).total_seconds() / _SECONDS_PER_DAY
    if delta < _ONE_MINUTE_IN_DAYS:
        return 0.0
    return delta


# ==================================================================== 平滑内核
def _smooth(
    events: Sequence[Any],
    credit_of,
    now: datetime,
    *,
    prior: float,
    k: float,
    half_life: float,
) -> tuple[float, float]:
    """贝叶斯式平滑：``score = (prior*k + Σ credit*decay) / (k + Σ decay)``。

    Args:
        events: 已筛选的事件序列（任一带 ``occurred_at`` / ``weight`` 的对象）。
        credit_of: 取事件信用分的函数（正确 1.0 / 硬伤 0 / 其余 slip）。
        now: 计算基准时刻（UTC aware）。
        prior / k / half_life: 平滑参数（来自 ``MasteryDimConfig``）。

    Returns:
        ``(score, effective_weight)``；有效样本为 0 时返回 ``(None, 0.0)``。
    """
    effective: float = 0.0
    accumulated: float = 0.0
    for ev in events:
        occurred = as_utc(ev.occurred_at) or now
        weight = float(ev.weight) if ev.weight and ev.weight > 0 else 1.0
        decay = decay_weight(_days_between(now, occurred), half_life)
        d = decay * weight
        effective += d
        accumulated += float(credit_of(ev)) * d
    if effective <= 0.0:
        return None, 0.0
    score = (prior * k + accumulated) / (k + effective)
    return min(MASTERY_CLAMP_HIGH, max(MASTERY_CLAMP_LOW, score)), effective


def _concept_credit(ev: SixDimEvent, cfg: MasteryDimConfig) -> float:
    """D1 信用：正确满分；命中概念硬伤 0；其余错误给半分。"""
    if ev.is_correct:
        return 1.0
    if ev.error_type == cfg.concept_deficit:
        return 0.0
    return float(cfg.slip_credit)


def _procedure_credit(ev: SixDimEvent, cfg: MasteryDimConfig) -> float:
    """D2 信用：正确满分；命中程序硬伤(CHECK/CALC) 0；其余错误给半分。"""
    if ev.is_correct:
        return 1.0
    if ev.error_type in cfg.procedure_deficits:
        return 0.0
    return float(cfg.slip_credit)


# ==================================================================== 单点计算
def _to_attempt(ev: SixDimEvent) -> AttemptEvent:
    """把 ``SixDimEvent`` 投影成 ``mastery_engine.AttemptEvent``（D0 复用既有引擎）。"""
    return AttemptEvent(
        kp_id=ev.kp_id,
        is_correct=ev.is_correct,
        occurred_at=ev.occurred_at,
        weight=ev.weight,
    )


def _six_dim_one(
    kp_id: str,
    events: Sequence[SixDimEvent],
    retention: Optional[Sequence[RetentionEvent]],
    now: datetime,
    cfg: MasteryDimConfig,
) -> SixDimMastery:
    """对单个知识点算六维（事件已按 kp_id 分组）。"""
    # D0 整体（复用 mastery_engine.compute_mastery 的贝叶斯平滑与 level 口径）
    d0 = compute_mastery(
        [_to_attempt(e) for e in events],
        now=now,
        prior_score=cfg.prior,
        confidence_k=cfg.k,
        half_life_days=cfg.half_life,
    )

    # D1 概念理解
    c_score, c_eff = _smooth(
        events, lambda e: _concept_credit(e, cfg), now,
        prior=cfg.prior, k=cfg.k, half_life=cfg.half_life,
    )
    d1 = c_score if c_eff >= cfg.min_samples else None

    # D2 程序执行
    p_score, p_eff = _smooth(
        events, lambda e: _procedure_credit(e, cfg), now,
        prior=cfg.prior, k=cfg.k, half_life=cfg.half_life,
    )
    d2 = p_score if p_eff >= cfg.min_samples else None

    # D3 独立完成（仅 independent 标记事件）
    ind_events = [e for e in events if e.independent]
    i_score, i_eff = _smooth(
        ind_events, lambda e: 1.0 if e.is_correct else 0.0, now,
        prior=cfg.prior, k=cfg.k, half_life=cfg.half_life,
    )
    d3 = i_score if i_eff >= cfg.min_samples else None

    # D4 迁移应用（仅 transfer 标记事件）
    tr_events = [e for e in events if e.transfer]
    t_score, t_eff = _smooth(
        tr_events, lambda e: 1.0 if e.is_correct else 0.0, now,
        prior=cfg.prior, k=cfg.k, half_life=cfg.half_life,
    )
    d4 = t_score if t_eff >= cfg.min_samples else None

    # D5 保持留存（仅复习召回事件）
    ret_events = [r for r in (retention or [])]
    r_score, r_eff = _smooth(
        ret_events, lambda r: 1.0 if r.recalled_correctly else 0.0, now,
        prior=cfg.prior, k=cfg.k, half_life=cfg.half_life,
    )
    d5 = r_score if r_eff >= cfg.min_samples else None

    return SixDimMastery(
        kp_id=kp_id,
        mastery=round(float(d0.score), 4),
        concept=round(d1, 4) if d1 is not None else None,
        procedure=round(d2, 4) if d2 is not None else None,
        independent=round(d3, 4) if d3 is not None else None,
        transfer=round(d4, 4) if d4 is not None else None,
        retention=round(d5, 4) if d5 is not None else None,
        samples=int(d0.attempts),
    )


# ==================================================================== 入口
def six_dim_mastery(
    kp_id: str,
    events: Sequence[SixDimEvent],
    *,
    retention: Optional[Sequence[RetentionEvent]] = None,
    now: Optional[datetime] = None,
    config: Optional[MasteryDimConfig] = None,
) -> SixDimMastery:
    """对一个知识点算六维掌握度（允许 ``events`` 混入多个知识点，内部按 kp_id 过滤）。

    时间统一按 UTC 处理；``now`` 缺省取当前 UTC（测试务必注入固定值）。
    """
    cfg = config or DEFAULT_MASTERY_DIM_CONFIG
    base_now = as_utc(now) or datetime.now(timezone.utc)
    kp_events = [e for e in events if e.kp_id == kp_id]
    kp_retention = [r for r in (retention or []) if r.kp_id == kp_id]
    return _six_dim_one(kp_id, kp_events, kp_retention, base_now, cfg)


def six_dim_mastery_for_student(
    events: Sequence[SixDimEvent],
    *,
    retention: Optional[Sequence[RetentionEvent]] = None,
    now: Optional[datetime] = None,
    config: Optional[MasteryDimConfig] = None,
) -> Dict[str, SixDimMastery]:
    """对一名学生的全部知识点算六维（按 kp_id 分组，返回 ``{kp_id: SixDimMastery}``）。"""
    cfg = config or DEFAULT_MASTERY_DIM_CONFIG
    base_now = as_utc(now) or datetime.now(timezone.utc)
    by_kp: Dict[str, list[SixDimEvent]] = {}
    for ev in events:
        by_kp.setdefault(ev.kp_id, []).append(ev)
    ret_by_kp: Dict[str, list[RetentionEvent]] = {}
    for r in (retention or []):
        ret_by_kp.setdefault(r.kp_id, []).append(r)
    return {
        kp_id: _six_dim_one(kp_id, evs, ret_by_kp.get(kp_id), base_now, cfg)
        for kp_id, evs in by_kp.items()
    }


__all__ = [
    "SixDimEvent",
    "RetentionEvent",
    "SixDimMastery",
    "six_dim_mastery",
    "six_dim_mastery_for_student",
]

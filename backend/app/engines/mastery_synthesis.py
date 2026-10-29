"""L3 综合洞察层纯函数引擎（C02 · Phase 3）。

硬约束（与 ``mastery_engine`` / ``mastery_sixdim`` 同源）：
    * 本模块**不 import 任何 ORM / Session / FastAPI**——只吃 dataclass 吐
      dataclass，可被 ``tests/engines/test_mastery_synthesis.py`` 纯单测覆盖；
    * 复用 ``mastery_engine`` 的 ``aggregate_error_patterns`` / ``detect_prerequisite_gaps``
      / ``compute_priority`` / ``compute_mastery``，不重复造轮子；
    * 缺口线复用常量 ``MASTERY_GAP_THRESHOLD``（后台可调阈值经 ``adaptive_config``
      注入的口径在本 Phase 仅复用既有常量，新增阈值键留待 Phase 4 校准）；
    * 不调用 LLM——L3 走纯规则聚合（错因聚类 / 前置缺口 / 趋势斜率），保证可检视、
      可回滚（设计文档 §3/§5）。

三种综合类型（与设计文档 ``synthesis_type`` 枚举、迁移 DDL CHECK 约束一致）：
    * ``ERROR_PATTERN`` 错因模式聚类（复用 aggregate_error_patterns）；
    * ``WEAK_CHAIN``    薄弱链路（复用 detect_prerequisite_gaps）；
    * ``TREND``         学习趋势（事件按时窗线性斜率）。

溯源边：每个 ``SynthesisResult`` 都带 ``ref_kp_ids`` / ``ref_event_ids``，
服务层落库时据此做「溯源边缺失即拒写」守卫（铁律 7），防止编造无依据的 L3 结论。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.core.constants import MASTERY_GAP_THRESHOLD
from app.core.error_categories import (
    ERROR_UNCLASSIFIED,
    error_label,
    normalize_error,
)
from app.services.mastery_engine import (
    AttemptEvent,
    ErrorEvidence,
    MasteryResult,
    PrerequisiteEdge,
    aggregate_error_patterns,
    as_utc,
    compute_mastery,
    compute_priority,
    detect_prerequisite_gaps,
    top_error_category,
)


# 综合类型枚举（与迁移 DDL CHECK 约束、设计文档 §3 一致）
SYNTHESIS_ERROR_PATTERN: str = "ERROR_PATTERN"
SYNTHESIS_WEAK_CHAIN: str = "WEAK_CHAIN"
SYNTHESIS_TREND: str = "TREND"
SYNTHESIS_TYPES: tuple = (SYNTHESIS_ERROR_PATTERN, SYNTHESIS_WEAK_CHAIN, SYNTHESIS_TREND)

#: 置信度随样本量增长系数（与 update_mastery_from_event 的 confidence 同形：
#: 1 次 0.5，3 次 0.875，10 次及以上 ≈ 1）。
_CONFIDENCE_K: int = 10


# ==================================================================== 数据结构
@dataclass(frozen=True)
class SynthesisEvent:
    """一条用于 L3 综合的学习事件（服务层由 ``student_learning_events`` 组装）。

    Attributes:
        event_id: 原始事件 UUID 字符串（L1 溯源边）。
        event_type: 八类事件枚举之一（用于判定 ERROR / CORRECT / 其余）。
        kp_id: 关联知识点 ID（字符串；可为 None，表示无知识点归因）。
        occurred_at: 发生时间（UTC）。
        is_correct: 仅 CORRECT/ERROR 事件有判定意义（True/False），其余为 None。
        error_text: 错误文本（错因证据，可为 None）。
        error_type: 错因机器码（可为 None）。
        independent / transfer: 派生维度标记（本 Phase 暂未进入综合，预留）。
    """

    event_id: str
    event_type: str
    kp_id: Optional[str]
    occurred_at: datetime
    is_correct: Optional[bool] = None
    error_text: Optional[str] = None
    error_type: Optional[str] = None
    independent: bool = False
    transfer: bool = False


@dataclass(frozen=True)
class SynthesisResult:
    """一条 L3 综合结论（落库前的中间态）。

    Attributes:
        synthesis_type: 见 ``SYNTHESIS_TYPES``。
        ref_kp_ids: 本结论综合了哪些 L2 知识点（服务层 json.dumps 存 TEXT）。
        ref_event_ids: 本结论消费了哪些 L1 事件（服务层 json.dumps 存 TEXT）。
        payload: 综合结论（dict，服务层 json.dumps 存 TEXT）。
        confidence: 0~1 置信度，随样本量增长。
    """

    synthesis_type: str
    ref_kp_ids: List[str]
    ref_event_ids: List[str]
    payload: Dict[str, Any]
    confidence: float


# ==================================================================== 小工具
def _confidence_from_samples(n: int) -> float:
    """置信度随有效样本量增长：``1 - 0.5 ** min(n, K)``。"""
    return round(min(1.0, 1.0 - 0.5 ** min(max(0, int(n)), _CONFIDENCE_K)), 4)


def mastery_map_from_events(
    events: Sequence[SynthesisEvent],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, MasteryResult]:
    """把事件按知识点聚合成 ``{kp_id: MasteryResult}``（供薄弱 KP 排序 / 兜底用）。

    kp_id 为 None 的事件被忽略（无法归因到具体知识点）。复用 ``compute_mastery``
    的贝叶斯平滑口径，保证与 L2 展示档位同源。

    ``now`` 必须与调用方的综合基准时刻一致（否则时间衰减用的是墙上时钟，
    与同一批综合结论里的其它口径打架，且不可复现）。
    """
    base_now = as_utc(now) or datetime.now(timezone.utc)
    by_kp: Dict[str, List[AttemptEvent]] = {}
    for ev in events:
        if not ev.kp_id:
            continue
        by_kp.setdefault(ev.kp_id, []).append(
            AttemptEvent(
                kp_id=ev.kp_id,
                is_correct=bool(ev.is_correct),
                occurred_at=ev.occurred_at,
                weight=1.0,
            )
        )
    return {kp_id: compute_mastery(evs, now=base_now) for kp_id, evs in by_kp.items()}


def _week_index(now: datetime, occurred: datetime, weeks: int) -> Optional[int]:
    """事件落在「最近 weeks 周内」的索引（0=最早周，weeks-1=最新周）；
    超出窗口（太旧 / 未来）返回 None。"""
    delta_days = (as_utc(now) - as_utc(occurred)).total_seconds() / 86400.0
    if delta_days < 0:
        return None
    idx = weeks - 1 - int(delta_days // 7)
    return idx if 0 <= idx < weeks else None


def _linear_slope(series: Sequence[float]) -> float:
    """最小二乘斜率（x = 0..n-1）。不足 2 点返回 0.0。"""
    n = len(series)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(series) / n
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0.0:
        return 0.0
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, series))
    return num / den


def _std(series: Sequence[float]) -> float:
    """总体标准差（不足 2 点返回 0.0）。"""
    n = len(series)
    if n < 2:
        return 0.0
    mean = sum(series) / n
    return (sum((s - mean) ** 2 for s in series) / n) ** 0.5


def _error_evidence_text(ev: SynthesisEvent) -> str:
    """把一条错误事件转成可分类的证据文本。

    ⚠️ 关键细节：错因关键词表**并不覆盖全部机器码**——``METHOD``/``CHECK``/
    ``CALC`` 因含英文关键词恰好能命中，但 ``KNOWLEDGE``/``READ`` 直接丢进去
    会被判成 UNCLASSIFIED 丢弃。因此机器码必须先过 ``normalize_error`` 归一，
    再取中文标签（标签里的「知识点」「读题」等词才是关键词表真正认的东西）。
    """
    text = (ev.error_text or "").strip()
    if text:
        return text
    if ev.error_type:
        code = normalize_error(ev.error_type)
        if code and code != ERROR_UNCLASSIFIED:
            return error_label(code)
    return "作答错误"


# ==================================================================== ERROR_PATTERN
def synthesize_error_patterns(
    events: Sequence[SynthesisEvent],
    *,
    now: Optional[datetime] = None,
) -> SynthesisResult:
    """错因模式聚类：复用 ``aggregate_error_patterns``，挑 Top3 薄弱 KP。

    数据缺口诚实标注：``error_text`` / ``error_type`` 当前多来自错题本
    ``Mistake.error_type``，练习事件 meta 大多不带 → 大量错误会被归为
    UNCLASSIFIED（不计入五类分布）。但本函数仍基于「每知识点正确率 × 样本量」
    用 ``compute_priority`` 选出 Top3 薄弱 KP，**保证 ERROR_PATTERN 即使分类为空
    也能产出有价值的 payload**（不编造错因）。
    """
    _base = as_utc(now) or datetime.now(timezone.utc)
    error_events = [e for e in events if e.event_type == "ERROR"]
    evidences = [
        ErrorEvidence(
            text=_error_evidence_text(e),
            occurred_at=e.occurred_at,
            kp_id=e.kp_id,
        )
        for e in error_events
    ]
    patterns = aggregate_error_patterns(evidences)

    # 每知识点 MasteryResult → 优先级口径选 Top3 薄弱 KP
    mastery = mastery_map_from_events(events, now=_base)
    err_per_kp: Dict[str, int] = {}
    for e in error_events:
        if e.kp_id:
            key = str(e.kp_id)
            err_per_kp[key] = err_per_kp.get(key, 0) + 1

    weak = sorted(
        (
            (kp_id, res, compute_priority(res))
            for kp_id, res in mastery.items()
            if int(res.attempts) > 0
        ),
        key=lambda item: -item[2],
    )
    top_weak = [
        {
            "kp_id": kp_id,
            "score": round(float(res.score), 4),
            "level": int(res.level),
            "priority": prio,
            "error_count": int(err_per_kp.get(kp_id, 0)),
        }
        for kp_id, res, prio in weak[:3]
    ]

    category_counts = {p.category: p.count for p in patterns}
    payload = {
        "dominant_category": top_error_category(category_counts),
        "patterns": [
            {
                "category": p.category,
                "label": p.label,
                "count": p.count,
                "ratio": p.ratio,
                "sample_hits": list(p.sample_hits),
            }
            for p in patterns
        ],
        "top_weak_kps": top_weak,
        "total_errors": len(error_events),
        "total_events": len(events),
    }

    ref_kp_ids = sorted({str(e.kp_id) for e in error_events if e.kp_id})
    ref_event_ids = [str(e.event_id) for e in error_events]
    confidence = _confidence_from_samples(len(error_events))
    return SynthesisResult(
        synthesis_type=SYNTHESIS_ERROR_PATTERN,
        ref_kp_ids=ref_kp_ids,
        ref_event_ids=ref_event_ids,
        payload=payload,
        confidence=confidence,
    )


# ==================================================================== WEAK_CHAIN
def synthesize_weak_chains(
    mastery: Mapping[str, MasteryResult],
    edges: Sequence[PrerequisiteEdge],
    *,
    threshold: float = MASTERY_GAP_THRESHOLD,
    now: Optional[datetime] = None,
) -> SynthesisResult:
    """薄弱链路：复用 ``detect_prerequisite_gaps``（GKT 前置缺口检测）。

    Args:
        mastery: ``{kp_id(str): MasteryResult}``——本生当前 L2 掌握度。
        edges: 前置依赖边（来自 ``knowledge_prerequisite``）。
        threshold: 前置缺口线（默认 ``MASTERY_GAP_THRESHOLD=0.5``）。
    """
    gaps = detect_prerequisite_gaps(mastery, edges, threshold=threshold)
    gap_payload = [
        {
            "kp_id": g.kp_id,
            "pre_kp_id": g.pre_kp_id,
            "kp_score": round(float(g.kp_score), 4),
            "pre_score": round(float(g.pre_score), 4),
            "weight": round(float(g.weight), 4),
            "missing": bool(g.missing),
        }
        for g in gaps
    ]
    # 置信度随「被考察的已练知识点数」增长（L2 证据越多，缺口结论越可信）
    practiced = sum(1 for r in mastery.values() if int(r.attempts) > 0)
    payload = {
        "gaps": gap_payload,
        "gap_count": len(gap_payload),
        "practiced_kps": practiced,
        "threshold": round(float(threshold), 4),
    }
    ref_kp_ids = sorted({g.kp_id for g in gaps} | {g.pre_kp_id for g in gaps})
    ref_event_ids: List[str] = []  # 链路由掌握度 + 图谱推导，无单事件溯源
    confidence = _confidence_from_samples(practiced)
    return SynthesisResult(
        synthesis_type=SYNTHESIS_WEAK_CHAIN,
        ref_kp_ids=ref_kp_ids,
        ref_event_ids=ref_event_ids,
        payload=payload,
        confidence=confidence,
    )


# ==================================================================== TREND
def synthesize_trend(
    events: Sequence[SynthesisEvent],
    *,
    now: Optional[datetime] = None,
    weeks: int = 4,
) -> SynthesisResult:
    """学习趋势：把判定事件按周窗聚合正确率，算线性斜率 + 稳定性。

    仅 CORRECT/ERROR（``is_correct`` 非 None）计入正确率；其余事件类型
    （HINT/REVIEW/SESSION 等）不进正确率，但计入总活跃量 ``total_events``。
    """
    base_now = as_utc(now) or datetime.now(timezone.utc)
    judged = [e for e in events if e.is_correct is not None]
    weekly: Dict[int, List[bool]] = {i: [] for i in range(weeks)}
    total_correct = 0
    in_window = 0
    for e in judged:
        idx = _week_index(base_now, e.occurred_at, weeks)
        if idx is None:
            continue
        ok = bool(e.is_correct)
        weekly[idx].append(ok)
        in_window += 1
        if ok:
            total_correct += 1

    weekly_stats: List[Dict[str, Any]] = []
    acc_series: List[float] = []
    for i in range(weeks):
        bucket = weekly[i]
        n = len(bucket)
        correct = sum(1 for c in bucket if c)
        acc = round(correct / n, 4) if n else None
        weekly_stats.append({"week_index": i, "accuracy": acc, "count": n})
        if acc is not None:
            acc_series.append(acc)

    slope = _linear_slope(acc_series) if len(acc_series) >= 2 else 0.0
    stability = round(max(0.0, 1.0 - _std(acc_series)), 4) if acc_series else 0.0
    # ⚠️ 分子分母必须同口径：**都只算窗口内**的判定事件。
    # （早期版本分子只累加窗口内、分母却用全部判定事件 → 窗口外的老错会把
    #   总体正确率压低，趋势结论与周窗数据自相矛盾。）
    overall_acc = round(total_correct / in_window, 4) if in_window > 0 else 0.0
    recent = weekly_stats[-1]["accuracy"]
    recent_acc = overall_acc if recent is None else recent

    payload = {
        "slope_per_week": round(slope, 4),
        "recent_accuracy": recent_acc,
        "overall_accuracy": overall_acc,
        "weekly": weekly_stats,
        "total_events": len(events),
        "judged_events": in_window,
        "stability": stability,
    }
    ref_kp_ids = sorted({str(e.kp_id) for e in events if e.kp_id})
    ref_event_ids = [str(e.event_id) for e in events]
    confidence = _confidence_from_samples(in_window)
    return SynthesisResult(
        synthesis_type=SYNTHESIS_TREND,
        ref_kp_ids=ref_kp_ids,
        ref_event_ids=ref_event_ids,
        payload=payload,
        confidence=confidence,
    )


# ==================================================================== 入口（编排）
def synthesize_student(
    events: Sequence[SynthesisEvent],
    mastery: Mapping[str, MasteryResult],
    edges: Sequence[PrerequisiteEdge],
    *,
    now: Optional[datetime] = None,
    threshold: float = MASTERY_GAP_THRESHOLD,
    weeks: int = 4,
) -> Dict[str, SynthesisResult]:
    """一次性产出三种综合结论，返回 ``{synthesis_type: SynthesisResult}``。

    服务层 ``consolidate_mastery`` 直接消费本函数的结果。
    """
    base_now = as_utc(now) or datetime.now(timezone.utc)
    return {
        SYNTHESIS_ERROR_PATTERN: synthesize_error_patterns(events, now=base_now),
        SYNTHESIS_WEAK_CHAIN: synthesize_weak_chains(
            mastery, edges, threshold=threshold, now=base_now
        ),
        SYNTHESIS_TREND: synthesize_trend(events, now=base_now, weeks=weeks),
    }


__all__ = [
    "SynthesisEvent",
    "SynthesisResult",
    "SYNTHESIS_ERROR_PATTERN",
    "SYNTHESIS_WEAK_CHAIN",
    "SYNTHESIS_TREND",
    "SYNTHESIS_TYPES",
    "mastery_map_from_events",
    "synthesize_error_patterns",
    "synthesize_weak_chains",
    "synthesize_trend",
    "synthesize_student",
]

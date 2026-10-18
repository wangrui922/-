"""Benchmark 维度一：教学行为指标（AI 直接给答案率 / 讲解质量）落库与聚合。

两维口径定义（每个指标一句话定义 + 计算式，评测报告与看板必须与此一致）：

1. **AI 直接给答案率**（``answer_leak_rate``）
   定义：时间窗内「学生实际收到的 AI 回复仍命中答案泄漏特征」的次数，占
   同期 AI 应答总次数的比例——衡量 AI 是否忍不住直接给孩子结论。
   计算式：``answer_leak`` trace 条数 ÷ ``ai_messages(role='assistant')``
   条数（分母取不到（窗内无任何应答）时返回 ``None``，不编造 0%）。
   检测口径：与 ``app.ai.answer_guard`` 完全一致——同一 ``has_answer_leak``、
   同一严格度（引导/开场严格，讲解/评判宽松只拦露骨宣告）；引导类模式下
   泄漏会被 guard 重生成/兜底拦截，学生面不会出现，故该模式天然为 0，
   本指标主要暴露讲解（DEMO/JUDGE）链路里露骨的「答案：x」宣告。

2. **讲解质量均分趋势**（``explanation_quality_trend``）
   定义：DEMO 讲解质量闸门每次成功评测的 5 维 rubric 综合分（overall），
   按 UTC 日期的均值走势，用于观察「教得好不好」随时间的变化。
   计算式：当日 ``explanation_quality`` trace 的 ``overall`` 均值与 5 个
   维度各自均值；裁判解析失败（``parsed_ok=False``）的评测**不落分**——
   默认 3 分不是真实评分，落进去会污染均分。

落库容错约定：本模块的 ``record_*`` 只负责写（``add`` + ``flush``，不
``commit``，由调用方事务收口）；「失败不拖垮教学主流程」的 try/except 由
调用方埋点处负责（见 ``orchestrator._emit_answer_leak_trace`` 等同款
旁路风格）。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ai import AiMessage, AiSession
from app.models.quality_trace import QualityTrace

logger = logging.getLogger("app.services.quality_metrics_service")

#: 事件类型常量（与模型 CHECK 约束一致）
EVENT_ANSWER_LEAK: str = "answer_leak"
EVENT_EXPLANATION_QUALITY: str = "explanation_quality"

#: 5 维 rubric 维度 key 的有序列表（与 explanation_quality.DIMENSION_KEYS 对齐）
DIMENSION_KEYS: List[str] = ["guidance", "correctness", "clarity", "relevance", "warmth"]

#: 默认聚合时间窗（天）
DEFAULT_WINDOW_DAYS: int = 7

#: 泄漏摘录落库的最大长度（避免 payload 膨胀）
_EXCERPT_MAX_LEN: int = 200


def _to_uuid(value: Any) -> Optional[uuid.UUID]:
    """把任意输入规整为 ``uuid.UUID``；非法/空值返回 ``None``（不抛）。"""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


# ================================================================== 落库
def record_answer_leak_trace(
    db: Session,
    *,
    student_id: Any = None,
    session_id: Any = None,
    question_id: Any = None,
    severity: str = "warning",
    payload: Optional[Dict[str, Any]] = None,
) -> QualityTrace:
    """落一条答案泄漏事件（``event_type='answer_leak'``）。

    Args:
        db: 数据库会话（只 ``add`` + ``flush``，``commit`` 由调用方收口）。
        student_id: 所属学生；``None`` = 系统级事件（列可空）。
        session_id: 关联 AI 会话；``None`` = 无会话归属。
        question_id: 关联题目；``None`` = 无题目归属（不编造）。
        severity: 严重度，默认 ``warning``（非法值收敛为 ``warning``）。
        payload: 事件上下文（建议含 ``mode`` / ``strict`` / ``excerpt``）。

    Returns:
        落库的 ``QualityTrace``（已 flush，未 commit）。
    """
    trace = QualityTrace(
        student_id=_to_uuid(student_id),
        session_id=_to_uuid(session_id),
        question_id=_to_uuid(question_id),
        event_type=EVENT_ANSWER_LEAK,
        severity=severity if severity in ("info", "warning", "error") else "warning",
        payload=payload,
    )
    db.add(trace)
    db.flush()
    return trace


def record_explanation_quality_trace(
    db: Session,
    *,
    student_id: Any = None,
    session_id: Any = None,
    question_id: Any = None,
    quality: Any = None,
    threshold: Optional[int] = None,
) -> Optional[QualityTrace]:
    """落一条讲解质量评分事件（``event_type='explanation_quality'``）。

    评测**解析失败**（``quality.parsed_ok=False``，如裁判超时 / JSON 脏）
    时返回 ``None`` 不落库：此时的 5 维分数是默认值不是真实评分，落进去
    会污染均分趋势（口径见模块 docstring）。

    Args:
        db: 数据库会话（只 ``add`` + ``flush``）。
        student_id: 所属学生；可空。
        session_id: 关联 AI 会话；可空。
        question_id: 关联题目；可空。
        quality: ``app.services.explanation_quality.ExplanationQuality``
            实例（鸭子类型：需要有 ``dimensions`` / ``overall`` /
            ``parsed_ok`` / ``suggestion`` / ``grade`` 属性）。
        threshold: 本次质量闸门阈值（综合分 1-5），写入 payload 备查。

    Returns:
        落库的 ``QualityTrace``；``quality`` 为空或解析失败时返回 ``None``。
    """
    if quality is None or not bool(getattr(quality, "parsed_ok", False)):
        return None

    dimensions = dict(getattr(quality, "dimensions", None) or {})
    # 只保留 5 维权威 key，缺维度补 3（与解析器降级口径一致）
    dims: Dict[str, int] = {
        key: int(dimensions.get(key, 3)) for key in DIMENSION_KEYS
    }
    overall = int(getattr(quality, "overall", 3))
    passed: Optional[bool] = None
    if threshold is not None:
        try:
            passed = overall >= max(1, min(5, int(threshold)))
        except (TypeError, ValueError):
            passed = None
    severity = "info" if (passed is not False) else "warning"

    payload: Dict[str, Any] = {
        "dimensions": dims,
        "overall": overall,
        "grade": getattr(quality, "grade", None),
        "threshold": threshold,
        "passed": passed,
        "suggestion": getattr(quality, "suggestion", "") or "",
        "parsed_ok": True,
    }
    trace = QualityTrace(
        student_id=_to_uuid(student_id),
        session_id=_to_uuid(session_id),
        question_id=_to_uuid(question_id),
        event_type=EVENT_EXPLANATION_QUALITY,
        severity=severity,
        payload=payload,
    )
    db.add(trace)
    db.flush()
    return trace


# ================================================================== 聚合
def _window_start(
    *, window_days: int = DEFAULT_WINDOW_DAYS, now: Optional[datetime] = None
) -> datetime:
    """时间窗起点（UTC，含）；``now`` 缺省取当前 UTC 时间。"""
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:  # 容错：朴素时间按 UTC 解释
        base = base.replace(tzinfo=timezone.utc)
    return base - timedelta(days=max(0, int(window_days)))


def answer_leak_rate(
    db: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
    student_id: Any = None,
) -> Dict[str, Any]:
    """AI 直接给答案率（口径见模块 docstring）。

    Args:
        db: 数据库会话。
        window_days: 聚合时间窗（天），默认 7。
        now: 窗口终点（UTC）；缺省当前时间。
        student_id: 可选的学生过滤（只统计该学生的会话应答与泄漏事件）。

    Returns:
        dict::

            {
                "window_days": int,
                "since": datetime,          # 窗内起点（UTC）
                "student_id": str | None,
                "answer_leak_count": int,   # 分子
                "ai_response_count": int,   # 分母（assistant 消息数）
                "rate": float | None,       # 分母为 0 → None（不编造）
            }
    """
    since = _window_start(window_days=window_days, now=now)
    sid = _to_uuid(student_id)

    # ---- 分子：窗内 answer_leak trace 条数 ----------------------------------
    leak_stmt = select(func.count()).select_from(QualityTrace).where(
        QualityTrace.event_type == EVENT_ANSWER_LEAK,
        QualityTrace.created_at >= since,
    )
    if sid is not None:
        leak_stmt = leak_stmt.where(QualityTrace.student_id == sid)
    leak_count = int(db.execute(leak_stmt).scalar_one())

    # ---- 分母：窗内 AI 应答总次数（assistant 消息）--------------------------
    resp_stmt = (
        select(func.count())
        .select_from(AiMessage)
        .join(AiSession, AiMessage.session_id == AiSession.id)
        .where(
            AiMessage.role == "assistant",
            AiMessage.created_at >= since,
        )
    )
    if sid is not None:
        resp_stmt = resp_stmt.where(AiSession.student_id == sid)
    response_count = int(db.execute(resp_stmt).scalar_one())

    rate: Optional[float] = None
    if response_count > 0:
        rate = leak_count / response_count

    return {
        "window_days": int(window_days),
        "since": since,
        "student_id": str(sid) if sid is not None else None,
        "answer_leak_count": leak_count,
        "ai_response_count": response_count,
        "rate": rate,
    }


def explanation_quality_trend(
    db: Session,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
    student_id: Any = None,
) -> Dict[str, Any]:
    """讲解质量均分趋势（按 UTC 日分桶；口径见模块 docstring）。

    数据量级小（闸门只在 DEMO 讲解开启时触发），直接取回窗内 trace 在
    Python 侧分桶，避免方言相关的日期函数差异。

    Args:
        db: 数据库会话。
        window_days: 聚合时间窗（天），默认 7。
        now: 窗内终点（UTC）；缺省当前时间。
        student_id: 可选的学生过滤。

    Returns:
        dict::

            {
                "window_days": int,
                "since": datetime,
                "student_id": str | None,
                "total_count": int,
                "buckets": [               # 按日期升序；无数据时为空列表
                    {
                        "date": "YYYY-MM-DD",
                        "count": int,
                        "overall_avg": float,
                        "dimensions": {key: float, ...},
                    },
                    ...
                ],
            }
    """
    since = _window_start(window_days=window_days, now=now)
    sid = _to_uuid(student_id)

    stmt = select(QualityTrace).where(
        QualityTrace.event_type == EVENT_EXPLANATION_QUALITY,
        QualityTrace.created_at >= since,
    )
    if sid is not None:
        stmt = stmt.where(QualityTrace.student_id == sid)
    rows: Sequence[QualityTrace] = db.execute(stmt).scalars().all()

    # ---- Python 侧按 UTC 日期分桶 ------------------------------------------
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        created = row.created_at
        if created is None:
            continue
        if created.tzinfo is None:  # 容错：朴素时间按 UTC 解释
            created = created.replace(tzinfo=timezone.utc)
        day = created.date().isoformat()
        payload = row.payload if isinstance(row.payload, dict) else {}
        grouped.setdefault(day, []).append(payload)

    buckets: List[Dict[str, Any]] = []
    for day in sorted(grouped):
        items = grouped[day]
        overalls: List[float] = []
        dim_values: Dict[str, List[float]] = {key: [] for key in DIMENSION_KEYS}
        for payload in items:
            try:
                overalls.append(float(payload.get("overall")))
            except (TypeError, ValueError):
                continue
            dims = payload.get("dimensions")
            if isinstance(dims, dict):
                for key in DIMENSION_KEYS:
                    try:
                        dim_values[key].append(float(dims.get(key)))
                    except (TypeError, ValueError):
                        continue
        bucket: Dict[str, Any] = {
            "date": day,
            "count": len(items),
            "overall_avg": round(sum(overalls) / len(overalls), 4) if overalls else None,
            "dimensions": {
                key: round(sum(values) / len(values), 4) if values else None
                for key, values in dim_values.items()
            },
        }
        buckets.append(bucket)

    return {
        "window_days": int(window_days),
        "since": since,
        "student_id": str(sid) if sid is not None else None,
        "total_count": len(rows),
        "buckets": buckets,
    }


__all__ = [
    "EVENT_ANSWER_LEAK",
    "EVENT_EXPLANATION_QUALITY",
    "DIMENSION_KEYS",
    "DEFAULT_WINDOW_DAYS",
    "record_answer_leak_trace",
    "record_explanation_quality_trace",
    "answer_leak_rate",
    "explanation_quality_trend",
]

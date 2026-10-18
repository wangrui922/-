"""学生 DNA 服务（V6.5 Sprint 2：DNA-002/003/004/005）。

职责：
    * DNA-002 ``upsert_student_profile``：画像字段写入（学习目标八类枚举
      硬校验「非法枚举拒绝」，其余字段按建议枚举/JSON 落地，写入后读回一致）；
    * DNA-003 ``update_mastery_from_event``：掌握度增量更新（消费已埋点
      事件 ANSWER/CORRECT/ERROR，upsert 到 ``student_knowledge_mastery``，
      维护 mastery/confidence/计数/时间戳；规划 M-002/M-003 规则）；
    * DNA-004 ``record_learning_event``：行为事件落库（八类枚举校验，
      practice/chat 回调埋点统一走这里，落库后同事务联动 DNA-003）；
    * DNA-005 ``get_learning_dna``：画像 + 知识点掌握汇总 + 最近事件摘要
      聚合（无数据学生返回默认值，不报错）。

事务：只 ``flush``，``commit`` 由 ``app.db.get_db`` 收口
（与 student_service.py 同一约定；联动更新不额外 commit）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.response import ApiError, CODE_BAD_REQUEST
from app.core import constants as C
from app.models.content import KnowledgePoint
from app.models.dna import (
    HINT_DEPENDENCY_VALUES,
    LEARNING_EVENT_TYPE_VALUES,
    LEARNING_GOAL_VALUES,
    StudentKnowledgeMastery,
    StudentLearningEvent,
    StudentLearningProfile,
)
from app.services.mastery_engine import grade_level

#: 最近事件摘要条数上限
RECENT_EVENTS_LIMIT: int = 10


# ================================================================== 内部工具
def _check_enum(value: str, allowed: tuple, field: str) -> None:
    """通用枚举硬校验：不在白名单内一律 400。"""
    if value not in allowed:
        allowed_text = "/".join(allowed)
        raise ApiError(CODE_BAD_REQUEST, f"{field} 非法，可选值：{allowed_text}")


def _as_list(value: Optional[Any], field: str) -> Optional[List[Any]]:
    """JSON 字段只接受 list / dict / None，其余拒绝（防止脏数据混入）。"""
    if value is None:
        return None
    if not isinstance(value, (list, dict)):
        raise ApiError(CODE_BAD_REQUEST, f"{field} 必须是数组或对象")
    return value


# ================================================================== DNA-002 画像写入
def upsert_student_profile(
    db: Session,
    *,
    student_id: uuid.UUID,
    learning_goal: Optional[str] = None,
    learning_stage: Optional[str] = None,
    current_level: Optional[str] = None,
    learning_habits: Optional[Any] = None,
    common_errors: Optional[Any] = None,
    hint_dependency: Optional[str] = None,
    learning_trend: Optional[Any] = None,
) -> Dict[str, Any]:
    """写入（upsert）学生画像字段。

    校验规则（按 V6.5 规划 DNA-002 验收）：
        * ``learning_goal`` 学习目标**必填**且必须是八类枚举，
          非法值抛 ``ApiError(400)``（DB CHECK 二次兜底）；
        * ``hint_dependency`` 提示依赖若提供必须为 high/medium/low
          （自定义建议枚举，本项目内硬校验保持一致）；
        * ``learning_stage`` / ``current_level`` 建议枚举，非空字符串即可；
        * ``learning_habits`` / ``common_errors`` / ``learning_trend``
          必须是 list 或 dict（JSON 承载）。

    Returns:
        写入后从 DB 读回的画像 dict（与入参一致，证明落库可读回）。
    """
    if learning_goal is None:
        raise ApiError(CODE_BAD_REQUEST, "学习目标（learning_goal）必填")
    _check_enum(learning_goal, LEARNING_GOAL_VALUES, "学习目标")

    if hint_dependency is not None:
        _check_enum(hint_dependency, HINT_DEPENDENCY_VALUES, "提示依赖")

    habits = _as_list(learning_habits, "学习习惯")
    errors = _as_list(common_errors, "常见错误")
    trend = _as_list(learning_trend, "学习趋势")

    profile = db.execute(
        select(StudentLearningProfile).where(
            StudentLearningProfile.student_id == student_id
        )
    ).scalar_one_or_none()

    if profile is None:
        profile = StudentLearningProfile(
            student_id=student_id,
            learning_goal=learning_goal,
        )
        db.add(profile)

    profile.learning_goal = learning_goal
    if learning_stage is not None:
        profile.learning_stage = learning_stage.strip() or None
    if current_level is not None:
        profile.current_level = current_level.strip() or None
    if habits is not None:
        profile.learning_habits = habits
    if errors is not None:
        profile.common_errors = errors
    if hint_dependency is not None:
        profile.hint_dependency = hint_dependency
    if trend is not None:
        profile.learning_trend = trend

    db.flush()
    return get_student_profile(db, student_id=student_id)


# ================================================================== DNA-002 读回
def get_student_profile(db: Session, *, student_id: uuid.UUID) -> Dict[str, Any]:
    """读回学生画像；无数据返回默认空画像（不报错）。"""
    profile = db.execute(
        select(StudentLearningProfile).where(
            StudentLearningProfile.student_id == student_id
        )
    ).scalar_one_or_none()
    if profile is None:
        return {
            "student_id": str(student_id),
            "learning_goal": None,
            "learning_stage": None,
            "current_level": None,
            "learning_habits": None,
            "common_errors": None,
            "hint_dependency": None,
            "learning_trend": None,
            "updated_at": None,
        }
    return {
        "student_id": str(profile.student_id),
        "learning_goal": profile.learning_goal,
        "learning_stage": profile.learning_stage,
        "current_level": profile.current_level,
        "learning_habits": profile.learning_habits,
        "common_errors": profile.common_errors,
        "hint_dependency": profile.hint_dependency,
        "learning_trend": profile.learning_trend,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


# ================================================================== DNA-004 事件记录
def record_learning_event(
    db: Session,
    *,
    student_id: uuid.UUID,
    event_type: str,
    question_id: Optional[uuid.UUID] = None,
    knowledge_point_id: Optional[uuid.UUID] = None,
    meta: Optional[Dict[str, Any]] = None,
    mastery_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """记录一条学习行为事件。

    事件类型八类枚举硬校验（QUESTIONVIEW/ANSWER/HINT/ERROR/CORRECT/
    REVIEW/SESSIONSTART/SESSIONEND），非法值抛 ``ApiError(400)``。

    Args:
        db: 数据库会话（只 ``flush``，``commit`` 由调用方收口）。
        student_id: 学生 ID。
        event_type: 八类事件之一。
        question_id: 关联题目（可为空）。
        knowledge_point_id: 关联知识点（可为空）。
        meta: 事件上下文，原样落库到 ``student_learning_events.meta``。
        mastery_meta: 传给 ``update_mastery_from_event`` 的 meta **视图**；
            缺省（``None``）时与 ``meta`` 相同。存在的原因：判分事件 meta 里的
            ``independent`` 是**作答级**独立标志（无提示且未看答案且非引导，
            服务于「独立完成率」指标），而 ``mastery_delta_for_event`` 把顶层
            ``independent`` 读成**迁移题标记**（+0.12 档，M-002「独立迁移题」）。
            两种语义不同，调用方（practice_service.submit_answer）用本参数
            剔除作答级标志，保住「基础题独立正确 +0.10」口径，同时事件 meta
            仍带全量字段供指标侧消费。

    Returns:
        落库事件的 dict（含 created_at，便于埋点冒烟断言）。
    """
    _check_enum(event_type, LEARNING_EVENT_TYPE_VALUES, "事件类型")

    if meta is not None and not isinstance(meta, dict):
        raise ApiError(CODE_BAD_REQUEST, "事件上下文必须为对象")

    event = StudentLearningEvent(
        student_id=student_id,
        event_type=event_type,
        question_id=question_id,
        knowledge_point_id=knowledge_point_id,
        meta=meta,
    )
    db.add(event)
    db.flush()

    # ---- DNA-003 联动：同事务内增量更新掌握表（ANSWER/CORRECT/ERROR 驱动）----
    # 只 flush、不 commit：随事件所在事务由调用方一并提交；失败不吞——
    # 让外层埋点旁路（_emit_dna_event / _emit_chat_dna_event）统一兜底。
    update_mastery_from_event(
        db,
        student_id=student_id,
        event_type=event_type,
        knowledge_point_id=knowledge_point_id,
        meta=meta if mastery_meta is None else mastery_meta,
    )

    return {
        "id": str(event.id),
        "student_id": str(event.student_id),
        "event_type": event.event_type,
        "question_id": str(event.question_id) if event.question_id else None,
        "knowledge_point_id": str(event.knowledge_point_id)
        if event.knowledge_point_id
        else None,
        "meta": event.meta,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


# ================================================================== DNA-003 掌握度更新
# 增量规则源：V6.5 规划 M-002 初始规则（0~100 制 → 表口径 0~1 制 ÷100）。
# 数值全部进 ``app.core.constants``（MASTERY_DELTA_*），此处不写死。
#: 正确事件增量档位的 meta 键（埋点可传；缺省走基础档）
META_KEY_HINT_COUNT: str = "hint_count"
META_KEY_HINT_USED: str = "hint_used"
META_KEY_TRANSFER: str = "transfer"
META_KEY_INDEPENDENT: str = "independent"


def _clamp_mastery(value: float) -> float:
    """掌握度夹紧到 [0, 1]（M-003：0≤mastery≤100 → 0≤mastery≤1）。"""
    return min(C.MASTERY_CLAMP_HIGH, max(C.MASTERY_CLAMP_LOW, float(value)))


def _bounded_delta(value: float) -> float:
    """单次跳变上限（M-003：|delta| ≤ 15 → ≤ 0.15，防一次事件大幅跳变）。"""
    return min(C.MASTERY_DELTA_MAX_ABS, max(-C.MASTERY_DELTA_MAX_ABS, float(value)))


def mastery_delta_for_event(
    event_type: str, meta: Optional[Dict[str, Any]] = None
) -> float:
    """由事件类型 + 上下文算出掌握度增量（0~1 制）。

    规则（规划 M-002）：
        * ``CORRECT`` → 基础独立正确 +0.10；若 meta 携带迁移/独立标记
          （``transfer`` / ``independent``）按独立迁移题 +0.12；
          若 ``hint_count >= 3`` 按大量提示后正确 +0.02；
          若 ``hint_used`` 或 ``hint_count >= 1`` 按提示后正确 +0.05；
        * ``ERROR`` → 核心题错误 -0.08；
        * 其余事件 → 0（不驱动掌握表）。

    Returns:
        有符号增量；单次跳变已受 ±0.15 上限约束。
    """
    meta = meta or {}
    if event_type == "CORRECT":
        if meta.get(META_KEY_TRANSFER) or meta.get(META_KEY_INDEPENDENT):
            return _bounded_delta(C.MASTERY_DELTA_CORRECT_TRANSFER)
        try:
            hint_count = int(meta.get(META_KEY_HINT_COUNT) or 0)
        except (TypeError, ValueError):
            hint_count = 0
        if hint_count >= 3:
            return _bounded_delta(C.MASTERY_DELTA_CORRECT_HINTED_HEAVY)
        if meta.get(META_KEY_HINT_USED) or hint_count >= 1:
            return _bounded_delta(C.MASTERY_DELTA_CORRECT_HINTED)
        return _bounded_delta(C.MASTERY_DELTA_CORRECT_BASE)
    if event_type == "ERROR":
        return _bounded_delta(-C.MASTERY_DELTA_WRONG_CORE)
    return 0.0


def update_mastery_from_event(
    db: Session,
    *,
    student_id: uuid.UUID,
    event_type: str,
    knowledge_point_id: Optional[uuid.UUID] = None,
    meta: Optional[Dict[str, Any]] = None,
    occurred_at: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """DNA-003 掌握度增量更新：消费一条已埋点事件，upsert 掌握表一行。

    事件→字段映射（不驱动的事件直接跳过，不建行）：
        * ``ANSWER``  → ``practice_count +1``、``last_practice_at``；
        * ``CORRECT`` → ``correct_count +1``、``last_correct_at``、
          ``mastery += mastery_delta_for_event``；
        * ``ERROR``   → ``wrong_count +1``、``last_wrong_at``、
          ``mastery += mastery_delta_for_event``（负增量）；
        * 其余五类（QUESTIONVIEW/HINT/REVIEW/SESSIONSTART/SESSIONEND）
          只留在事件流里，不改变掌握表（防刷掌握）。

    upsert 幂等：按 ``(student_id, knowledge_point_id)`` 唯一键查行，
    已存在则更新、不存在则建默认行（mastery/confidence=0、计数=0）。

    confidence：随样本量增长趋近 1，``1 - 0.5 ** min(practice_count, 10)``
    （1 次作答 0.5，3 次 0.875，10 次及以上 ≈ 1）。

    事务：只 ``flush``，``commit`` 由调用方收口（与 DNA-002/004 同约定）。

    Returns:
        更新后掌握行 dict（无 kp 或不驱动事件返回 None）。
    """
    if knowledge_point_id is None or event_type not in ("ANSWER", "CORRECT", "ERROR"):
        return None

    now = occurred_at or datetime.now(timezone.utc)
    row = db.execute(
        select(StudentKnowledgeMastery).where(
            StudentKnowledgeMastery.student_id == student_id,
            StudentKnowledgeMastery.knowledge_point_id == knowledge_point_id,
        )
    ).scalar_one_or_none()

    if row is None:
        row = StudentKnowledgeMastery(
            student_id=student_id,
            knowledge_point_id=knowledge_point_id,
        )
        db.add(row)

    if event_type == "ANSWER":
        row.practice_count = int(row.practice_count or 0) + 1
        row.last_practice_at = now
    elif event_type == "CORRECT":
        row.correct_count = int(row.correct_count or 0) + 1
        row.last_correct_at = now
    elif event_type == "ERROR":
        row.wrong_count = int(row.wrong_count or 0) + 1
        row.last_wrong_at = now

    delta = mastery_delta_for_event(event_type, meta)
    row.mastery = _clamp_mastery(float(row.mastery or 0.0) + delta)

    practice_total = int(row.practice_count or 0)
    row.confidence = round(
        1.0 - 0.5 ** min(practice_total, 10),
        4,
    )

    db.flush()
    return {
        "student_id": str(row.student_id),
        "knowledge_point_id": str(row.knowledge_point_id),
        "mastery": round(float(row.mastery), 4),
        "confidence": round(float(row.confidence), 4),
        "practice_count": int(row.practice_count),
        "correct_count": int(row.correct_count),
        "wrong_count": int(row.wrong_count),
        "last_practice_at": row.last_practice_at.isoformat()
        if row.last_practice_at
        else None,
        "last_correct_at": row.last_correct_at.isoformat()
        if row.last_correct_at
        else None,
        "last_wrong_at": row.last_wrong_at.isoformat() if row.last_wrong_at else None,
    }


# ================================================================== DNA-005 聚合
def _dna_level(row: StudentKnowledgeMastery) -> int:
    """DNA 掌握度 0~1 → 展示档位 0~3。

    复用 T04 看板同源 ``grade_level`` 的阈值与「样本不足降级」语义
    （0.40/0.60/0.85 + 有效样本 3/5 门槛），保证家长端/学生端看到的
    档位与既有看板口径一致、不产生两套规则打架；DNA 无时效衰减概念，
    以累计练习次数 ``practice_count`` 充当有效样本量输入。
    """
    attempts = int(row.practice_count)
    return grade_level(
        score=float(row.mastery),
        effective_attempts=float(attempts),
        attempts=attempts,
    )


def get_learning_dna(db: Session, *, student_id: uuid.UUID) -> Dict[str, Any]:
    """聚合学生 DNA：画像 + 知识点掌握汇总 + 最近事件摘要。

    * 无画像/无掌握/无事件均返回空默认值，**不报错**（与规划验收一致）；
    * 掌握汇总按 ``(kp_id, kp_name, mastery, level, confidence, error_rate,
      counts, last*)`` 列表返回——``kp_name`` 关联知识库，``level`` 复用
      T04 同源档位口径，便于家长端/学生端直接驱动展示；
    * 最近事件取 ``RECENT_EVENTS_LIMIT`` 条（学生 → 时间倒序）。
    """
    profile = get_student_profile(db, student_id=student_id)

    mastery_rows = (
        db.execute(
            select(StudentKnowledgeMastery).where(
                StudentKnowledgeMastery.student_id == student_id
            )
        )
        .scalars()
        .all()
    )
    # 一次查出全部关联知识点名称，避免 N+1（join 知识库，闭口表 name 必在）
    kp_ids = [row.knowledge_point_id for row in mastery_rows]
    kp_name_map: Dict[uuid.UUID, str] = {}
    if kp_ids:
        kp_rows = db.execute(
            select(KnowledgePoint.id, KnowledgePoint.name).where(
                KnowledgePoint.id.in_(kp_ids)
            )
        ).all()
        kp_name_map = {kp_id: name for kp_id, name in kp_rows}

    mastery_summary = [
        {
            "knowledge_point_id": str(row.knowledge_point_id),
            "kp_name": kp_name_map.get(row.knowledge_point_id, ""),
            "mastery": round(float(row.mastery), 4),
            "confidence": round(float(row.confidence), 4),
            # 展示档位与 T04 看板同源口径：DNA 连续值按同一 grade_level 映射，
            # 避免两套计算规则造成同一知识点档位打架（M4 一致）
            "level": _dna_level(row),
            # 错误率直接给出，前端无需再算（练习数为 0 时为 0）
            "error_rate": round(float(row.wrong_count) / float(row.practice_count), 4)
            if row.practice_count > 0
            else 0.0,
            "practice_count": int(row.practice_count),
            "correct_count": int(row.correct_count),
            "wrong_count": int(row.wrong_count),
            "last_practice_at": row.last_practice_at.isoformat()
            if row.last_practice_at
            else None,
            "last_correct_at": row.last_correct_at.isoformat()
            if row.last_correct_at
            else None,
            "last_wrong_at": row.last_wrong_at.isoformat()
            if row.last_wrong_at
            else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in mastery_rows
    ]

    event_rows = (
        db.execute(
            select(StudentLearningEvent)
            .where(StudentLearningEvent.student_id == student_id)
            .order_by(StudentLearningEvent.created_at.desc())
            .limit(RECENT_EVENTS_LIMIT)
        )
        .scalars()
        .all()
    )
    recent_events = [
        {
            "id": str(ev.id),
            "event_type": ev.event_type,
            "question_id": str(ev.question_id) if ev.question_id else None,
            "knowledge_point_id": str(ev.knowledge_point_id)
            if ev.knowledge_point_id
            else None,
            "meta": ev.meta,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
        }
        for ev in event_rows
    ]

    return {
        "student_id": str(student_id),
        "profile": profile,
        "mastery_summary": mastery_summary,
        "recent_events": recent_events,
    }


__all__ = [
    "RECENT_EVENTS_LIMIT",
    "get_student_profile",
    "upsert_student_profile",
    "record_learning_event",
    "mastery_delta_for_event",
    "update_mastery_from_event",
    "get_learning_dna",
]

"""家长-孩子绑定服务（V4.2 商业核心：家长付费、学生使用）。

职责：
    * 家长绑定 / 解绑 / 列出孩子；
    * 家长视角越权校验（仅可访问自己绑定的孩子）；
    * 家长视角孩子概览（复用学生画像 / 周报快照 / 学情雷达）。

事务：本模块只 ``flush``，``commit`` 交给 ``app.db.get_db`` 依赖收口。
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.response import (
    ApiError,
    CODE_BAD_REQUEST,
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
)
from app.models.base import utcnow
from app.models.family import ParentChild, RELATION_VALUES
from app.models.user import Student, User


def _parse_uuid(raw: Any, field: str) -> uuid.UUID:
    """把字符串安全转 UUID，失败按 400 处理。"""
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_BAD_REQUEST, f"{field} 非法") from exc


def bind_child(
    session: Session,
    parent_user_id: uuid.UUID,
    child_phone: str,
    relation: str,
) -> ParentChild:
    """家长绑定一个孩子（按手机号）。

    Raises:
        ApiError: 400 关系非法 / 孩子非学生账号；404 孩子账号或档案不存在。
    """
    if relation not in RELATION_VALUES:
        raise ApiError(CODE_BAD_REQUEST, f"关系必须是 {','.join(RELATION_VALUES)} 之一")

    child_user = session.execute(
        select(User).where(User.phone == child_phone, User.deleted_at.is_(None))
    ).scalar_one_or_none()
    if child_user is None:
        raise ApiError(CODE_NOT_FOUND, "该手机号对应的孩子账号不存在，请先让孩子登录一次")
    if child_user.role != "student":
        raise ApiError(CODE_BAD_REQUEST, "该账号不是学生账号，无法绑定")

    student = session.execute(
        select(Student).where(Student.user_id == child_user.id)
    ).scalar_one_or_none()
    if student is None:
        raise ApiError(CODE_NOT_FOUND, "该孩子尚未创建学习档案")

    existing = session.execute(
        select(ParentChild).where(
            ParentChild.parent_id == parent_user_id,
            ParentChild.student_id == student.id,
            ParentChild.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing  # 幂等：已绑定直接返回

    pc = ParentChild(parent_id=parent_user_id, student_id=student.id, relation=relation)
    session.add(pc)
    session.flush()
    return pc


def unbind_child(session: Session, parent_user_id: uuid.UUID, student_id: uuid.UUID) -> None:
    """解绑（软删，保留关系历史）。"""
    sid = _parse_uuid(student_id, "studentId")
    row = session.execute(
        select(ParentChild).where(
            ParentChild.parent_id == parent_user_id,
            ParentChild.student_id == sid,
            ParentChild.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(CODE_NOT_FOUND, "未找到绑定关系")
    row.deleted_at = utcnow()
    session.flush()


def list_children(session: Session, parent_user_id: uuid.UUID) -> List[Dict[str, Any]]:
    """列出当前家长绑定的所有孩子。"""
    rows = session.execute(
        select(ParentChild, Student, User)
        .join(Student, Student.id == ParentChild.student_id)
        .join(User, User.id == Student.user_id)
        .where(ParentChild.parent_id == parent_user_id, ParentChild.deleted_at.is_(None))
    ).all()

    result: List[Dict[str, Any]] = []
    for pc, student, user in rows:
        result.append(
            {
                "student_id": str(student.id),
                "nickname": user.nickname,
                "real_name": student.real_name,
                "grade": student.grade,
                "stage": student.stage,
                "relation": pc.relation,
            }
        )
    return result


def assert_parent_of(session: Session, parent_user_id: uuid.UUID, student_id: uuid.UUID) -> None:
    """越权校验：家长只能访问自己绑定的孩子，否则 403。"""
    sid = _parse_uuid(student_id, "studentId")
    row = session.execute(
        select(ParentChild).where(
            ParentChild.parent_id == parent_user_id,
            ParentChild.student_id == sid,
            ParentChild.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(CODE_FORBIDDEN, "您未绑定该孩子，无法查看其数据")


def get_child_summary(session: Session, student_id: uuid.UUID) -> Dict[str, Any]:
    """家长视角孩子概览（复用学生画像 + 周报快照 + 学情雷达）。"""
    sid = _parse_uuid(student_id, "studentId")
    student = session.get(Student, sid)
    if student is None:
        raise ApiError(CODE_NOT_FOUND, "孩子档案不存在")
    user = session.get(User, student.user_id)

    # 函数内 import，避免与 student/profile/report 服务形成顶层循环依赖
    from app.services.profile_service import get_radar
    from app.services.report_service import build_weekly_snapshot
    from app.services.student_service import build_profile_out

    profile = build_profile_out(session, user, student)
    snapshot = build_weekly_snapshot(session, sid)
    radar = get_radar(session, student_id=sid)
    radar_dict = dataclasses.asdict(radar) if not hasattr(radar, "to_dict") else radar.to_dict()

    return {
        "student_id": str(sid),
        "nickname": user.nickname,
        "real_name": student.real_name,
        "grade": student.grade,
        "stage": student.stage,
        "profile": profile,
        "weekly": snapshot.to_dict(),
        "radar": radar_dict,
    }


__all__ = [
    "bind_child",
    "unbind_child",
    "list_children",
    "assert_parent_of",
    "get_child_summary",
]


# ==================================================================== 家长只读视图
# 以下接口全部要求 ``parent`` 角色，并在入口 ``assert_parent_of`` 校验绑定关系。

#: 掌握度数值档 → 家长端展示标签（0未入门/1薄弱/2基本掌握/3熟练）
_MASTERY_LABEL = {0: "未入门", 1: "薄弱", 2: "基本掌握", 3: "熟练"}


def list_child_mistakes(session: Session, student_id: uuid.UUID, page: int = 1, size: int = 20) -> Dict[str, Any]:
    """家长视角：孩子错题列表（含题干，按最近创建倒序）。"""
    from app.models.content import Question
    from app.models.learning import Mistake

    sid = _parse_uuid(student_id, "studentId")
    base = select(Mistake, Question).join(
        Question, Question.id == Mistake.question_id
    ).where(Mistake.student_id == sid)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = session.execute(
        base.order_by(Mistake.created_at.desc()).offset((page - 1) * size).limit(size)
    ).all()
    items = [
        {
            "id": str(m.id),
            "question_id": str(m.question_id),
            "stem": q.content,
            "difficulty": q.difficulty,
            "kp_id": str(m.kp_id) if m.kp_id else None,
            "error_type": m.error_type,
            "error_reason": m.error_reason,
            "note": m.note,
            "solved": m.solved,
            "solved_at": m.solved_at.isoformat() if m.solved_at else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m, q in rows
    ]
    return {"total": total, "page": page, "size": size, "items": items}


def list_child_attempts(session: Session, student_id: uuid.UUID, page: int = 1, size: int = 20) -> Dict[str, Any]:
    """家长视角：孩子练习记录（作答历史）。"""
    from app.models.content import Question
    from app.models.learning import Attempt

    sid = _parse_uuid(student_id, "studentId")
    base = select(Attempt, Question).join(
        Question, Question.id == Attempt.question_id, isouter=True
    ).where(Attempt.student_id == sid)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = session.execute(
        base.order_by(Attempt.created_at.desc()).offset((page - 1) * size).limit(size)
    ).all()
    items = [
        {
            "id": str(a.id),
            "question_id": str(a.question_id) if a.question_id else None,
            "stem": (q.content[:120] + "…") if q and q.content and len(q.content) > 120 else (q.content if q and q.content else None),
            "selected_answer": a.selected_answer,
            "is_correct": a.is_correct,
            "duration_sec": a.duration_sec,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a, q in rows
    ]
    return {"total": total, "page": page, "size": size, "items": items}


def list_child_mastery(session: Session, student_id: uuid.UUID, page: int = 1, size: int = 50) -> Dict[str, Any]:
    """家长视角：孩子知识点掌握度列表。"""
    from app.models.content import KnowledgePoint
    from app.models.student_mastery import StudentMastery

    sid = _parse_uuid(student_id, "studentId")
    base = select(StudentMastery, KnowledgePoint).join(
        KnowledgePoint, KnowledgePoint.id == StudentMastery.knowledge_point_id, isouter=True
    ).where(StudentMastery.student_id == sid)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = session.execute(
        base.order_by(StudentMastery.computed_at.desc()).offset((page - 1) * size).limit(size)
    ).all()

    items = []
    for sm, kp in rows:
        ts = getattr(sm, "computed_at", None) or getattr(sm, "updated_at", None)
        items.append(
            {
                "kp_id": str(sm.knowledge_point_id),
                "kp_name": kp.name if kp else None,
                "score": sm.mastery_score,
                "level": _MASTERY_LABEL.get(sm.mastery_level, "未知"),
                "level_int": sm.mastery_level,
                "attempts": sm.attempts,
                "updated_at": ts.isoformat() if ts else None,
            }
        )
    return {"total": total, "page": page, "size": size, "items": items}


def get_child_entitlement(session: Session, student_id: uuid.UUID) -> Dict[str, Any]:
    """家长视角：孩子订阅/权益状态（免费额度当前版本）。"""
    from app.ai.guards import count_today_usage
    from app.core.config import settings

    sid = _parse_uuid(student_id, "studentId")
    session_count, attempt_count = count_today_usage(sid, session)
    # 内测期不限流：free_daily_ai <= 0 表示无限，向前端统一上报 -1（"不限"语义）。
    raw_limit = int(settings.free_daily_ai or 0)
    daily_ai_limit = -1 if raw_limit <= 0 else raw_limit
    return {
        "plan": "free",
        "plan_name": "内测免费版",
        "daily_ai_limit": daily_ai_limit,
        "ai_sessions_today": session_count,
        "ai_attempts_today": attempt_count,
        "expires_at": None,
    }

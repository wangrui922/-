"""学生档案服务（T02 · 2.2）。

职责：
    * 由 JWT 里的 ``sub``（users.id）定位学生档案，找不到 → 404，非学生角色 → 403；
    * 读：拼装档案 + 画像摘要；
    * 写：只更新传入字段，``grade`` 越界（0 / 7）显式抛 ``ApiError(400)``。

事务：只 ``flush``，``commit`` 由 ``app.db.get_db`` 收口。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser
from app.core.response import (
    ApiError,
    CODE_BAD_REQUEST,
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_UNAUTHORIZED,
)
from app.core.security import ROLE_STUDENT
from app.models import Student, StudentProfile, User
from app.models.user import GRADE_MAX, GRADE_MIN, stage_for_grade
from app.schemas.student import ProfileSummary, StudentProfileIn, StudentProfileOut
from app.services.auth_service import ensure_student_profile

#: 每日目标题量取值范围（与 students 表 CHECK 约束一致）
DAILY_TARGET_MIN: int = 1
DAILY_TARGET_MAX: int = 50

#: 掌握度达标阈值（0-100）
MASTERY_PASS_LINE: float = 80.0


def _parse_uuid(raw: Any, field: str) -> uuid.UUID:
    """把 JWT 里的字符串安全转 UUID，失败按 401 处理（令牌内容不可信）。"""
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_UNAUTHORIZED, f"访问令牌无效（{field} 非法）") from exc


def load_student(session: Session, current: CurrentUser) -> Tuple[User, Student]:
    """按当前登录用户加载 ``(User, Student)``。

    Raises:
        ApiError: 401 令牌内容非法；403 非学生角色；404 档案不存在。
    """
    if current.role != ROLE_STUDENT:
        raise ApiError(CODE_FORBIDDEN, "仅学生账号可访问学生档案")

    user_id = _parse_uuid(current.sub, "sub")
    user = session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError(CODE_UNAUTHORIZED, "账号不存在或已注销")

    student = session.execute(
        select(Student).where(Student.user_id == user.id)
    ).scalar_one_or_none()
    if student is None:
        raise ApiError(CODE_NOT_FOUND, "学生档案不存在")

    return user, student


def _summarize_profile(profile: Optional[StudentProfile]) -> ProfileSummary:
    """把 ``student_profile`` 折算成对外的画像摘要。"""
    if profile is None:
        return ProfileSummary()

    mastery: Dict[str, Any] = profile.mastery if isinstance(profile.mastery, dict) else {}
    weak_points: List[Any] = profile.weak_points if isinstance(profile.weak_points, list) else []
    learning_style: Dict[str, Any] = (
        profile.learning_style if isinstance(profile.learning_style, dict) else {}
    )

    mastered = 0
    for value in mastery.values():
        try:
            if float(value) >= MASTERY_PASS_LINE:
                mastered += 1
        except (TypeError, ValueError):
            continue

    return ProfileSummary(
        mastery=mastery,
        weak_points=weak_points,
        learning_style=learning_style,
        mastered_count=mastered,
        tracked_count=len(mastery),
        updated_at=profile.updated_at.isoformat() if profile.updated_at is not None else None,
    )


def build_profile_out(session: Session, user: User, student: Student) -> Dict[str, Any]:
    """拼装 ``GET /api/student/profile`` 的 data（camelCase）。"""
    profile = ensure_student_profile(session, student)
    payload = StudentProfileOut(
        student_id=str(student.id),
        user_id=str(user.id),
        phone=user.phone,
        nickname=user.nickname,
        avatar_url=user.avatar_url,
        real_name=student.real_name,
        grade=student.grade,
        stage=student.stage,
        goal=student.goal,
        daily_target=student.daily_target,
        streak_days=student.streak_days,
        total_points=student.total_points,
        profile=_summarize_profile(profile),
    )
    return payload.model_dump(by_alias=True)


def get_profile(session: Session, current: CurrentUser) -> Dict[str, Any]:
    """读取当前学生档案。"""
    user, student = load_student(session, current)
    return build_profile_out(session, user, student)


def update_profile(session: Session, current: CurrentUser, data: StudentProfileIn) -> Dict[str, Any]:
    """更新当前学生档案（只改传了的字段）。

    Raises:
        ApiError: 400 未传任何字段 / grade 越界 / dailyTarget 越界。
    """
    if not data.has_any():
        raise ApiError(CODE_BAD_REQUEST, "至少需要提交一个可更新字段")

    user, student = load_student(session, current)

    if data.grade is not None:
        if not isinstance(data.grade, int) or isinstance(data.grade, bool):
            raise ApiError(CODE_BAD_REQUEST, "年级必须是整数")
        if data.grade < GRADE_MIN or data.grade > GRADE_MAX:
            raise ApiError(CODE_BAD_REQUEST, f"年级必须在 {GRADE_MIN}-{GRADE_MAX} 之间")
        student.grade = data.grade
        student.stage = stage_for_grade(data.grade)

    if data.daily_target is not None:
        if data.daily_target < DAILY_TARGET_MIN or data.daily_target > DAILY_TARGET_MAX:
            raise ApiError(
                CODE_BAD_REQUEST,
                f"每日目标题量必须在 {DAILY_TARGET_MIN}-{DAILY_TARGET_MAX} 之间",
            )
        student.daily_target = data.daily_target

    if data.goal is not None:
        student.goal = data.goal.strip() or None

    if data.real_name is not None:
        student.real_name = data.real_name.strip() or None

    if data.nickname is not None:
        user.nickname = data.nickname.strip() or None

    session.flush()
    return build_profile_out(session, user, student)


__all__ = [
    "DAILY_TARGET_MIN",
    "DAILY_TARGET_MAX",
    "MASTERY_PASS_LINE",
    "load_student",
    "get_profile",
    "update_profile",
    "build_profile_out",
]

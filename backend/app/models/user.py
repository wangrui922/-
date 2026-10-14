"""账号域模型：``users``、``students``。

字段以 docs/v4-db-api.md §A.2 为准。
V4.1 不建 ``parents`` 表（留 V4.2），家长越权校验以 ``students.user_id`` 兜底。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, SoftDeleteMixin, TimestampMixin, uuid_pk

if TYPE_CHECKING:  # 仅类型检查期引用，运行期不产生循环导入
    from app.models.ai import AiSession
    from app.models.gamification import GameSession, Leaderboard, StudentBadge, StudentPoint
    from app.models.learning import Attempt, LearningTask, Mistake, StudentProfile
    from app.models.subscription import Subscription

# 角色 / 状态枚举（用 CHECK 约束落库，避免 PG 专属 ENUM 类型）
ROLE_VALUES = ("student", "parent", "admin")
USER_STATUS_VALUES = ("active", "frozen")
STAGE_VALUES = ("primary", "junior", "senior")

#: 产品覆盖小学、初中、高中 1-12 年级。
GRADE_MIN = 1
GRADE_MAX = 12


def stage_for_grade(grade: int) -> str:
    """把 1-12 年级映射为学生档案使用的英文 学段值。"""
    if grade <= 6:
        return "primary"
    if grade <= 9:
        return "junior"
    return "senior"


class User(Base, TimestampMixin, SoftDeleteMixin):
    """统一账号（学生 / 家长 / 管理员共用）。"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, comment="手机号或内部登录标识")
    access_pin_hash: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="激活码账号的6位个人密码摘要"
    )
    nickname: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="昵称")
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="头像地址")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="student", comment="student/parent/admin")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", comment="active/frozen")
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近登录时间(UTC)"
    )

    student: Mapped[Optional["Student"]] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    subscriptions: Mapped[List["Subscription"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="Subscription.created_at.desc()",
    )

    __table_args__ = (
        CheckConstraint("role IN ('student','parent','admin')", name="ck_users_role"),
        CheckConstraint("status IN ('active','frozen')", name="ck_users_status"),
        Index("idx_users_created_at", "created_at"),
        Index("idx_users_role", "role"),
        {"comment": "统一账号表"},
    )


class Student(Base, TimestampMixin):
    """学生档案（与 ``users`` 一对一）。"""

    __tablename__ = "students"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="所属账号(1:1)",
    )
    real_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="真实姓名")
    grade: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=4, comment="年级 1-12")
    stage: Mapped[str] = mapped_column(
        String(16), nullable=False, default="primary", comment="primary/junior/senior"
    )
    goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="学习目标")
    daily_target: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5, comment="每日目标题量")
    streak_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="连续打卡天数")
    total_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="成就积分")

    user: Mapped["User"] = relationship(back_populates="student")
    profile: Mapped[Optional["StudentProfile"]] = relationship(
        back_populates="student",
        uselist=False,
        cascade="all, delete-orphan",
    )
    mistakes: Mapped[List["Mistake"]] = relationship(back_populates="student", cascade="all, delete-orphan")
    attempts: Mapped[List["Attempt"]] = relationship(back_populates="student", cascade="all, delete-orphan")
    tasks: Mapped[List["LearningTask"]] = relationship(back_populates="student", cascade="all, delete-orphan")
    sessions: Mapped[List["AiSession"]] = relationship(back_populates="student", cascade="all, delete-orphan")
    # 方向六 · 游戏化学习
    game_sessions: Mapped[list["GameSession"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )
    points: Mapped[Optional["StudentPoint"]] = relationship(
        back_populates="student", uselist=False, cascade="all, delete-orphan"
    )
    badges: Mapped[list["StudentBadge"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )
    leaderboard_entries: Mapped[list["Leaderboard"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(f"grade >= {GRADE_MIN} AND grade <= {GRADE_MAX}", name="ck_students_grade"),
        CheckConstraint("stage IN ('primary','junior','senior')", name="ck_students_stage"),
        CheckConstraint("daily_target > 0 AND daily_target <= 50", name="ck_students_daily_target"),
        Index("idx_students_user_id", "user_id"),
        Index("idx_students_grade", "grade"),
        {"comment": "学生档案表"},
    )


__all__ = [
    "User",
    "Student",
    "ROLE_VALUES",
    "USER_STATUS_VALUES",
    "STAGE_VALUES",
    "GRADE_MIN",
    "GRADE_MAX",
    "stage_for_grade",
]

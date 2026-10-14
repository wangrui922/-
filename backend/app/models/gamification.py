"""游戏化学习域模型：游戏会话、积分、徽章、排行榜。

方向六 · 游戏化学习（提升低龄段留存）⭐ P2
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, JSONType, TimestampMixin, utcnow, uuid_pk

if TYPE_CHECKING:
    from app.models.user import Student


#: 游戏类型枚举
GAME_TYPE_VALUES = ("speed_challenge", "math_battle")
#: 徽章类别枚举
BADGE_CATEGORY_VALUES = ("streak", "accuracy", "speed", "explore", "battle")
#: 稀有度枚举
BADGE_RARITY_VALUES = ("common", "rare", "epic", "legendary")
#: 排行榜类型枚举
LEADERBOARD_TYPE_VALUES = ("weekly", "monthly")


class GameSession(Base):
    """游戏会话记录（限时挑战 / 回合战斗）。"""

    __tablename__ = "game_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属学生",
    )
    game_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="speed_challenge / math_battle"
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="本次得分")
    correct_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="答对题数"
    )
    wrong_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="答错题数"
    )
    max_combo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="最大连击数"
    )
    duration_sec: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="游戏时长(秒)"
    )
    difficulty_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, comment="难度 L1-L3"
    )
    battle_wave: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="战斗当前波次(0=非战斗)"
    )
    battle_best_wave: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="战斗历史最高波次"
    )
    session_metadata: Mapped[Any] = mapped_column(
        JSONType(), nullable=True, comment="JSON：题目列表、答题记录、怪物配置快照"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="结束时间(UTC)"
    )

    student: Mapped["Student"] = relationship(back_populates="game_sessions")

    __table_args__ = (
        CheckConstraint(
            "game_type IN ('speed_challenge','math_battle')", name="ck_game_sessions_type"
        ),
        CheckConstraint(
            "difficulty_level BETWEEN 1 AND 3", name="ck_game_sessions_difficulty"
        ),
        Index("idx_game_sessions_student_type", "student_id", "game_type"),
        Index("idx_game_sessions_created", "created_at"),
        {"comment": "游戏会话记录表"},
    )


class StudentPoint(Base):
    """学生积分总览（1:1 对应 Student，游戏数据聚合）。"""

    __tablename__ = "student_points"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
        comment="所属学生(1:1)",
    )
    total_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="总积分"
    )
    weekly_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="本周积分(周日清零)"
    )
    monthly_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="本月积分(月初清零)"
    )
    battle_best_wave: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="战斗历史最高波次"
    )
    speed_best_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="限时挑战历史最高分"
    )
    total_games: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="累计游戏局数"
    )
    total_correct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="累计答对题数"
    )
    total_wrong: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="累计答错题数"
    )
    streak_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="连续登录天数"
    )
    last_played_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近游戏时间(UTC)"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        comment="更新时间(UTC)",
    )

    student: Mapped["Student"] = relationship(back_populates="points")

    __table_args__ = (
        Index("idx_student_points_weekly", "weekly_points"),
        Index("idx_student_points_monthly", "monthly_points"),
        {"comment": "学生积分总览表"},
    )


class Badge(Base):
    """徽章定义表（全局配置，初始化后只读）。"""

    __tablename__ = "badges"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, comment="徽章唯一标识名"
    )
    display_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="展示名称"
    )
    description: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="解锁描述"
    )
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="streak/accuracy/speed/explore/battle"
    )
    rarity: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="common/rare/epic/legendary"
    )
    icon_url: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True, comment="图标 URL 或 emoji"
    )
    condition_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="触发条件类型(供服务端解析)"
    )
    condition_value: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="触发阈值"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="排序权重"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )

    student_badges: Mapped[list["StudentBadge"]] = relationship(
        back_populates="badge", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('streak','accuracy','speed','explore','battle')",
            name="ck_badges_category",
        ),
        CheckConstraint(
            "rarity IN ('common','rare','epic','legendary')",
            name="ck_badges_rarity",
        ),
        Index("idx_badges_category", "category"),
        Index("idx_badges_rarity", "rarity"),
        {"comment": "徽章定义表"},
    )


class StudentBadge(Base):
    """学生徽章关联（幂等：一个学生同一徽章只能有一条记录）。"""

    __tablename__ = "student_badges"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属学生",
    )
    badge_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("badges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联徽章",
    )
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        comment="解锁时间(UTC)",
    )
    unlock_metadata: Mapped[Any] = mapped_column(
        JSONType(), nullable=True, comment="JSON：触发上下文(波次/得分等)"
    )

    student: Mapped["Student"] = relationship(back_populates="badges")
    badge: Mapped["Badge"] = relationship(back_populates="student_badges")

    __table_args__ = (
        UniqueConstraint("student_id", "badge_id", name="uq_student_badges"),
        {"comment": "学生徽章关联表"},
    )


class Leaderboard(Base):
    """排行榜快照（周榜/月榜，定时任务刷新）。"""

    __tablename__ = "leaderboard"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属学生",
    )
    rank_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="weekly / monthly"
    )
    rank_period: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="格式：2026W38 或 2026-09"
    )
    rank_position: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="排名(1开始)"
    )
    score: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="本周/月积分"
    )
    games_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="游戏局数"
    )
    best_wave: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="最佳波次"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        comment="更新时间(UTC)",
    )

    student: Mapped["Student"] = relationship(back_populates="leaderboard_entries")

    __table_args__ = (
        CheckConstraint(
            "rank_type IN ('weekly','monthly')", name="ck_leaderboard_type"
        ),
        UniqueConstraint(
            "rank_type", "rank_period", "student_id", name="uq_leaderboard_period_student"
        ),
        Index("idx_leaderboard_period", "rank_type", "rank_period", "rank_position"),
        {"comment": "排行榜快照表"},
    )


__all__ = [
    "GameSession",
    "StudentPoint",
    "Badge",
    "StudentBadge",
    "Leaderboard",
    "GAME_TYPE_VALUES",
    "BADGE_CATEGORY_VALUES",
    "BADGE_RARITY_VALUES",
    "LEADERBOARD_TYPE_VALUES",
]

"""学习域模型：``mistakes``、``attempts``、``student_profile``、``learning_tasks``。

字段以 docs/v4-db-api.md §A.2 为准。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, JSONType, utcnow, uuid_pk

if TYPE_CHECKING:  # 仅类型检查期引用，运行期不产生循环导入
    from app.models.ai import AiSession
    from app.models.content import KnowledgePoint, Question

#: 任务类型枚举
TASK_TYPE_VALUES = ("daily", "review", "exam")
#: 任务状态枚举
TASK_STATUS_VALUES = ("pending", "doing", "done", "skipped")


class Mistake(Base):
    """错题本（学生 × 题目，记录错误类型与是否攻克）。"""

    __tablename__ = "mistakes"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属学生",
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联题目",
    )
    error_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, comment="错误类型")
    error_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="错误原因")
    # T04 新增：学生标记「已掌握」时可附一句自述心得，与 error_reason（AI 写的错因）分开存
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="攻克备注(学生自述)")
    solved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否已攻克")
    solved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="攻克时间(UTC)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )
    # T01 新增：关联知识点（建档时从 Question.kp_id 快照写入，FK→knowledge_points.id）
    # 快照语义：题目的 kp 后续被修正时，历史错题的归因不应被追溯改写。
    kp_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联知识点(建档时从Question.kp_id快照)",
    )

    # 知识点关系：用于 _to_item 取 kp_name，list_mistakes 用 selectinload 避免 N+1
    kp: Mapped[Optional["KnowledgePoint"]] = relationship()

    student: Mapped["Student"] = relationship(back_populates="mistakes")
    question: Mapped["Question"] = relationship(back_populates="mistakes")

    __table_args__ = (
        Index("idx_mistakes_student_solved", "student_id", "solved"),
        Index("idx_mistakes_kp", "kp_id"),
        {"comment": "错题本表"},
    )


class Attempt(Base):
    """作答流水（每次答题/对话作答一条，支持幂等 client_event_id）。"""

    __tablename__ = "attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属学生",
    )
    question_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联题目(可为空,如自由对话)",
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("ai_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联 AI 会话",
    )
    selected_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="作答内容")
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, comment="是否正确")
    duration_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="作答耗时(秒)")
    client_event_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, comment="客户端幂等键"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )

    student: Mapped["Student"] = relationship(back_populates="attempts")
    question: Mapped[Optional["Question"]] = relationship(back_populates="attempts")
    session: Mapped[Optional["AiSession"]] = relationship(back_populates="attempts")

    __table_args__ = (
        Index("idx_attempts_student_created", "student_id", "created_at"),
        {"comment": "作答流水表"},
    )


class StudentProfile(Base):
    """学生画像（掌握度 / 薄弱点 / 学习风格，JSON 存储，1:1 对应 Student）。

    注意：V4.1 **不建** ``long_term_vector`` 字段（向量召回留待 V4.2）。
    """

    __tablename__ = "student_profile"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="所属学生(1:1)",
    )
    mastery: Mapped[Any] = mapped_column(JSONType(), nullable=False, default=dict, comment="知识点掌握度(JSON)")
    weak_points: Mapped[Any] = mapped_column(JSONType(), nullable=False, default=list, comment="薄弱点列表(JSON)")
    learning_style: Mapped[Any] = mapped_column(JSONType(), nullable=False, default=dict, comment="学习风格(JSON)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        comment="更新时间(UTC)",
    )

    student: Mapped["Student"] = relationship(back_populates="profile")

    __table_args__ = ({"comment": "学生画像表"},)


class LearningTask(Base):
    """学习任务（每日/复习/测验，items 为题目清单 JSON）。"""

    __tablename__ = "learning_tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属学生",
    )
    task_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="daily/review/exam")
    items: Mapped[Any] = mapped_column(JSONType(), nullable=False, comment="任务题目清单(JSON)")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", comment="pending/doing/done")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="生成时间(UTC)"
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="完成时间(UTC)"
    )

    student: Mapped["Student"] = relationship(back_populates="tasks")

    __table_args__ = (
        CheckConstraint("task_type IN ('daily','review','exam')", name="ck_learning_tasks_type"),
        Index("idx_learning_tasks_student", "student_id"),
        {"comment": "学习任务表"},
    )


# ``Student`` 类型在 user.py 定义；此处仅做惰性标注以满足类型检查器。
if TYPE_CHECKING:
    from app.models.user import Student  # noqa: F401


class ReviewSchedule(Base):
    """复习排程表（学生 × 知识点，SM-2 间隔重复状态物化）—— #65「留得住」。

    与 ``student_mastery``（掌握度）互补：
        * ``student_mastery`` 回答「现在掌握得怎么样」；
        * 本表回答「下一次什么时候该复习这道知识点」。

    状态字段直接映射 ``app.services.review_scheduler.ReviewState``，
    ``review_service`` 负责在两者间做转换与 upsert。
    """

    __tablename__ = "review_schedule"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属学生",
    )
    knowledge_point_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联知识点",
    )
    # ---- SM-2 状态（与 review_scheduler.ReviewState 对齐） ------------------
    ease_factor: Mapped[float] = mapped_column(
        Float, nullable=False, default=2.5, comment="难度系数 EF(初始2.5,下限1.3)"
    )
    interval_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="当前间隔天数"
    )
    repetitions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="连续记住次数(答错归零)"
    )
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="上次复习时间(UTC)"
    )
    due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="下次应复习时间(UTC);None=从未复习→立即到期",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        comment="更新时间(UTC)",
    )

    __table_args__ = (
        UniqueConstraint("student_id", "knowledge_point_id", name="uq_review_schedule_student_kp"),
        Index("idx_review_schedule_due", "student_id", "due_at"),
        {"comment": "复习排程表(SM-2 #65)"},
    )


__all__ = [
    "Mistake",
    "Attempt",
    "StudentProfile",
    "LearningTask",
    "ReviewSchedule",
    "TASK_TYPE_VALUES",
    "TASK_STATUS_VALUES",
]

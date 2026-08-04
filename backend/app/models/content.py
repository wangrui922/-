"""内容域模型：``knowledge_points``、``questions``、``answers``。

字段以 docs/v4-db-api.md §A.2 为准。
所有 UUID 主键/外键统一用 ``base.GUID``，JSON 用 ``base.JSONType``，跨方言一致。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

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

from app.models.base import GUID, Base, JSONType, utcnow, uuid_pk

if TYPE_CHECKING:  # 仅类型检查期引用，运行期不产生循环导入
    from app.models.ai import AiSession
    from app.models.learning import Attempt, Mistake

#: 题型枚举（CHECK 落库，避免 PG 专属 ENUM）
QUESTION_TYPE_VALUES = ("选择", "填空", "应用", "计算")
#: 难度档位 1/3/5
DIFFICULTY_VALUES = (1, 3, 5)
#: 学科，默认数学
SUBJECT_DEFAULT = "数学"
#: 题目上下架状态：active 可用 / archived 归档（V4.1 初中题入库即归档，不进推荐池）
QUESTION_STATUS_VALUES = ("active", "archived")
QUESTION_STATUS_ACTIVE = "active"
QUESTION_STATUS_ARCHIVED = "archived"


class KnowledgePoint(Base):
    """知识点（自引用树，支持多级归类）。"""

    __tablename__ = "knowledge_points"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="知识点名称")
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="父知识点(自引用)",
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="描述")
    code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, unique=True, comment="编码(唯一)")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="排序权重")

    parent: Mapped[Optional["KnowledgePoint"]] = relationship(
        back_populates="children",
        remote_side=[id],
    )
    children: Mapped[List["KnowledgePoint"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    questions: Mapped[List["Question"]] = relationship(
        back_populates="kp",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_knowledge_points_parent", "parent_id"),
        {"comment": "知识点表"},
    )


class Question(Base):
    """题目（关联知识点，可有标准答案）。"""

    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = uuid_pk()
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="题干")
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, comment="配图地址")
    subject: Mapped[str] = mapped_column(String(16), nullable=False, default=SUBJECT_DEFAULT, comment="学科")
    grade: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True, comment="适用年级 1-6")
    difficulty: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True, comment="难度 1/3/5")
    kp_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联知识点",
    )
    type_: Mapped[Optional[str]] = mapped_column("type", String(16), nullable=True, comment="选择/填空/应用/计算")
    options: Mapped[Any] = mapped_column(JSONType(), nullable=True, comment="选择题选项(JSON)")
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=QUESTION_STATUS_ACTIVE,
        server_default=QUESTION_STATUS_ACTIVE,
        comment="active 可用 / archived 归档",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )

    kp: Mapped[Optional["KnowledgePoint"]] = relationship(back_populates="questions")
    answer: Mapped[Optional["Answer"]] = relationship(
        back_populates="question",
        uselist=False,
        cascade="all, delete-orphan",
    )
    mistakes: Mapped[List["Mistake"]] = relationship(back_populates="question", cascade="all, delete-orphan")
    attempts: Mapped[List["Attempt"]] = relationship(back_populates="question", cascade="all, delete-orphan")
    ai_sessions: Mapped[List["AiSession"]] = relationship(back_populates="question", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("difficulty IN (1,3,5)", name="ck_questions_difficulty"),
        Index("idx_questions_kp", "kp_id"),
        Index("idx_questions_status_grade", "status", "grade"),
        {"comment": "题目表"},
    )


class Answer(Base):
    """题目标准答案与解析（1:1 对应 Question）。"""

    __tablename__ = "answers"

    id: Mapped[uuid.UUID] = uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="所属题目(1:1)",
    )
    correct_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="正确答案")
    analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="解析")
    distractor_analysis: Mapped[Any] = mapped_column(
        JSONType(), nullable=False, default=dict, comment="干扰项分析(JSON)"
    )

    question: Mapped["Question"] = relationship(back_populates="answer")

    __table_args__ = ({"comment": "题目答案表"},)


__all__ = [
    "KnowledgePoint",
    "Question",
    "Answer",
    "QUESTION_TYPE_VALUES",
    "DIFFICULTY_VALUES",
    "SUBJECT_DEFAULT",
    "QUESTION_STATUS_VALUES",
    "QUESTION_STATUS_ACTIVE",
    "QUESTION_STATUS_ARCHIVED",
]

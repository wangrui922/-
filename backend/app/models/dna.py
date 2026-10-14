"""Sprint 2 DNA 模型：学生画像 / 知识点掌握 / 学习行为事件（V6.5 学生 DNA 域）。

三张落地表（与 V6.5 开发总规划 DNA-001/003/004 逐字对齐，勿漂移）：
    * ``student_learning_profiles``   学生画像（1:1 student_id）——DNA-001/DNA-002
    * ``student_knowledge_mastery``   学生 × 知识点掌握（含五维预留列）——DNA-003
    * ``student_learning_events``     学习行为事件流——DNA-004

与既有表的定位区分（避免语义打架）：
    * ``student_profile``（T02）是给 AI 提示词看的粗粒度 JSON 摘要；
    * ``student_mastery``（T04）是给看板算法用的细粒度物化缓存；
    * 本模块是 V6.5「学生 DNA」的**源数据**：画像字段结构化可校验、
      掌握按知识点展开（唯一约束 (student_id, knowledge_point_id)）、
      事件流直接落库，三张表互不替代。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    JSONType,
    TimestampMixin,
    CreatedAtMixin,
    uuid_pk,
)

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查期引用
    from app.models.content import KnowledgePoint, Question
    from app.models.user import Student

# ==================================================================== 枚举
#: 学习目标八类枚举（V6.5 规划 DNA-002 权威值，非法值一律拒绝）
LEARNING_GOAL_VALUES = (
    "提高校内成绩",
    "跟上进度",
    "补基础",
    "期中",
    "期末",
    "中考",
    "高考",
    "自主提高",
)

#: 学习行为事件类型（V6.5 规划 DNA-004 权威值，大写存储）
LEARNING_EVENT_TYPE_VALUES = (
    "QUESTIONVIEW",
    "ANSWER",
    "HINT",
    "ERROR",
    "CORRECT",
    "REVIEW",
    "SESSIONSTART",
    "SESSIONEND",
)

#: 提示依赖程度建议枚举（规划未硬性规定，软校验；仅服务层建议集合，不做 CHECK）
HINT_DEPENDENCY_VALUES = ("high", "medium", "low")


class StudentLearningProfile(TimestampMixin, Base):
    """学生画像（1:1 对应 Student）。

    V6.5 DNA-001 决策点结论：现有 ``student_profile`` 只承载
    mastery/weak_points/learning_style 三个 JSON 摘要，无法承载
    DNA-002 需要「非法枚举拒绝」校验的结构化画像字段，故**新建独立表**。
    """

    __tablename__ = "student_learning_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="所属学生(1:1)",
    )
    # ---- DNA-002 画像字段 --------------------------------------------------
    learning_goal: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="学习目标(八类枚举，硬校验)"
    )
    learning_stage: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, comment="学习阶段(建议枚举)"
    )
    current_level: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, comment="当前水平(建议枚举)"
    )
    learning_habits: Mapped[Optional[Any]] = mapped_column(
        JSONType(), nullable=True, default=None, comment="学习习惯(JSON 自由结构)"
    )
    common_errors: Mapped[Optional[Any]] = mapped_column(
        JSONType(), nullable=True, default=None, comment="常见错误(JSON 自由结构)"
    )
    hint_dependency: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, comment="提示依赖 high/medium/low(建议枚举)"
    )
    learning_trend: Mapped[Optional[Any]] = mapped_column(
        JSONType(), nullable=True, default=None, comment="学习趋势(JSON 自由结构)"
    )

    student: Mapped["Student"] = relationship("Student")

    __table_args__ = (
        CheckConstraint(
            "learning_goal IN ('提高校内成绩','跟上进度','补基础','期中','期末','中考','高考','自主提高')",
            name="ck_student_learning_profiles_goal",
        ),
        {"comment": "学生画像表(V6.5 DNA-001,1:1)"},
    )


class StudentKnowledgeMastery(TimestampMixin, Base):
    """学生 × 知识点掌握（V6.5 DNA-003）。

    与 T04 ``student_mastery``（看板物化缓存）的区别：
    * 本表是**源数据**，记录每次掌握评估落点（mastery/confidence + 历史时间戳）；
    * 五维 concept/procedure/independent/transfer/retention 先建列、Sprint 3 启用；
    * 唯一约束 (student_id, knowledge_point_id)，同一学生同知识点只一行。
    """

    __tablename__ = "student_knowledge_mastery"

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
    # ---- 掌握核心指标 ------------------------------------------------------
    mastery: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="整体掌握度 0~1"
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="置信度 0~1"
    )
    last_practice_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近练习时间(UTC)"
    )
    last_correct_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近答对时间(UTC)"
    )
    last_wrong_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近答错时间(UTC)"
    )
    practice_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="累计练习次数"
    )
    correct_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="累计答对次数"
    )
    wrong_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="累计答错次数"
    )
    # ---- 五维预留列（Sprint 3 启用，先建列） ------------------------------
    concept_mastery: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None, comment="概念理解维 0~1(Sprint 3 启用)"
    )
    procedure_mastery: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None, comment="程序执行维 0~1(Sprint 3 启用)"
    )
    independent_mastery: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None, comment="独立完成维 0~1(Sprint 3 启用)"
    )
    transfer_mastery: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None, comment="迁移应用维 0~1(Sprint 3 启用)"
    )
    retention_mastery: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None, comment="保持留存维 0~1(Sprint 3 启用)"
    )

    student: Mapped["Student"] = relationship("Student")

    __table_args__ = (
        UniqueConstraint("student_id", "knowledge_point_id", name="uq_skm_student_kp"),
        Index("idx_skm_student_updated", "student_id", "last_practice_at"),
        {"comment": "学生知识点掌握表(V6.5 DNA-003)"},
    )


class StudentLearningEvent(CreatedAtMixin, Base):
    """学习行为事件流（V6.5 DNA-004）。

    事件类型八类：QUESTIONVIEW/ANSWER/HINT/ERROR/CORRECT/REVIEW/
    SESSIONSTART/SESSIONEND（大写存储，DB CHECK + 服务层双保险）。
    meta 为自由 JSON，承载事件上下文（时长、session、客户端等）。
    """

    __tablename__ = "student_learning_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属学生",
    )
    event_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        comment="事件类型 QUESTIONVIEW/ANSWER/HINT/ERROR/CORRECT/REVIEW/SESSIONSTART/SESSIONEND",
    )
    question_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联题目(可为空)",
    )
    knowledge_point_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联知识点(可为空)",
    )
    meta: Mapped[Optional[Any]] = mapped_column(
        JSONType(), nullable=True, default=None, comment="事件上下文(JSON)"
    )

    student: Mapped["Student"] = relationship("Student")

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('QUESTIONVIEW','ANSWER','HINT','ERROR','CORRECT','REVIEW','SESSIONSTART','SESSIONEND')",
            name="ck_student_learning_events_type",
        ),
        Index("idx_sle_student_created", "student_id", "created_at"),
        {"comment": "学习行为事件表(V6.5 DNA-004)"},
    )


__all__ = [
    "StudentLearningProfile",
    "StudentKnowledgeMastery",
    "StudentLearningEvent",
    "LEARNING_GOAL_VALUES",
    "LEARNING_EVENT_TYPE_VALUES",
    "HINT_DEPENDENCY_VALUES",
]

"""Benchmark 维度一（教学行为）轻量 Trace 表：``quality_traces``。

用途：把原先「只写 logger 不累计」的两类教学行为事件落库，支撑 60 人内测
Benchmark 的两项核心指标：

* ``answer_leak``        AI 回复直接给答案（答案泄漏特征命中，学生面口径）；
* ``explanation_quality`` DEMO 讲解质量闸门的 5 维 rubric 评分结果。

定位说明（与既有表的边界，勿越界）：
    * 本表是**指标事件流水**，不是学生行为事件——``student_learning_events``
      （DNA-004）记录「学生做了什么」，本表记录「AI 教得怎么样」；
    * ``student_id`` 可空：系统级 / 无归属会话的教学行为事件也要能记；
    * 只追加不更新，聚合口径全部由 ``app.services.quality_metrics_service``
      提供，业务代码不直接写本表以外的统计逻辑。

与迁移文件的关系：表结构与 ``migrations/023_quality_traces*.sql`` 逐字对齐，
勿漂移（测试内存库由 ``Base.metadata.create_all`` 建表，依赖本模型已登记到
``app.models.__init__``）。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, CreatedAtMixin, JSONType, uuid_pk

if TYPE_CHECKING:  # 仅类型检查期引用，运行期不产生循环导入
    from app.models.ai import AiSession
    from app.models.content import Question
    from app.models.user import Student

#: 事件类型枚举（Benchmark 维度一目前两维，后续可扩展）
QUALITY_TRACE_EVENT_TYPES = ("answer_leak", "explanation_quality")

#: 严重度枚举（info=正常评分 / warning=未达标或轻度违规 / error=严重违规）
QUALITY_TRACE_SEVERITIES = ("info", "warning", "error")


class QualityTrace(CreatedAtMixin, Base):
    """教学行为质量事件流水（Benchmark 维度一）。

    字段语义：
        * ``student_id`` 可空——系统级（无学生归属）事件同样要能落库；
        * ``session_id`` / ``question_id`` 归参，可空（取不到不编造）；
        * ``payload`` 自由 JSON：answer_leak 带 mode/strict/excerpt；
          explanation_quality 带 5 维分数 / overall / grade / threshold /
          passed / suggestion / parsed_ok；
        * 只追加不更新：``created_at`` 即事件发生时间（UTC）。
    """

    __tablename__ = "quality_traces"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="所属学生(可空:系统级事件)",
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("ai_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联 AI 会话(可空)",
    )
    question_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联题目(可空)",
    )
    event_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="事件类型 answer_leak/explanation_quality",
    )
    severity: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="warning",
        comment="严重度 info/warning/error",
    )
    payload: Mapped[Optional[Any]] = mapped_column(
        JSONType(), nullable=True, default=None, comment="事件上下文(JSON)"
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('answer_leak','explanation_quality')",
            name="ck_quality_traces_event_type",
        ),
        CheckConstraint(
            "severity IN ('info','warning','error')",
            name="ck_quality_traces_severity",
        ),
        Index("idx_quality_traces_type_created", "event_type", "created_at"),
        Index("idx_quality_traces_student_created", "student_id", "created_at"),
        {"comment": "教学行为质量事件流水表(Benchmark维度一)"},
    )


__all__ = [
    "QualityTrace",
    "QUALITY_TRACE_EVENT_TYPES",
    "QUALITY_TRACE_SEVERITIES",
]

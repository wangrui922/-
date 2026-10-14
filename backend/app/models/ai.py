"""AI 会话域模型：``ai_sessions``、``ai_messages``。

字段以 docs/v4-db-api.md §A.2 为准。
消息随会话级联删除（ON DELETE CASCADE）。
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
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, utcnow, uuid_pk

if TYPE_CHECKING:  # 仅类型检查期引用，运行期不产生循环导入
    from app.models.content import Question
    from app.models.learning import Attempt

#: 消息角色枚举
MESSAGE_ROLE_VALUES = ("user", "assistant", "system")
#: 会话状态枚举
SESSION_STATUS_VALUES = ("active", "closed", "expired")


class AiSession(Base):
    """AI 辅导会话（学生维度，可绑定一道题）。"""

    __tablename__ = "ai_sessions"

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
        comment="关联题目(可为空)",
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", comment="active/closed/expired")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )
    # —— T04B 状态机落库：阶段 + 步数（与 app.ai.state_machine 的取值保持一致）——
    session_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="intro",
        comment="intro/guiding/practicing/summarizing/closed",
    )
    step: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="辅导轮次步数",
    )
    state_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        comment="状态最后更新时间(UTC)",
    )
    summary_text: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="本次辅导完成后的学习小结"
    )

    student: Mapped["Student"] = relationship(back_populates="sessions")
    question: Mapped[Optional["Question"]] = relationship(back_populates="ai_sessions")
    messages: Mapped[List["AiMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    attempts: Mapped[List["Attempt"]] = relationship(back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_ai_sessions_student", "student_id"),
        CheckConstraint(
            "session_state IN ('intro','guiding','practicing','summarizing','closed')",
            name="ck_ai_sessions_state",
        ),
        {"comment": "AI 会话表"},
    )


class AiMessage(Base):
    """会话消息（用户/助手/系统，随会话级联删除）。"""

    __tablename__ = "ai_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("ai_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属会话",
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="user/assistant/system")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息内容")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )

    session: Mapped["AiSession"] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_ai_messages_role"),
        Index("idx_ai_messages_session", "session_id"),
        {"comment": "AI 消息表"},
    )


# ``Student`` 类型在 user.py 定义；此处仅做惰性标注以满足类型检查器。
if TYPE_CHECKING:
    from app.models.user import Student  # noqa: F401

__all__ = ["AiSession", "AiMessage", "MESSAGE_ROLE_VALUES", "SESSION_STATUS_VALUES"]

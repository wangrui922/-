"""家长-孩子绑定关系（V4.2 商业核心：家长付费、学生使用）。

关系：多对多（一个家长可绑多孩，一个孩子可被多家长绑）。
软删：解绑走 ``deleted_at``，不硬删，保留关系历史。
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, fk_uuid, uuid_pk

RELATION_VALUES = ("father", "mother", "guardian", "other")
RELATION_DEFAULT = "guardian"


class ParentChild(Base, TimestampMixin, SoftDeleteMixin):
    """家长-孩子绑定。"""

    __tablename__ = "parent_child"

    id: Mapped[uuid.UUID] = uuid_pk()
    parent_id: Mapped[uuid.UUID] = fk_uuid("users.id")
    student_id: Mapped[uuid.UUID] = fk_uuid("students.id")
    relation: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=RELATION_DEFAULT,
        comment="father/mother/guardian/other",
    )

    parent: Mapped["User"] = relationship("User")
    student: Mapped["Student"] = relationship("Student")

    __table_args__ = (
        UniqueConstraint("parent_id", "student_id", name="uq_parent_child"),
        CheckConstraint(
            "relation IN ('father','mother','guardian','other')",
            name="ck_parent_child_relation",
        ),
        Index("idx_parent_child_parent", "parent_id"),
        Index("idx_parent_child_student", "student_id"),
        {"comment": "家长-孩子绑定表"},
    )


__all__ = ["ParentChild", "RELATION_VALUES", "RELATION_DEFAULT"]

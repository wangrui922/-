"""知识点掌握度物化表 ``student_mastery``（T04 · B2）。

⚠️ 核查结论（R1）：本表在 V4.1 **从未存在**——
    ``migrations/001_init.sql`` / ``001_init_sqlite.sql`` 里只有 ``student_profile``
    （单行 JSON 画像），没有按知识点展开的 ``student_mastery``。
    因此这里是**全新建表**，而不是给老表加列：
      * SQLite 开发/测试：``Base.metadata.create_all()`` 直接建出来，零成本；
      * PostgreSQL 生产：执行 ``backend/scripts/migrate_t04.sql``（CREATE TABLE IF NOT EXISTS）。

定位：``student_profile.mastery``（JSON）是**给 AI 提示词看的粗粒度摘要**，
本表是**给看板算法用的细粒度物化缓存**，两者并存、互不替代。

跨方言：UUID 走 ``base.GUID``，JSON 走 ``base.JSONType``，无任何 PG 专属语法。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, JSONType, utcnow, uuid_pk

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查期引用
    from app.models.content import KnowledgePoint
    from app.models.user import Student


class StudentMastery(Base):
    """学生 × 知识点的掌握度物化缓存。

    Attributes:
        mastery_score: 0~1 的掌握度分值（贝叶斯平滑后）。
        mastery_level: 0 未入门 / 1 薄弱 / 2 基本掌握 / 3 熟练。
        attempts: 参与计算的事件总数（原始次数，未衰减）。
        correct_count: 其中判定为「正确」的次数。
        error_rate: ``(attempts - correct_count) / attempts``，无数据时为 0。
        effective_attempts: 时效衰减后的有效样本量 ``Σ decay_weight``。
        error_summary: 该知识点下的错因分布 JSON，形如 ``{"CALC": 3, "STEP": 1}``（OQ4）。
        last_practiced_at: 最近一次练习/攻克时间；**大于 computed_at 即表示缓存已脏**（OQ4）。
        computed_at: 本行的计算时间，配合 TTL 判定缓存是否过期。
    """

    __tablename__ = "student_mastery"

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

    # ---- 掌握度核心指标 ---------------------------------------------------
    mastery_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="掌握度 0~1"
    )
    mastery_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="0未入门/1薄弱/2基本掌握/3熟练"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="参与计算的事件总数"
    )
    correct_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="其中正确次数"
    )
    error_rate: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="错误率 0~1"
    )
    effective_attempts: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="时效衰减后的有效样本量"
    )

    # ---- T04 新增：错因摘要 + 时效戳（OQ4） -------------------------------
    error_summary: Mapped[Optional[Any]] = mapped_column(
        JSONType(), nullable=True, default=None, comment="错因分布 JSON，如 {\"CALC\":3}"
    )
    last_practiced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
        comment="最近练习时间(UTC)；> computed_at 表示缓存已脏",
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        comment="本行计算时间(UTC)，配合 TTL 判定缓存新鲜度",
    )

    __table_args__ = (
        UniqueConstraint("student_id", "knowledge_point_id", name="uq_student_mastery_student_kp"),
        Index("idx_student_mastery_student", "student_id"),
        Index("idx_student_mastery_computed", "student_id", "computed_at"),
        {"comment": "学生知识点掌握度物化表(T04)"},
    )


__all__ = ["StudentMastery"]

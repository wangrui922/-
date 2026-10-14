"""知识点前置依赖边表 ``knowledge_prerequisite``（知识图谱 P0·Phase 1）。

定位：
    把「13 L1 + 80 L2」的**树结构**（``knowledge_points.parent_id``）进一步扩展为
    **有向图结构**：本表每条记录表达「要掌握 ``kp_id``，必须先掌握 ``pre_kp_id``」。
    这是 GKT（Graph-based Knowledge Tracing）的图骨架，也是「前置知识点补漏」
    抽题策略的数据基础。

设计要点：
    * 边语义：``pre_kp_id``（前置）→ ``kp_id``（后置）。**方向不可颠倒**；
    * 树结构（parent_id）负责「归类/统计桶」，本表负责「学习次序/依赖」，
      两者并存、互不替代；
    * ``weight``：0~1 的依赖强度，默认 1.0。用于抽题补漏的优先级加权
      （例如整数运算 → 分数运算的边权重高于「相邻概念」的弱边）；
    * ``note``：人工维护时记录「为什么依赖」（数学教学逻辑），供审核与回滚；
    * 幂等口径：``(pre_kp_id, kp_id)`` 唯一，seed 脚本先清理同名边再写入；
    * 不设 ``sort``：边的优先级直接用 ``weight`` 表达，避免额外排序字段。

跨方言：UUID 走 ``base.GUID``，无任何 PG 专属语法，SQLite / PostgreSQL 均可。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, utcnow, uuid_pk

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查期引用
    from app.models.content import KnowledgePoint


class KnowledgePrerequisite(Base):
    """知识点前置依赖边（前置 → 后置，GKT 图骨架 + 抽题补漏依据）。"""

    __tablename__ = "knowledge_prerequisite"

    id: Mapped[uuid.UUID] = uuid_pk()
    pre_kp_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="前置知识点",
    )
    kp_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="后置知识点(依赖前置)",
    )
    weight: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, comment="依赖强度 0~1"
    )
    note: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None, comment="依赖理由"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )

    __table_args__ = (
        UniqueConstraint("pre_kp_id", "kp_id", name="uq_kp_prereq_pair"),
        {"comment": "知识点前置依赖边表(P0知识图谱)"},
    )


__all__ = ["KnowledgePrerequisite"]
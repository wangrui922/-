"""邀请制试运营使用的激活码模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, uuid_pk


class ActivationCode(Base, TimestampMixin):
    """一条激活码只绑定一名学生，明文码不会写入数据库。"""

    __tablename__ = "activation_codes"

    id: Mapped[uuid.UUID] = uuid_pk()
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="SHA-256 摘要")
    label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="发放备注")
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True, comment="已绑定学生账号"
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_activation_codes_active", "revoked_at", "expires_at"),
        {"comment": "邀请制试运营激活码"},
    )


__all__ = ["ActivationCode"]

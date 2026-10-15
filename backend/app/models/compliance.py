"""V6.5 T13 合规模型：监护人同意留痕。

落地表 ``guardian_consents``（与 ``migrations/022_guardian_consents{,_sqlite}.sql``
逐字对齐，勿漂移）：
    * 一行 = 某账号在一个规则版本上的一次监护人同意；
    * ``(user_id, consent_version)`` 唯一：重复提交幂等返回已有记录；
    * ``agreed_at``（同意动作时间）与 ``created_at``（落库时间）分开记录，
      便于合规举证；``ip`` / ``user_agent`` / ``channel`` 为同意取证字段。

法律背景：
    * 《儿童个人信息网络保护规定》第八条——网络运营者应当设置专门的儿童
      个人信息保护规则，并指定专人负责儿童个人信息保护；
    * 第九条、第十条——收集、使用儿童个人信息前应以显著、清晰的方式告知
      儿童监护人并征得同意，同意须留痕备查；
    * 《个人信息保护法》第三十一条——不满十四周岁未成年人的个人信息属
      敏感个人信息，处理应取得父母或其他监护人同意，并制定专门处理规则。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, CreatedAtMixin, utcnow, uuid_pk

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查期引用，避免循环导入
    from app.models.user import User


class GuardianConsent(CreatedAtMixin, Base):
    """监护人同意留痕（一行 = 一个账号在一个规则版本上的同意）。"""

    __tablename__ = "guardian_consents"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="同意所属账号(users.id)",
    )
    guardian_phone: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="监护人手机号"
    )
    consent_version: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="同意的规则版本号"
    )
    agreed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        comment="监护人同意时间(UTC)",
    )
    channel: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, comment="提交渠道 web/miniapp"
    )
    ip: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="提交IP(合规取证)"
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True, comment="客户端UA(合规取证)"
    )

    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        UniqueConstraint(
            "user_id", "consent_version", name="uq_guardian_consents_user_version"
        ),
        {"comment": "监护人同意留痕表(V6.5 T13)"},
    )


__all__ = ["GuardianConsent"]

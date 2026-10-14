"""订阅与收费域模型：``subscriptions``、``orders``。

M2 收费骨架：
    * ``subscriptions`` 记录用户当前订阅状态（套餐、有效期、来源）。
    * ``orders`` 记录每一笔订单（mock 支付或真实支付共用）。
    * 套餐定义在 ``services/subscription_service.py`` 的 ``PLANS`` 常量里，
      不入库（改套餐配置不需要迁移数据）。

双方言兼容：GUID/JSONType 走 base.py 的 TypeDecorator，CHECK 约束用 String + CheckConstraint。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    GUID,
    Base,
    CreatedAtMixin,
    JSONType,
    TimestampMixin,
    uuid_pk,
)

# 订阅状态枚举（用 CHECK 约束，避免 PG ENUM）
SUB_STATUS_VALUES = ("active", "expired", "cancelled")
# 订单状态枚举
ORDER_STATUS_VALUES = ("pending", "paid", "failed", "refunded")
# 支付渠道（mock 为开发/试运营用，wechat 为真实接入预留）
PAY_CHANNEL_VALUES = ("mock", "wechat")


class Subscription(Base, TimestampMixin):
    """用户订阅记录。

    一个用户同一时间只有一条 ``status='active'`` 的记录；
    续费/升级时旧记录置 ``expired``，新建一条 ``active``。
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    plan_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="套餐编码 free/monthly/seasonal/yearly"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", comment="active/expired/cancelled"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="订阅生效时间(UTC)"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="订阅到期时间(UTC)"
    )
    source_order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        comment="开通来源订单(免费套餐为NULL)",
    )

    __table_args__ = (
        CheckConstraint("status IN ('active','expired','cancelled')", name="ck_subscriptions_status"),
        CheckConstraint("plan_code IN ('free','monthly','seasonal','yearly')", name="ck_subscriptions_plan"),
        Index("idx_subscriptions_user_active", "user_id", "status"),
        {"comment": "用户订阅表"},
    )


class Order(Base, CreatedAtMixin):
    """订单记录（mock 支付与真实支付共用）。"""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = uuid_pk()
    order_no: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, comment="业务订单号(唯一)"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="下单用户",
    )
    plan_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="购买的套餐编码"
    )
    amount: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, comment="实付金额(元)"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", comment="pending/paid/failed/refunded"
    )
    pay_channel: Mapped[str] = mapped_column(
        String(16), nullable=False, default="mock", comment="mock/wechat"
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="支付完成时间(UTC)"
    )
    extra: Mapped[Optional[dict]] = mapped_column(
        JSONType(), nullable=True, comment="附加信息(支付回调原始数据等)"
    )
    remark: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="备注"
    )

    __table_args__ = (
        CheckConstraint("status IN ('pending','paid','failed','refunded')", name="ck_orders_status"),
        CheckConstraint("pay_channel IN ('mock','wechat')", name="ck_orders_pay_channel"),
        Index("idx_orders_user_id", "user_id"),
        Index("idx_orders_status", "status"),
        {"comment": "订单表"},
    )


__all__ = [
    "Subscription",
    "Order",
    "SUB_STATUS_VALUES",
    "ORDER_STATUS_VALUES",
    "PAY_CHANNEL_VALUES",
]

"""订阅/权益模型（billing 域）。

V4.2 商业核心：支持多计划（plan）、订阅生命周期、额度控制。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

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

from app.models.base import Base, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.models.user import User

# 计划标识
PLAN_FREE: str = "free"
PLAN_BASIC: str = "basic"
PLAN_PRO: str = "pro"
PLAN_ENTERPRISE: str = "enterprise"

PLAN_VALUES = (PLAN_FREE, PLAN_BASIC, PLAN_PRO, PLAN_ENTERPRISE)

# 订阅状态
SUBSCRIPTION_STATUS_ACTIVE: str = "active"
SUBSCRIPTION_STATUS_CANCELLED: str = "cancelled"
SUBSCRIPTION_STATUS_EXPIRED: str = "expired"
SUBSCRIPTION_STATUS_PENDING: str = "pending"
SUBSCRIPTION_STATUS_REFUNDED: str = "refunded"

SUBSCRIPTION_STATUS_VALUES = (
    SUBSCRIPTION_STATUS_ACTIVE,
    SUBSCRIPTION_STATUS_CANCELLED,
    SUBSCRIPTION_STATUS_EXPIRED,
    SUBSCRIPTION_STATUS_PENDING,
    SUBSCRIPTION_STATUS_REFUNDED,
)

# 支付方式
PAYMENT_PROVIDER_LEMON: str = "lemonsqueezy"
PAYMENT_PROVIDER_STRIPE: str = "stripe"
PAYMENT_PROVIDER_ALIPAY: str = "alipay"
PAYMENT_PROVIDER_WECHAT: str = "wechat"

PAYMENT_PROVIDER_VALUES = (
    PAYMENT_PROVIDER_LEMON,
    PAYMENT_PROVIDER_STRIPE,
    PAYMENT_PROVIDER_ALIPAY,
    PAYMENT_PROVIDER_WECHAT,
)


class Plan(Base, TimestampMixin):
    """订阅计划定义（可运营后台配置）。"""

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, comment="计划标识：free/basic/pro/enterprise"
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="展示名称")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="计划描述")
    price_monthly: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="月付价格（分）"
    )
    price_yearly: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="年付价格（分）"
    )
    ai_daily_limit: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=20, comment="每日 AI 对话次数上限"
    )
    practice_daily_limit: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=50, comment="每日练习题数上限"
    )
    max_mistake_storage: Mapped[int] = mapped_column(
        Integer, nullable=False, default=500, comment="错题本存储上限"
    )
    has_advanced_analytics: Mapped[bool] = mapped_column(
        default=False, comment="是否包含高级学习分析"
    )
    has_family_sharing: Mapped[bool] = mapped_column(
        default=False, comment="是否支持家庭共享"
    )
    has_ocr_premium: Mapped[bool] = mapped_column(
        default=False, comment="是否包含高级 OCR 识别"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="展示排序"
    )
    is_visible: Mapped[bool] = mapped_column(
        default=True, comment="是否在前端展示"
    )

    __table_args__ = (
        CheckConstraint(f"slug IN {PLAN_VALUES}", name="ck_plans_slug"),
        Index("idx_plans_visible_order", "is_visible", "sort_order"),
        {"comment": "订阅计划定义表"},
    )


class Subscription(Base, TimestampMixin):
    """用户订阅记录。"""

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户",
    )
    plan_slug: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="计划标识",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SUBSCRIPTION_STATUS_PENDING,
        comment="订阅状态",
    )
    provider: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="支付提供商",
    )
    provider_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, comment="支付平台订阅 ID",
    )
    provider_customer_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, comment="支付平台客户 ID",
    )
    current_period_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="当前计费周期开始",
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="当前计费周期结束",
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="取消时间",
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        default=False, comment="是否在周期末取消",
    )
    amount_paid: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="已付金额（分）",
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="CNY", comment="货币代码",
    )
    interval: Mapped[str] = mapped_column(
        String(8), nullable=False, default="month", comment="计费周期：month/year",
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="支付平台原始 JSON",
    )

    user: Mapped["User"] = relationship(back_populates="subscriptions")

    __table_args__ = (
        CheckConstraint(
            f"status IN {SUBSCRIPTION_STATUS_VALUES}",
            name="ck_subscriptions_status",
        ),
        CheckConstraint(
            f"provider IN {PAYMENT_PROVIDER_VALUES}",
            name="ck_subscriptions_provider",
        ),
        CheckConstraint("interval IN ('month','year')", name="ck_subscriptions_interval"),
        Index("idx_subscriptions_user", "user_id"),
        Index("idx_subscriptions_provider_id", "provider", "provider_subscription_id"),
        {"comment": "用户订阅记录表"},
    )


class PaymentTransaction(Base, TimestampMixin):
    """支付流水（幂等对账用）。"""

    __tablename__ = "payment_transactions"

    id: Mapped[uuid.UUID] = uuid_pk()
    subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联订阅",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户",
    )
    provider: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="支付提供商",
    )
    provider_transaction_id: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="支付平台流水号",
    )
    amount: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="金额（分）",
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="CNY", comment="货币",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="流水状态：succeeded/failed/refunded/pending",
    )
    event_type: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, comment="webhook 事件类型",
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="原始 JSON",
    )

    __table_args__ = (
        CheckConstraint(
            f"provider IN {PAYMENT_PROVIDER_VALUES}",
            name="ck_payment_transactions_provider",
        ),
        Index("idx_payment_transactions_provider", "provider", "provider_transaction_id"),
        Index("idx_payment_transactions_user", "user_id"),
        {"comment": "支付流水表"},
    )


__all__ = [
    "Plan",
    "Subscription",
    "PaymentTransaction",
    "PLAN_FREE",
    "PLAN_BASIC",
    "PLAN_PRO",
    "PLAN_ENTERPRISE",
    "PLAN_VALUES",
    "SUBSCRIPTION_STATUS_ACTIVE",
    "SUBSCRIPTION_STATUS_CANCELLED",
    "SUBSCRIPTION_STATUS_EXPIRED",
    "SUBSCRIPTION_STATUS_PENDING",
    "SUBSCRIPTION_STATUS_REFUNDED",
    "SUBSCRIPTION_STATUS_VALUES",
    "PAYMENT_PROVIDER_LEMON",
    "PAYMENT_PROVIDER_STRIPE",
    "PAYMENT_PROVIDER_ALIPAY",
    "PAYMENT_PROVIDER_WECHAT",
    "PAYMENT_PROVIDER_VALUES",
]

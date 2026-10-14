"""订阅与收费域出入参 Schema（camelCase alias）。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from app.schemas.common import BaseSchema


class PlanInfo(BaseSchema):
    """套餐信息（展示用）。"""

    plan_code: str = Field(..., description="套餐编码 free/monthly/seasonal/yearly")
    name: str = Field(..., description="套餐名称")
    price: float = Field(..., description="价格(元)")
    duration_days: int = Field(..., description="有效天数")
    daily_ai_limit: int = Field(..., description="每日AI讲解上限")
    mistake_limit: int = Field(..., description="错题本容量上限")
    features: list[str] = Field(default_factory=list, description="功能列表")
    is_free: bool = Field(default=False, description="是否免费套餐")


class SubscriptionOut(BaseSchema):
    """当前订阅状态。"""

    plan_code: str = Field(..., description="套餐编码")
    plan_name: str = Field(..., description="套餐名称")
    status: str = Field(..., description="active/expired/cancelled")
    started_at: Optional[datetime] = Field(default=None, description="生效时间")
    expires_at: Optional[datetime] = Field(default=None, description="到期时间")
    daily_ai_limit: int = Field(..., description="每日AI上限")
    mistake_limit: int = Field(..., description="错题本上限")
    is_active: bool = Field(..., description="当前是否有效")


class CreateOrderIn(BaseSchema):
    """下单入参。"""

    plan_code: str = Field(..., description="套餐编码 monthly/seasonal/yearly")
    pay_channel: str = Field(default="mock", description="mock/wechat")


class OrderOut(BaseSchema):
    """订单信息。"""

    order_no: str = Field(..., description="订单号")
    plan_code: str = Field(..., description="套餐编码")
    amount: float = Field(..., description="金额")
    status: str = Field(..., description="pending/paid/failed")
    pay_channel: str = Field(..., description="支付渠道")
    created_at: datetime = Field(..., description="创建时间")
    paid_at: Optional[datetime] = Field(default=None, description="支付时间")


class MockPayIn(BaseSchema):
    """mock 支付确认入参。"""

    order_no: str = Field(..., description="订单号")


class EntitlementOut(BaseSchema):
    """权益信息。"""

    plan_code: str = Field(..., description="套餐编码")
    daily_ai_limit: int = Field(..., description="每日AI上限")
    mistake_limit: int = Field(..., description="错题本上限")
    today_ai_used: int = Field(..., description="今日已用AI次数")
    today_ai_remaining: int = Field(..., description="今日剩余AI次数")
    is_active: bool = Field(..., description="订阅是否有效")


__all__ = [
    "PlanInfo",
    "SubscriptionOut",
    "CreateOrderIn",
    "OrderOut",
    "MockPayIn",
    "EntitlementOut",
]

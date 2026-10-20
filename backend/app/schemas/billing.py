"""Billing / 订阅相关 Schema（Pydantic V2）。

⚠️ 别名方向约定（2026-10-20 修复）：
    前端（student-web）**收发都用 camelCase**，而后端 service 层产出的是
    snake_case 裸 dict。原先用 ``Field(alias="snake_case")`` 写法有两个致命错：

    1. ``alias`` 在 Pydantic V2 里是**输入**别名 —— 前端传 camelCase
       （``{"planSlug": ...}``）时校验直接报 "plan_slug Field required"；
    2. ``model_dump(by_alias=True)`` 输出的是 snake_case，前端拿不到
       ``dailyAiLimit``。

    正确写法：``validation_alias`` 管输入（认 camelCase）、``serialization_alias``
    管输出（出 camelCase），两者都指向 camelCase；输入同时兼容 snake_case
    （用 ``AliasChoices``），这样 service 层的 dict 也能直接 ``model_validate``。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator


def _uuid_to_str(value: object) -> object:
    """UUID → str（ORM ``Subscription.id`` 是 UUID，JSON 契约要字符串）。"""
    import uuid as _uuid

    if isinstance(value, _uuid.UUID):
        return str(value)
    return value


class _UuidStrMixin(BaseModel):
    """给 ``id`` 字段加 UUID→str 兼容（``from_attributes`` 场景）。"""

    @field_validator("id", mode="before", check_fields=False)
    @classmethod
    def _coerce_id(cls, v: object) -> object:
        return _uuid_to_str(v)


# ------------------------------------------------------------------ Plan

class PlanOut(BaseModel):
    """订阅计划展示。"""

    slug: str
    name: str
    description: Optional[str] = None
    priceMonthly: int = Field(
        ..., validation_alias=AliasChoices("priceMonthly", "price_monthly"),
        serialization_alias="priceMonthly",
    )
    priceYearly: int = Field(
        ..., validation_alias=AliasChoices("priceYearly", "price_yearly"),
        serialization_alias="priceYearly",
    )
    aiDailyLimit: int = Field(
        ..., validation_alias=AliasChoices("aiDailyLimit", "ai_daily_limit"),
        serialization_alias="aiDailyLimit",
    )
    practiceDailyLimit: int = Field(
        ..., validation_alias=AliasChoices("practiceDailyLimit", "practice_daily_limit"),
        serialization_alias="practiceDailyLimit",
    )
    maxMistakeStorage: int = Field(
        ..., validation_alias=AliasChoices("maxMistakeStorage", "max_mistake_storage"),
        serialization_alias="maxMistakeStorage",
    )
    hasAdvancedAnalytics: bool = Field(
        ..., validation_alias=AliasChoices("hasAdvancedAnalytics", "has_advanced_analytics"),
        serialization_alias="hasAdvancedAnalytics",
    )
    hasFamilySharing: bool = Field(
        ..., validation_alias=AliasChoices("hasFamilySharing", "has_family_sharing"),
        serialization_alias="hasFamilySharing",
    )
    hasOcrPremium: bool = Field(
        ..., validation_alias=AliasChoices("hasOcrPremium", "has_ocr_premium"),
        serialization_alias="hasOcrPremium",
    )

    class Config:
        populate_by_name = True
        from_attributes = True


# ------------------------------------------------------------------ Subscription

class SubscriptionOut(_UuidStrMixin):
    """订阅状态。"""

    id: str = Field(
        ..., validation_alias=AliasChoices("id"), serialization_alias="id",
    )
    planSlug: str = Field(
        ..., validation_alias=AliasChoices("planSlug", "plan_slug"),
        serialization_alias="planSlug",
    )
    status: str
    provider: str
    currentPeriodStart: Optional[datetime] = Field(
        None, validation_alias=AliasChoices("currentPeriodStart", "current_period_start"),
        serialization_alias="currentPeriodStart",
    )
    currentPeriodEnd: Optional[datetime] = Field(
        None, validation_alias=AliasChoices("currentPeriodEnd", "current_period_end"),
        serialization_alias="currentPeriodEnd",
    )
    cancelAtPeriodEnd: bool = Field(
        False, validation_alias=AliasChoices("cancelAtPeriodEnd", "cancel_at_period_end"),
        serialization_alias="cancelAtPeriodEnd",
    )
    interval: str
    amountPaid: int = Field(
        0, validation_alias=AliasChoices("amountPaid", "amount_paid"),
        serialization_alias="amountPaid",
    )
    currency: str
    createdAt: Optional[datetime] = Field(
        None, validation_alias=AliasChoices("createdAt", "created_at"),
        serialization_alias="createdAt",
    )

    class Config:
        populate_by_name = True
        from_attributes = True


# ------------------------------------------------------------------ Entitlement

class EntitlementOut(BaseModel):
    """用户当前权益（聚合视图）。"""

    plan: str
    planName: str = Field(
        ..., validation_alias=AliasChoices("planName", "plan_name"),
        serialization_alias="planName",
    )
    dailyAiLimit: int = Field(
        ..., validation_alias=AliasChoices("dailyAiLimit", "daily_ai_limit"),
        serialization_alias="dailyAiLimit",
    )
    practiceDailyLimit: int = Field(
        ..., validation_alias=AliasChoices("practiceDailyLimit", "practice_daily_limit"),
        serialization_alias="practiceDailyLimit",
    )
    maxMistakeStorage: int = Field(
        ..., validation_alias=AliasChoices("maxMistakeStorage", "max_mistake_storage"),
        serialization_alias="maxMistakeStorage",
    )
    hasAdvancedAnalytics: bool = Field(
        ..., validation_alias=AliasChoices("hasAdvancedAnalytics", "has_advanced_analytics"),
        serialization_alias="hasAdvancedAnalytics",
    )
    hasFamilySharing: bool = Field(
        ..., validation_alias=AliasChoices("hasFamilySharing", "has_family_sharing"),
        serialization_alias="hasFamilySharing",
    )
    hasOcrPremium: bool = Field(
        ..., validation_alias=AliasChoices("hasOcrPremium", "has_ocr_premium"),
        serialization_alias="hasOcrPremium",
    )
    aiSessionsToday: int = Field(
        0, validation_alias=AliasChoices("aiSessionsToday", "ai_sessions_today"),
        serialization_alias="aiSessionsToday",
    )
    aiAttemptsToday: int = Field(
        0, validation_alias=AliasChoices("aiAttemptsToday", "ai_attempts_today"),
        serialization_alias="aiAttemptsToday",
    )
    expiresAt: Optional[datetime] = Field(
        None, validation_alias=AliasChoices("expiresAt", "expires_at"),
        serialization_alias="expiresAt",
    )
    isActive: bool = Field(
        True, validation_alias=AliasChoices("isActive", "is_active"),
        serialization_alias="isActive",
    )

    class Config:
        populate_by_name = True


# ------------------------------------------------------------------ Checkout

class CheckoutCreateIn(BaseModel):
    """创建结账会话。"""

    planSlug: str = Field(
        ..., min_length=1,
        validation_alias=AliasChoices("planSlug", "plan_slug"),
        serialization_alias="planSlug",
    )
    interval: str = Field("month", pattern="^(month|year)$")
    successUrl: Optional[str] = Field(
        None, validation_alias=AliasChoices("successUrl", "success_url"),
        serialization_alias="successUrl",
    )
    cancelUrl: Optional[str] = Field(
        None, validation_alias=AliasChoices("cancelUrl", "cancel_url"),
        serialization_alias="cancelUrl",
    )

    class Config:
        populate_by_name = True


class CheckoutOut(BaseModel):
    """结账会话返回。"""

    checkoutUrl: str = Field(
        ..., validation_alias=AliasChoices("checkoutUrl", "checkout_url"),
        serialization_alias="checkoutUrl",
    )
    sessionId: str = Field(
        ..., validation_alias=AliasChoices("sessionId", "session_id"),
        serialization_alias="sessionId",
    )

    class Config:
        populate_by_name = True


# ------------------------------------------------------------------ Webhook

class WebhookPayload(BaseModel):
    """通用 webhook 入参（ Lemon Squeezy / Stripe 风格）。"""

    event: str
    data: dict


__all__ = [
    "PlanOut",
    "SubscriptionOut",
    "EntitlementOut",
    "CheckoutCreateIn",
    "CheckoutOut",
    "WebhookPayload",
]

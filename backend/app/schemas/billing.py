"""Billing / 订阅相关 Schema（Pydantic V2）。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ Plan

class PlanOut(BaseModel):
    """订阅计划展示。"""

    slug: str
    name: str
    description: Optional[str] = None
    priceMonthly: int = Field(..., alias="price_monthly")
    priceYearly: int = Field(..., alias="price_yearly")
    aiDailyLimit: int = Field(..., alias="ai_daily_limit")
    practiceDailyLimit: int = Field(..., alias="practice_daily_limit")
    maxMistakeStorage: int = Field(..., alias="max_mistake_storage")
    hasAdvancedAnalytics: bool = Field(..., alias="has_advanced_analytics")
    hasFamilySharing: bool = Field(..., alias="has_family_sharing")
    hasOcrPremium: bool = Field(..., alias="has_ocr_premium")

    class Config:
        populate_by_name = True
        from_attributes = True


# ------------------------------------------------------------------ Subscription

class SubscriptionOut(BaseModel):
    """订阅状态。"""

    id: str
    planSlug: str = Field(..., alias="plan_slug")
    status: str
    provider: str
    currentPeriodStart: Optional[datetime] = Field(None, alias="current_period_start")
    currentPeriodEnd: Optional[datetime] = Field(None, alias="current_period_end")
    cancelAtPeriodEnd: bool = Field(False, alias="cancel_at_period_end")
    interval: str
    amountPaid: int = Field(0, alias="amount_paid")
    currency: str
    createdAt: Optional[datetime] = Field(None, alias="created_at")

    class Config:
        populate_by_name = True
        from_attributes = True


# ------------------------------------------------------------------ Entitlement

class EntitlementOut(BaseModel):
    """用户当前权益（聚合视图）。"""

    plan: str
    planName: str = Field(..., alias="plan_name")
    dailyAiLimit: int = Field(..., alias="daily_ai_limit")
    practiceDailyLimit: int = Field(..., alias="practice_daily_limit")
    maxMistakeStorage: int = Field(..., alias="max_mistake_storage")
    hasAdvancedAnalytics: bool = Field(..., alias="has_advanced_analytics")
    hasFamilySharing: bool = Field(..., alias="has_family_sharing")
    hasOcrPremium: bool = Field(..., alias="has_ocr_premium")
    aiSessionsToday: int = Field(0, alias="ai_sessions_today")
    aiAttemptsToday: int = Field(0, alias="ai_attempts_today")
    expiresAt: Optional[datetime] = Field(None, alias="expires_at")
    isActive: bool = Field(True, alias="is_active")

    class Config:
        populate_by_name = True


# ------------------------------------------------------------------ Checkout

class CheckoutCreateIn(BaseModel):
    """创建结账会话。"""

    planSlug: str = Field(..., alias="plan_slug", min_length=1)
    interval: str = Field("month", pattern="^(month|year)$")
    successUrl: Optional[str] = Field(None, alias="success_url")
    cancelUrl: Optional[str] = Field(None, alias="cancel_url")


class CheckoutOut(BaseModel):
    """结账会话返回。"""

    checkoutUrl: str = Field(..., alias="checkout_url")
    sessionId: str = Field(..., alias="session_id")


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

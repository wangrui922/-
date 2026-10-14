"""Billing / 订阅 API。

路由：
    GET  /api/billing/plans            — 计划列表（公开）
    GET  /api/billing/entitlement      — 当前用户权益（需登录）
    GET  /api/billing/subscription     — 当前订阅状态（需登录）
    POST /api/billing/checkout         — 创建结账会话（需登录）
    POST /api/billing/webhook/{provider} — 支付平台 webhook（公开，需签名验证占位）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.db import get_db
from app.schemas.billing import CheckoutCreateIn
from app.services import billing_service

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans")
async def list_plans(db: Session = Depends(get_db)) -> dict:
    """获取所有可见订阅计划。"""
    plans = billing_service.list_visible_plans(db)
    return ok([{
        "slug": p.slug,
        "name": p.name,
        "description": p.description,
        "priceMonthly": p.price_monthly,
        "priceYearly": p.price_yearly,
        "aiDailyLimit": p.ai_daily_limit,
        "practiceDailyLimit": p.practice_daily_limit,
        "maxMistakeStorage": p.max_mistake_storage,
        "hasAdvancedAnalytics": p.has_advanced_analytics,
        "hasFamilySharing": p.has_family_sharing,
        "hasOcrPremium": p.has_ocr_premium,
    } for p in plans])


@router.get("/entitlement")
async def get_entitlement(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """当前登录用户的权益聚合。"""
    data = billing_service.get_user_entitlement(db, user.id)
    return ok(data)


@router.get("/subscription")
async def get_subscription(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """当前用户的有效订阅（如有）。"""
    sub = billing_service.get_active_subscription(db, user.id)
    if sub is None:
        return ok(None)
    return ok({
        "id": str(sub.id),
        "planSlug": sub.plan_slug,
        "status": sub.status,
        "provider": sub.provider,
        "currentPeriodStart": sub.current_period_start,
        "currentPeriodEnd": sub.current_period_end,
        "cancelAtPeriodEnd": sub.cancel_at_period_end,
        "interval": sub.interval,
        "amountPaid": sub.amount_paid,
        "currency": sub.currency,
        "createdAt": sub.created_at,
    })


@router.post("/checkout")
async def create_checkout(
    payload: CheckoutCreateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """创建订阅结账会话。"""
    result = billing_service.create_checkout_session(
        db, user,
        plan_slug=payload.planSlug,
        interval=payload.interval,
        success_url=payload.successUrl,
        cancel_url=payload.cancelUrl,
    )
    return ok(result)


@router.post("/webhook/{provider}")
async def webhook(
    provider: str,
    payload: dict,
    db: Session = Depends(get_db),
) -> dict:
    """接收支付平台 webhook（Lemon Squeezy / Stripe / 支付宝 / 微信）。

    当前为简化实现：直接透传 payload 处理，不做签名验证（生产环境务必补签名校验）。
    """
    event = payload.get("event", payload.get("type", "unknown"))
    billing_service.process_webhook(db, provider, event, payload)
    return ok({"received": True})

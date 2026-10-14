"""订阅与收费 API 路由。

    GET  /api/plans              套餐列表（无鉴权）
    GET  /api/subscription       当前订阅状态（需登录）
    GET  /api/entitlement        当前权益+今日用量（需登录）
    POST /api/orders             创建订单（需登录）
    POST /api/orders/mock-pay    mock 支付确认（需登录）
    GET  /api/orders             用户订单列表（需登录）
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.db import get_db
from app.schemas.subscription import CreateOrderIn, MockPayIn
from app.services import subscription_service as svc

router = APIRouter(tags=["subscription"])


# ---- 套餐列表（无需登录）----------------------------------------------------
@router.get("/plans")
async def list_plans() -> dict:
    """获取所有套餐信息。"""
    plans = svc.list_plans()
    data = [
        {
            "planCode": p.code,
            "name": p.name,
            "price": p.price,
            "durationDays": p.duration_days,
            "dailyAiLimit": p.daily_ai_limit,
            "mistakeLimit": p.mistake_limit,
            "features": p.features,
            "isFree": p.is_free,
        }
        for p in plans
    ]
    return ok(data)


# ---- 当前订阅状态 -----------------------------------------------------------
@router.get("/subscription")
async def get_subscription(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取当前用户的订阅状态。"""
    uid = uuid.UUID(user.sub)
    ent = svc.get_entitlement(db, uid)
    db.commit()
    data = {
        "planCode": ent.plan_code,
        "planName": ent.plan_name,
        "status": "active" if ent.is_active else "expired",
        "startedAt": ent.started_at.isoformat() if ent.started_at else None,
        "expiresAt": ent.expires_at.isoformat() if ent.expires_at else None,
        "dailyAiLimit": ent.daily_ai_limit,
        "mistakeLimit": ent.mistake_limit,
        "isActive": ent.is_active,
    }
    return ok(data)


# ---- 当前权益 + 今日用量 ----------------------------------------------------
@router.get("/entitlement")
async def get_entitlement(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取当前权益和今日 AI 用量。"""
    uid = uuid.UUID(user.sub)
    ent = svc.get_entitlement(db, uid)

    # 查今日用量
    today_used = 0
    student_id = svc._resolve_student_id(db, uid)
    if student_id:
        today_used = svc.count_today_ai_usage(db, student_id)

    today_remaining = (
        -1 if ent.daily_ai_limit == -1 else max(0, ent.daily_ai_limit - today_used)
    )

    db.commit()
    data = {
        "planCode": ent.plan_code,
        "dailyAiLimit": ent.daily_ai_limit,
        "mistakeLimit": ent.mistake_limit,
        "todayAiUsed": today_used,
        "todayAiRemaining": today_remaining,
        "isActive": ent.is_active,
    }
    return ok(data)


# ---- 创建订单 ---------------------------------------------------------------
@router.post("/orders")
async def create_order(
    payload: CreateOrderIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """创建订单（status=pending）。"""
    uid = uuid.UUID(user.sub)
    order = svc.create_order(db, uid, payload.plan_code, payload.pay_channel)
    db.commit()
    data = {
        "orderNo": order.order_no,
        "planCode": order.plan_code,
        "amount": float(order.amount),
        "status": order.status,
        "payChannel": order.pay_channel,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
    }
    return ok(data)


# ---- mock 支付确认 ----------------------------------------------------------
@router.post("/orders/mock-pay")
async def mock_pay(
    payload: MockPayIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """mock 支付确认：标记订单已支付并开通订阅。"""
    uid = uuid.UUID(user.sub)
    order = svc.confirm_mock_pay(db, payload.order_no)

    # 安全校验：订单必须属于当前用户
    if order.user_id != uid:
        from app.core.response import ApiError, CODE_FORBIDDEN

        raise ApiError(CODE_FORBIDDEN, "无权操作此订单")

    db.commit()
    data = {
        "orderNo": order.order_no,
        "status": order.status,
        "paidAt": order.paid_at.isoformat() if order.paid_at else None,
    }
    return ok(data)


# ---- 用户订单列表 -----------------------------------------------------------
@router.get("/orders")
async def list_orders(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取用户订单列表。"""
    uid = uuid.UUID(user.sub)
    orders = svc.list_user_orders(db, uid)
    data = [
        {
            "orderNo": o.order_no,
            "planCode": o.plan_code,
            "amount": float(o.amount),
            "status": o.status,
            "payChannel": o.pay_channel,
            "createdAt": o.created_at.isoformat() if o.created_at else None,
            "paidAt": o.paid_at.isoformat() if o.paid_at else None,
        }
        for o in orders
    ]
    return ok(data)


__all__ = ["router"]

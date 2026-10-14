"""订阅与收费服务层。

职责：
    * 套餐定义（PLANS 常量，不入库）。
    * 查询用户当前有效订阅与权益。
    * 创建订单、mock 支付确认、开通订阅。
    * 日配额计数（基于 DB 查询，无 Redis）。

设计要点：
    * 免费套餐不需要订单，用户注册时自动开通。
    * mock 支付：下单 → 确认支付 → 开通订阅，三步走。
    * 真实微信支付接入时只需替换 ``confirm_mock_pay`` 为微信回调处理。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import (
    ApiError,
    CODE_BAD_REQUEST,
    CODE_NOT_FOUND,
    CODE_SERVER_ERROR,
    CODE_TOO_MANY_REQUESTS,
)
from app.models.subscription import Order, Subscription


# ============================================================ 套餐配置
@dataclass(frozen=True)
class PlanConfig:
    """套餐配置（不入库，改配置不需要迁移数据）。"""

    code: str
    name: str
    price: float
    duration_days: int  # 0 = 永久(免费)
    daily_ai_limit: int  # -1 = 无限
    mistake_limit: int  # -1 = 无限
    features: List[str]
    is_free: bool = False


PLANS: Dict[str, PlanConfig] = {
    "free": PlanConfig(
        code="free",
        name="免费体验",
        price=0.0,
        duration_days=0,
        daily_ai_limit=3,
        mistake_limit=50,
        features=["每日3次AI讲解", "错题本50题", "基础练习"],
        is_free=True,
    ),
    "monthly": PlanConfig(
        code="monthly",
        name="月卡会员",
        price=29.0,
        duration_days=30,
        daily_ai_limit=100,
        mistake_limit=2000,
        features=["每日100次AI讲解", "错题本2000题", "全部练习", "学情分析"],
    ),
    "seasonal": PlanConfig(
        code="seasonal",
        name="季卡会员",
        price=79.0,
        duration_days=90,
        daily_ai_limit=100,
        mistake_limit=5000,
        features=["每日100次AI讲解", "错题本5000题", "全部练习", "学情分析", "家长周报"],
    ),
    "yearly": PlanConfig(
        code="yearly",
        name="年卡会员",
        price=249.0,
        duration_days=365,
        daily_ai_limit=-1,  # 无限
        mistake_limit=-1,  # 无限
        features=["无限AI讲解", "无限错题本", "全部功能", "学情分析", "家长周报", "优先客服"],
    ),
}


def get_plan(code: str) -> Optional[PlanConfig]:
    return PLANS.get(code)


def list_plans() -> List[PlanConfig]:
    return list(PLANS.values())


# ============================================================ 权益查询
@dataclass
class EntitlementInfo:
    """当前用户权益快照。"""

    plan_code: str
    plan_name: str
    is_active: bool
    daily_ai_limit: int
    mistake_limit: int
    started_at: Optional[datetime]
    expires_at: Optional[datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_active_subscription(session: Session, user_id: uuid.UUID) -> Optional[Subscription]:
    """查询用户当前有效订阅（status=active 且未过期）。"""
    now = _utcnow()
    stmt = (
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.expires_at > now,
        )
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_or_create_free_subscription(session: Session, user_id: uuid.UUID) -> Subscription:
    """获取或创建免费订阅（新用户自动开通）。"""
    sub = get_active_subscription(session, user_id)
    if sub:
        return sub

    # 创建免费订阅，有效期 100 年（实际等于永久）
    now = _utcnow()
    sub = Subscription(
        user_id=user_id,
        plan_code="free",
        status="active",
        started_at=now,
        expires_at=now + timedelta(days=36500),
    )
    session.add(sub)
    session.flush()
    return sub


def get_entitlement(session: Session, user_id: uuid.UUID) -> EntitlementInfo:
    """获取用户当前权益。无订阅时自动开通免费套餐。"""
    sub = get_or_create_free_subscription(session, user_id)
    plan = PLANS.get(sub.plan_code, PLANS["free"])
    now = _utcnow()
    is_active = sub.status == "active" and sub.expires_at > now
    return EntitlementInfo(
        plan_code=sub.plan_code,
        plan_name=plan.name,
        is_active=is_active,
        daily_ai_limit=plan.daily_ai_limit,
        mistake_limit=plan.mistake_limit,
        started_at=sub.started_at,
        expires_at=sub.expires_at,
    )


# ============================================================ 日配额计数
def _resolve_student_id(session: Session, user_id: uuid.UUID) -> Optional[uuid.UUID]:
    """通过 user_id 查关联 student_id（users→students 1:1）。"""
    from app.models.user import Student

    stmt = select(Student.id).where(Student.user_id == user_id)
    return session.execute(stmt).scalar_one_or_none()


def count_today_ai_usage(session: Session, student_id: uuid.UUID) -> int:
    """统计学生今日 AI 调用次数（ai_messages JOIN ai_sessions）。"""
    from app.models.ai import AiMessage, AiSession

    now = _utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = (
        select(func.count(AiMessage.id))
        .join(AiSession, AiMessage.session_id == AiSession.id)
        .where(
            AiSession.student_id == student_id,
            AiMessage.role == "assistant",
            AiMessage.created_at >= today_start,
        )
    )
    return session.execute(stmt).scalar() or 0


def check_ai_quota(session: Session, user_id: uuid.UUID) -> None:
    """检查 AI 讲解配额，超限抛 429。"""
    ent = get_entitlement(session, user_id)
    if ent.daily_ai_limit == -1:
        return  # 无限
    student_id = _resolve_student_id(session, user_id)
    if not student_id:
        return  # 无学生档案，不计数
    used = count_today_ai_usage(session, student_id)
    if used >= ent.daily_ai_limit:
        raise ApiError(
            CODE_TOO_MANY_REQUESTS,
            f"今日AI讲解已达上限({ent.daily_ai_limit}次)，升级会员可获更多次数",
        )


def check_mistake_quota(session: Session, user_id: uuid.UUID, current_count: int) -> None:
    """检查错题本容量，超限抛 429。"""
    ent = get_entitlement(session, user_id)
    if ent.mistake_limit == -1:
        return  # 无限
    if current_count >= ent.mistake_limit:
        raise ApiError(
            CODE_TOO_MANY_REQUESTS,
            f"错题本已满({ent.mistake_limit}题)，升级会员可扩容",
        )


# ============================================================ 订单与支付
def create_order(
    session: Session,
    user_id: uuid.UUID,
    plan_code: str,
    pay_channel: str = "mock",
) -> Order:
    """创建订单（status=pending）。"""
    plan = get_plan(plan_code)
    if not plan:
        raise ApiError(CODE_NOT_FOUND, f"套餐 {plan_code} 不存在")
    if plan.is_free:
        raise ApiError(CODE_BAD_REQUEST, "免费套餐不需要下单")

    now = _utcnow()
    order_no = f"MC{now.strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:8].upper()}"

    order = Order(
        order_no=order_no,
        user_id=user_id,
        plan_code=plan_code,
        amount=plan.price,
        status="pending",
        pay_channel=pay_channel,
    )
    session.add(order)
    session.flush()
    return order


def confirm_mock_pay(session: Session, order_no: str) -> Order:
    """mock 支付确认：标记订单已支付并开通订阅。"""
    stmt = select(Order).where(Order.order_no == order_no)
    order = session.execute(stmt).scalar_one_or_none()
    if not order:
        raise ApiError(CODE_NOT_FOUND, f"订单 {order_no} 不存在")
    if order.status == "paid":
        raise ApiError(CODE_BAD_REQUEST, "该订单已支付，请勿重复操作")
    if order.status in ("failed", "refunded"):
        raise ApiError(CODE_BAD_REQUEST, f"订单状态为 {order.status}，无法支付")

    plan = get_plan(order.plan_code)
    if not plan:
        raise ApiError(CODE_SERVER_ERROR, "套餐配置缺失")

    now = _utcnow()

    # 1. 标记订单已支付
    order.status = "paid"
    order.paid_at = now

    # 2. 将用户之前的 active 订阅置为 expired
    old_subs = session.execute(
        select(Subscription).where(
            Subscription.user_id == order.user_id,
            Subscription.status == "active",
        )
    ).scalars().all()
    for old in old_subs:
        old.status = "expired"

    # 3. 开通新订阅
    new_sub = Subscription(
        user_id=order.user_id,
        plan_code=plan.code,
        status="active",
        started_at=now,
        expires_at=now + timedelta(days=plan.duration_days),
        source_order_id=order.id,
    )
    session.add(new_sub)
    session.flush()
    return order


def get_order_by_no(session: Session, order_no: str) -> Optional[Order]:
    return session.execute(select(Order).where(Order.order_no == order_no)).scalar_one_or_none()


def list_user_orders(session: Session, user_id: uuid.UUID, limit: int = 20) -> List[Order]:
    stmt = (
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


__all__ = [
    "PlanConfig",
    "PLANS",
    "get_plan",
    "list_plans",
    "EntitlementInfo",
    "get_active_subscription",
    "get_or_create_free_subscription",
    "get_entitlement",
    "count_today_ai_usage",
    "check_ai_quota",
    "check_mistake_quota",
    "create_order",
    "confirm_mock_pay",
    "get_order_by_no",
    "list_user_orders",
]

"""Billing / 订阅权益服务。

核心：
    * 查询计划列表 / 用户当前权益
    * 处理支付 webhook（幂等，防重放）
    * 创建结账会话（ Lemon Squeezy / Stripe 风格模拟 ）
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_NOT_FOUND, CODE_SERVER_ERROR
from app.models import User
from app.models.ai import AiSession
from app.models.learning import Attempt
from app.models.subscription import (
    PAYMENT_PROVIDER_LEMON,
    PLAN_BASIC,
    PLAN_ENTERPRISE,
    PLAN_FREE,
    PLAN_PRO,
    PLAN_VALUES,
    SUBSCRIPTION_STATUS_ACTIVE,
    SUBSCRIPTION_STATUS_CANCELLED,
    SUBSCRIPTION_STATUS_EXPIRED,
    SUBSCRIPTION_STATUS_REFUNDED,
    SUBSCRIPTION_STATUS_VALUES,
    PaymentTransaction,
    Plan,
    Subscription,
)

logger = logging.getLogger("app.services.billing")

# ---- 默认计划配置（用于 seed / 兜底）--------------------------------------------

DEFAULT_PLANS: List[Dict[str, Any]] = [
    {
        "slug": PLAN_FREE,
        "name": "免费版",
        "description": "内测期全免费：AI 对话与练习均不限次数",
        "price_monthly": 0,
        "price_yearly": 0,
        "ai_daily_limit": -1,
        "practice_daily_limit": -1,
        "max_mistake_storage": 99999,
        "has_advanced_analytics": False,
        "has_family_sharing": False,
        "has_ocr_premium": False,
        "sort_order": 0,
    },
    {
        "slug": PLAN_BASIC,
        "name": "基础版",
        "description": "每日 100 次 AI 对话，200 道练习题，家庭共享 2 人",
        "price_monthly": 2900,
        "price_yearly": 29000,
        "ai_daily_limit": 100,
        "practice_daily_limit": 200,
        "max_mistake_storage": 2000,
        "has_advanced_analytics": True,
        "has_family_sharing": True,
        "has_ocr_premium": False,
        "sort_order": 1,
    },
    {
        "slug": PLAN_PRO,
        "name": "专业版",
        "description": "无限 AI 对话，500 道练习题，家庭共享 4 人，高级 OCR",
        "price_monthly": 5900,
        "price_yearly": 59000,
        "ai_daily_limit": 9999,
        "practice_daily_limit": 500,
        "max_mistake_storage": 10000,
        "has_advanced_analytics": True,
        "has_family_sharing": True,
        "has_ocr_premium": True,
        "sort_order": 2,
    },
    {
        "slug": PLAN_ENTERPRISE,
        "name": "机构版",
        "description": "定制化方案，请联系商务",
        "price_monthly": 0,
        "price_yearly": 0,
        "ai_daily_limit": 9999,
        "practice_daily_limit": 9999,
        "max_mistake_storage": 99999,
        "has_advanced_analytics": True,
        "has_family_sharing": True,
        "has_ocr_premium": True,
        "sort_order": 3,
    },
]


# ---- Seed ----------------------------------------------------------------------

def seed_plans(session: Session) -> None:
    """幂等插入默认计划（部署初始化用）。

    对 free 档额外做**配额同步**：内测期口径变更（全免费不限额）后，
    库里已存在的 free 行不会因"已存在"而更新，必须显式对齐，
    否则运营看到的仍是旧额度。这里只同步配额与描述，不动价格。
    """
    existing = session.execute(select(Plan.slug)).scalars().all()
    existing_set = set(existing)
    for p in DEFAULT_PLANS:
        if p["slug"] not in existing_set:
            session.add(Plan(**p))
            logger.info("[billing] seed plan: %s", p["slug"])
    session.flush()

    sync_free_plan_limits(session)


def sync_free_plan_limits(session: Session) -> None:
    """把 free 档的配额/描述对齐到 DEFAULT_PLANS（幂等，不动价格）。"""
    target = next(p for p in DEFAULT_PLANS if p["slug"] == PLAN_FREE)
    plan = session.execute(
        select(Plan).where(Plan.slug == PLAN_FREE)
    ).scalar_one_or_none()
    if plan is None:
        return
    changed = False
    for field in (
        "name",
        "description",
        "ai_daily_limit",
        "practice_daily_limit",
        "max_mistake_storage",
    ):
        new_value = target[field]
        if getattr(plan, field) != new_value:
            setattr(plan, field, new_value)
            changed = True
    if changed:
        session.flush()
        logger.info(
            "[billing] free 档配额已同步: ai=%s practice=%s mistake=%s",
            plan.ai_daily_limit,
            plan.practice_daily_limit,
            plan.max_mistake_storage,
        )


# ---- 查询 ----------------------------------------------------------------------

def list_visible_plans(session: Session) -> List[Plan]:
    """返回前端展示的计划列表（按 sort_order）。"""
    return session.execute(
        select(Plan).where(Plan.is_visible == True).order_by(Plan.sort_order)
    ).scalars().all()


def get_plan_by_slug(session: Session, slug: str) -> Optional[Plan]:
    """按 slug 查计划；找不到抛 404。"""
    plan = session.execute(select(Plan).where(Plan.slug == slug)).scalar_one_or_none()
    if plan is None:
        raise ApiError(CODE_NOT_FOUND, f"计划 {slug} 不存在")
    return plan


def get_active_subscription(session: Session, user_id: uuid.UUID) -> Optional[Subscription]:
    """取用户当前有效订阅（最新一条 active 且未过期）。"""
    now = datetime.now(timezone.utc)
    sub = session.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == SUBSCRIPTION_STATUS_ACTIVE,
            Subscription.current_period_end > now,
        )
        .order_by(Subscription.current_period_end.desc())
    ).scalar_one_or_none()
    return sub


def get_user_entitlement(session: Session, user_id: uuid.UUID) -> Dict[str, Any]:
    """聚合用户当前权益（优先取 active 订阅，否则回退 free）。"""
    sub = get_active_subscription(session, user_id)
    if sub is not None:
        plan = get_plan_by_slug(session, sub.plan_slug)
        expires = sub.current_period_end
    else:
        plan = get_plan_by_slug(session, PLAN_FREE)
        expires = None

    # 今日用量统计（通过 Student 关联）
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # 查找学生的 student_id
    from app.models.user import Student
    student = session.execute(
        select(Student).where(Student.user_id == user_id)
    ).scalar_one_or_none()
    student_id = student.id if student else None

    # AI 会话次数
    if student_id:
        ai_sessions_count = session.execute(
            select(AiSession).where(
                AiSession.student_id == student_id,
                AiSession.created_at >= today_start,
            )
        ).scalars().all()
        ai_sessions_today = len(ai_sessions_count)
    else:
        ai_sessions_today = 0

    # 练习尝试次数
    if student_id:
        attempts_today = session.execute(
            select(Attempt).where(
                Attempt.student_id == student_id,
                Attempt.created_at >= today_start,
            )
        ).scalars().all()
        ai_attempts_today = len(attempts_today)
    else:
        ai_attempts_today = 0

    return {
        "plan": plan.slug,
        "plan_name": plan.name,
        "daily_ai_limit": plan.ai_daily_limit,
        "practice_daily_limit": plan.practice_daily_limit,
        "max_mistake_storage": plan.max_mistake_storage,
        "has_advanced_analytics": plan.has_advanced_analytics,
        "has_family_sharing": plan.has_family_sharing,
        "has_ocr_premium": plan.has_ocr_premium,
        "ai_sessions_today": ai_sessions_today,
        "ai_attempts_today": ai_attempts_today,
        "expires_at": expires,
        "is_active": sub is not None and sub.status == SUBSCRIPTION_STATUS_ACTIVE,
    }


# ---- Webhook（幂等处理）--------------------------------------------------------

def _find_transaction(
    session: Session,
    provider: str,
    provider_transaction_id: str,
) -> Optional[PaymentTransaction]:
    """按支付平台流水号查流水（幂等键）。"""
    return session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.provider == provider,
            PaymentTransaction.provider_transaction_id == provider_transaction_id,
        )
    ).scalar_one_or_none()


def _update_subscription_from_webhook(
    session: Session,
    subscription_id: uuid.UUID,
    event: str,
    payload: Dict[str, Any],
) -> None:
    """根据 webhook 事件更新订阅状态。"""
    sub = session.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    ).scalar_one_or_none()
    if sub is None:
        logger.warning("[billing] webhook: subscription %s not found", subscription_id)
        return

    now = datetime.now(timezone.utc)

    if event in ("subscription_created", "subscription_payment_success", "subscription_updated"):
        sub.status = SUBSCRIPTION_STATUS_ACTIVE
        # 尝试从 payload 解析周期
        if "current_period_start" in payload:
            sub.current_period_start = _parse_ts(payload.get("current_period_start"))
        if "current_period_end" in payload:
            sub.current_period_end = _parse_ts(payload.get("current_period_end"))
        if "cancel_at_period_end" in payload:
            sub.cancel_at_period_end = bool(payload.get("cancel_at_period_end"))
        logger.info("[billing] subscription %s activated", subscription_id)

    elif event in ("subscription_cancelled", "subscription_canceled"):
        sub.status = SUBSCRIPTION_STATUS_CANCELLED
        sub.cancelled_at = now
        logger.info("[billing] subscription %s cancelled", subscription_id)

    elif event == "subscription_expired":
        sub.status = SUBSCRIPTION_STATUS_EXPIRED
        logger.info("[billing] subscription %s expired", subscription_id)

    elif event == "subscription_refunded":
        sub.status = SUBSCRIPTION_STATUS_REFUNDED
        logger.info("[billing] subscription %s refunded", subscription_id)

    session.flush()


def _parse_ts(ts: Any) -> Optional[datetime]:
    """解析时间戳（支持 ISO 字符串 / Unix 秒 / Unix 毫秒）。"""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        if ts > 1_000_000_000_000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return None


def process_webhook(
    session: Session,
    provider: str,
    event: str,
    payload: Dict[str, Any],
) -> None:
    """处理支付平台 webhook，幂等更新订阅与流水。

    Args:
        session: 数据库会话。
        provider: 支付平台标识（lemonsqueezy / stripe / ...）。
        event: webhook 事件类型。
        payload: 原始事件 payload。
    """
    # 提取关键字段（不同 provider 字段名不同，做兼容）
    transaction_id = _extract_transaction_id(provider, payload)
    if transaction_id is None:
        logger.warning("[billing] webhook: no transaction_id extracted")
        return

    # 幂等：已处理过直接跳过
    existing = _find_transaction(session, provider, transaction_id)
    if existing is not None:
        logger.info("[billing] webhook: transaction %s already processed", transaction_id)
        return

    # 提取订阅与用户
    subscription_ext_id = _extract_subscription_id(provider, payload)
    user_id = _extract_user_id(provider, payload)
    amount = _extract_amount(provider, payload)
    currency = _extract_currency(provider, payload)

    # 查找本地订阅记录（通过 provider_subscription_id）
    local_sub: Optional[Subscription] = None
    if subscription_ext_id:
        local_sub = session.execute(
            select(Subscription).where(
                Subscription.provider_subscription_id == subscription_ext_id,
            )
        ).scalar_one_or_none()

    # 创建流水
    tx = PaymentTransaction(
        subscription_id=local_sub.id if local_sub else None,
        user_id=user_id or (local_sub.user_id if local_sub else uuid.UUID(int=0)),
        provider=provider,
        provider_transaction_id=transaction_id,
        amount=amount or 0,
        currency=currency or "CNY",
        status="succeeded" if event.endswith("success") else ("refunded" if "refund" in event else "pending"),
        event_type=event,
        metadata_json=json.dumps(payload, ensure_ascii=False, default=str),
    )
    session.add(tx)
    session.flush()

    # 更新订阅状态
    if local_sub is not None:
        _update_subscription_from_webhook(session, local_sub.id, event, payload)

    logger.info("[billing] webhook processed: %s %s", provider, event)


# ---- 字段提取辅助（兼容多 provider）----------------------------------------------

def _extract_transaction_id(provider: str, payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data", payload)
    if provider == PAYMENT_PROVIDER_LEMON:
        return data.get("id") or data.get("attributes", {}).get("order_number")
    return data.get("id")


def _extract_subscription_id(provider: str, payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data", payload)
    attrs = data.get("attributes", data)
    if provider == PAYMENT_PROVIDER_LEMON:
        return attrs.get("subscription_id") or attrs.get("first_subscription_item", {}).get("subscription_id")
    return attrs.get("subscription")


def _extract_user_id(provider: str, payload: Dict[str, Any]) -> Optional[uuid.UUID]:
    """尝试从 webhook payload 提取用户 ID（通过 custom data）。"""
    data = payload.get("data", payload)
    attrs = data.get("attributes", data)
    custom = attrs.get("custom_data", attrs.get("metadata", {}))
    user_id_str = custom.get("user_id")
    if user_id_str:
        try:
            return uuid.UUID(user_id_str)
        except ValueError:
            pass
    return None


def _extract_amount(provider: str, payload: Dict[str, Any]) -> int:
    """提取金额（分）。"""
    data = payload.get("data", payload)
    attrs = data.get("attributes", data)
    if provider == PAYMENT_PROVIDER_LEMON:
        total = attrs.get("total", 0)
        if isinstance(total, (int, float)):
            return int(total)
        return int(attrs.get("subtotal", 0))
    # Stripe 风格
    amount = attrs.get("amount_total", attrs.get("amount", 0))
    return int(amount)


def _extract_currency(provider: str, payload: Dict[str, Any]) -> str:
    data = payload.get("data", payload)
    attrs = data.get("attributes", data)
    return attrs.get("currency", "CNY")


# ---- 结账（模拟）--------------------------------------------------------------

def create_checkout_session(
    session: Session,
    user: User,
    plan_slug: str,
    interval: str,
    success_url: Optional[str],
    cancel_url: Optional[str],
) -> Dict[str, str]:
    """创建结账会话。

    当前版本为模拟实现：直接生成一个本地 subscription pending 记录，
    返回一个模拟支付链接。真实环境需替换为 Lemon Squeezy / Stripe SDK。
    """
    plan = get_plan_by_slug(session, plan_slug)
    if plan.price_monthly <= 0 and plan.price_yearly <= 0:
        raise ApiError(CODE_BAD_REQUEST, "免费计划无需支付")

    # 创建 pending 订阅
    now = datetime.now(timezone.utc)
    period_end = now + (timedelta(days=365) if interval == "year" else timedelta(days=30))
    sub = Subscription(
        user_id=user.id,
        plan_slug=plan_slug,
        status=SUBSCRIPTION_STATUS_PENDING,
        provider=PAYMENT_PROVIDER_LEMON,
        current_period_start=now,
        current_period_end=period_end,
        interval=interval,
        amount_paid=plan.price_yearly if interval == "year" else plan.price_monthly,
    )
    session.add(sub)
    session.flush()

    # 模拟：返回一个需要用户确认支付的 URL
    # 生产环境这里应该调用 Lemon Squeezy / Stripe 的 checkout API
    base = success_url or settings.public_base_url or "http://localhost"
    checkout_url = f"{base}/payment/confirm?subscription_id={sub.id}&plan={plan_slug}&interval={interval}"

    return {
        "checkout_url": checkout_url,
        "session_id": str(sub.id),
    }


__all__ = [
    "seed_plans",
    "sync_free_plan_limits",
    "list_visible_plans",
    "get_plan_by_slug",
    "get_active_subscription",
    "get_user_entitlement",
    "process_webhook",
    "create_checkout_session",
    "DEFAULT_PLANS",
]

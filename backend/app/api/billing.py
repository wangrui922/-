"""Billing / 订阅 API。

路由：
    GET  /api/billing/plans            — 计划列表（公开）
    GET  /api/billing/entitlement      — 当前用户权益（需登录）
    GET  /api/billing/subscription     — 当前订阅状态（需登录）
    POST /api/billing/checkout         — 创建结账会话（需登录）
    POST /api/billing/webhook/{provider} — 支付平台 webhook（公开，需签名验证占位）
    GET  /api/billing/pending          — 待人工对账订单（admin-only）
    POST /api/billing/confirm          — 人工对账：确认收款 / 拒绝（admin-only）

内测期付费走「线下转账 + 人工对账」：用户下单后订阅停在 ``pending``，
运营在 admin-web「支付对账」页看到待确认列表，核对到账后点「确认收款」
即把订阅置为 ``active``。**不发放激活码**（理由见「人工对账」小节顶部注释）。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.guards import CN_TZ
from app.core.deps import CurrentUser, get_current_user, require_role
from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_NOT_FOUND, ok
from app.db import get_db
from app.models import User
from app.models.subscription import (
    PLAN_FREE,
    SUBSCRIPTION_STATUS_ACTIVE,
    SUBSCRIPTION_STATUS_CANCELLED,
    SUBSCRIPTION_STATUS_PENDING,
    Plan,
    Subscription,
)
from app.schemas.billing import (
    CheckoutCreateIn,
    CheckoutOut,
    EntitlementOut,
    SubscriptionOut,
)
from app.services import billing_service

logger = logging.getLogger("app.api.billing")

router = APIRouter(prefix="/billing", tags=["billing"])


def _user_uuid(user: CurrentUser) -> uuid.UUID:
    """把 ``CurrentUser.sub``（字符串）转成 UUID。

    ⚠️ ``CurrentUser`` 只有 ``sub``/``role``/``sid`` 三个字段，**没有** ``id``。
    早期版本写 ``user.id`` 会在运行时抛 AttributeError → 500（2026-10-20 修复）。
    """
    try:
        return uuid.UUID(str(user.sub))
    except (TypeError, ValueError) as exc:
        from app.core.response import ApiError, CODE_BAD_REQUEST

        raise ApiError(CODE_BAD_REQUEST, "用户标识非法") from exc


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
    """当前登录用户的权益聚合。

    ⚠️ 必须走 ``EntitlementOut`` 转成 camelCase：service 层返回的是
    snake_case 裸 dict，直接透传会导致前端拿不到 ``dailyAiLimit`` 等字段
    （2026-10-20 修复）。
    """
    data = billing_service.get_user_entitlement(db, _user_uuid(user))
    # 注意：schema 字段名本身就是 camelCase（serialization_alias 已指向 camelCase），
    # 所以这里用默认 model_dump()，加 by_alias=True 反而会输出 snake_case。
    payload = EntitlementOut.model_validate(data).model_dump(mode="json")
    return ok(payload)


@router.get("/subscription")
async def get_subscription(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """当前用户的有效订阅（如有）。"""
    sub = billing_service.get_active_subscription(db, _user_uuid(user))
    if sub is None:
        return ok(None)
    payload = SubscriptionOut.model_validate(sub).model_dump(mode="json")
    return ok(payload)


@router.post("/checkout")
async def create_checkout(
    payload: CheckoutCreateIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """创建订阅结账会话。"""
    # service 层需要 ORM User（要用 user.id 落库），这里按 sub 取真实用户。
    uid = _user_uuid(user)
    db_user = db.get(User, uid)
    if db_user is None:
        from app.core.response import ApiError, CODE_NOT_FOUND

        raise ApiError(CODE_NOT_FOUND, "用户不存在")
    result = billing_service.create_checkout_session(
        db, db_user,
        plan_slug=payload.planSlug,
        interval=payload.interval,
        success_url=payload.successUrl,
        cancel_url=payload.cancelUrl,
    )
    return ok(CheckoutOut.model_validate(result).model_dump(mode="json"))


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


# ---- 人工对账（内测期线下转账）-----------------------------------------------
#
# 服务器版 admin-web ``views/payments/index.vue`` 依赖的两个端点：
#     GET  /api/billing/pending  — 待确认订单（data **直接是数组**）
#     POST /api/billing/confirm  — 确认收款 / 拒绝
#
# **不发放激活码**（主理人决定）：``activation_codes`` 表无 plan / duration
# 字段，学生端 ``enter_by_activation_code``（auth_service.py）只建号、不读套餐，
# 发一枚「开了会员但学生端认不出」的码是假功能；且内测期本就全免费不限额。
# 前端 ``payments/index.vue`` 有 ``if (data.activation_code) {...} else { 显示
# message }`` 分支，不返该字段天然兼容，零迁移。

#: ``interval`` → 订阅时长（天）。
#: ⚠️ ``subscriptions.interval`` 的 DB CHECK 只允许 month/year，故 quarter 分支
#: 当前走不到；保留是为了将来放开约束时不必改代码（**不改 DB CHECK**）。
_INTERVAL_DAYS: Dict[str, int] = {"month": 30, "quarter": 90, "year": 365}

#: ``subscriptions.metadata_json`` 里存人工对账补充信息的键。
_META_PAYER_REMARK = "payer_remark"
_META_REJECT_REASON = "reject_reason"


class BillingConfirmIn(BaseModel):
    """人工对账入参（字段名与 admin-web ``confirmPaymentApi`` 严格一致）。"""

    subscription_id: str = Field(..., min_length=1)
    confirm: bool
    reject_reason: Optional[str] = None


def _interval_days(interval: Optional[str]) -> int:
    """按 ``interval`` 推算订阅时长（天），未知值兜底月付 30 天。"""
    return _INTERVAL_DAYS.get((interval or "").strip().lower(), _INTERVAL_DAYS["month"])


def _read_metadata(raw: Optional[str]) -> Dict[str, Any]:
    """安全解析 ``metadata_json``。

    该列是 ``Text``，可能是 ``None``、空串或历史遗留的非法 JSON——解析失败
    一律按空 dict 处理，绝不让一个脏字段把整个对账列表 500 掉。
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_metadata(raw: Optional[str], updates: Dict[str, Any]) -> str:
    """在 ``metadata_json`` 上叠加若干键后回写（保留其它既有键）。"""
    merged = _read_metadata(raw)
    merged.update(updates)
    return json.dumps(merged, ensure_ascii=False)


def _fmt_local(value: Optional[datetime]) -> str:
    """UTC 时间 → 北京时间可读串 ``YYYY-MM-DD HH:mm:ss``（对账表格直接展示）。

    运营对「几点下的单」敏感，而 ISO 带 ``+08:00`` 后缀在表格里又长又丑。
    """
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


@router.get("/pending")
async def list_pending_payments(
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """待人工对账订单列表（仅 ``status == 'pending'`` 的订阅）。

    Returns:
        ``{"code":0,"msg":"ok","data":[...]}``——``data`` **直接是数组**
        （前端 ``tableData.value = res.data || []``），每项::

            {
              "subscription_id": str,   # 前端拿它调 confirm
              "user": {"phone": str|null, "nickname": str|null},
              "plan": {"name": str, "interval": "month"|"year"} | None,
              "amount_yuan": str,       # **元**（两位小数字符串），前端不做换算
              "provider": str,          # wechat / alipay ...
              "payer_remark": str,      # 取自 metadata_json，取不到为 ""（前端有「（未填写）」兜底）
              "created_at": str,        # 本地时区可读串 YYYY-MM-DD HH:mm:ss（前端直接进表格）
              "interval": str,          # 顶层冗余一份，便于排序/筛选
            }
    """
    # Plan 用 **LEFT JOIN**：``plan_slug`` 无外键约束，历史脏数据 / 手工改库的
    # 订单可能对不上 plans 表。用 INNER JOIN 会让这些订单**从对账列表里凭空
    # 消失**——对账页面少一单等于少收一笔款，这种"静默丢数据"比显示 null 危险得多。
    rows = db.execute(
        select(Subscription, User, Plan)
        .join(User, User.id == Subscription.user_id)
        .outerjoin(Plan, Plan.slug == Subscription.plan_slug)
        .where(Subscription.status == SUBSCRIPTION_STATUS_PENDING)
        .order_by(Subscription.created_at.desc())
    ).all()

    return ok(
        [
            {
                "subscription_id": str(sub.id),
                "user": {"phone": user.phone, "nickname": user.nickname},
                "plan": (
                    None
                    if plan is None
                    else {"name": plan.name, "interval": sub.interval}
                ),
                # 前端直接 `¥{{ amount_yuan }}` 展示，不做 /100，故此处给「元」
                "amount_yuan": f"{(sub.amount_paid or 0) / 100:.2f}",
                "provider": sub.provider,
                # 库里无独立列，转账备注约定存在 metadata_json 里
                "payer_remark": _read_metadata(sub.metadata_json).get(
                    _META_PAYER_REMARK, ""
                )
                or "",
                "created_at": _fmt_local(sub.created_at),
                "interval": sub.interval,
            }
            for sub, user, plan in rows
        ]
    )


@router.post("/confirm")
async def confirm_payment(
    payload: BillingConfirmIn,
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """人工对账：确认收款（开通订阅）或拒绝（关闭订单）。

    ``confirm=true``：订阅置 ``active``，周期按 ``interval`` 重算。**不发放
    激活码**——``activation_codes`` 表无 plan/duration 字段，学生端
    ``enter_by_activation_code`` 只建号不读套餐，发码等于给运营一个「开了会员
    但学生端认不出」的假功能（主理人决定，详见本节顶部注释）。

    ``confirm=false``：订阅置 ``cancelled``（``status`` 的 CHECK 无
    ``rejected``），拒绝原因写入 ``metadata_json.reject_reason`` 并拼进
    message——库里没有独立列，这是零迁移的留痕方式。

    **幂等**：已 ``active`` 的订单重复 confirm → 200 + 标注「此前已确认」的
    message，且**不重复改期**（重复点击不该把用户的会员期白送延长一遍）；
    已是 ``cancelled`` 的再 confirm → 视为重新确认，按当前时间重新起算。

    Args:
        payload: ``{subscription_id, confirm, reject_reason?}``。

    Returns:
        ``{"message": str, ...}``。

    Raises:
        ApiError: 404 订阅不存在；400 UUID 非法 / 套餐是 free。
    """
    try:
        sub_id = uuid.UUID(str(payload.subscription_id))
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_BAD_REQUEST, "subscription_id 不是合法 UUID") from exc

    sub = db.get(Subscription, sub_id)
    if sub is None:
        raise ApiError(CODE_NOT_FOUND, "订阅订单不存在")

    # free 档不走支付对账：确认收款会把 0 元订单开成「已付」，污染收入口径
    if sub.plan_slug == PLAN_FREE:
        raise ApiError(CODE_BAD_REQUEST, "免费套餐无需确认支付")

    now = datetime.now(timezone.utc)

    if not payload.confirm:
        reason = (payload.reject_reason or "").strip()
        sub.status = SUBSCRIPTION_STATUS_CANCELLED
        sub.cancelled_at = now
        sub.metadata_json = _write_metadata(
            sub.metadata_json, {_META_REJECT_REASON: reason}
        )
        db.commit()
        message = "已拒绝该订单"
        if reason:
            message = f"{message}：{reason}"
        logger.info(
            "[billing] 人工对账拒绝: subscription=%s reason=%s", sub.id, reason or "(空)"
        )
        return ok({"message": message, "subscription_id": str(sub.id)})

    # 幂等：已生效的重复确认不报错、也不重复顺延周期（否则运营手滑点两下，
    # 用户就白赚一个月的会员——这是会真实发生资损的路径）
    if sub.status == SUBSCRIPTION_STATUS_ACTIVE:
        db.commit()
        logger.info(
            "[billing] 人工对账重复确认(已 active，未改期): subscription=%s", sub.id
        )
        return ok(
            {
                "message": "该订单此前已确认收款，订阅已生效（本次未重复开通）",
                "subscription_id": str(sub.id),
                "already_confirmed": True,
            }
        )

    days = _interval_days(sub.interval)
    sub.status = SUBSCRIPTION_STATUS_ACTIVE
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=days)
    if not sub.provider:
        # provider 列有 CHECK 约束（仅 lemonsqueezy/stripe/alipay/wechat），
        # 且列非空——留空直接落库会 500，故只在为空时补一个合法默认值。
        sub.provider = "wechat"
    db.commit()
    logger.info(
        "[billing] 人工对账确认收款: subscription=%s plan=%s days=%s",
        sub.id,
        sub.plan_slug,
        days,
    )

    return ok(
        {
            "message": "已确认收款，订阅已激活",
            "subscription_id": str(sub.id),
            "duration_days": days,
        }
    )

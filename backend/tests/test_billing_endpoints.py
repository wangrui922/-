"""billing 域端点的回归护栏（2026-10-20 修复 3 个线上 500 后加测）。

修复前的三个真实缺陷（都是"代码在，但跑起来就 500"）：

    1. ``GET /api/billing/entitlement`` 调 ``user.id`` —— ``CurrentUser`` 只有
       ``sub``/``role``/``sid``，**没有 id**，运行时抛 AttributeError → 500。
    2. ``GET /api/billing/subscription`` 同样用 ``user.id`` → 500。
    3. ``POST /api/billing/checkout`` 把 ``CurrentUser`` 传给期望 ORM ``User``
       的 service（service 内部取 ``user.id``）→ 500。

    另外 entitlement 直接透传 service 的 snake_case 裸 dict，没走
    ``EntitlementOut`` schema，前端拿不到 ``dailyAiLimit`` 等 camelCase 字段。

本文件锁死：4 个端点在有登录用户时都返回 200，且字段名是前端约定的 camelCase。

口径备注：本文件**不设** ``DATABASE_URL``（conftest 基线闸门，走会话级
共享内存 SQLite）。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.models import User
from app.models.subscription import Plan, Subscription


def _headers(make_token, sub: str) -> dict:
    """Authorization 头。``sub`` 必须是合法 UUID 字符串（与生产一致）。"""
    return {"Authorization": f"Bearer {make_token(sub=sub, role='student', sid=sub)}"}


@pytest.fixture()
def student_user(db, new_phone):
    """建一个真实用户行（checkout 端点要按 sub 取 ORM User）。"""
    uid = uuid.uuid4()
    db.add(User(id=uid, phone=new_phone(), role="student", nickname="口径测试"))
    db.commit()
    yield uid
    db.execute(delete(User).where(User.id == uid))
    db.commit()


@pytest.fixture()
def free_plan(db):
    """确保 free 档计划存在（幂等）。"""
    plan = db.execute(select(Plan).where(Plan.slug == "free")).scalar_one_or_none()
    if plan is None:
        plan = Plan(
            slug="free",
            name="内测免费版",
            description="内测期全免费",
            price_monthly=0,
            price_yearly=0,
            ai_daily_limit=-1,
            practice_daily_limit=-1,
            max_mistake_storage=99999,
            has_advanced_analytics=False,
            has_family_sharing=False,
            has_ocr_premium=False,
            sort_order=0,
        )
        db.add(plan)
        db.commit()
    return plan


def test_entitlement_returns_camel_case_fields(client, db, make_token, student_user, free_plan):
    """entitlement 必须 200，且返回 camelCase 字段（前端按这些名字取数）。"""
    r = client.get("/api/billing/entitlement", headers=_headers(make_token, str(student_user)))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    for field in (
        "plan",
        "planName",
        "dailyAiLimit",
        "practiceDailyLimit",
        "maxMistakeStorage",
        "aiSessionsToday",
        "aiAttemptsToday",
        "isActive",
    ):
        assert field in data, f"entitlement 缺少前端约定字段 {field}（camelCase 别名未生效）"
    # 内测期 free 档必须是无限
    assert data["dailyAiLimit"] == -1
    assert data["practiceDailyLimit"] == -1


def test_entitlement_no_auth_is_401(client):
    """未登录必须 401（不能因异常处理不当而 500）。"""
    r = client.get("/api/billing/entitlement")
    assert r.status_code == 401, r.text


def test_subscription_endpoint_returns_200(client, make_token, student_user):
    """无订阅时返回 200 + data=null（不能 500）。"""
    r = client.get("/api/billing/subscription", headers=_headers(make_token, str(student_user)))
    assert r.status_code == 200, r.text
    assert r.json()["code"] == 0


def test_subscription_with_active_row_returns_camel_case(client, db, make_token, student_user, free_plan):
    """有 active 订阅时返回 camelCase 字段。"""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    db.add(
        Subscription(
            user_id=student_user,
            plan_slug="free",
            status="active",
            provider="wechat",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            interval="month",
            amount_paid=0,
            currency="CNY",
        )
    )
    db.commit()

    r = client.get("/api/billing/subscription", headers=_headers(make_token, str(student_user)))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    for field in ("id", "planSlug", "status", "provider", "interval", "amountPaid", "currency"):
        assert field in data, f"subscription 缺少 camelCase 字段 {field}"


def test_checkout_endpoint_does_not_500(client, db, make_token, student_user, free_plan):
    """checkout 端点不能因 CurrentUser/User 混用而 500。

    free 档价格 0 → 期望 400（"免费计划无需支付"），不是 500。
    """
    r = client.post(
        "/api/billing/checkout",
        headers=_headers(make_token, str(student_user)),
        json={"planSlug": "free", "interval": "month"},
    )
    assert r.status_code != 500, f"checkout 返回 500：{r.text}"
    assert r.status_code == 400, r.text


def test_checkout_missing_user_returns_404_not_500(client, make_token):
    """token 合法但库里没有对应用户 → 404，不能 500。"""
    r = client.post(
        "/api/billing/checkout",
        headers=_headers(make_token, str(uuid.uuid4())),
        json={"planSlug": "free", "interval": "month"},
    )
    assert r.status_code != 500, f"checkout 返回 500：{r.text}"
    assert r.status_code == 404, r.text

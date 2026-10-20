"""内测期「全免费不限额」口径的护栏单测。

背景（2026-10-20 决策）：内测期全免费不限额。此前配额在三处口径不一致：
    1. ``billing_service.DEFAULT_PLANS`` free 档 ai=20 / practice=50；
    2. ``settings.free_daily_ai``（线上 .env.production = 20）；
    3. 前端把 ``-1`` 当成"每日 -1 次"渲染，限量语义在链路上是碎的。

    （注：曾还有第三处 ``services/subscription_service.PLANS["free"]`` 写死 3 次，
     但那是 billing 域取代它之后遗留的孤儿模块，import 即崩、零引用，
     已于 2026-10-20 删除，见 test_module_import_health.py。）

本文件锁死以下不变量，任一条被改坏即红：
    1. 后端两处口径统一为"无限"（-1 / 0）；
    2. ``guards.check_ai_quota`` 在 limit<=0 时**直接放行**（不限流），
       且这与 ``settings.free_daily_ai`` 的实际取值无关（用参数化覆盖 0/-1）；
    3. ``family_service.get_child_entitlement`` 上报的 daily_ai_limit 为 -1；
    4. ``seed_plans`` 对**已存在**的 free 行也做配额同步（否则库里仍是旧值 20/50）；
    5. 前端把 -1 渲染为"不限"（源码契约测试，防回归）。

口径备注：本文件**不设** ``DATABASE_URL``（conftest 基线闸门，走会话级
共享内存 SQLite）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.subscription import Plan
from app.services import billing_service


# ==================================================================== 1. 口径统一
def test_free_plan_quota_is_unlimited():
    """free 档配额必须是"无限"（-1）且免费。"""
    free_billing = next(p for p in billing_service.DEFAULT_PLANS if p["slug"] == "free")
    assert free_billing["ai_daily_limit"] == -1, "billing free 档 AI 额度必须为 -1"
    assert free_billing["practice_daily_limit"] == -1, "billing free 档练习额度必须为 -1"
    assert free_billing["price_monthly"] == 0 and free_billing["price_yearly"] == 0, "内测期必须免费"


def test_settings_free_daily_ai_default_is_unlimited():
    """默认配置即不限流（0 = 不限），避免漏配 .env 时又变回 20 次。"""
    assert settings.free_daily_ai <= 0, "free_daily_ai 默认必须 <= 0（0/负数 = 不限流）"


# ==================================================================== 2. guards 不限流
@pytest.mark.parametrize("limit_value", [0, -1, -100])
def test_guards_check_ai_quota_allows_when_limit_not_positive(monkeypatch, limit_value):
    """``guards.check_ai_quota`` 在 limit<=0 时必须直接返回，不查用量、不抛 429。"""
    from app.ai import guards

    monkeypatch.setattr(guards.settings, "free_daily_ai", limit_value, raising=False)

    class _BoomSession:
        """一旦真的去查用量就说明没被短路，用于捕获"忘了判 limit"的自噬。"""

        def execute(self, *args, **kwargs):  # pragma: no cover - 不应被调用
            raise AssertionError("limit<=0 时不应查询用量，应直接放行")

    # 不应抛异常
    guards.check_ai_quota("00000000-0000-0000-0000-000000000001", _BoomSession())


# ==================================================================== 3. 家长端上报 -1
def test_child_entitlement_reports_unlimited(db):
    """家长端孩子权益必须上报 daily_ai_limit = -1（前端据此显示"不限"）。"""
    import uuid

    from app.services import family_service

    student_id = uuid.uuid4()
    ent = family_service.get_child_entitlement(db, student_id)
    assert ent["daily_ai_limit"] == -1, "内测期家长端额度必须上报 -1"
    assert ent["plan"] == "free"


# ==================================================================== 4. seed 幂等同步
def test_seed_plans_syncs_existing_free_row(db):
    """已存在的 free 行也必须被同步成无限额度（seed 的老逻辑只插不更）。"""
    existing = db.execute(select(Plan).where(Plan.slug == "free")).scalar_one_or_none()
    if existing is None:
        db.add(
            Plan(
                slug="free",
                name="免费版",
                description="旧文案",
                price_monthly=0,
                price_yearly=0,
                ai_daily_limit=20,
                practice_daily_limit=50,
                max_mistake_storage=500,
                has_advanced_analytics=False,
                has_family_sharing=False,
                has_ocr_premium=False,
                sort_order=0,
            )
        )
        db.flush()

    billing_service.seed_plans(db)
    db.flush()

    plan = db.execute(select(Plan).where(Plan.slug == "free")).scalar_one()
    assert plan.ai_daily_limit == -1, "seed 必须把 free 档 AI 额度同步为 -1"
    assert plan.practice_daily_limit == -1, "seed 必须把 free 档练习额度同步为 -1"
    assert plan.max_mistake_storage >= 99999, "seed 必须把 free 档错题本上限同步为不限"
    # 价格不被 seed 改动（运营定价是另一件事）
    assert plan.price_monthly == 0


# ==================================================================== 5. 前端契约
def test_frontend_renders_unlimited_for_negative_limit():
    """前端源码必须把 -1 渲染成"不限"，不能出现"每日 -1 次"。"""
    from pathlib import Path

    web_root = Path(__file__).resolve().parents[2] / "student-web" / "src"
    assert web_root.exists(), "找不到 student-web 源码目录"

    for rel in ("views/Subscription.vue", "views/parent/ChildDetail.vue"):
        src = (web_root / rel).read_text(encoding="utf-8")
        assert "每日 -1" not in src, f"{rel} 出现了负数额度直渲染"
        assert "isUnlimited" in src, f"{rel} 缺少 isUnlimited 判定函数"

    sub_src = (web_root / "views/Subscription.vue").read_text(encoding="utf-8")
    assert "BETA_FREE_ONLY" in sub_src, "内测期应隐藏付费订阅入口（BETA_FREE_ONLY 开关）"

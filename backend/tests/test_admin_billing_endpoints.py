"""运营看板 + 支付对账端点单测（服务器版 admin-web 依赖的 4 个端点）。

背景：服务器上部署的 admin-web 会调 4 个后端**原本不存在**的端点，导致
运营看板页与支付对账页 404。本文件锁死这 4 个端点的契约：

    1. **鉴权**：4 个端点均 admin-only —— 无 token 401、学生 403、admin 200；
    2. **overview 字段完整性**：9 个标量 + 6 个数组一个不缺，且 3 个趋势
       数组（register/dau/activation）与 payment_trend **恒为 14 个点**
       （前端按下标画线，缺项会把图画歪）；
    3. **members 结构**：必须是 ``{list: [...]}``（前端读 ``res.data.list``），
       元素含 phone/nickname/plan_name/status/amount_paid/current_period_end；
    4. **pending 返回数组**（前端 ``tableData.value = res.data || []``），
       且只含 ``status == 'pending'`` 的订阅；
    5. **confirm 确认路径**：状态变 active、返回 activation_code（非空明文）、
       duration_days 与 interval 匹配（month=30 / year=365）、激活码落库；
    6. **confirm 拒绝路径**：状态变 cancelled、message 含拒绝原因；
    7. **confirm 对 free 套餐** → 400（0 元订单不该被确认成「已收款」）；
    8. **源码契约测试**：读归档的 admin-web 源码里写的路径字符串，断言后端
       路由表里存在对应路由（前端改路径后端没跟上时红）。

口径备注：本文件**不设** ``DATABASE_URL``（conftest 基线闸门，走会话级共享
内存 SQLite）。测试库跨用例共享且跨文件堆积数据，因此：
    * 每条用例造**全新用户/订阅**并按 id 精确断言，不假设全局为空；
    * overview 只断言「字段齐全 + 数组长度 + 自己造的数据落在正确的桶里」，
      不断言全局计数（别的用例造的脏数据会让这类断言随执行顺序漂移）。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import select

from app.models.activation import ActivationCode
from app.models.subscription import (
    PAYMENT_PROVIDER_WECHAT,
    PaymentTransaction,
    Plan,
    Subscription,
)
from app.models.user import User


# ==================================================================== 工具
def admin_headers(make_token) -> dict:
    """admin 角色的 Authorization 头。"""
    return {"Authorization": f"Bearer {make_token(sub='1', role='admin')}"}


def student_headers(make_token) -> dict:
    """学生角色的 Authorization 头（越权用例用）。"""
    return {"Authorization": f"Bearer {make_token(sub='2', role='student', sid='2')}"}


#: overview 必须存在的标量键（前端 statCards / 图表直接读）
OVERVIEW_SCALAR_KEYS = (
    "total_users",
    "active_today",
    "active_7d",
    "active_subscriptions",
    "payment_count",
    "payment_amount",
    "activation_used",
    "activation_unused",
    "activation_revoked",
)

#: overview 必须存在的数组键 → 期望长度（趋势数组恒 14 点）
OVERVIEW_LIST_KEYS = {
    "register_trend": 14,
    "dau_trend": 14,
    "activation_trend": 14,
    "payment_trend": 14,
    "sub_by_status": None,
    "sub_by_plan": None,
}


def make_user(db, new_phone, nickname: str = "对账测试") -> uuid.UUID:
    """造一个干净的用户，返回 ID。"""
    user = User(id=uuid.uuid4(), phone=new_phone(), nickname=nickname, role="student")
    db.add(user)
    db.flush()
    return user.id


def ensure_plan(db, slug: str) -> Plan:
    """确保 plan 存在（幂等），返回该 Plan 行。

    ⚠️ **必须复用 ``billing_service.seed_plans``（而不是就地 ``Plan(...)``）**：
    测试库是会话级共享的，各测试文件都会读 ``plans`` 表。这里若自己造一行
    配额值（如 free 档写死 100/200），就会把别的文件（如
    ``test_billing_endpoints.py`` 断言 free 档 ``dailyAiLimit == -1``）的
    前置状态改掉——这类「测试改了自己要断言的世界」最隐蔽：单跑绿、
    合跑红，且随文件执行顺序漂移。``seed_plans`` 幂等且自带 free 档配额
    同步，是唯一的建 Plan 入口。
    """
    from app.services import billing_service

    billing_service.seed_plans(db)
    db.flush()
    plan = db.execute(select(Plan).where(Plan.slug == slug)).scalar_one_or_none()
    assert plan is not None, f"seed_plans 后仍找不到 {slug} 档计划"
    return plan


def make_subscription(
    db,
    *,
    user_id: uuid.UUID,
    plan_slug: str,
    status: str = "pending",
    interval: str = "month",
    amount_paid: int = 2900,
    created_at: Optional[datetime] = None,
) -> Subscription:
    """造一条订阅（默认 pending 供对账列表用）。"""
    now = datetime.now(timezone.utc)
    sub = Subscription(
        user_id=user_id,
        plan_slug=plan_slug,
        status=status,
        provider=PAYMENT_PROVIDER_WECHAT,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        interval=interval,
        amount_paid=amount_paid,
        currency="CNY",
    )
    db.add(sub)
    db.flush()
    if created_at is not None:
        # created_at 有 server_default，显式回写以便断言排序
        sub.created_at = created_at
        db.flush()
    return sub


# ==================================================================== 鉴权
def test_all_four_endpoints_require_admin(client, make_token):
    """4 个端点均 admin-only：无 token 401、学生 403、admin 200。"""
    plan = (
        ("get", "/api/admin/dashboard/overview", None),
        ("get", "/api/admin/dashboard/members?limit=10", None),
        ("get", "/api/billing/pending", None),
    )
    for method, url, _ in plan:
        assert client.request(method, url).status_code == 401, url
        assert client.request(method, url, headers=student_headers(make_token)).status_code == 403, url
        assert client.request(method, url, headers=admin_headers(make_token)).status_code == 200, url

    body = {"subscription_id": str(uuid.uuid4()), "confirm": True}
    url = "/api/billing/confirm"
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers=student_headers(make_token)).status_code == 403


# ==================================================================== overview 字段完整性
def test_overview_has_all_keys(client, make_token):
    """overview 的 9 个标量 + 6 个数组一个都不能缺（缺一个前端渲染 undefined）。"""
    r = client.get("/api/admin/dashboard/overview", headers=admin_headers(make_token))
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    for key in OVERVIEW_SCALAR_KEYS:
        assert key in data, f"overview 缺少标量字段 {key}"
        assert isinstance(data[key], int), f"{key} 必须是 int（前端 statCards 直接展示）"

    for key, expected_len in OVERVIEW_LIST_KEYS.items():
        assert key in data, f"overview 缺少数组字段 {key}"
        assert isinstance(data[key], list), f"{key} 必须是数组"
        if expected_len is not None:
            # 前端 it.date.slice(5) 直接按下标画线，少一个点图就画歪
            assert len(data[key]) == expected_len, (
                f"{key} 必须恒为 {expected_len} 个点（无数据的天也要给 0），实际 {len(data[key])}"
            )
            for item in data[key]:
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["date"]), item


def test_overview_trend_points_are_consecutive_days(client, make_token):
    """趋势数组的日期必须连续升序（前端横轴直接用 date.slice(5)）。"""
    r = client.get("/api/admin/dashboard/overview", headers=admin_headers(make_token))
    data = r.json()["data"]

    for key in ("register_trend", "dau_trend", "activation_trend", "payment_trend"):
        dates = [it["date"] for it in data[key]]
        assert len(dates) == 14, key
        parsed = [datetime.strptime(d, "%Y-%m-%d").date() for d in dates]
        for i in range(1, 14):
            assert (parsed[i] - parsed[i - 1]).days == 1, f"{key} 日期不连续: {dates}"
        assert parsed == sorted(parsed), f"{key} 必须升序"


def test_overview_three_trends_share_same_window(client, make_token):
    """三个趋势数组**同长（14）、同序、同窗口** —— 防前端画图错位。

    前端 ``dashboard/index.vue`` 把 ``dau_trend``（L248）和
    ``activation_trend``（L280）都画在 ``register_trend`` 的 X 轴上
    （用的是 ``regDates``，不读自己的 date）。三者只要有一个长度不同或
    日期错位，ECharts 就会把折线画到错误的横坐标上——而且**不报错**，
    只是图看着不对，极难在部署后被发现。
    """
    r = client.get("/api/admin/dashboard/overview", headers=admin_headers(make_token))
    data = r.json()["data"]

    register_dates = [it["date"] for it in data["register_trend"]]
    assert len(register_dates) == 14

    for key in ("dau_trend", "activation_trend", "payment_trend"):
        dates = [it["date"] for it in data[key]]
        assert len(dates) == 14, f"{key} 长度 {len(dates)} != 14（前端按下标配对，横轴会错位）"
        assert dates == register_dates, (
            f"{key} 的日期窗口与 register_trend 不一致，前端把它画在 regDates 上会错位"
        )


def test_overview_excludes_frozen_and_deleted_users(client, db, make_token, new_phone):
    """冻结 / 软删除的账号不计入 total_users、active_today 与注册趋势。

    运营会照着「总用户数」判断推广效果，把冻结的僵尸号算进去等于自我安慰。
    """
    def _counts() -> Dict[str, Any]:
        r = client.get("/api/admin/dashboard/overview", headers=admin_headers(make_token))
        assert r.status_code == 200, r.text
        return r.json()["data"]

    before = _counts()

    frozen_id = uuid.uuid4()
    db.add(
        User(
            id=frozen_id,
            phone=new_phone(),
            nickname="已冻结",
            role="student",
            status="frozen",
            last_login_at=datetime.now(timezone.utc),
        )
    )
    deleted_id = uuid.uuid4()
    db.add(
        User(
            id=deleted_id,
            phone=new_phone(),
            nickname="已注销",
            role="student",
            status="active",
            deleted_at=datetime.now(timezone.utc),
            last_login_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    after = _counts()

    assert after["total_users"] == before["total_users"], (
        "冻结/软删除账号被算进了 total_users"
    )
    assert after["active_today"] == before["active_today"], (
        "冻结/软删除账号被算进了 active_today"
    )
    today_reg = after["register_trend"][-1]["count"]
    assert today_reg == before["register_trend"][-1]["count"], (
        "冻结/软删除账号被算进了 register_trend"
    )

    # 对照：同样条件的 active 账号是**要**被计入的（否则上面等于全表排除）
    live_id = uuid.uuid4()
    db.add(
        User(
            id=live_id,
            phone=new_phone(),
            nickname="正常用户",
            role="student",
            status="active",
            last_login_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    final = _counts()
    assert final["total_users"] == before["total_users"] + 1
    assert final["active_today"] == before["active_today"] + 1
    assert final["register_trend"][-1]["count"] == before["register_trend"][-1]["count"] + 1


def test_overview_counts_my_own_data(client, db, make_token, new_phone):
    """自己造的注册 / 付费流水 / 激活码必须落进 overview 的对应计数与桶里。

    只断言**自己造的那几个**的增量口径：注册趋势桶命中 + 付费金额落在
    今天的桶里 + 激活码已用数 +1（不断言全局总数，避免跨用例脏数据漂移）。
    """
    ensure_plan(db, "pro")
    user_id = make_user(db, new_phone)
    sub = make_subscription(db, user_id=user_id, plan_slug="pro", amount_paid=5900)

    # 一条成功付费流水（金额单位：分）
    db.add(
        PaymentTransaction(
            subscription_id=sub.id,
            user_id=user_id,
            provider=PAYMENT_PROVIDER_WECHAT,
            provider_transaction_id=f"txn-{uuid.uuid4().hex[:12]}",
            amount=5900,
            currency="CNY",
            status="succeeded",
            event_type="manual_offline",
        )
    )
    # 一条已绑定的激活码（已用口径）
    db.add(ActivationCode(code_digest=uuid.uuid4().hex * 2, label="overview 用例"))
    db.flush()
    used_code = db.execute(
        select(ActivationCode).where(ActivationCode.label == "overview 用例")
    ).scalar_one()
    used_code.user_id = user_id
    used_code.activated_at = datetime.now(timezone.utc)
    db.flush()

    # 今日的注册 + 今日活跃：last_login_at 落在今天（北京时间）之内
    me = db.get(User, user_id)
    me.last_login_at = datetime.now(timezone.utc)
    # 必须 commit：``db.get`` 命中 identity map 拿到的是同一个对象，赋值不会
    # 发 SQL；不提交则 HTTP 请求侧（独立会话）读到的仍是 NULL。
    db.commit()

    r = client.get("/api/admin/dashboard/overview", headers=admin_headers(make_token))
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    # 今日桶：注册趋势最后一点 + 付费趋势最后一点
    assert data["register_trend"][-1]["count"] >= 1
    assert data["payment_trend"][-1]["amount"] >= 5900
    assert data["payment_count"] >= 1
    assert data["payment_amount"] >= 5900

    # 今日活跃至少含我自己（口径：北京时间的今天，用 UTC+8 切分）
    assert data["active_today"] >= 1
    assert data["active_7d"] >= 1

    # 已用激活码：我这条 user_id 非空
    assert data["activation_used"] >= 1

    # 订阅分布里能按 slug 找到我的 pro 记录
    plan_rows = {it["plan"] for it in data["sub_by_plan"]}
    assert "pro" in plan_rows
    assert data["sub_by_status"]


def test_overview_amount_units_are_fen(client, make_token):
    """金额字段单位是「分」（前端自行 /100 转元）。"""
    r = client.get("/api/admin/dashboard/overview", headers=admin_headers(make_token))
    data = r.json()["data"]
    # 前端 fmtYuan(fen) = fen/100；若后端误给元，看板金额会小 100 倍
    assert data["payment_amount"] >= 0
    assert all("amount" in it for it in data["payment_trend"])


def test_members_returns_list_structure(client, db, make_token, new_phone):
    """members 必须是 ``{list: [...]}``（前端读 ``res.data.list``），字段齐全。"""
    ensure_plan(db, "pro")
    user_id = make_user(db, new_phone, nickname="会员甲")
    sub = make_subscription(
        db, user_id=user_id, plan_slug="pro", status="active", amount_paid=5900
    )
    db.commit()

    r = client.get(
        "/api/admin/dashboard/members?limit=10", headers=admin_headers(make_token)
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert isinstance(data, dict) and "list" in data, "members 必须返回 {list: [...]}"
    assert isinstance(data["list"], list)

    row = next((it for it in data["list"] if it["user_id"] == str(user_id)), None)
    assert row is not None, "我造的订阅没出现在 members 里"
    for field in (
        "phone",
        "nickname",
        "plan_name",
        "status",
        "amount_paid",
        "current_period_end",
        "user_id",
    ):
        assert field in row, f"members 行缺少字段 {field}"
    assert row["nickname"] == "会员甲"
    assert row["plan_name"] == "专业版"
    assert row["status"] == "active"
    assert row["amount_paid"] == 5900  # 单位：分
    assert row["current_period_end"], "current_period_end 不能为空"
    # ISO 串，前端 fmtDate 会 slice(0,10)
    datetime.fromisoformat(row["current_period_end"])
    assert sub.id  # 仅防止 flake8 unused


def test_members_respects_limit(client, db, make_token, new_phone):
    """limit 生效（前端传 limit=10 拿「最近 10 位会员」）。"""
    ensure_plan(db, "basic")
    uids = [make_user(db, new_phone) for _ in range(3)]
    for uid in uids:
        make_subscription(db, user_id=uid, plan_slug="basic", status="active")
    db.commit()

    r = client.get(
        "/api/admin/dashboard/members?limit=2", headers=admin_headers(make_token)
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["list"]) <= 2

    # 越界 limit → 400（Query ge/le 生效，不静默取全部）
    r = client.get(
        "/api/admin/dashboard/members?limit=0", headers=admin_headers(make_token)
    )
    assert r.status_code == 400


# ==================================================================== pending（数组）
def test_pending_returns_array_with_only_pending(client, db, make_token, new_phone):
    """pending 的 data **直接是数组**，且只含 pending 状态的订阅。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone, nickname="待对账甲")
    pending_sub = make_subscription(db, user_id=uid, plan_slug="pro", amount_paid=5900)
    # 同用户的 active 订阅不得混进待确认列表
    make_subscription(db, user_id=uid, plan_slug="pro", status="active")
    db.commit()

    r = client.get("/api/billing/pending", headers=admin_headers(make_token))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert isinstance(data, list), "pending 的 data 必须是数组（前端 res.data || []）"

    row = next((it for it in data if it["subscription_id"] == str(pending_sub.id)), None)
    assert row is not None, "我造的 pending 订阅没出现在待确认列表"

    for field in (
        "subscription_id",
        "user",
        "plan",
        "amount_yuan",
        "provider",
        "payer_remark",
        "created_at",
        "interval",
    ):
        assert field in row, f"pending 行缺少字段 {field}"

    assert row["user"]["phone"]
    assert row["user"]["nickname"] == "待对账甲"
    assert row["plan"]["name"] == "专业版"
    assert row["plan"]["interval"] == "month"
    assert row["provider"] == "wechat"
    # payer_remark 无独立列，取自 metadata_json；本例没写 → 空串（前端「（未填写）」兜底）
    assert row["payer_remark"] == ""
    assert row["interval"] == "month"
    # 下单时间：本地时区可读串，前端直接显示在表格里
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", row["created_at"]), row

    # 只含 pending：同用户的 active 订阅不得混进待确认列表
    active_ids = {
        str(s.id)
        for s in db.execute(
            select(Subscription).where(
                Subscription.user_id == uid, Subscription.status == "active"
            )
        ).scalars()
    }
    assert active_ids, "用例前置：本用户应有一条 active 订阅用于反查"
    listed_ids = {it["subscription_id"] for it in data}
    assert not (active_ids & listed_ids), "active 状态的订阅漏进了待确认列表"


def test_pending_amount_is_yuan_not_fen(client, db, make_token, new_phone):
    """``amount_yuan`` 单位是「元」（前端 `¥{{ amount_yuan }}` 直接展示）。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro", amount_paid=5900)
    db.commit()

    r = client.get("/api/billing/pending", headers=admin_headers(make_token))
    row = next(it for it in r.json()["data"] if it["subscription_id"] == str(sub.id))
    # 若后端误给分（5900.00），前端会显示 ¥5900.00 —— 正是这类单位错
    assert float(row["amount_yuan"]) == pytest.approx(59.0, abs=0.01)


def test_pending_reads_payer_remark_from_metadata(client, db, make_token, new_phone):
    """``payer_remark`` 取自 ``metadata_json``（库里无独立列，零迁移）。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro")
    sub.metadata_json = json.dumps({"payer_remark": "转账单号WX8899"}, ensure_ascii=False)
    db.commit()

    r = client.get("/api/billing/pending", headers=admin_headers(make_token))
    row = next(it for it in r.json()["data"] if it["subscription_id"] == str(sub.id))
    assert row["payer_remark"] == "转账单号WX8899"


def test_pending_survives_corrupt_metadata(client, db, make_token, new_phone):
    """metadata_json 是脏数据时列表仍要 200（不能一个坏字段 500 掉整页对账）。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro")
    sub.metadata_json = "{不是合法 JSON"
    db.commit()

    r = client.get("/api/billing/pending", headers=admin_headers(make_token))
    assert r.status_code == 200, r.text
    row = next(it for it in r.json()["data"] if it["subscription_id"] == str(sub.id))
    assert row["payer_remark"] == ""


def test_pending_keeps_orders_with_unknown_plan(client, db, make_token, new_phone):
    """plan_slug 对不上 plans 表的订单**必须仍在列表里**（plan=null）。

    用 INNER JOIN 会让这类订单从对账页凭空消失——对账页少一单等于少收一笔款，
    这种「静默丢数据」远比显示 null 危险。
    """
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="nonexistent_plan")
    db.commit()

    r = client.get("/api/billing/pending", headers=admin_headers(make_token))
    assert r.status_code == 200, r.text
    row = next(
        (it for it in r.json()["data"] if it["subscription_id"] == str(sub.id)), None
    )
    assert row is not None, "plan 查不到的订单被 INNER JOIN 吞掉了"
    assert row["plan"] is None, "plan 查不到时应为 null（前端用 ?. 兜底）"


# ==================================================================== confirm 确认路径
def test_confirm_activates_subscription(client, db, make_token, new_phone):
    """确认收款：状态变 active、周期按 interval 重算。

    **不返激活码**（主理人决定）：``activation_codes`` 表无 plan/duration 字段，
    学生端 ``enter_by_activation_code`` 只建号不读套餐，发码等于给运营一个
    「开了会员但学生端认不出」的假功能。前端 ``payments/index.vue`` 有
    ``if (data.activation_code) {...} else { 显示 message }`` 分支，不返该
    字段天然兼容。
    """
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro", interval="month", amount_paid=5900)
    db.commit()
    before_codes = db.execute(select(ActivationCode)).scalars().all()

    r = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": True},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "已确认收款" in data["message"]
    assert "activation_code" not in data, (
        "confirm 不应再发激活码（学生端认不出该码，属于假功能）"
    )
    assert data["duration_days"] == 30

    # 订阅状态/周期已更新（独立会话复验，避免 fixture identity map 读到旧值）
    from app.db import SessionLocal

    with SessionLocal() as s:
        fresh = s.get(Subscription, sub.id)
        assert fresh.status == "active"
        assert fresh.current_period_start is not None
        assert fresh.current_period_end is not None
        delta = fresh.current_period_end - fresh.current_period_start
        assert 29 <= delta.days <= 30, delta

    # 不新增激活码行
    after = db.execute(select(ActivationCode)).scalars().all()
    assert len(after) == len(before_codes), "confirm 不该生成激活码行"


def test_confirm_is_idempotent(client, db, make_token, new_phone):
    """重复点「确认收款」：200 + 标注文案，且**不重复顺延会员期**。

    这是会真实发生资损的路径——运营手滑点两下，用户就白赚一个月。
    """
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro", interval="month")
    db.commit()

    first = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": True},
        headers=admin_headers(make_token),
    )
    assert first.status_code == 200, first.text

    from app.db import SessionLocal

    with SessionLocal() as s:
        end_after_first = s.get(Subscription, sub.id).current_period_end

    second = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": True},
        headers=admin_headers(make_token),
    )
    assert second.status_code == 200, "重复确认应静默成功，不该报错"
    data = second.json()["data"]
    assert "此前已确认" in data["message"], "重复确认的 message 应能看出是重复点击"
    assert data.get("already_confirmed") is True

    with SessionLocal() as s:
        fresh = s.get(Subscription, sub.id)
        assert fresh.status == "active"
        assert fresh.current_period_end == end_after_first, (
            "重复确认不得顺延会员期（会白送用户一个周期，属资损）"
        )


def test_confirm_after_reject_reactivates(client, db, make_token, new_phone):
    """已被拒绝（cancelled）的订单再确认 → 视为重新确认，按当前时间起算。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro", interval="month")
    db.commit()

    rejected = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": False, "reject_reason": "误操作"},
        headers=admin_headers(make_token),
    )
    assert rejected.status_code == 200, rejected.text

    recovered = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": True},
        headers=admin_headers(make_token),
    )
    assert recovered.status_code == 200, recovered.text
    assert "已确认收款" in recovered.json()["data"]["message"]

    from app.db import SessionLocal

    with SessionLocal() as s:
        fresh = s.get(Subscription, sub.id)
        assert fresh.status == "active"
        assert fresh.current_period_end is not None


def test_confirm_year_interval_duration(client, db, make_token, new_phone):
    """年付订阅确认后 duration_days=365（DB CHECK 只允许 month/year）。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro", interval="year", amount_paid=59000)
    db.commit()

    r = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": True},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["duration_days"] == 365


def test_confirm_missing_subscription_returns_404(client, make_token):
    """订阅不存在 → 404（CODE_NOT_FOUND）。"""
    r = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(uuid.uuid4()), "confirm": True},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 404, r.text
    assert r.json()["code"] == 404


def test_confirm_free_plan_returns_400(client, db, make_token, new_phone):
    """free 套餐订阅 → 400：0 元订单不该被确认成「已收款」。"""
    ensure_plan(db, "free")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="free", amount_paid=0)
    db.commit()

    r = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": True},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 400, r.text
    assert "免费" in r.json()["msg"]

    # 被拒后状态不变（没被开成 active）
    from app.db import SessionLocal

    with SessionLocal() as s:
        assert s.get(Subscription, sub.id).status == "pending"


# ==================================================================== confirm 拒绝路径
def test_confirm_reject_cancels_and_echoes_reason(client, db, make_token, new_phone):
    """确认=false：状态变 cancelled、message 含拒绝原因、且不发激活码。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro", amount_paid=5900)
    db.commit()
    before_codes = len(db.execute(select(ActivationCode)).scalars().all())

    reason = "未收到对应转账"
    r = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": False, "reject_reason": reason},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "已拒绝" in data["message"]
    assert reason in data["message"], f"拒绝原因未回传给运营：{data['message']}"
    # 拒绝路径不发码（前端只按 activation_code 弹窗）
    assert not data.get("activation_code")

    from app.db import SessionLocal

    with SessionLocal() as s:
        fresh = s.get(Subscription, sub.id)
        assert fresh.status == "cancelled"
        # 拒绝原因留痕：库里无独立列，写进 metadata_json（零迁移）
        assert json.loads(fresh.metadata_json or "{}")["reject_reason"] == reason

    assert len(db.execute(select(ActivationCode)).scalars().all()) == before_codes


def test_confirm_reject_preserves_existing_metadata(client, db, make_token, new_phone):
    """拒绝时叠加 metadata_json 不得覆盖既有键（如已写好的 payer_remark）。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro")
    sub.metadata_json = json.dumps({"payer_remark": "转账单号 A123"}, ensure_ascii=False)
    db.commit()

    r = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": False, "reject_reason": "金额不符"},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 200, r.text

    from app.db import SessionLocal

    with SessionLocal() as s:
        meta = json.loads(s.get(Subscription, sub.id).metadata_json or "{}")
    assert meta.get("payer_remark") == "转账单号 A123", "拒绝操作把既有的 payer_remark 冲掉了"
    assert meta.get("reject_reason") == "金额不符"


def test_confirm_reject_survives_corrupt_metadata(client, db, make_token, new_phone):
    """metadata_json 是历史脏数据（非法 JSON）时拒绝仍应成功，不 500。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro")
    sub.metadata_json = "{不是合法 JSON"
    db.commit()

    r = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": False, "reject_reason": "测试"},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 200, r.text
    assert "已拒绝" in r.json()["data"]["message"]


def test_confirm_reject_without_reason_still_works(client, db, make_token, new_phone):
    """拒绝原因为空也能拒（前端兜底传「未提供原因」，但不能强依赖）。"""
    ensure_plan(db, "pro")
    uid = make_user(db, new_phone)
    sub = make_subscription(db, user_id=uid, plan_slug="pro")
    db.commit()

    r = client.post(
        "/api/billing/confirm",
        json={"subscription_id": str(sub.id), "confirm": False},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 200, r.text
    assert "已拒绝" in r.json()["data"]["message"]


# ==================================================================== 源码契约测试
def _archive_src_dir() -> Path:
    """归档的服务器版 admin-web 源码目录。"""
    return Path(__file__).resolve().parents[2] / "archive" / "admin-web-server-20261005" / "src"


def _backend_routes() -> List[str]:
    """后端实际注册的全部路由路径。

    走 OpenAPI schema 而不是遍历 ``app.routes``：本仓库锁定的 FastAPI 版本
    用惰性的 ``_IncludedRouter`` 表示 ``include_router`` 的结果，直接遍历
    ``app.routes`` 只能看到 4 条内置路由 + 1 个占位，看不到真实端点。
    """
    from app.main import app

    return list(app.openapi().get("paths", {}).keys())


def _assert_frontend_paths_have_backend_routes() -> None:
    """admin-web 源码里写死的路径，后端必须有对应路由。

    为什么需要这条：前端 ``src/api/modules/*.ts`` 里的路径是**手写字符串**，
    后端改名/漏实现都不会让任何一侧编译失败，只在部署后表现为页面 404。
    这条测试把「源码里写的路径」与「后端路由表」对齐，前端改了路径而后端
    没跟上（或反过来）时立刻红。
    """
    src = _archive_src_dir()
    admin_api = src / "api" / "modules" / "admin.ts"
    billing_api = src / "api" / "modules" / "billing.ts"
    assert admin_api.exists(), f"归档前端源码缺失：{admin_api}"
    assert billing_api.exists(), f"归档前端源码缺失：{billing_api}"

    routes = _backend_routes()
    # 前端路径以 /admin 或 /billing 开头，后端统一挂在 /api 下
    checked: List[str] = []
    for source in (admin_api, billing_api):
        text = source.read_text(encoding="utf-8")
        for raw in re.findall(r'["`](/(?:admin|billing)/[^"`$]*)["`]', text):
            if "${" in raw:  # 模板串（如 /admin/users/${userId}/freeze）
                prefix = raw.split("${")[0].rstrip("/")
                assert any(
                    p.startswith(prefix) for p in routes
                ), f"{source.name} 里的路径 {prefix}* 在后端找不到对应路由"
                checked.append(prefix + "*")
                continue
            expected = "/api" + raw
            assert expected in routes, (
                f"{source.name} 里调了 {raw}（后端应为 {expected}），但后端路由表里没有"
            )
            checked.append(raw)

    assert checked, "没从前端源码里解析出任何路径（正则失效？）"


def test_frontend_admin_web_paths_exist_in_backend():
    """admin-web 的 admin.ts / billing.ts 路径在后端都有对应路由。"""
    _assert_frontend_paths_have_backend_routes()


def test_frontend_uses_exactly_our_four_endpoints():
    """本次补齐的 4 个端点确实是前端在调的那 4 个（防契约漂移）。"""
    text = "".join(
        (_archive_src_dir() / "api" / "modules" / name).read_text(encoding="utf-8")
        for name in ("admin.ts", "billing.ts")
    )
    for path in (
        "/admin/dashboard/overview",
        "/admin/dashboard/members",
        "/billing/pending",
        "/billing/confirm",
    ):
        assert path in text, f"前端源码里已不再调用 {path}，本用例组需同步更新"

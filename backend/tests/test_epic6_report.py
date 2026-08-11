"""Epic6 家长周报集成测试（C1~C13）。

覆盖：
    C1  生成接口必须登录（无 token → 401）
    C2  生成成功返回 share_url，token 32 字符
    C3  幂等：force=false 命中缓存（cached=true，token 不变）
    C4  重新生成（force=true）不换 token，原地更新
    C5  公开接口：有效 token 返回完整载荷
    C6  公开接口：无效 token 返回 404 统一文案（不区分不存在/已过期）
    C7  公开接口：空 token / 超长 token 直接 404
    C8  家长每打开一次分享页 view_count +1（U4）
    C9  零练习 → 整页空态（is_empty=true，不出现任何 0%）
    C10 练习量偏少（1~2 题）→ low_data.is_low=true
    C11 环比：首份 reason=first_report；跨周第二份 available=true 且有 improved/declined
    C12 分享令牌有效期 30 天；重新生成不换 token
    C13 历史列表按周期倒序，含 view_count / is_expired

测试库是会话级共享内存 SQLite，每个用例用独立手机号造独立学生，避免互相污染。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models.learning import Attempt
from app.services import report_service
from app.services.report_service import (
    ComparisonResult,
    EngagementStat,
    WeeklySnapshot,
    diff_snapshots,
    resolve_period,
)

from e6_helpers import login, patch_report_ark, seed_report_week
from t03_helpers import assert_fail, assert_ok

from sqlalchemy import select

from app.models.weekly_report import WeeklyReport


# ==================================================================== 工具
def _new_student(client, new_phone) -> tuple[dict, uuid.UUID]:
    """造一个独立学生，返回 (请求头, 学生ID)。"""
    phone = new_phone()
    headers, sid = login(client, phone)
    return headers, sid


def _gen(client, headers, *, force: bool = False) -> dict:
    return assert_ok(client.post("/api/student/report/generate", json={"force": force}, headers=headers))


# ==================================================================== C1 鉴权
def test_generate_requires_auth(client):
    """无登录访问生成接口 → 401。"""
    resp = client.post("/api/student/report/generate", json={"force": False})
    assert_fail(resp, 401)


def test_generate_requires_student_role(client, new_phone, make_token):
    """非学生角色（role=parent）访问生成接口 → 403。"""
    phone = new_phone()
    login(client, phone)  # 先建一个学生档案（不影响本用例鉴权判定）
    # 用 parent 角色伪造令牌去打生成接口
    bad = {"Authorization": f"Bearer {make_token(sub='999', role='parent', sid=None)}"}
    resp = client.post("/api/student/report/generate", json={"force": False}, headers=bad)
    assert_fail(resp, 403)


# ==================================================================== C2 生成成功
def test_generate_success_returns_share_url(client, new_phone):
    """生成成功：返回 share_url，token 32 字符，首次 cached=false。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=3, wrong=1)

    data = _gen(client, headers)
    assert data["share_token"]
    assert len(data["share_token"]) == 32
    assert data["share_url"].endswith(f"/r/{data['share_token']}")
    assert data["cached"] is False
    assert "report_id" in data


# ==================================================================== C3 幂等缓存
def test_generate_idempotent_cache(client, new_phone):
    """同一周第二次生成（force=false）命中缓存：token 不变，cached=true。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=3, wrong=1)

    first = _gen(client, headers)
    second = _gen(client, headers)  # force 默认 false
    assert second["share_token"] == first["share_token"]
    assert second["cached"] is True


# ==================================================================== C4/C12 重新生成不换 token
def test_force_regenerate_keeps_token(client, new_phone):
    """force=true 重新生成：token 不变（家长链接持续可用），report_id 不变。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=3, wrong=1)

    first = _gen(client, headers)
    regen = _gen(client, headers, force=True)
    assert regen["share_token"] == first["share_token"]
    assert regen["report_id"] == first["report_id"]
    assert regen["cached"] is False


def test_token_validity_30_days(client, new_phone):
    """分享令牌有效期 30 天。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=2, wrong=0)

    data = _gen(client, headers)
    # 从库里读出记录核对 expires_at
    with SessionLocal() as db:
        rec = db.execute(
            select(WeeklyReport).where(WeeklyReport.token == data["share_token"])
        ).scalar_one()
        ttl = (rec.expires_at - rec.created_at).total_seconds() / 86400.0
    assert abs(ttl - 30) < 1.0


# ==================================================================== C5 公开读取
def test_share_valid_token_full_payload(client, new_phone):
    """有效 token 返回完整载荷。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=3, wrong=1)

    data = _gen(client, headers)
    payload = assert_ok(client.get(f"/api/report/share/{data['share_token']}"))

    # 顶层结构齐备
    for key in (
        "student",
        "period",
        "engagement",
        "error_patterns",
        "weak_points",
        "comparison",
        "narrative",
        "empty_state",
        "low_data",
    ):
        assert key in payload, f"缺失字段 {key}"

    # 不回显任何 ID / 手机号
    assert "student_id" not in payload["student"]
    assert "user_id" not in payload["student"]

    # 学习投入是真实计数，且 weak_points 由掌握度驱动（已造练习）
    assert payload["engagement"]["attempt_count"] == 4
    assert payload["engagement"]["correct_count"] == 3
    assert len(payload["weak_points"]) >= 1

    # 叙述三段都有
    assert payload["narrative"]["headline"]
    assert payload["narrative"]["overview_text"]
    assert payload["narrative"]["progress_text"]


# ==================================================================== C6 失效统一文案
def test_share_invalid_token_404(client):
    """无效 token → 404 统一文案，data 为空，不区分不存在/已过期。"""
    resp = client.get("/api/report/share/this-token-does-not-exist")
    body = assert_fail(resp, 404)
    assert body["msg"] == "这个链接已失效，请让孩子重新生成一份"
    assert body["data"] == {}


# ==================================================================== C7 空/超长 token
def test_share_empty_and_overlong_token_404(client):
    """空 token 与超长 token 直接 404。"""
    assert_fail(client.get("/api/report/share/"), 404)
    overlong = "x" * 100
    assert_fail(client.get(f"/api/report/share/{overlong}"), 404)


# ==================================================================== C8 view_count (U4)
def test_share_view_count_increments(client, new_phone):
    """家长每打开一次分享页 view_count +1。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=2, wrong=0)

    data = _gen(client, headers)
    token = data["share_token"]

    assert_ok(client.get(f"/api/report/share/{token}"))  # 第 1 次
    assert_ok(client.get(f"/api/report/share/{token}"))  # 第 2 次

    # 通过历史接口读 view_count（公开接口载荷不含该字段）
    hist = assert_ok(client.get("/api/student/report/history", headers=headers))
    assert hist["total"] == 1
    assert hist["items"][0]["view_count"] == 2


# ==================================================================== C9 空态
def test_share_empty_state_when_no_practice(client, new_phone):
    """零练习：empty_state.is_empty=true，engagement 计数全 0，correct_rate=null。"""
    headers, sid = _new_student(client, new_phone)  # 完全不造练习

    data = _gen(client, headers)
    payload = assert_ok(client.get(f"/api/report/share/{data['share_token']}"))

    assert payload["empty_state"]["is_empty"] is True
    assert payload["empty_state"]["message"]
    assert payload["engagement"]["attempt_count"] == 0
    assert payload["engagement"]["correct_rate"] is None
    assert payload["comparison"]["available"] is False


# ==================================================================== C10 低数据
def test_share_low_data_flag(client, new_phone):
    """练习量偏少（1 题）：low_data.is_low=true。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=1, wrong=0)

    data = _gen(client, headers)
    payload = assert_ok(client.get(f"/api/report/share/{data['share_token']}"))

    assert payload["engagement"]["attempt_count"] == 1
    assert payload["low_data"]["is_low"] is True
    assert payload["low_data"]["message"]


# ==================================================================== C11 环比
def test_diff_snapshots_first_report():
    """首份周报：prev=None → available=false, reason=first_report。"""
    curr = WeeklySnapshot(engagement=EngagementStat(attempt_count=10, correct_count=8, correct_rate=0.8))
    comp = diff_snapshots(curr, None)
    assert comp.available is False
    assert comp.reason == "first_report"
    assert comp.correct_rate_delta is None


def test_diff_snapshots_improved_and_declined():
    """跨周第二份：correct_rate 上升，部分知识点进步 / 退步。"""
    curr = WeeklySnapshot(
        mastery=[
            {"kp_id": "A", "kp_name": "A", "score": 0.8, "level": 2, "error_rate": 0.2},
            {"kp_id": "B", "kp_name": "B", "score": 0.5, "level": 1, "error_rate": 0.5},
        ],
        engagement=EngagementStat(attempt_count=10, correct_count=8, correct_rate=0.8),
    )
    prev = WeeklySnapshot(
        mastery=[
            {"kp_id": "A", "kp_name": "A", "score": 0.6, "level": 1, "error_rate": 0.4},
            {"kp_id": "B", "kp_name": "B", "score": 0.9, "level": 3, "error_rate": 0.1},
        ],
        engagement=EngagementStat(attempt_count=10, correct_count=6, correct_rate=0.6),
    )
    comp = diff_snapshots(curr, prev)
    assert comp.available is True
    assert comp.correct_rate_delta == 0.2
    assert any(item["kp_id"] == "A" for item in comp.improved)
    assert any(item["kp_id"] == "B" for item in comp.declined)


def test_comparison_across_weeks_available(client, new_phone):
    """集成：跨两个自然周各生成一份，第二份 comparison.available=true。"""
    headers, sid = _new_student(client, new_phone)
    qid = seed_report_week(sid, correct=2, wrong=0)["question_id"]

    # 北京时间：week1=2024-08-05(周一) 当周；week2=两周后
    week1 = datetime(2024, 8, 7, 4, 0, 0, tzinfo=timezone.utc)  # 北京 12:00 周三
    week2 = week1 + timedelta(days=14)

    _add_attempt(sid, qid, True, week1 - timedelta(hours=2))
    _add_attempt(sid, qid, True, week1 - timedelta(hours=1))

    with SessionLocal() as db:
        rec1, cached1 = report_service.generate_weekly_report(db, sid, now=week1)
        assert not cached1
        # week2 正确率下降
        for _ in range(3):
            _add_attempt(sid, qid, False, week2 - timedelta(hours=2))
        _add_attempt(sid, qid, True, week2 - timedelta(hours=1))

        rec2, cached2 = report_service.generate_weekly_report(db, sid, now=week2, force=True)
        assert cached2 is False
        payload = report_service.build_report_payload(db, rec2)
        assert payload["comparison"]["available"] is True
        assert payload["comparison"]["prev_period_start"]


def test_resolve_period_is_beijing_natural_week():
    """resolve_period：落在当前北京时间自然周（周一~周日）。"""
    # 任取一个周三（北京时间）作为参照
    now = datetime(2024, 8, 7, 4, 0, 0, tzinfo=timezone.utc)  # 北京 2024-08-07 12:00
    period = resolve_period(now=now)
    # 库内 UTC 边界
    assert period.start_utc.tzinfo is not None
    # 北京本地：start 必为周一，且 now 落在 [start, end]
    assert period.start_local.weekday() == 0  # 周一
    assert period.start_local <= now.astimezone(report_service.LOCAL_TZ).date() <= period.end_local


# ==================================================================== AI 路径（source == "ai"）
def test_generate_ai_source_when_ark_ok(client, new_phone, monkeypatch):
    """方舟可用时叙述 source=ai，且 advice_items 在 2~3 条。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=3, wrong=1)
    patch_report_ark(monkeypatch)

    data = _gen(client, headers)
    payload = assert_ok(client.get(f"/api/report/share/{data['share_token']}"))
    assert payload["narrative"]["source"] == "ai"
    assert 2 <= len(payload["narrative"]["advice_items"]) <= 3


# ==================================================================== C13 历史列表
def test_history_list_desc_and_fields(client, new_phone):
    """历史列表按周期倒序，含 view_count / is_expired / share_url。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=2, wrong=0)

    data = _gen(client, headers)
    token = data["share_token"]
    # 打开一次，view_count 变 1
    assert_ok(client.get(f"/api/report/share/{token}"))

    hist = assert_ok(client.get("/api/student/report/history", headers=headers))
    assert hist["total"] == 1
    item = hist["items"][0]
    assert item["share_token"] == token
    assert item["share_url"].endswith(f"/r/{token}")
    assert item["view_count"] == 1
    assert item["is_expired"] is False


# ==================================================================== 内部：按时间插作答
def _add_attempt(student_id: uuid.UUID, question_id: str, is_correct: bool, when: datetime) -> None:
    """在指定时刻插入一条作答（用于构造跨周统计）。"""
    with SessionLocal() as db:
        db.add(
            Attempt(
                student_id=student_id,
                question_id=uuid.UUID(question_id),
                is_correct=bool(is_correct),
                created_at=when,
                client_event_id=f"e6-{uuid.uuid4().hex}",
            )
        )
        db.commit()

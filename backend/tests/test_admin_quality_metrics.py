"""后台 AI 质量监控端点单测（Benchmark 维度一 · 模块5 看板数据面）。

覆盖（与任务书验收逐条对应）：
    1. **端点鉴权**：无 token 401、学生角色 403、admin 200；
    2. **空库分母 0 返 None**：按全新学生过滤时 answer_leak_rate.value=None
       （不编造 0%），讲解质量空趋势（total 0 / buckets 空 / 均分 None）；
    3. **数值与 service 手工对账**：端点返回值与直接调用
       ``quality_metrics_service`` 的结果逐字段一致（rate / 分子 / 分母 /
       五维均分 / 趋势桶）；
    4. **分页与筛选正确**：page/size 边界、event_type / severity 组合筛选、
       非法筛选值 400、排序为 created_at 倒序；
    5. **趋势时间窗边界**：窗外（10 天前）trace 不计入默认 7 天窗，
       ``window_days`` 参数生效后计入。

口径备注：本文件**不设** ``DATABASE_URL``（conftest 基线闸门，走会话级共享
内存 SQLite）。测试库跨用例数据会堆积，因此每条用例自造**全新学生**并按
``student_id`` 过滤，断言只覆盖自己造的数据，不假设全局为空。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytest

from app.models.ai import AiMessage, AiSession
from app.models.quality_trace import QualityTrace
from app.models.user import Student, User
from app.services import quality_metrics_service as qms


# ==================================================================== 造数据
def make_student(db) -> uuid.UUID:
    """造一个干净的学生，返回 ID（共享内存库隔离键）。"""
    user = User(id=uuid.uuid4(), phone=f"13{uuid.uuid4().hex[:9]}", nickname="质检后台")
    db.add(user)
    db.flush()
    student = Student(id=uuid.uuid4(), user_id=user.id, grade=4)
    db.add(student)
    db.flush()
    return student.id


def make_session(db, *, student_id: uuid.UUID) -> uuid.UUID:
    """造一个 AI 会话，返回 ID。"""
    session = AiSession(student_id=student_id)
    db.add(session)
    db.flush()
    return session.id


def make_assistant_message(db, *, session_id: uuid.UUID, content: str = "引导语") -> None:
    """造一条 AI 应答（answer_leak_rate 的分母来源）。"""
    db.add(AiMessage(session_id=session_id, role="assistant", content=content))
    db.flush()


def make_leak_trace(
    db,
    *,
    student_id: uuid.UUID,
    created_at: Optional[datetime] = None,
    severity: str = "warning",
) -> None:
    """直接落一条 answer_leak trace。"""
    db.add(
        QualityTrace(
            student_id=student_id,
            event_type="answer_leak",
            severity=severity,
            payload={"mode": "DEMO", "strict": False, "excerpt": "答案：12"},
            created_at=created_at or datetime.now(timezone.utc),
        )
    )
    db.flush()


def make_quality_trace(
    db,
    *,
    student_id: uuid.UUID,
    overall: int = 4,
    dimensions: Optional[Dict[str, int]] = None,
    created_at: Optional[datetime] = None,
    severity: str = "info",
) -> None:
    """直接落一条 explanation_quality trace（5 维 + overall）。"""
    dims = dimensions or {k: overall for k in qms.DIMENSION_KEYS}
    db.add(
        QualityTrace(
            student_id=student_id,
            event_type="explanation_quality",
            severity=severity,
            payload={
                "dimensions": dims,
                "overall": overall,
                "grade": "良好",
                "threshold": 3,
                "passed": True,
                "suggestion": "保持",
                "parsed_ok": True,
            },
            created_at=created_at or datetime.now(timezone.utc),
        )
    )
    db.flush()


def admin_headers(make_token) -> dict:
    """admin 角色的 Authorization 头。"""
    return {"Authorization": f"Bearer {make_token(sub='1', role='admin')}"}


def student_headers(make_token) -> dict:
    """学生角色的 Authorization 头（越权用例用）。"""
    return {"Authorization": f"Bearer {make_token(sub='2', role='student', sid='2')}"}


def get_metrics(client, make_token, **params) -> Dict[str, Any]:
    """请求质量总览端点，返回 ``data``。"""
    resp = client.get("/api/admin/quality-metrics", params=params, headers=admin_headers(make_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def metric_by_key(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    """按 key 取总览里的指标行。"""
    for row in data["metrics"]:
        if row["key"] == key:
            return row
    raise AssertionError(f"指标 {key} 不在总览里: {[r['key'] for r in data['metrics']]}")


# ==================================================================== 鉴权
def test_quality_metrics_requires_admin(client, make_token):
    """无 token 401；学生角色 403；admin 200。"""
    # 无 token
    r = client.get("/api/admin/quality-metrics")
    assert r.status_code == 401, r.text

    # 学生角色越权
    r = client.get("/api/admin/quality-metrics", headers=student_headers(make_token))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == 403

    # admin 放行，且 6 项规划指标恒在、顺序稳定
    r = client.get("/api/admin/quality-metrics", headers=admin_headers(make_token))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [row["key"] for row in data["metrics"]] == [
        "recognition_accuracy",
        "answer_accuracy",
        "error_cause_accuracy",
        "answer_leak_rate",
        "practice_generation_success_rate",
        "validation_failure_rate",
    ]
    # 无数据源的指标必须显式标注 pending + note，不得给假数值
    for row in data["metrics"]:
        if row["key"] != "answer_leak_rate":
            assert row["status"] == "pending"
            assert row["value"] is None
            assert "数据源待建" in row["note"]
    assert "explanation_quality" in data
    assert "buckets" in data["explanation_quality"]


def test_quality_traces_requires_admin(client, make_token):
    """trace 列表端点同样只放行 admin。"""
    assert client.get("/api/admin/quality/traces").status_code == 401
    r = client.get("/api/admin/quality/traces", headers=student_headers(make_token))
    assert r.status_code == 403
    r = client.get("/api/admin/quality/traces", headers=admin_headers(make_token))
    assert r.status_code == 200
    assert r.json()["code"] == 0


# ==================================================================== 空库口径
def test_quality_metrics_empty_student_returns_none(client, db, make_token):
    """全新学生（窗内无应答无事件）：分母 0 → None，不编造 0%。"""
    fresh = make_student(db)

    data = get_metrics(client, make_token, student_id=str(fresh))
    leak = metric_by_key(data, "answer_leak_rate")
    assert leak["status"] == "available"
    assert leak["numerator"] == 0
    assert leak["denominator"] == 0
    assert leak["value"] is None  # 分母 0：不编造 0%

    quality = data["explanation_quality"]
    assert quality["total_count"] == 0
    assert quality["overall_avg"] is None
    assert all(v is None for v in quality["dimension_avgs"].values())
    assert quality["buckets"] == []


# ==================================================================== service 对账
def test_quality_metrics_matches_service_reconciliation(client, db, make_token):
    """有数据时端点数值与 quality_metrics_service 直接调用逐字段对账。"""
    fresh = make_student(db)
    session_id = make_session(db, student_id=fresh)
    for _ in range(4):
        make_assistant_message(db, session_id=session_id)
    make_leak_trace(db, student_id=fresh)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    make_quality_trace(
        db,
        student_id=fresh,
        overall=4,
        dimensions={"guidance": 5, "correctness": 3, "clarity": 4, "relevance": 4, "warmth": 4},
        created_at=yesterday,
    )
    make_quality_trace(db, student_id=fresh, overall=2, created_at=datetime.now(timezone.utc))

    # ---- 端点值 ----------------------------------------------------------
    data = get_metrics(client, make_token, student_id=str(fresh))
    leak_row = metric_by_key(data, "answer_leak_rate")

    # ---- service 手工值（口径唯一真相源）---------------------------------
    leak = qms.answer_leak_rate(db, window_days=7, student_id=fresh)
    trend = qms.explanation_quality_trend(db, window_days=7, student_id=fresh)

    # 1) 直接给答案率：1/4，与 service 一致
    assert leak_row["numerator"] == leak["answer_leak_count"] == 1
    assert leak_row["denominator"] == leak["ai_response_count"] == 4
    assert leak_row["value"] == pytest.approx(leak["rate"]) == pytest.approx(1 / 4)

    # 2) 讲解质量：窗内均分 = (4+2)/2 = 3；维度均分由桶内数据加权得出
    quality = data["explanation_quality"]
    assert quality["total_count"] == trend["total_count"] == 2
    assert quality["overall_avg"] == pytest.approx(3.0)
    dims = quality["dimension_avgs"]
    assert dims["guidance"] == pytest.approx((5 + 2) / 2)   # 3.5
    assert dims["correctness"] == pytest.approx((3 + 2) / 2)  # 2.5
    assert dims["clarity"] == pytest.approx(3.0)
    assert dims["relevance"] == pytest.approx(3.0)
    assert dims["warmth"] == pytest.approx(3.0)

    # 3) 趋势桶与 service 完全一致（按 UTC 日升序，两天各 1 条）
    assert quality["buckets"] == trend["buckets"]
    assert [b["date"] for b in quality["buckets"]] == sorted(
        b["date"] for b in quality["buckets"]
    )
    assert quality["buckets"][0]["count"] == 1
    assert quality["buckets"][0]["overall_avg"] == 4.0
    assert quality["buckets"][1]["overall_avg"] == 2.0


def test_quality_metrics_student_filter_isolates(client, db, make_token):
    """学生过滤：只统计该学生的应答与事件（下钻口径与 service 一致）。"""
    target = make_student(db)
    other = make_student(db)
    sess_t = make_session(db, student_id=target)
    sess_o = make_session(db, student_id=other)
    make_assistant_message(db, session_id=sess_t)
    make_assistant_message(db, session_id=sess_o)
    make_assistant_message(db, session_id=sess_o)
    make_leak_trace(db, student_id=target)
    make_leak_trace(db, student_id=other)
    make_leak_trace(db, student_id=other)
    make_quality_trace(db, student_id=other, overall=5)

    data = get_metrics(client, make_token, student_id=str(target))
    leak_row = metric_by_key(data, "answer_leak_rate")
    assert leak_row["numerator"] == 1
    assert leak_row["denominator"] == 1
    assert leak_row["value"] == pytest.approx(1.0)
    assert data["explanation_quality"]["total_count"] == 0
    assert data["student_id"] == str(target)


# ==================================================================== 时间窗边界
def test_quality_metrics_window_boundary(client, db, make_token):
    """窗外 trace 不计入默认 7 天窗；window_days=30 后计入。"""
    fresh = make_student(db)
    session_id = make_session(db, student_id=fresh)
    make_assistant_message(db, session_id=session_id)
    stale = datetime.now(timezone.utc) - timedelta(days=10)
    make_leak_trace(db, student_id=fresh, created_at=stale)
    make_quality_trace(db, student_id=fresh, overall=3, created_at=stale)

    # 默认 7 天窗：窗外事件不计入；有分母、分子 0 → 真 0%（不是 None）
    data = get_metrics(client, make_token, student_id=str(fresh))
    leak_row = metric_by_key(data, "answer_leak_rate")
    assert leak_row["numerator"] == 0
    assert leak_row["denominator"] == 1
    assert leak_row["value"] == 0.0
    assert data["explanation_quality"]["total_count"] == 0
    assert data["explanation_quality"]["buckets"] == []
    assert data["window_days"] == 7

    # 30 天窗：窗外事件进入统计
    data30 = get_metrics(client, make_token, student_id=str(fresh), window_days=30)
    leak30 = metric_by_key(data30, "answer_leak_rate")
    assert leak30["numerator"] == 1
    assert leak30["value"] == pytest.approx(1.0)
    assert data30["explanation_quality"]["total_count"] == 1
    assert len(data30["explanation_quality"]["buckets"]) == 1
    assert data30["explanation_quality"]["overall_avg"] == pytest.approx(3.0)


def test_quality_metrics_rejects_bad_params(client, make_token):
    """非法参数 400：student_id 非 UUID、window_days 越界。"""
    r = client.get(
        "/api/admin/quality-metrics",
        params={"student_id": "not-a-uuid"},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == 400

    r = client.get(
        "/api/admin/quality-metrics",
        params={"window_days": 0},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 400, r.text


# ==================================================================== trace 列表
def _seed_traces(db, student_id: uuid.UUID, leak_n: int = 15, quality_n: int = 10) -> None:
    """给一个学生灌 mix 好的 trace（created_at 显式错开，供排序断言）。"""
    now = datetime.now(timezone.utc)
    for i in range(leak_n):
        make_leak_trace(db, student_id=student_id, created_at=now - timedelta(minutes=2 * i))
    for j in range(quality_n):
        make_quality_trace(
            db, student_id=student_id, created_at=now - timedelta(minutes=2 * (leak_n + j))
        )


def test_quality_traces_pagination(client, db, make_token):
    """分页：total 正确、每页条数正确、末页余量正确。"""
    fresh = make_student(db)
    _seed_traces(db, fresh, leak_n=15, quality_n=10)

    r = client.get(
        "/api/admin/quality/traces",
        params={"student_id": str(fresh), "page": 1, "size": 10},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["total"] == 25
    assert d["page"] == 1
    assert d["size"] == 10
    assert len(d["items"]) == 10

    r = client.get(
        "/api/admin/quality/traces",
        params={"student_id": str(fresh), "page": 3, "size": 10},
        headers=admin_headers(make_token),
    )
    d = r.json()["data"]
    assert len(d["items"]) == 5  # 25 = 10 + 10 + 5

    r = client.get(
        "/api/admin/quality/traces",
        params={"student_id": str(fresh), "page": 1, "size": 100},
        headers=admin_headers(make_token),
    )
    d = r.json()["data"]
    assert d["total"] == 25
    assert len(d["items"]) == 25  # size 上限 200 内全量返回


def test_quality_traces_filters(client, db, make_token):
    """筛选：event_type / severity 单独与组合过滤，命中行全部满足条件。"""
    fresh = make_student(db)
    _seed_traces(db, fresh, leak_n=15, quality_n=10)

    def _query(**params) -> Dict[str, Any]:
        resp = client.get(
            "/api/admin/quality/traces",
            params={"student_id": str(fresh), **params},
            headers=admin_headers(make_token),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    d = _query(event_type="answer_leak")
    assert d["total"] == 15
    assert all(it["event_type"] == "answer_leak" for it in d["items"])

    d = _query(severity="info")
    assert d["total"] == 10
    assert all(it["severity"] == "info" for it in d["items"])

    # 组合筛选：leak 全为 warning、quality 全为 info → 交集为空
    d = _query(event_type="answer_leak", severity="info")
    assert d["total"] == 0
    assert d["items"] == []

    # 行结构完整（供前端直接渲染）
    d = _query(event_type="explanation_quality", size=5)
    row = d["items"][0]
    assert set(row) == {
        "id",
        "event_type",
        "severity",
        "student_id",
        "session_id",
        "question_id",
        "payload",
        "created_at",
    }
    assert row["student_id"] == str(fresh)
    assert isinstance(row["payload"], dict)
    assert set(row["payload"]["dimensions"]) == set(qms.DIMENSION_KEYS)


def test_quality_traces_ordered_by_created_desc(client, db, make_token):
    """排序：created_at 倒序（最新在前）。"""
    fresh = make_student(db)
    _seed_traces(db, fresh, leak_n=5, quality_n=5)

    d = client.get(
        "/api/admin/quality/traces",
        params={"student_id": str(fresh), "size": 10},
        headers=admin_headers(make_token),
    ).json()["data"]
    stamps = [it["created_at"] for it in d["items"]]
    assert stamps == sorted(stamps, reverse=True)


def test_quality_traces_rejects_bad_filters(client, make_token):
    """非法筛选值 400（不静默忽略、不 500）。"""
    for bad in ({"event_type": "unknown_event"}, {"severity": "critical"}, {"student_id": "xx"}):
        r = client.get(
            "/api/admin/quality/traces", params=bad, headers=admin_headers(make_token)
        )
        assert r.status_code == 400, (bad, r.text)
        assert r.json()["code"] == 400


# ==================================================================== 全量列表
def test_quality_traces_global_list_includes_seeded(client, db, make_token):
    """不带 student_id 时走全量列表（与其他 admin 列表同款分页范式）。"""
    fresh = make_student(db)
    _seed_traces(db, fresh, leak_n=2, quality_n=1)

    r = client.get(
        "/api/admin/quality/traces",
        params={"page": 1, "size": 50},
        headers=admin_headers(make_token),
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["total"] >= 3
    assert str(fresh) in {it["student_id"] for it in d["items"]}

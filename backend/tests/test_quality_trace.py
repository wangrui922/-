"""Benchmark 两维评测测试（60 人内测第二梯队 · 教学行为 + 学习效果）。

与任务书验收逐条对应：

1. **新表可建**：``QualityTrace`` 已登记 ``app.models``，内存库
   ``create_all`` 能建出 ``quality_traces``；
2. **hook 落 trace**：DEMO 讲解泄漏 → answer_leak trace；DEMO 质量闸门
   评分 → explanation_quality trace（含 5 维分数 + overall）；
3. **hook 容错**：落库失败只 warning 不拖垮辅导主流程（answer_leak 与
   explanation_quality 两条各一条）；
4. **聚合正确性**：answer_leak_rate 在 mock 数据上的计算（含分母 0 返回
   None、时间窗过滤、学生过滤）；explanation_quality_trend 按日分桶；
5. **eval_learning_gain 脚本 sanity**：构造数据上的输出（样本量报告、
   空数据不编造、review 表缺失降级）。

口径备注：本文件**不设** ``DATABASE_URL``，走 conftest 的会话级共享内存
SQLite（团队回归基线闸门，见 conftest 顶部注释）。
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import inspect as sa_inspect, select, text

from app.core.config import settings
from app.db import SessionLocal, engine
from app.models.ai import AiMessage, AiSession
from app.models.content import KnowledgePoint, Question
from app.models.dna import StudentLearningEvent
from app.models.learning import Attempt
from app.models.quality_trace import QualityTrace
from app.models.user import Student, User
from app.services import quality_metrics_service as qms
from t03_helpers import FakeArk, assert_ok, patch_ark

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import eval_learning_gain  # noqa: E402


# ==================================================================== 造数据
def _uid() -> str:
    return uuid.uuid4().hex[:8]


def make_student(db) -> uuid.UUID:
    """造一个干净的学生，返回 ID。"""
    user = User(id=uuid.uuid4(), phone=f"13{uuid.uuid4().hex[:9]}", nickname="质检")
    db.add(user)
    db.flush()
    student = Student(id=uuid.uuid4(), user_id=user.id, grade=4)
    db.add(student)
    db.flush()
    return student.id


def make_kp(db, name: Optional[str] = None) -> uuid.UUID:
    """造一个知识点（code 带随机后缀避开 UNIQUE），返回 ID。"""
    kp = KnowledgePoint(name=name or f"知识点-{_uid()}", code=f"KP-{_uid()}")
    db.add(kp)
    db.flush()
    return kp.id


def make_question(db, *, kp_id: Optional[uuid.UUID] = None, content: Optional[str] = None) -> uuid.UUID:
    """造一道题，返回 ID。"""
    question = Question(
        content=content or f"题目-{_uid()}",
        subject="数学",
        grade=4,
        difficulty=3,
        kp_id=kp_id,
    )
    db.add(question)
    db.flush()
    return question.id


def make_attempt(
    db,
    *,
    student_id: uuid.UUID,
    question_id: uuid.UUID,
    is_correct: bool,
    created_at: datetime,
) -> uuid.UUID:
    """造一条作答流水，返回 ID。"""
    attempt = Attempt(
        student_id=student_id,
        question_id=question_id,
        selected_answer="x",
        is_correct=is_correct,
        created_at=created_at,
    )
    db.add(attempt)
    db.flush()
    return attempt.id


def make_assistant_message(db, *, session_id: uuid.UUID, content: str = "引导语") -> None:
    """造一条 AI 应答（分母来源）。"""
    db.add(AiMessage(session_id=session_id, role="assistant", content=content))
    db.flush()


def make_session(db, *, student_id: uuid.UUID) -> uuid.UUID:
    """造一个 AI 会话，返回 ID。"""
    session = AiSession(student_id=student_id)
    db.add(session)
    db.flush()
    return session.id


def make_leak_trace(
    db,
    *,
    student_id: Optional[uuid.UUID] = None,
    created_at: Optional[datetime] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """直接落一条 answer_leak trace（测试聚合用）。"""
    db.add(
        QualityTrace(
            student_id=student_id,
            event_type="answer_leak",
            severity="warning",
            payload=payload or {"mode": "DEMO"},
            created_at=created_at or datetime.now(timezone.utc),
        )
    )
    db.flush()


def make_quality_trace(
    db,
    *,
    student_id: Optional[uuid.UUID] = None,
    overall: int = 4,
    dimensions: Optional[Dict[str, int]] = None,
    created_at: Optional[datetime] = None,
) -> None:
    """直接落一条 explanation_quality trace（测试趋势用）。"""
    dims = dimensions or {k: overall for k in qms.DIMENSION_KEYS}
    db.add(
        QualityTrace(
            student_id=student_id,
            event_type="explanation_quality",
            severity="info",
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


#: review_cards / review_logs 的建表 SQL（与 migration 013 对齐，只读脚本要读它们）
_REVIEW_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS review_cards (
        id CHAR(36) PRIMARY KEY,
        student_id CHAR(36) NOT NULL,
        question_id CHAR(36),
        kp_id CHAR(36),
        due TEXT,
        stability REAL NOT NULL DEFAULT 0,
        difficulty REAL NOT NULL DEFAULT 0,
        elapsed_days INTEGER NOT NULL DEFAULT 0,
        scheduled_days INTEGER NOT NULL DEFAULT 0,
        reps INTEGER NOT NULL DEFAULT 0,
        lapses INTEGER NOT NULL DEFAULT 0,
        state INTEGER NOT NULL DEFAULT 0,
        last_review TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS review_logs (
        id CHAR(36) PRIMARY KEY,
        card_id CHAR(36) NOT NULL,
        student_id CHAR(36) NOT NULL,
        rating INTEGER NOT NULL,
        state INTEGER NOT NULL,
        elapsed_days INTEGER NOT NULL DEFAULT 0,
        scheduled_days INTEGER NOT NULL DEFAULT 0,
        stability_before REAL,
        stability_after REAL,
        difficulty_before REAL,
        difficulty_after REAL,
        review_at TEXT NOT NULL
    )
    """,
]


def ensure_review_tables(db) -> None:
    """确保 review_cards / review_logs 存在（内存库无 013 迁移，需自建）。"""
    for sql in _REVIEW_TABLES_SQL:
        db.execute(text(sql))
    db.flush()


def drop_review_tables(db) -> None:
    """删掉 review 表（测「表缺失降级」路径用）。"""
    db.execute(text("DROP TABLE IF EXISTS review_logs"))
    db.execute(text("DROP TABLE IF EXISTS review_cards"))
    db.flush()


def make_review_log(
    db,
    *,
    card_id: uuid.UUID,
    student_id: uuid.UUID,
    rating: int,
    elapsed_days: int,
    review_at: datetime,
    kp_id: Optional[uuid.UUID] = None,
    question_id: Optional[uuid.UUID] = None,
) -> None:
    """插一张复习卡 + 一条复习日志。"""
    now = review_at
    db.execute(
        text(
            "INSERT INTO review_cards "
            "(id, student_id, question_id, kp_id, due, stability, difficulty, "
            " elapsed_days, scheduled_days, reps, lapses, state, last_review, "
            " created_at, updated_at) "
            "VALUES (:id, :sid, :qid, :kp, :due, 1.0, 2.0, :ed, 7, 1, 0, 1, :lr, :ca, :ua)"
        ),
        {
            "id": str(card_id),
            "sid": str(student_id),
            "qid": str(question_id) if question_id else None,
            "kp": str(kp_id) if kp_id else None,
            "due": now.isoformat(),
            "ed": elapsed_days,
            "lr": now.isoformat(),
            "ca": now.isoformat(),
            "ua": now.isoformat(),
        },
    )
    db.execute(
        text(
            "INSERT INTO review_logs "
            "(id, card_id, student_id, rating, state, elapsed_days, scheduled_days, "
            " stability_before, stability_after, difficulty_before, difficulty_after, review_at) "
            "VALUES (:id, :cid, :sid, :rating, 1, :ed, 7, 1.0, 1.2, 2.0, 2.0, :ra)"
        ),
        {
            "id": str(uuid.uuid4()),
            "cid": str(card_id),
            "sid": str(student_id),
            "rating": rating,
            "ed": elapsed_days,
            "ra": now.isoformat(),
        },
    )
    db.flush()


def count_traces(
    db,
    *,
    event_type: str,
    student_id: Optional[uuid.UUID] = None,
    session_id: Optional[uuid.UUID] = None,
) -> int:
    """按隔离键数 trace（**不假设全表为 0**——测试库是会话级共享内存库，
    跨用例数据会堆积，全局计数会让用例互相拖垮）。"""
    stmt = select(QualityTrace).where(QualityTrace.event_type == event_type)
    if student_id is not None:
        stmt = stmt.where(QualityTrace.student_id == student_id)
    if session_id is not None:
        stmt = stmt.where(QualityTrace.session_id == session_id)
    return len(db.execute(stmt).scalars().all())


# ==================================================================== 模型
def test_quality_trace_registered_and_table_created():
    """QualityTrace 登记进 app.models，且内存库 create_all 能建出表。"""
    from app.models import QualityTrace as Exported
    from app.models.base import Base

    assert Exported is QualityTrace
    assert "quality_traces" in Base.metadata.tables
    assert sa_inspect(engine).has_table("quality_traces")

    # 列齐全（与迁移 023 对齐的关键列）
    columns = {c.name for c in Base.metadata.tables["quality_traces"].columns}
    assert {
        "id",
        "student_id",
        "session_id",
        "question_id",
        "event_type",
        "severity",
        "payload",
        "created_at",
    } <= columns


# ==================================================================== 落库服务
def test_record_answer_leak_trace_persists(db):
    """answer_leak trace 正常落库，字段与 payload 原样读回。"""
    student_id = make_student(db)
    session_id = make_session(db, student_id=student_id)
    question_id = make_question(db)

    trace = qms.record_answer_leak_trace(
        db,
        student_id=student_id,
        session_id=session_id,
        question_id=question_id,
        payload={"mode": "DEMO", "strict": False, "excerpt": "答案：12"},
    )

    assert trace.id is not None
    assert trace.event_type == "answer_leak"
    assert trace.severity == "warning"
    assert trace.student_id == student_id
    assert trace.payload["excerpt"] == "答案：12"
    # 按本用例的学生隔离键计数（共享内存库，不假设全表只有 1 条）
    assert count_traces(db, event_type="answer_leak", student_id=student_id) == 1


def test_record_explanation_quality_trace_skips_unparsed(db):
    """解析失败（parsed_ok=False）的评分不落库——默认 3 分不是真实评分。"""

    class _Unparsed:
        parsed_ok = False
        dimensions = {k: 3 for k in qms.DIMENSION_KEYS}
        overall = 3
        grade = "合格"
        suggestion = ""

    # 用一个本用例独有的 session 哨兵键计数：record 返回 None 且该键下无行
    sentinel_session = make_session(db, student_id=make_student(db))
    result = qms.record_explanation_quality_trace(
        db, session_id=sentinel_session, quality=_Unparsed(), threshold=3
    )
    assert result is None
    assert count_traces(
        db, event_type="explanation_quality", session_id=sentinel_session
    ) == 0


def test_record_explanation_quality_trace_persists_scores(db):
    """评分解析成功 → 落库含 5 维分数 + overall + 达标结论。"""

    class _Quality:
        parsed_ok = True
        dimensions = {
            "guidance": 5,
            "correctness": 4,
            "clarity": 4,
            "relevance": 3,
            "warmth": 5,
        }
        overall = 4
        grade = "良好"
        suggestion = "可以再缩小提示颗粒度"

    student_id = make_student(db)
    trace = qms.record_explanation_quality_trace(
        db, student_id=student_id, quality=_Quality(), threshold=3
    )

    assert trace is not None
    assert trace.event_type == "explanation_quality"
    assert trace.severity == "info"  # overall 4 >= threshold 3
    payload = trace.payload
    assert payload["overall"] == 4
    assert payload["dimensions"]["guidance"] == 5
    assert set(payload["dimensions"]) == set(qms.DIMENSION_KEYS)
    assert payload["passed"] is True
    assert payload["threshold"] == 3


def test_record_explanation_quality_trace_below_threshold_is_warning(db):
    """低于阈值的评分 severity=warning（便于筛「待改进」样本）。"""

    class _Quality:
        parsed_ok = True
        dimensions = {k: 2 for k in qms.DIMENSION_KEYS}
        overall = 2
        grade = "待改进"
        suggestion = "需改进"

    trace = qms.record_explanation_quality_trace(
        db, quality=_Quality(), threshold=3
    )
    assert trace is not None
    assert trace.severity == "warning"
    assert trace.payload["passed"] is False


# ==================================================================== 聚合：直接给答案率
# 注意：测试库是**会话级共享**内存 SQLite（conftest），跨用例数据不互可见地
# 堆积——所以下面每个聚合用例都自造一个**全新学生**并按学生过滤，断言只覆盖
# 自己造的数据，不假设全局为空。
def test_answer_leak_rate_denominator_zero_returns_none(db):
    """窗内无任何 AI 应答 → rate 为 None，不编造 0%。"""
    fresh = make_student(db)
    result = qms.answer_leak_rate(db, window_days=7, student_id=fresh)
    assert result["answer_leak_count"] == 0
    assert result["ai_response_count"] == 0
    assert result["rate"] is None


def test_answer_leak_rate_computes_on_mock_data(db):
    """mock 数据：3 次 AI 应答 + 1 次泄漏 → 1/3。"""
    fresh = make_student(db)
    session_id = make_session(db, student_id=fresh)
    for _ in range(3):
        make_assistant_message(db, session_id=session_id)
    make_leak_trace(db, student_id=fresh)

    result = qms.answer_leak_rate(db, window_days=7, student_id=fresh)
    assert result["ai_response_count"] == 3
    assert result["answer_leak_count"] == 1
    assert result["rate"] == pytest.approx(1 / 3)


def test_answer_leak_rate_respects_window(db):
    """窗外（10 天前）的泄漏不计入 7 天窗。"""
    fresh = make_student(db)
    session_id = make_session(db, student_id=fresh)
    make_assistant_message(db, session_id=session_id)
    make_leak_trace(
        db,
        student_id=fresh,
        created_at=datetime.now(timezone.utc) - timedelta(days=10),
    )

    result = qms.answer_leak_rate(db, window_days=7, student_id=fresh)
    assert result["answer_leak_count"] == 0
    assert result["ai_response_count"] == 1
    assert result["rate"] == 0.0  # 有分母、分子为 0 → 真 0%，不是 None


def test_answer_leak_rate_student_filter(db):
    """学生过滤：只统计该学生的应答与泄漏。"""
    sid_a = make_student(db)
    sid_b = make_student(db)
    sess_a = make_session(db, student_id=sid_a)
    sess_b = make_session(db, student_id=sid_b)
    make_assistant_message(db, session_id=sess_a)
    make_assistant_message(db, session_id=sess_b)
    make_assistant_message(db, session_id=sess_b)
    make_leak_trace(db, student_id=sid_a)
    make_leak_trace(db, student_id=sid_b)

    filtered_a = qms.answer_leak_rate(db, window_days=7, student_id=sid_a)
    assert filtered_a["student_id"] == str(sid_a)
    assert filtered_a["answer_leak_count"] == 1
    assert filtered_a["ai_response_count"] == 1
    assert filtered_a["rate"] == pytest.approx(1.0)

    filtered_b = qms.answer_leak_rate(db, window_days=7, student_id=sid_b)
    assert filtered_b["answer_leak_count"] == 1
    assert filtered_b["ai_response_count"] == 2
    assert filtered_b["rate"] == pytest.approx(0.5)


# ==================================================================== 聚合：质量趋势
def test_explanation_quality_trend_buckets_by_day(db):
    """按 UTC 日分桶，均值与维度均分正确；无数据时为空列表。"""
    now = datetime.now(timezone.utc)
    fresh = make_student(db)
    make_quality_trace(db, student_id=fresh, overall=4,
                       dimensions={"guidance": 5, "correctness": 3},
                       created_at=now - timedelta(days=1))
    make_quality_trace(db, student_id=fresh, overall=2, created_at=now)
    make_quality_trace(db, student_id=fresh, overall=5, created_at=now)

    result = qms.explanation_quality_trend(db, window_days=7, student_id=fresh)
    assert result["total_count"] == 3
    assert len(result["buckets"]) == 2  # 两天
    day1, day2 = result["buckets"]  # 按日期升序
    assert day1["count"] == 1
    assert day1["overall_avg"] == 4.0
    assert day1["dimensions"]["guidance"] == 5.0
    assert day2["count"] == 2
    assert day2["overall_avg"] == pytest.approx(3.5)  # (2+5)/2

    # 另一个从无评分记录的学生 → 空趋势
    quiet = make_student(db)
    empty = qms.explanation_quality_trend(
        db, window_days=7, student_id=quiet, now=now
    )
    assert empty["total_count"] == 0
    assert empty["buckets"] == []


# ==================================================================== hook：答案泄漏
class _LeakTutorArk(FakeArk):
    """tutor 回复固定为泄漏文本（含露骨「答案：」宣告）。"""

    def _reply(self, kind: str) -> str:
        if kind == "tutor":
            return "答案：12，直接填这个就行。"
        return super()._reply(kind)


def _auth(client):
    from conftest import login_with_code

    data = login_with_code(client)
    return {"Authorization": f"Bearer {data['access']}"}, data["user"]["studentId"]


def _session_traces(session_id: Any, event_type: str) -> List[QualityTrace]:
    """读某个会话的 trace（测试库会话级共享，必须按 session 过滤）。"""
    sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    with SessionLocal() as s:
        return list(
            s.execute(
                select(QualityTrace).where(
                    QualityTrace.session_id == sid,
                    QualityTrace.event_type == event_type,
                )
            )
            .scalars()
            .all()
        )


def test_demo_leak_records_answer_leak_trace(client, monkeypatch):
    """DEMO 模式 guard 直通：露骨「答案：」宣告落到学生面 → 落 trace。"""
    monkeypatch.setattr(settings, "explanation_quality_gate_enabled", False)
    patch_ark(monkeypatch, fake=_LeakTutorArk())
    headers, _sid = _auth(client)

    data = assert_ok(client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"}))
    session_id = data.get("session_id") or data.get("session_id")
    resp = assert_ok(
        client.post(
            f"/api/chat/{session_id}/message",
            headers=headers,
            json={"text": "我不会做，给我讲", "mode": "DEMO"},
        )
    )
    assert "答案：12" in resp["reply"]

    traces = _session_traces(session_id, "answer_leak")
    assert len(traces) == 1
    assert traces[0].payload["mode"] == "DEMO"
    assert traces[0].payload["strict"] is False
    assert str(session_id) == str(traces[0].session_id)


def test_guide_leak_is_guarded_and_leaves_no_trace(client, monkeypatch):
    """引导模式 guard 拦截：泄漏回复被重生成/兜底，学生面干净 → 不落 trace。"""
    monkeypatch.setattr(settings, "explanation_quality_gate_enabled", False)
    patch_ark(monkeypatch, fake=_LeakTutorArk())
    headers, _sid = _auth(client)

    data = assert_ok(client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"}))
    session_id = data.get("session_id") or data.get("session_id")
    resp = assert_ok(
        client.post(
            f"/api/chat/{session_id}/message",
            headers=headers,
            json={"text": "我写了 x=4", "mode": "GUIDE"},
        )
    )
    # guard 兜底语不含答案
    assert "答案：12" not in resp["reply"]

    assert _session_traces(session_id, "answer_leak") == []


def test_answer_leak_hook_failure_tolerated(client, monkeypatch):
    """落库失败只 warning：主流程照常返回，不 500。"""
    monkeypatch.setattr(settings, "explanation_quality_gate_enabled", False)
    patch_ark(monkeypatch, fake=_LeakTutorArk())

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(qms, "record_answer_leak_trace", _boom)
    headers, _sid = _auth(client)

    data = assert_ok(client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"}))
    session_id = data.get("session_id") or data.get("session_id")
    resp = assert_ok(
        client.post(
            f"/api/chat/{session_id}/message",
            headers=headers,
            json={"text": "不会做，给我讲", "mode": "DEMO"},
        )
    )
    assert "答案：12" in resp["reply"]  # 主链路未被埋点故障拖垮


# ==================================================================== hook：讲解质量
class _QualityGateArk(FakeArk):
    """judge 可控质量分；tutor 讲解不泄漏。"""

    def _classify(self, messages, system):
        if system and "评估专家" in system:
            return "judge"
        return super()._classify(messages, system)

    def _reply(self, kind: str) -> str:
        if kind == "judge":
            overall = 2 if self.count("judge") == 1 else 5
            return json.dumps(
                {
                    "guidance": overall,
                    "correctness": overall,
                    "clarity": overall,
                    "relevance": overall,
                    "warmth": overall,
                    "overall": overall,
                    "suggestion": "保持" if overall == 5 else "需改进",
                },
                ensure_ascii=False,
            )
        if kind == "tutor":
            return "我们反过来想：等号两边先同时减 3，会得到什么？"
        return super()._reply(kind)


def test_demo_gate_records_explanation_quality_trace(client, monkeypatch):
    """DEMO 质量闸门评分完成 → 落 trace（末次评分 overall=5，达标 info）。"""
    monkeypatch.setattr(settings, "explanation_quality_gate_enabled", True)
    monkeypatch.setattr(settings, "explanation_quality_gate_threshold", 3)
    patch_ark(monkeypatch, fake=_QualityGateArk())
    headers, _sid = _auth(client)

    data = assert_ok(client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"}))
    session_id = data.get("session_id") or data.get("session_id")
    resp = assert_ok(
        client.post(
            f"/api/chat/{session_id}/message",
            headers=headers,
            json={"text": "不会做，给我讲", "mode": "DEMO"},
        )
    )
    assert "反过来想" in resp["reply"]

    traces = _session_traces(session_id, "explanation_quality")
    assert len(traces) == 1
    payload = traces[0].payload
    assert payload["overall"] == 5  # 末次（重讲后）评分
    assert set(payload["dimensions"]) == set(qms.DIMENSION_KEYS)
    assert payload["passed"] is True
    assert traces[0].severity == "info"
    assert str(traces[0].session_id) == str(session_id)


def test_quality_gate_hook_failure_tolerated(client, monkeypatch):
    """质量 trace 落库失败 → 闸门与主流程照常。"""
    monkeypatch.setattr(settings, "explanation_quality_gate_enabled", True)
    monkeypatch.setattr(settings, "explanation_quality_gate_threshold", 3)
    patch_ark(monkeypatch, fake=_QualityGateArk())

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(qms, "record_explanation_quality_trace", _boom)
    headers, _sid = _auth(client)

    data = assert_ok(client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"}))
    session_id = data.get("session_id") or data.get("session_id")
    resp = assert_ok(
        client.post(
            f"/api/chat/{session_id}/message",
            headers=headers,
            json={"text": "不会做，给我讲", "mode": "DEMO"},
        )
    )
    assert "反过来想" in resp["reply"]


# ==================================================================== eval_learning_gain 脚本
def _build_learning_gain_fixture(db) -> Dict[str, Any]:
    """构造一组「有信号」的数据：1 学生 2 知识点 3 题 + 复习记录。

    时间线（均在 30 天窗内）：
        q1(kp1) 先错后对（首答错、复测对）；q2(kp1) 只错；
        q3(kp2) 首答即对；q1 另挂 QUESTIONVIEW(role=transfer) 视为迁移题。

    注意：知识点名称带随机后缀（``name`` 无 UNIQUE，但 ``--kp`` 按名称解析
    时会命中同名多行），返回 ``kp1_name`` 供 main() 用例使用。
    """
    now = datetime.now(timezone.utc)
    student_id = make_student(db)
    kp1_name = f"一元一次方程-{_uid()}"
    kp1 = make_kp(db, name=kp1_name)
    kp2 = make_kp(db, name=f"分数约分-{_uid()}")
    q1 = make_question(db, kp_id=kp1, content="解方程 3x-5=7")
    q2 = make_question(db, kp_id=kp1, content="解方程 2x+1=5")
    q3 = make_question(db, kp_id=kp2, content="约分 6/8")

    make_attempt(db, student_id=student_id, question_id=q1, is_correct=False,
                 created_at=now - timedelta(days=20))
    make_attempt(db, student_id=student_id, question_id=q1, is_correct=True,
                 created_at=now - timedelta(days=10))
    make_attempt(db, student_id=student_id, question_id=q2, is_correct=False,
                 created_at=now - timedelta(days=9))
    make_attempt(db, student_id=student_id, question_id=q3, is_correct=True,
                 created_at=now - timedelta(days=5))

    # q1 认定为迁移题（QUESTIONVIEW meta.role=transfer）
    db.add(
        StudentLearningEvent(
            student_id=student_id,
            event_type="QUESTIONVIEW",
            question_id=q1,
            knowledge_point_id=kp1,
            meta={"role": "transfer", "channel": "ai_exercise"},
        )
    )
    db.flush()

    ensure_review_tables(db)
    # 间隔 8 天的复习：记得（rating=3）→ 计入；间隔 3 天：太简单（rating=4）→ 不计入
    make_review_log(db, card_id=uuid.uuid4(), student_id=student_id, rating=3,
                    elapsed_days=8, review_at=now - timedelta(days=2), kp_id=kp1,
                    question_id=q1)
    make_review_log(db, card_id=uuid.uuid4(), student_id=student_id, rating=4,
                    elapsed_days=3, review_at=now - timedelta(days=1), kp_id=kp1)

    return {
        "student_id": student_id,
        "kp1": kp1,
        "kp1_name": kp1_name,
        "kp2": kp2,
        "q1": q1,
        "q2": q2,
        "q3": q3,
    }


def test_eval_learning_gain_on_constructed_data(db):
    """构造数据上各指标数值与样本量正确。"""
    fixture = _build_learning_gain_fixture(db)
    report = eval_learning_gain.compute_learning_gain(
        db,
        student_ids=[fixture["student_id"]],
        since=datetime.now(timezone.utc) - timedelta(days=30),
    )
    metrics = report["metrics"]

    # 首次正确率：3 题首答 1 对
    assert metrics["first_attempt_accuracy"] == {"correct": 1, "total": 3, "rate": pytest.approx(1 / 3)}
    # 复测正确率：q1 的第二次作答 1 对
    assert metrics["retest_accuracy"] == {"correct": 1, "total": 1, "rate": 1.0}
    # Learning Gain：(1 - 1/3) / (1 - 1/3) = 1.0
    assert metrics["learning_gain"]["pre"] == pytest.approx(1 / 3)
    assert metrics["learning_gain"]["post"] == 1.0
    assert metrics["learning_gain"]["gain"] == pytest.approx(1.0)
    # 纠错后正确率：(stu,kp1) 先错后对 → 1/1
    assert metrics["corrected_accuracy"] == {"correct": 1, "total": 1, "rate": 1.0}
    # 迁移题正确率：q1 两次作答 1 对
    assert metrics["transfer_accuracy"] == {"correct": 1, "total": 2, "rate": 0.5}
    # 7 天复习正确率：仅 elapsed_days=8/rating=3 计入 → 1/1
    assert metrics["review_7d_accuracy"] == {"correct": 1, "total": 1, "rate": 1.0}

    samples = report["samples"]
    assert samples["attempts_total"] == 4
    assert samples["attempts_with_question"] == 4
    assert samples["questions"] == 3
    assert samples["students"] == 1
    assert samples["transfer_questions_identified"] >= 1
    assert samples["review_logs_total"] == 2
    assert samples["review_logs_gap"] == 1
    # 数据充足时不应出现「无样本」类 note
    assert all("样本为 0" not in n for n in report["notes"])

    # kp 过滤：只看 kp1 → 只剩 q1/q2 的 3 条 attempt
    kp1_report = eval_learning_gain.compute_learning_gain(
        db,
        kp_ids=[fixture["kp1"]],
        student_ids=[fixture["student_id"]],
        since=datetime.now(timezone.utc) - timedelta(days=30),
    )
    assert kp1_report["samples"]["attempts_with_question"] == 3
    assert kp1_report["metrics"]["first_attempt_accuracy"]["total"] == 2


def test_eval_learning_gain_empty_data_reports_samples_not_fake_numbers(db):
    """空数据（按一个从无作答的学生过滤）：所有率 None、样本量 0、有 note。"""
    quiet = make_student(db)
    report = eval_learning_gain.compute_learning_gain(db, student_ids=[quiet])
    metrics = report["metrics"]
    assert metrics["first_attempt_accuracy"]["rate"] is None
    assert metrics["retest_accuracy"]["rate"] is None
    assert metrics["corrected_accuracy"]["rate"] is None
    assert metrics["transfer_accuracy"]["rate"] is None
    assert metrics["review_7d_accuracy"]["rate"] is None
    assert metrics["learning_gain"]["gain"] is None
    assert metrics["learning_gain"]["note"]
    assert report["samples"]["attempts_total"] == 0
    assert any("样本为 0" in n for n in report["notes"])


def test_eval_learning_gain_missing_review_tables_degrades(db):
    """review_logs 表不存在 → 复习指标 None + note，其余指标不受影响。"""
    fixture = _build_learning_gain_fixture(db)
    drop_review_tables(db)
    try:
        report = eval_learning_gain.compute_learning_gain(
            db,
            student_ids=[fixture["student_id"]],
            since=datetime.now(timezone.utc) - timedelta(days=30),
        )
    finally:
        ensure_review_tables(db)  # 还原，避免影响同会话后续用例

    metrics = report["metrics"]
    assert metrics["review_7d_accuracy"] == {"correct": 0, "total": 0, "rate": None}
    assert any("review_logs" in n for n in report["notes"])
    # attempts 类指标照常
    assert metrics["first_attempt_accuracy"]["total"] == 3
    assert fixture["q1"] is not None


def test_eval_learning_gain_main_writes_report(db, tmp_path, capsys):
    """main() 端到端：解析 --kp 名称 → 只读聚合 → 写 JSON 报告。"""
    fixture = _build_learning_gain_fixture(db)
    db.commit()  # main() 自开会话，需先提交可见

    out = tmp_path / "lg.json"
    code = eval_learning_gain.main([
        "--kp", fixture["kp1_name"],
        "--days", "30",
        "--out", str(out),
    ])
    assert code == 0
    assert out.exists()

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["scope"]["kp_names"] == [fixture["kp1_name"]]
    assert len(report["scope"]["kp_ids"]) == 1
    assert report["metrics"]["first_attempt_accuracy"]["total"] == 2  # 只剩 kp1 的题
    assert report["metrics"]["transfer_accuracy"]["total"] == 2
    assert isinstance(report["notes"], list)

    printed = capsys.readouterr().out
    assert "Learning Gain 评测" in printed
    assert "首次正确率" in printed


def test_eval_learning_gain_main_rejects_bad_student_uuid(capsys):
    """非法 --student UUID → 退出码 2，不写报告。"""
    code = eval_learning_gain.main(["--all", "--student", "not-a-uuid"])
    assert code == 2
    assert "UUID" in capsys.readouterr().err

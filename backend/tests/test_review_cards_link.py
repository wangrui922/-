"""V6.5 → 复习卡流联动：练习结算后自动建档 FSRS 复习卡片。

覆盖：
    * ``practice_service._aggregate_kp_results``：按知识点聚合正确/错误；
    * ``review_service.upsert_cards_from_practice``：建卡幂等、rating 语义；
    * finish_session 端到端：练习结算后 review_cards 出现对应卡片。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text as sa_text

from app.db import SessionLocal, engine
from app.models.content import KnowledgePoint, Question
from app.models.learning import Attempt, LearningTask
from app.models.user import Student, User
from app.services import practice_service, review_service

NOW = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module", autouse=True)
def _fsrs_tables():
    import pathlib

    sql = (
        pathlib.Path(__file__).resolve().parents[1]
        / "migrations"
        / "013_fsrs_cards_logs_sqlite.sql"
    ).read_text(encoding="utf-8")
    with engine.raw_connection() as conn:
        conn.executescript(sql)
    yield


def _make_student_with_kp(db):
    user = User(id=uuid.uuid4(), phone=f"13{uuid.uuid4().hex[:9]}", nickname="联动")
    db.add(user)
    db.flush()
    stu = Student(id=uuid.uuid4(), user_id=user.id, grade=4)
    db.add(stu)
    db.flush()
    kps = []
    for i in range(2):
        kp = KnowledgePoint(
            id=uuid.uuid4(), name=f"联动KP{i}", code=f"LD_KP_{i}", sort=0
        )
        db.add(kp)
        db.flush()
        kps.append(kp)
    return stu, kps


def test_aggregate_kp_results(db, new_phone):
    """正确聚合：kp0 全对、kp1 有错。"""
    stu, kps = _make_student_with_kp(db)

    qs = []
    for i, kp in enumerate(kps):
        for j in range(2):
            q = Question(
                id=uuid.uuid4(), content=f"Q{i}{j}", kp_id=kp.id, subject="数学"
            )
            db.add(q)
            db.flush()
            qs.append((q, kp))

    latest = {}
    for idx, (q, kp) in enumerate(qs):
        att = Attempt(
            id=uuid.uuid4(),
            student_id=stu.id,
            question_id=q.id,
            is_correct=(kp == kps[1] and idx % 2 == 0),  # kp1 第1题错
        )
        db.add(att)
        db.flush()
        latest[q.id] = att

    res = practice_service._aggregate_kp_results(db, latest, [q.id for q, _ in qs])
    assert res[str(kps[0].id)] == {"correct": True, "attempts": 2}
    assert res[str(kps[1].id)] == {"correct": False, "attempts": 2}


def test_upsert_cards_from_practice_idempotent(db, new_phone):
    """由练习聚合建档：幂等（重复调用不重复建卡）、rating 语义正确。"""
    stu, kps = _make_student_with_kp(db)
    results = {
        str(kps[0].id): {"correct": True, "attempts": 2},
        str(kps[1].id): {"correct": False, "attempts": 2},
    }
    with patch("app.services.review_service.datetime") as mdt:
        mdt.now.return_value = NOW
        out1 = review_service.upsert_cards_from_practice(stu.id, results, now=NOW)
    assert out1 == {"created": 2, "skipped": 0, "total": 2}

    with SessionLocal() as s:
        rows = s.execute(
            sa_text(
                "SELECT kp_id, state, due FROM review_cards WHERE student_id = :p1"
            ),
            {"p1": str(stu.id)},
        ).all()
    assert len(rows) == 2
    by_kp = {r[0]: r for r in rows}
    # kp0 答对 → Good(3) → state=1(学习/复习中)、due 在未来
    assert by_kp[str(kps[0].id)][2] > NOW.isoformat()
    # kp1 答错 → Again(1) → due 不晚于 now（近期再出现）
    assert by_kp[str(kps[1].id)][2] <= NOW.isoformat()

    # 幂等：再次调用不重复建
    out2 = review_service.upsert_cards_from_practice(stu.id, results, now=NOW)
    assert out2 == {"created": 0, "skipped": 2, "total": 2}


def test_finish_session_creates_cards(db, client, new_phone):
    """端到端：练习结算后 review_cards 出现对应卡片（走真实接口）。"""
    from tests.conftest import login_with_code  # noqa: F401

    phone = new_phone()
    data = login_with_code(client, phone)
    token = data["access"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()["data"]
    sid = uuid.UUID(me["studentId"])

    # 用一个真实可归因的知识点 + 题目
    s = SessionLocal()
    try:
        kp = KnowledgePoint(
            id=uuid.uuid4(), name="联动E2E", code="LD_E2E_KP", sort=0
        )
        s.add(kp)
        s.flush()
        q = Question(id=uuid.uuid4(), content="2+2=?", kp_id=kp.id, subject="数学")
        s.add(q)
        s.commit()
        qid = q.id
    finally:
        s.close()

    # 练习会话：抽题 → 作答 → 结算
    r = client.post(
        "/api/practice/session",
        headers=headers,
        json={"task_type": "daily", "question_ids": [str(qid)]},
    )
    assert r.status_code == 200, r.text
    session_id = r.json()["data"]["session_id"]

    r = client.post(
        "/api/practice/answer",
        headers=headers,
        json={"session_id": session_id, "question_id": str(qid), "selected_answer": "A"},
    )
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/practice/finish", headers=headers, json={"session_id": session_id}
    )
    assert r.status_code == 200, r.text

    # 结算后应出现该知识点的复习卡
    with SessionLocal() as chk:
        row = chk.execute(
            sa_text(
                "SELECT state, due FROM review_cards WHERE student_id = :p1 AND kp_id = :p2"
            ),
            {"p1": str(sid), "p2": str(kp.id)},
        ).first()
    assert row is not None, "练习结算后应自动建档复习卡"
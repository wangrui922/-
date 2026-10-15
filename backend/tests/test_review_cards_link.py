"""V6.5 → 复习卡流联动：练习结算后自动建档 FSRS 复习卡片。

覆盖：
    * ``practice_service._aggregate_kp_results``：按知识点聚合正确/错误；
    * ``review_service.upsert_cards_from_practice``：建卡幂等、rating 语义；
    * finish_session 端到端：练习结算后 review_cards 出现对应卡片。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text as sa_text

from app.db import SessionLocal, engine
from app.models.content import Answer, KnowledgePoint, Question
from app.models.learning import Attempt, LearningTask
from app.models.user import Student, User
from app.services import practice_service, review_service
from app.services.fsrs_scheduler import STATE_LEARNING, STATE_RELEARNING

NOW = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)


def _parse_due(raw: Any) -> datetime:
    """把 SQLite RAW SQL 取出的 ``due`` 串解析成 datetime。

    SQLite 存的是 **空格分隔**（``2026-10-02 08:00:00+00:00``），PG 侧是 ``T``
    分隔；两种都能被 ``datetime.fromisoformat`` 吃掉，所以不用再分方言。
    """
    text = str(raw).strip().replace(" ", "T", 1)
    return datetime.fromisoformat(text)


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
    """造一个学生 + 2 个知识点，返回 ``(student, [kp0, kp1])``。

    ⚠️ ``knowledge_points.code`` 有 UNIQUE 约束，且本会话把 ``db`` 的写入
    **提交**（见 conftest 的 ``db`` fixture：用例正常跑完即 commit）。所以这里
    的 code 必须每次带随机后缀，否则同文件内第二个用例调本函数就会撞
    ``UNIQUE constraint failed: knowledge_points.code``。
    """
    user = User(id=uuid.uuid4(), phone=f"13{uuid.uuid4().hex[:9]}", nickname="联动")
    db.add(user)
    db.flush()
    stu = Student(id=uuid.uuid4(), user_id=user.id, grade=4)
    db.add(stu)
    db.flush()
    suffix = uuid.uuid4().hex[:8]
    kps = []
    for i in range(2):
        kp = KnowledgePoint(
            id=uuid.uuid4(), name=f"联动KP{i}", code=f"LD_KP_{i}_{suffix}", sort=0
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
            # kp0 两题全对 → correct=True；kp1 两题全错 → correct=False。
            # （原写 ``kp == kps[1] and idx % 2 == 0`` 把语义写反了：kp0 全错、
            #   kp1 只对一题，与本文件的用例意图和断言都不符。）
            is_correct=(kp is kps[0]),
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
                "SELECT kp_id, state, due, lapses FROM review_cards WHERE student_id = :p1"
            ),
            {"p1": str(stu.id)},
        ).all()
    assert len(rows) == 2
    by_kp = {r[0]: r for r in rows}

    # ⚠️ SQLite 取出的 due 是 **空格分隔** 的串（``2026-10-02 08:00:00+00:00``），
    # 而 ``NOW.isoformat()`` 是 **T 分隔**。两者直接比字符串大小毫无意义
    # （原写法正是踩了这个坑）。统一解析成 datetime 再比。
    due_good = _parse_due(by_kp[str(kps[0].id)][2])
    due_again = _parse_due(by_kp[str(kps[1].id)][2])

    # 首次复习一律 +1 天（SM-2 核心：首次间隔为 1 天，与 rating 无关）
    assert due_good > NOW
    assert due_again > NOW

    # rating 语义真正体现在 state / lapses 上，而不是 due：
    # kp0 答对 → Good(3) → 学习态 STATE_LEARNING(1)，无遗忘
    assert by_kp[str(kps[0].id)][1] == STATE_LEARNING
    assert by_kp[str(kps[0].id)][3] == 0
    # kp1 答错 → Again(1) → 遗忘重置：Relearning 态 STATE_RELEARNING(3) + lapses=1
    assert by_kp[str(kps[1].id)][1] == STATE_RELEARNING
    assert by_kp[str(kps[1].id)][3] == 1

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

    # 用一个真实可归因的知识点 + 题目。
    #
    # 这里必须把题目造「完整」，否则抽题接口会直接 404：
    # ``practice_service._candidate_rows`` 先 JOIN ``answers`` 做 SQL 粗筛
    # （无 answers 行 → 直接落空），再要求 status='active'、题型在
    # ``judge_engine.SUPPORTED_TYPES`` 内、标准答案非空，最后逐行过
    # ``judge_engine.is_l1_safe()``。早期版本只填 content/kp_id/subject，
    # 既没 answers 行也没题型，(type_, options, correct_answer) 三件套不齐，
    # is_l1_safe 判否 → 三档 relaxation 全空 → 404。
    s = SessionLocal()
    try:
        kp = KnowledgePoint(
            id=uuid.uuid4(),
            name="联动E2E",
            code=f"LD_E2E_KP_{uuid.uuid4().hex[:8]}",
            sort=0,
        )
        s.add(kp)
        s.flush()
        q = Question(
            id=uuid.uuid4(),
            content="2+2=?",
            kp_id=kp.id,
            subject="数学",
            grade=3,
            difficulty=3,
            type_="选择",
            options={"A": "4", "B": "3", "C": "2", "D": "1"},
            kp_verified=True,
            status="active",
        )
        s.add(q)
        s.flush()
        s.add(Answer(question_id=q.id, correct_answer="A", analysis="凑十法"))
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
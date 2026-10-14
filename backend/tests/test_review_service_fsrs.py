"""方向三 FSRS 落地 · review_service 类方法链路契约测试。

前提：
    * conftest session 级 ``_create_schema`` 已 create_all 全部 ORM 表
      （questions / knowledge_points 等）；
    * review_cards / review_logs 是 raw SQL 迁移表（013），不在 ORM 内，
      本文件用 ``fixture`` 在测试前幂等 executescript 补建。

覆盖（vs 既有受测 stub 期间的链路空白）：
    * create_card_for_question / create_card_for_knowledge_point：落库字段、
      幂等；
    * submit_review：沿真实 FSRS 推进、写 review_logs（before/after 字段）；
    * get_due_cards：到期过滤 + content 组装 + retrievability；
    * 遗忘重置：Again → reps=0 / lapses+1 / Relearning；
    * get_review_stats：统计口径。
"""

from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text as sa_text

from app.db import SessionLocal, engine
from app.models.content import KnowledgePoint, Question
from app.models.student_mastery import StudentMastery
from app.models.user import Student, User
from app.services.fsrs_scheduler import (
    STATE_LEARNING,
    STATE_RELEARNING,
    STATE_REVIEW,
)
from app.services.review_scheduler import MAX_EASE_FACTOR, MIN_EASE_FACTOR
from app.services.review_service import ReviewService

NOW = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)
SQL_013 = None


@pytest.fixture(scope="session", autouse=True)
def _create_fsrs_tables(_create_schema):
    """幂等补建 013 迁移的 review_cards / review_logs 表。"""
    global SQL_013
    if SQL_013 is None:
        import pathlib

        SQL_013 = (
            pathlib.Path(__file__).resolve().parents[1]
            / "migrations"
            / "013_fsrs_cards_logs_sqlite.sql"
        )
        SQL_013 = SQL_013.read_text(encoding="utf-8")
    with engine.raw_connection() as conn:
        conn.executescript(SQL_013)
    yield


def _make_student(s, phone: str):
    u = User(
        id=uuid.uuid4(),
        phone=phone,
        role="student",
        status="active",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    st = Student(
        id=uuid.uuid4(),
        user_id=u.id,
        grade=4,
        stage="primary",
        daily_target=5,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    s.add_all([u, st])
    s.flush()
    return st


def _make_kp(s, code: str) -> uuid.UUID:
    kp = KnowledgePoint(id=uuid.uuid4(), name=code, code=code, sort=0)
    s.add(kp)
    s.flush()
    return kp.id


def _make_question(s, kp_id: uuid.UUID | None = None) -> uuid.UUID:
    q = Question(
        id=uuid.uuid4(),
        content="3 + 5 = ?",
        kp_id=kp_id,
        type_="choice",
        subject="math",
    )
    s.add(q)
    s.flush()
    return q.id


_PHONE_SEQ = itertools.count(10000001)


def _student_phone() -> str:
    return "137" + str(next(_PHONE_SEQ)).zfill(8)


# ================================================================ 卡片创建
class TestCreateCard:
    def test_create_question_card_persists_scheduler_output(self):
        s = SessionLocal()
        try:
            st = _make_student(s, _student_phone())
            qid = _make_question(s)
            s.commit()

            svc = ReviewService()
            result = svc.create_card_for_question(
                str(st.id), str(qid), initial_rating=3, now=NOW
            )

            assert result["created"] is True
            assert result["state"] == STATE_LEARNING
            assert result["stability"] == 1.0
            assert result["difficulty"] == 2.5
            assert result["due"] == (NOW + timedelta(days=1)).isoformat()

            row = s.execute(
                sa_text(
                    "SELECT student_id, question_id, kp_id, stability, difficulty, reps, state "
                    "FROM review_cards WHERE id = :p"
                ),
                {"p": result["id"]},
            ).fetchone()
            assert row is not None
            assert row[0] == str(st.id)
            assert row[1] == str(qid)
            assert row[2] is None
            assert row[3] == 1.0
            assert row[4] == 2.5
            assert row[5] == 1
            assert row[6] == STATE_LEARNING
        finally:
            s.rollback()
            s.close()

    def test_create_question_card_idempotent(self):
        s = SessionLocal()
        try:
            st = _make_student(s, _student_phone())
            qid = _make_question(s)
            s.commit()
            svc = ReviewService()
            r1 = svc.create_card_for_question(str(st.id), str(qid), now=NOW)
            r2 = svc.create_card_for_question(str(st.id), str(qid), now=NOW)
            assert r1["created"] is True
            assert r2["created"] is False and r2["reason"] == "already_exists"
            assert r2["id"] == r1["id"]
        finally:
            s.rollback()
            s.close()

    def test_create_kp_card_branch(self):
        s = SessionLocal()
        try:
            st = _make_student(s, _student_phone())
            kp_id = _make_kp(s, f"FSRS_KP_{uuid.uuid4().hex[:6]}")
            s.commit()
            svc = ReviewService()
            result = svc.create_card_for_knowledge_point(
                str(st.id), str(kp_id), initial_rating=4, now=NOW
            )
            assert result["created"] is True
            assert result["difficulty"] == 2.6  # Easy → EF 2.6
            row = s.execute(
                sa_text("SELECT kp_id, question_id FROM review_cards WHERE id = :p"),
                {"p": result["id"]},
            ).fetchone()
            assert row[0] == str(kp_id)
            assert row[1] is None
        finally:
            s.rollback()
            s.close()


# ================================================================ 到期队列
class TestDueCards:
    def test_due_filtering_and_content_assembly(self):
        s = SessionLocal()
        try:
            st = _make_student(s, _student_phone())
            kp_id = _make_kp(s, f"FSRS_KP_{uuid.uuid4().hex[:6]}")
            qid = _make_question(s, kp_id=kp_id)
            s.commit()
            svc = ReviewService()
            rst = svc.create_card_for_question(
                str(st.id), str(qid), initial_rating=3, now=NOW
            )
            # now 当天：新卡 due=+1d，不入队
            due_today = svc.get_due_cards(str(st.id), now=NOW)
            assert all(c["id"] != rst["id"] for c in due_today)
            # +2 天：到期入队，content 组装出题目信息与 retrievability
            due_later = svc.get_due_cards(str(st.id), now=NOW + timedelta(days=2))
            assert len(due_later) == 1
            card = due_later[0]
            assert card["id"] == rst["id"]
            assert card["content"]["type"] == "question"
            assert card["content"]["question_content"] == "3 + 5 = ?"
            assert card["stability"] == 1.0
            assert 0.0 < card["retrievability"] < 1.0
        finally:
            s.rollback()
            s.close()

    def test_kp_card_content_branch(self):
        s = SessionLocal()
        try:
            st = _make_student(s, _student_phone())
            kp_code = f"FSRS_KP_{uuid.uuid4().hex[:6]}"
            kp_id = _make_kp(s, kp_code)
            s.commit()
            svc = ReviewService()
            svc.create_card_for_knowledge_point(str(st.id), str(kp_id), now=NOW)
            due = svc.get_due_cards(str(st.id), now=NOW + timedelta(days=2))
            assert len(due) == 1
            assert due[0]["content"]["type"] == "knowledge_point"
            assert due[0]["content"]["kp_code"] == kp_code
        finally:
            s.rollback()
            s.close()


# ================================================================ 提交复习
class TestSubmitReview:
    def test_submit_review_updates_card_and_writes_log(self):
        s = SessionLocal()
        try:
            st = _make_student(s, _student_phone())
            qid = _make_question(s)
            s.commit()
            svc = ReviewService()
            created = svc.create_card_for_question(str(st.id), str(qid), now=NOW)

            next_time = NOW + timedelta(days=1)
            updated = svc.submit_review(created["id"], rating=3, now=next_time)

            assert updated["state"] == STATE_REVIEW
            assert updated["reps"] == 2
            assert updated["scheduled_days"] == 6
            assert updated["stability"] == 6.0
            assert updated["difficulty"] == 2.5
            assert updated["due"] == (next_time + timedelta(days=6)).isoformat()

            # review_logs 落库：before/after 字段齐全
            log = s.execute(
                sa_text(
                    "SELECT rating, state, stability_before, stability_after, "
                    "difficulty_before, difficulty_after FROM review_logs WHERE card_id = :p"
                ),
                {"p": created["id"]},
            ).fetchone()
            assert log is not None
            assert log[0] == 3  # rating 原样存
            assert log[1] == STATE_REVIEW
            assert log[2] == 1.0  # stability_before
            assert log[3] == 6.0  # stability_after
            assert log[4] == 2.5  # difficulty_before
            assert log[5] == 2.5  # difficulty_after
        finally:
            s.rollback()
            s.close()

    def test_submit_review_lapse_resets(self):
        s = SessionLocal()
        try:
            st = _make_student(s, _student_phone())
            qid = _make_question(s)
            s.commit()
            svc = ReviewService()
            created = svc.create_card_for_question(str(st.id), str(qid), now=NOW)
            updated = svc.submit_review(
                created["id"], rating=1, now=NOW + timedelta(days=1)
            )

            assert updated["state"] == STATE_RELEARNING
            assert updated["reps"] == 0
            assert updated["lapses"] == 1
            assert updated["scheduled_days"] == 1
            assert updated["difficulty"] < 2.5  # EF 下调
        finally:
            s.rollback()
            s.close()

    def test_submit_review_card_not_found(self):
        svc = ReviewService()
        with pytest.raises(ValueError):
            svc.submit_review(str(uuid.uuid4()), rating=3, now=NOW)


# ================================================================ 统计
class TestStats:
    def test_get_review_stats_aggregates(self):
        s = SessionLocal()
        try:
            st = _make_student(s, _student_phone())
            qid = _make_question(s)
            s.commit()
            svc = ReviewService()
            created = svc.create_card_for_question(str(st.id), str(qid), now=NOW)
            svc.submit_review(created["id"], rating=3, now=NOW + timedelta(days=1))

            stats = svc.get_review_stats(str(st.id), days=30)
            assert stats["total_cards"] == 1
            assert stats["recent_reviews"] == 1
            assert stats["average_rating"] == 3.0
            assert stats["state_distribution"] == {STATE_REVIEW: 1}
            assert stats["period_days"] == 30
        finally:
            s.rollback()
            s.close()


# ================================================================ mastery 联动难度
class TestAdjustDifficultyByMastery:
    """difficulty 列 = SM-2 EF（高=容易记）：mastery 高 → EF 上调，
    mastery 低 → EF 下调；钳制区间 [MIN_EASE_FACTOR, MAX_EASE_FACTOR]。"""

    def _mk_card(self, s, mastery_row: bool = True):
        st = _make_student(s, _student_phone())
        kp_id = _make_kp(s, f"FSRS_MST_{uuid.uuid4().hex[:6]}")
        s.commit()
        svc = ReviewService()
        created = svc.create_card_for_knowledge_point(
            str(st.id), str(kp_id), initial_rating=3, now=NOW
        )
        return svc, str(st.id), str(kp_id), created["id"]

    def test_mastery_high_raises_ef(self):
        s = SessionLocal()
        try:
            svc, sid, kp_id, _ = self._mk_card(s)
            # mastery 0.6 -> +0.2，EF 2.5 -> 2.7（方向：掌握度高 = 更容易记）
            res = svc.adjust_difficulty_by_mastery(sid, kp_id, 0.6)
            assert res["adjusted"] is True
            assert res["old_difficulty"] == 2.5
            assert round(res["adjustment"], 6) == 0.2
            assert round(res["new_difficulty"], 6) == 2.7
            row = s.execute(
                sa_text("SELECT difficulty FROM review_cards WHERE id = :p"),
                {"p": res["card_id"]},
            ).fetchone()
            assert round(row[0], 6) == 2.7
        finally:
            s.rollback()
            s.close()

    def test_mastery_low_lowers_ef(self):
        s = SessionLocal()
        try:
            svc, sid, kp_id, _ = self._mk_card(s)
            # mastery 0.0 -> -1.0，EF 2.5 -> 1.5（掌握度低 = 复习更密）
            res = svc.adjust_difficulty_by_mastery(sid, kp_id, 0.0)
            assert res["adjusted"] is True
            assert res["adjustment"] == -1.0
            assert res["new_difficulty"] == 1.5
        finally:
            s.rollback()
            s.close()

    def test_ef_floor_clamped(self):
        s = SessionLocal()
        try:
            svc, sid, kp_id, card_id = self._mk_card(s)
            s.execute(
                sa_text("UPDATE review_cards SET difficulty = 1.4 WHERE id = :p"),
                {"p": card_id},
            )
            s.commit()
            res = svc.adjust_difficulty_by_mastery(sid, kp_id, 0.0)
            assert res["new_difficulty"] == MIN_EASE_FACTOR == 1.3
        finally:
            s.rollback()
            s.close()

    def test_ef_cap_clamped(self):
        s = SessionLocal()
        try:
            svc, sid, kp_id, card_id = self._mk_card(s)
            s.execute(
                sa_text("UPDATE review_cards SET difficulty = 2.9 WHERE id = :p"),
                {"p": card_id},
            )
            s.commit()
            res = svc.adjust_difficulty_by_mastery(sid, kp_id, 1.0)
            # 2.9 + 1.0 = 3.9 → 钳到上限，防 mastery 全量同步反复叠加漂移
            assert res["new_difficulty"] == MAX_EASE_FACTOR == 3.0
        finally:
            s.rollback()
            s.close()

    def test_card_not_found(self):
        svc = ReviewService()
        res = svc.adjust_difficulty_by_mastery(
            str(uuid.uuid4()), str(uuid.uuid4()), 0.8
        )
        assert res == {"adjusted": False, "reason": "card_not_found"}

    def test_sync_cards_from_mastery_link(self):
        """链路衔接：sync 创建卡片后，再次同步走 adjust 更新难度且受上限钳制。"""
        s = SessionLocal()
        try:
            st = _make_student(s, _student_phone())
            kp_id = _make_kp(s, f"FSRS_MST_{uuid.uuid4().hex[:6]}")
            s.add(
                StudentMastery(
                    id=uuid.uuid4(),
                    student_id=st.id,
                    knowledge_point_id=kp_id,
                    mastery_score=0.7,
                    mastery_level=2,
                    attempts=10,
                    correct_count=8,
                )
            )
            s.commit()
            svc = ReviewService()
            sid = str(st.id)

            r1 = svc.sync_cards_from_mastery(sid, now=NOW)
            assert r1["created"] == 1 and r1["updated"] == 0

            # 第二次同步进入更新分支：mastery 0.7 -> +0.4，EF 2.5 -> 2.9
            r2 = svc.sync_cards_from_mastery(sid, now=NOW)
            assert r2["created"] == 0 and r2["updated"] == 1
            row = s.execute(
                sa_text(
                    "SELECT difficulty FROM review_cards "
                    "WHERE student_id = :p AND kp_id = :q"
                ),
                {"p": sid, "q": str(kp_id)},
            ).fetchone()
            assert round(row[0], 6) == 2.9

            # 连续同步不再无限漂移：2.9 + 0.4 = 3.3 → 钳在 3.0
            r3 = svc.sync_cards_from_mastery(sid, now=NOW)
            assert r3["updated"] == 1
            row = s.execute(
                sa_text(
                    "SELECT difficulty FROM review_cards "
                    "WHERE student_id = :p AND kp_id = :q"
                ),
                {"p": sid, "q": str(kp_id)},
            ).fetchone()
            assert row[0] == MAX_EASE_FACTOR == 3.0
        finally:
            s.rollback()
            s.close()

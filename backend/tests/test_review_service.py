"""#65 复习排程服务层单测（真实 ORM + 内存 SQLite，依赖 conftest 的 create_all）。

覆盖：
    * record_review 新建/更新行 + 落库 due_at；
    * record_review_from_result 用 对错+verdict 收敛 quality；
    * 答错(lapse) 重置 repetitions + 间隔回退 + EF 下调；
    * list_due_reviews 只挑「现在该复习」的（到期过滤）；
    * seed_initial 幂等（重复 seed 不建重复行）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from app.db import SessionLocal
from app.models.content import KnowledgePoint
from app.models.learning import ReviewSchedule
from app.models.user import Student, User
from app.services import review_service as rs


def _make_student(s: SessionLocal, phone: str):
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


def _make_kp(s: SessionLocal, code: str) -> uuid.UUID:
    kp = KnowledgePoint(id=uuid.uuid4(), name=code, code=code, sort=0)
    s.add(kp)
    s.flush()
    return kp.id


def test_record_review_creates_row_and_sets_due():
    s = SessionLocal()
    try:
        st = _make_student(s, "13900000001")
        kp_id = _make_kp(s, "CZ_RS_A")
        now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
        state = rs.record_review(s, st.id, kp_id, quality=5, now=now)
        s.commit()
        # 算法结果正确
        assert state.repetitions == 1
        assert state.interval_days == 1
        assert state.due_at == now + timedelta(days=1)
        # 落库正确
        row = s.execute(
            select(ReviewSchedule).where(
                ReviewSchedule.student_id == st.id,
                ReviewSchedule.knowledge_point_id == kp_id,
            )
        ).scalar_one()
        assert row is not None
        assert abs(row.ease_factor - 2.6) < 1e-6
        assert row.repetitions == 1
        # get_state 能读回
        got = rs.get_state(s, st.id, kp_id)
        assert got.repetitions == 1
    finally:
        s.rollback()
        s.close()


def test_record_review_from_result_maps_correct_to_quality5():
    s = SessionLocal()
    try:
        st = _make_student(s, "13900000002")
        kp_id = _make_kp(s, "CZ_RS_B")
        now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
        state = rs.record_review_from_result(s, st.id, kp_id, correct=True, verdict="correct", now=now)
        s.commit()
        assert state.repetitions == 1
        assert state.interval_days == 1  # quality5 → 首次间隔 1
    finally:
        s.rollback()
        s.close()


def test_lapse_resets_repetitions_and_ef():
    s = SessionLocal()
    try:
        st = _make_student(s, "13900000003")
        kp_id = _make_kp(s, "CZ_RS_C")
        now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
        # 连续两次记住：rep=2, interval=6, ef=2.6
        rs.record_review(s, st.id, kp_id, quality=5, now=now)
        rs.record_review(s, st.id, kp_id, quality=5, now=now)
        # 第三次答错（quality1）→ lapse
        state = rs.record_review(s, st.id, kp_id, quality=1, now=now)
        s.commit()
        assert state.repetitions == 0
        assert state.interval_days == 1
        assert state.interval_days == 1
        assert state.ease_factor < 2.6  # EF 下调
    finally:
        s.rollback()
        s.close()


def test_list_due_reviews_filters_by_due_at():
    s = SessionLocal()
    try:
        st = _make_student(s, "13900000004")
        kp_a = _make_kp(s, "CZ_RS_D")  # 会被推到未来到期
        kp_b = _make_kp(s, "CZ_RS_E")  # 保持初始（立即到期）
        kp_c = _make_kp(s, "CZ_RS_F")  # 保持初始（立即到期）
        now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
        # 先把三题都初始化成「立即到期」的排程行
        rs.seed_initial(s, st.id, [kp_a, kp_b, kp_c])
        # A 记一次 quality5 → due_at = now+1天（未来相对 now-2天）
        rs.record_review(s, st.id, kp_a, quality=5, now=now)
        s.commit()
        # 在「now-2天」这个时刻看：A 还没到期，B/C 立即到期
        earlier = now - timedelta(days=2)
        due = rs.list_due_reviews(s, st.id, now=earlier)
        due_kps = {k for (k, _st) in due}
        assert kp_a not in due_kps
        assert kp_b in due_kps and kp_c in due_kps
    finally:
        s.rollback()
        s.close()


def test_seed_initial_idempotent():
    s = SessionLocal()
    try:
        st = _make_student(s, "13900000005")
        kp_ids = [_make_kp(s, f"CZ_RS_G{i}") for i in range(3)]
        n1 = rs.seed_initial(s, st.id, kp_ids)
        n2 = rs.seed_initial(s, st.id, kp_ids)  # 重复 seed
        s.commit()
        assert n1 == 3
        assert n2 == 0  # 幂等
        rows = s.execute(
            select(ReviewSchedule).where(ReviewSchedule.student_id == st.id)
        ).scalars().all()
        assert len(rows) == 3
        # 初始行 due_at=None → 立即到期
        for r in rows:
            assert r.due_at is None
            assert r.repetitions == 0
    finally:
        s.rollback()
        s.close()

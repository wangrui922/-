"""C02 Phase 2 · 六维分维批量重算落库测试。

护栏重点：
    1. **写入字段与引擎输出逐列一致**——防 Phase 1 那种「把未降采样变量写进
       procedure/retention」的回归（recompute 必须把引擎的 D1~D5 准确落进对应列）；
    2. **样本不足降级**：事件数 < ``min_samples`` 时该维为 None（不编造 0）；
    3. **D3 独立完成**只统计 independent 标记事件；
    4. **D5 留存**从 FSRS review_logs 聚合（rating>=2 记召回，rating=1 Again 记未召回）；
    5. **has_table 守卫**：FSRS 表不存在时优雅跳过（retention 恒 None，不抛错）；
    6. **D0 不被碰**：recompute 严格只写 5 分维列，不动增量维护的 ``mastery``。
"""

from __future__ import annotations

import types
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import SessionLocal  # noqa: F401 - 确认主引擎可导入（守卫用）
from app.models.base import Base
from app.models.content import KnowledgePoint
from app.models.dna import StudentKnowledgeMastery, StudentLearningEvent
from app.models.system_config import SystemConfig  # noqa: F401 - 保证建表含 system_config
from app.models.user import Student, User
from app.services.student_dna_service import (
    recompute_student_six_dim_mastery,
    record_learning_event,
)


def _make_iso_db() -> tuple:
    """造独立内存库（与主测试会话隔离，计数确定），返回 (db, engine)。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    db = maker()
    return db, engine


def _read_session(engine) -> "sessionmaker":
    """绑定同一引擎的新会话（绕开 expire_on_commit=False 的身份映射假象）。"""
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _add_student(db, *, grade: int = 5) -> uuid.UUID:
    user = User(id=uuid.uuid4(), phone=f"c02-test-{uuid.uuid4()}", role="student")
    student = Student(id=uuid.uuid4(), user_id=user.id, grade=grade)
    db.add(user)
    db.add(student)
    db.commit()
    return student.id


def _add_kp(db, *, code: str = "KP_C02") -> uuid.UUID:
    kp = KnowledgePoint(id=uuid.uuid4(), code=code, name=f"知识点{code}", sort=0)
    db.add(kp)
    db.commit()
    return kp.id


def _emit(db, *, student_id, kp_id, event_type, meta=None) -> None:
    """走真实埋点落库（record_learning_event 只 flush，这里显式 commit 模拟请求末提交）。"""
    record_learning_event(
        db,
        student_id=student_id,
        event_type=event_type,
        knowledge_point_id=kp_id,
        meta=meta or {},
    )
    db.commit()


def _get_row(read_db, student_id, kp_id) -> Optional[StudentKnowledgeMastery]:
    return (
        read_db.execute(
            select(StudentKnowledgeMastery).where(
                StudentKnowledgeMastery.student_id == student_id,
                StudentKnowledgeMastery.knowledge_point_id == kp_id,
            )
        )
        .scalars()
        .one_or_none()
    )


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ================================================================ 1. 逐列一致性守卫
def test_recompute_writes_engine_output_column_by_column() -> None:
    """recompute 落库的 5 分维必须 == 引擎对相同输入算出的 D1~D5（逐列）。"""
    from app.engines.mastery_sixdim import six_dim_mastery_for_student

    db, engine = _make_iso_db()
    sid = _add_student(db)
    kp1 = _add_kp(db, code="KP_A")
    kp2 = _add_kp(db, code="KP_B")

    # kp1：4 次全对（其中 2 次独立），无错因
    for _ in range(2):
        _emit(db, student_id=sid, kp_id=kp1, event_type="CORRECT", meta={"independent": True})
    for _ in range(2):
        _emit(db, student_id=sid, kp_id=kp1, event_type="CORRECT")
    # kp2：3 次错（无错因，当前走 slip），1 次对
    for _ in range(3):
        _emit(db, student_id=sid, kp_id=kp2, event_type="ERROR")
    _emit(db, student_id=sid, kp_id=kp2, event_type="CORRECT")

    updated = recompute_student_six_dim_mastery(db, student_id=sid, now=NOW)
    assert updated == 2

    # 直接喂引擎，拿权威 D1~D5
    events = []
    for ev in (
        db.execute(select(StudentLearningEvent).where(StudentLearningEvent.student_id == sid))
        .scalars()
        .all()
    ):
        m = ev.meta or {}
        events.append(
            types.SimpleNamespace(
                kp_id=str(ev.knowledge_point_id),
                is_correct=(ev.event_type == "CORRECT"),
                occurred_at=ev.created_at,
                weight=1.0,
                error_type=m.get("error_type"),
                independent=bool(m.get("independent", False)),
                transfer=bool(m.get("transfer", False)),
            )
        )
    expected = six_dim_mastery_for_student(events, now=NOW)

    db.commit()
    read_db = _read_session(engine)
    for kp_id in (str(kp1), str(kp2)):
        row = _get_row(read_db, sid, uuid.UUID(kp_id))
        exp = expected[kp_id]
        assert row is not None
        # 逐列比对：recompute 写的必须 == 引擎算的（含 None 降级）
        assert row.concept_mastery == exp.concept, f"{kp_id} concept mismatch"
        assert row.procedure_mastery == exp.procedure, f"{kp_id} procedure mismatch"
        assert row.independent_mastery == exp.independent, f"{kp_id} independent mismatch"
        assert row.transfer_mastery == exp.transfer, f"{kp_id} transfer mismatch"
        assert row.retention_mastery == exp.retention, f"{kp_id} retention mismatch"


# ================================================================ 2. 样本不足降级
def test_low_sample_dims_null() -> None:
    """某知识点只 1 次作答 → D1/D2/D3 全部样本不足，落库为 None（不编造 0）。"""
    db, engine = _make_iso_db()
    sid = _add_student(db)
    kp = _add_kp(db)
    _emit(db, student_id=sid, kp_id=kp, event_type="CORRECT", meta={"independent": True})

    recompute_student_six_dim_mastery(db, student_id=sid, now=NOW)

    db.commit()
    read_db = _read_session(engine)
    row = _get_row(read_db, sid, kp)
    assert row is not None
    assert row.concept_mastery is None
    assert row.procedure_mastery is None
    assert row.independent_mastery is None
    assert row.transfer_mastery is None
    assert row.retention_mastery is None


# ================================================================ 3. D3 独立完成
def test_independent_dim_only_counts_flagged() -> None:
    """D3 仅统计 independent 标记事件：全是「非独立正确」→ D3 为 None。"""
    db, engine = _make_iso_db()
    sid = _add_student(db)
    kp = _add_kp(db)
    # 4 次全对但都「非独立」（看过提示/答案），达不到 D3 的独立样本
    for _ in range(4):
        _emit(db, student_id=sid, kp_id=kp, event_type="CORRECT", meta={"independent": False})

    recompute_student_six_dim_mastery(db, student_id=sid, now=NOW)

    db.commit()
    read_db = _read_session(engine)
    row = _get_row(read_db, sid, kp)
    assert row is not None
    assert row.independent_mastery is None
    # D1/D2 仍由全部正确事件撑起（>= min_samples）
    assert row.concept_mastery is not None
    assert row.procedure_mastery is not None


# ================================================================ 4. D5 留存（FSRS）
def _create_fsrs_tables(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS review_cards (
                    id TEXT PRIMARY KEY, student_id TEXT, question_id TEXT,
                    kp_id TEXT, due TEXT, state INTEGER, created_at TEXT, updated_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS review_logs (
                    id TEXT PRIMARY KEY, card_id TEXT, student_id TEXT,
                    rating INTEGER, review_at TEXT
                )
                """
            )
        )


def _seed_review(db, *, sid, kp_id, ratings) -> None:
    card_id = str(uuid.uuid4())
    qid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO review_cards (id, student_id, question_id, kp_id, due, state, created_at, updated_at) "
            "VALUES (:id, :sid, :qid, :kpid, :due, 1, :ts, :ts)"
        ),
        {"id": card_id, "sid": str(sid), "qid": qid, "kpid": str(kp_id), "due": "2099-01-01T00:00:00+00:00", "ts": NOW.isoformat()},
    )
    for i, rating in enumerate(ratings):
        db.execute(
            text(
                "INSERT INTO review_logs (id, card_id, student_id, rating, review_at) "
                "VALUES (:id, :cid, :sid, :r, :ts)"
            ),
            {"id": str(uuid.uuid4()), "cid": card_id, "sid": str(sid), "r": rating, "ts": NOW.isoformat()},
        )
    db.commit()


def test_retention_from_fsrs_review_logs() -> None:
    """D5：FSRS 复习日志 rating>=2 记召回、rating=1 记未召回，聚合进 retention。"""
    db, engine = _make_iso_db()
    _create_fsrs_tables(engine)
    sid = _add_student(db)
    kp = _add_kp(db)
    # 该知识点也需有练习事件，否则 recompute 不会建行；给 1 次正确触发建行
    _emit(db, student_id=sid, kp_id=kp, event_type="CORRECT")
    # 复习：2 次召回(rating3/4) + 1 次未召回(rating1) → 召回率 2/3，>= min_samples
    _seed_review(db, sid=sid, kp_id=kp, ratings=[3, 4, 1])

    recompute_student_six_dim_mastery(db, student_id=sid, now=NOW)

    db.commit()
    read_db = _read_session(engine)
    row = _get_row(read_db, sid, kp)
    assert row is not None
    assert row.retention_mastery is not None
    # 召回率 2/3 ≈ 0.6667，经贝叶斯平滑会略被先验拽动，落在 (0.4, 0.95) 区间
    assert 0.4 < row.retention_mastery < 0.95


# ================================================================ 5. has_table 守卫
def test_missing_fsrs_tables_graceful_skip() -> None:
    """默认内存库无 FSRS 表 → recompute 不抛错，retention 恒 None，其余维度照算。"""
    db, engine = _make_iso_db()  # 未建 FSRS 表
    sid = _add_student(db)
    kp = _add_kp(db)
    # 给足 3 次正确（>= min_samples）让 D1/D2/D3 被填充，同时验证 retention 守卫
    for _ in range(3):
        _emit(db, student_id=sid, kp_id=kp, event_type="CORRECT")

    # 不应抛任何异常
    updated = recompute_student_six_dim_mastery(db, student_id=sid, now=NOW)
    assert updated == 1

    db.commit()
    read_db = _read_session(engine)
    row = _get_row(read_db, sid, kp)
    assert row is not None
    assert row.retention_mastery is None  # 无 FSRS 表 → 优雅跳过，恒 None
    assert row.concept_mastery is not None  # 样本充足 → 维度照常计算
    assert row.procedure_mastery is not None


# ================================================================ 6. D0 不被碰
def test_recompute_does_not_touch_mastery_column() -> None:
    """recompute 严格只写 5 分维，不改动增量维护的 mastery / 计数。"""
    db, engine = _make_iso_db()
    sid = _add_student(db)
    kp = _add_kp(db)
    # 3 次独立正确 + 1 次错误（>= min_samples，让 D1/D2/D3 被填充；D4 无迁移标记→None）
    for _ in range(3):
        _emit(db, student_id=sid, kp_id=kp, event_type="CORRECT", meta={"independent": True})
    _emit(db, student_id=sid, kp_id=kp, event_type="ERROR")

    # 增量已写入 mastery / 计数
    read_db0 = _read_session(engine)
    before = _get_row(read_db0, sid, kp)
    before_mastery = float(before.mastery)
    before_practice = int(before.practice_count)

    recompute_student_six_dim_mastery(db, student_id=sid, now=NOW)

    db.commit()
    read_db1 = _read_session(engine)
    after = _get_row(read_db1, sid, kp)
    # mastery 与计数保持不变（recompute 不写这两类字段）
    assert float(after.mastery) == before_mastery
    assert int(after.practice_count) == before_practice
    # 5 分维已被填充（concept / procedure / independent 各有 >=3 样本）
    assert after.concept_mastery is not None
    assert after.procedure_mastery is not None
    assert after.independent_mastery is not None
    assert after.transfer_mastery is None  # 无 transfer 事件 → None
    assert after.retention_mastery is None  # 无 FSRS 表 → None


# ================================================================ 7. cron 接线守卫
def test_daily_sync_wires_recompute(db) -> None:
    """变异守卫：recompute 必须真的接进 daily_sync，否则六维列永远是空。

    做法：种子一个带 StudentProfile 的学生 + 足够练习事件，调真实
    ``daily_sync()``（它内部遍历 StudentProfile 调 recompute_student_six_dim_mastery），
    再开新会话读取，断言六维列被填充。**这条守卫对应「守卫类代码必须验证生效」——
    如果有人把 daily_sync 里的 recompute 调用删了，本例会直接红。**
    """
    from app.db import SessionLocal
    from app.models.learning import StudentProfile
    from app.tasks.cron_jobs import daily_sync

    user = User(id=uuid.uuid4(), phone=f"cron-{uuid.uuid4()}", role="student")
    student = Student(id=uuid.uuid4(), user_id=user.id, grade=5)
    db.add(user)
    db.add(student)
    db.add(StudentProfile(student_id=student.id))
    kp = KnowledgePoint(id=uuid.uuid4(), code="KP_CRON", name="知识点Cron", sort=0)
    db.add(kp)
    db.commit()

    sid = student.id
    for _ in range(3):
        _emit(db, student_id=sid, kp_id=kp.id, event_type="CORRECT", meta={"independent": True})
    _emit(db, student_id=sid, kp_id=kp.id, event_type="ERROR")

    # 通过真实每日钩子触发重算
    result = daily_sync()
    assert result["mastery_updated"] >= 1

    # 开新会话（绕开 expire_on_commit=False）读取已提交结果
    read_db = SessionLocal()
    try:
        row = (
            read_db.execute(
                select(StudentKnowledgeMastery).where(
                    StudentKnowledgeMastery.student_id == sid,
                    StudentKnowledgeMastery.knowledge_point_id == kp.id,
                )
            )
            .scalars()
            .one_or_none()
        )
        assert row is not None
        assert row.concept_mastery is not None
        assert row.procedure_mastery is not None
        assert row.independent_mastery is not None
        assert row.transfer_mastery is None
        assert row.retention_mastery is None
    finally:
        read_db.close()

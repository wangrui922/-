"""C02 Phase 3 · L3 综合洞察层（``student_mastery_synthesis``）落库测试。

护栏重点：
    1. **落库完整性**：三类综合各一行，``ref_kp_ids`` / ``ref_event_ids`` /
       ``payload`` 都是可解析的 JSON（溯源链 L3→L2→L1 可审计）；
    2. **溯源边缺失即拒写**（铁律 7 变异守卫）：把引擎换成「无溯源边」的结果，
       ``consolidate_mastery`` 必须**一行都不写**——若有人删掉该守卫，本例直接红；
    3. **表缺失优雅降级**：L3 表无 ORM 模型（``create_all`` 建不出来），
       表不存在时返回 0 且不抛错；
    4. **幂等 upsert**：重复重算每类仍只一行；
    5. **cron 接线变异守卫**：真实 ``daily_sync()`` 必须真的调 ``consolidate_mastery``；
    6. **schema 对账注册**：迁移文件必须在 ``schema_reconcile`` 的期望表集里
       （否则生产库永远建不出这张表）。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.content import KnowledgePoint
from app.models.dna import StudentLearningEvent
from app.models.system_config import SystemConfig  # noqa: F401 - 保证建表含 system_config
from app.models.user import Student, User

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

#: L3 表迁移文件（无 ORM 模型 → 走 raw SQL + schema_reconcile）
MIGRATION_FILE = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "025_student_mastery_synthesis_sqlite.sql"
)


# ================================================================== 工具
def _migration_sql() -> str:
    return MIGRATION_FILE.read_text(encoding="utf-8")


def _create_l3_table(engine) -> None:
    """按迁移文件建 L3 表（幂等）。

    走**真实迁移文件**而非内联 DDL —— 这样本测试同时证明迁移文件本身可用
    （语法正确、表名/列名与 raw SQL 读写一致）。
    """
    raw = engine.raw_connection()
    try:
        raw.executescript(_migration_sql())
        raw.commit()
    finally:
        raw.close()


def _make_iso_db() -> tuple:
    """造独立内存库（与共享测试库隔离，计数确定），返回 (db, engine)。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    db = maker()
    return db, engine


def _read_session(engine):
    """绑定同一引擎的新会话（绕开 expire_on_commit=False 的身份映射假象）。"""
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _add_student(db, *, grade: int = 5) -> uuid.UUID:
    user = User(id=uuid.uuid4(), phone=f"l3-test-{uuid.uuid4()}", role="student")
    student = Student(id=uuid.uuid4(), user_id=user.id, grade=grade)
    db.add(user)
    db.add(student)
    db.commit()
    return student.id


def _add_kp(db, *, code: str = "KP_L3") -> uuid.UUID:
    kp = KnowledgePoint(id=uuid.uuid4(), code=code, name=f"知识点{code}", sort=0)
    db.add(kp)
    db.commit()
    return kp.id


def _emit(db, *, student_id, kp_id, event_type, meta=None) -> None:
    """走真实埋点落库（record_learning_event 只 flush，这里显式 commit）。"""
    from app.services.student_dna_service import record_learning_event

    record_learning_event(
        db,
        student_id=student_id,
        event_type=event_type,
        knowledge_point_id=kp_id,
        meta=meta or {},
    )
    db.commit()


def _rows(read_db, student_id) -> List[Dict]:
    """读该生 L3 全部行（原始 tuple）。"""
    return list(
        read_db.execute(
            text(
                "SELECT synthesis_type, ref_kp_ids, ref_event_ids, payload, confidence "
                "FROM student_mastery_synthesis WHERE student_id = :sid "
                "ORDER BY synthesis_type"
            ),
            {"sid": str(student_id)},
        ).all()
    )


# ================================================================== 1. 落库完整性
def test_consolidate_writes_traceable_synthesis_rows() -> None:
    """有事件时写入**有证据支撑**的综合行，溯源边与 payload 均为可解析 JSON。

    注意：没有前置依赖边 → 没有前置缺口 → ``WEAK_CHAIN`` 无证据支撑 → 不写
    （溯源边守卫）。所以此处是 2 行而非 3 行。
    """
    from app.services.student_dna_service import consolidate_mastery

    db, engine = _make_iso_db()
    _create_l3_table(engine)
    sid = _add_student(db)
    kp = _add_kp(db)

    _emit(db, student_id=sid, kp_id=kp, event_type="CORRECT")
    _emit(db, student_id=sid, kp_id=kp, event_type="CORRECT")
    _emit(db, student_id=sid, kp_id=kp, event_type="ERROR", meta={"error_type": "CALC"})

    written = consolidate_mastery(db, student_id=sid, now=NOW)
    db.commit()

    assert written == 2
    read_db = _read_session(engine)
    rows = _rows(read_db, sid)
    assert len(rows) == 2
    assert [r[0] for r in rows] == ["ERROR_PATTERN", "TREND"]

    for stype, ref_kp, ref_evt, payload, conf in rows:
        # 溯源边必须是 JSON 数组
        assert isinstance(json.loads(ref_kp), list)
        assert isinstance(json.loads(ref_evt), list)
        assert isinstance(json.loads(payload), dict)
        assert 0.0 <= float(conf) <= 1.0

    by_type = {r[0]: r for r in rows}
    # ERROR_PATTERN 溯源到出错事件 + 知识点
    assert json.loads(by_type["ERROR_PATTERN"][2]) != []
    assert str(kp) in json.loads(by_type["ERROR_PATTERN"][1])
    # TREND 溯源到全部事件
    assert len(json.loads(by_type["TREND"][2])) == 3


def test_consolidate_writes_weak_chain_when_gap_exists() -> None:
    """存在前置缺口时 WEAK_CHAIN 也写入，且溯源边含后置 + 前置知识点。

    前置缺口成立的三个条件（沿用 ``detect_prerequisite_gaps`` 口径）：
        1. 后置知识点**已练过**（``practice_count > 0`` → 需 ANSWER 事件）；
        2. 前置知识点没练过 → ``missing=True``；
        3. 存在 ``knowledge_prerequisite`` 边。
    """
    from app.models.knowledge_prerequisite import KnowledgePrerequisite
    from app.services.student_dna_service import consolidate_mastery

    db, engine = _make_iso_db()
    _create_l3_table(engine)
    sid = _add_student(db)
    kp_target = _add_kp(db, code="KP_TARGET")
    kp_pre = _add_kp(db, code="KP_PRE")

    # 后置：ANSWER（练习数 +1）→ 已练过；再补一次 ERROR 让 ERROR_PATTERN 也有证据
    _emit(db, student_id=sid, kp_id=kp_target, event_type="ANSWER")
    _emit(db, student_id=sid, kp_id=kp_target, event_type="CORRECT")
    _emit(db, student_id=sid, kp_id=kp_target, event_type="ERROR")
    # 前置：完全没练过 → missing
    db.add(
        KnowledgePrerequisite(
            pre_kp_id=kp_pre, kp_id=kp_target, weight=1.0
        )
    )
    db.commit()

    written = consolidate_mastery(db, student_id=sid, now=NOW)
    db.commit()

    assert written == 3
    read_db = _read_session(engine)
    rows = _rows(read_db, sid)
    assert [r[0] for r in rows] == ["ERROR_PATTERN", "TREND", "WEAK_CHAIN"]

    wc = [r for r in rows if r[0] == "WEAK_CHAIN"][0]
    payload = json.loads(wc[3])
    assert payload["gap_count"] == 1
    gap = payload["gaps"][0]
    assert gap["kp_id"] == str(kp_target)
    assert gap["pre_kp_id"] == str(kp_pre)
    assert gap["missing"] is True
    # 溯源边必须同时含后置与前置知识点
    assert sorted(json.loads(wc[1])) == sorted([str(kp_pre), str(kp_target)])


def test_consolidate_removes_stale_row_when_conclusion_disappears() -> None:
    """结论消失时旧行必须被清掉（前置缺口被补上 → WEAK_CHAIN 行删除）。

    否则家长端会一直看到过期的「薄弱链路」结论。
    """
    from app.models.knowledge_prerequisite import KnowledgePrerequisite
    from app.services.student_dna_service import consolidate_mastery

    db, engine = _make_iso_db()
    _create_l3_table(engine)
    sid = _add_student(db)
    kp_target = _add_kp(db, code="KP_TARGET2")
    kp_pre = _add_kp(db, code="KP_PRE2")

    _emit(db, student_id=sid, kp_id=kp_target, event_type="ANSWER")
    _emit(db, student_id=sid, kp_id=kp_target, event_type="CORRECT")
    _emit(db, student_id=sid, kp_id=kp_target, event_type="ERROR")
    db.add(KnowledgePrerequisite(pre_kp_id=kp_pre, kp_id=kp_target, weight=1.0))
    db.commit()

    assert consolidate_mastery(db, student_id=sid, now=NOW) == 3
    db.commit()
    read_db0 = _read_session(engine)
    assert "WEAK_CHAIN" in [r[0] for r in _rows(read_db0, sid)]

    # 把前置补上来：ANSWER + 6 次正确 → mastery 0.6 ≥ 缺口线 0.5 → 缺口消失
    _emit(db, student_id=sid, kp_id=kp_pre, event_type="ANSWER")
    for _ in range(6):
        _emit(db, student_id=sid, kp_id=kp_pre, event_type="CORRECT")

    assert consolidate_mastery(db, student_id=sid, now=NOW) == 2
    db.commit()
    read_db1 = _read_session(engine)
    types = [r[0] for r in _rows(read_db1, sid)]
    assert "WEAK_CHAIN" not in types
    assert set(types) == {"ERROR_PATTERN", "TREND"}


# ================================================================== 2. 溯源边守卫（变异守卫）
def test_consolidate_rejects_results_without_traceability(monkeypatch) -> None:
    """变异守卫：**溯源边缺失即拒写**——没有 L1/L2 证据支撑的结论一行都不写。

    做法：把引擎整体换成一个「产出无溯源边结论」的假引擎，再调
    ``consolidate_mastery``。**如果有人把守卫删掉**，这些空结论就会被写进
    库（变成凭空捏造的 L3），本例的 ``written == 0`` 与「表里 0 行」断言直接红。
    """
    from app.engines.mastery_synthesis import (
        SYNTHESIS_ERROR_PATTERN,
        SYNTHESIS_TREND,
        SYNTHESIS_WEAK_CHAIN,
        SynthesisResult,
    )
    from app.services import student_dna_service

    def _fake_synthesize(*args, **kwargs):
        return {
            SYNTHESIS_ERROR_PATTERN: SynthesisResult(
                synthesis_type=SYNTHESIS_ERROR_PATTERN,
                ref_kp_ids=[],
                ref_event_ids=[],
                payload={"fabricated": True},
                confidence=0.9,
            ),
            SYNTHESIS_WEAK_CHAIN: SynthesisResult(
                synthesis_type=SYNTHESIS_WEAK_CHAIN,
                ref_kp_ids=[],
                ref_event_ids=[],
                payload={"fabricated": True},
                confidence=0.9,
            ),
            SYNTHESIS_TREND: SynthesisResult(
                synthesis_type=SYNTHESIS_TREND,
                ref_kp_ids=[],
                ref_event_ids=[],
                payload={"fabricated": True},
                confidence=0.9,
            ),
        }

    monkeypatch.setattr(student_dna_service, "synthesize_student", _fake_synthesize)

    db, engine = _make_iso_db()
    _create_l3_table(engine)
    sid = _add_student(db)
    kp = _add_kp(db)
    # 刻意造事件：证明「有数据但结论无溯源边」时依然拒写
    _emit(db, student_id=sid, kp_id=kp, event_type="ERROR")

    written = student_dna_service.consolidate_mastery(db, student_id=sid, now=NOW)
    db.commit()

    assert written == 0
    read_db = _read_session(engine)
    assert _rows(read_db, sid) == []


def test_consolidate_skips_only_the_untraceable_type(monkeypatch) -> None:
    """部分结论无溯源边时，只拒写那一类，其余照写（不整批丢弃）。"""
    from app.engines.mastery_synthesis import (
        SYNTHESIS_ERROR_PATTERN,
        SYNTHESIS_TREND,
        SYNTHESIS_WEAK_CHAIN,
        SynthesisResult,
    )
    from app.services import student_dna_service

    def _fake_synthesize(*args, **kwargs):
        return {
            # 无溯源边 → 拒写
            SYNTHESIS_ERROR_PATTERN: SynthesisResult(
                synthesis_type=SYNTHESIS_ERROR_PATTERN,
                ref_kp_ids=[],
                ref_event_ids=[],
                payload={},
                confidence=0.1,
            ),
            # 有溯源边 → 照写
            SYNTHESIS_WEAK_CHAIN: SynthesisResult(
                synthesis_type=SYNTHESIS_WEAK_CHAIN,
                ref_kp_ids=["kp-1"],
                ref_event_ids=[],
                payload={"gap_count": 1},
                confidence=0.5,
            ),
            SYNTHESIS_TREND: SynthesisResult(
                synthesis_type=SYNTHESIS_TREND,
                ref_kp_ids=["kp-1"],
                ref_event_ids=["ev-1"],
                payload={},
                confidence=0.5,
            ),
        }

    monkeypatch.setattr(student_dna_service, "synthesize_student", _fake_synthesize)

    db, engine = _make_iso_db()
    _create_l3_table(engine)
    sid = _add_student(db)
    kp = _add_kp(db)
    _emit(db, student_id=sid, kp_id=kp, event_type="ERROR")

    written = student_dna_service.consolidate_mastery(db, student_id=sid, now=NOW)
    db.commit()

    assert written == 2
    read_db = _read_session(engine)
    types = [r[0] for r in _rows(read_db, sid)]
    assert types == ["TREND", "WEAK_CHAIN"]
    assert "ERROR_PATTERN" not in types


# ================================================================== 3. 表缺失优雅降级
def test_consolidate_returns_zero_when_table_missing() -> None:
    """L3 表不存在（未跑 schema_reconcile）→ 返回 0 且不抛错。"""
    from app.services.student_dna_service import consolidate_mastery

    db, engine = _make_iso_db()  # 刻意不建 L3 表
    sid = _add_student(db)
    kp = _add_kp(db)
    _emit(db, student_id=sid, kp_id=kp, event_type="ERROR")

    assert consolidate_mastery(db, student_id=sid, now=NOW) == 0


# ================================================================== 4. 幂等 upsert
def test_consolidate_is_idempotent_upsert() -> None:
    """重复重算：同一类型仍只一行（UNIQUE(student_id, synthesis_type) upsert）。"""
    from app.services.student_dna_service import consolidate_mastery

    db, engine = _make_iso_db()
    _create_l3_table(engine)
    sid = _add_student(db)
    kp = _add_kp(db)
    _emit(db, student_id=sid, kp_id=kp, event_type="ERROR")

    consolidate_mastery(db, student_id=sid, now=NOW)
    db.commit()
    consolidate_mastery(db, student_id=sid, now=NOW)
    db.commit()

    read_db = _read_session(engine)
    rows = _rows(read_db, sid)
    assert len(rows) == len({r[0] for r in rows})
    assert {r[0] for r in rows} == {"ERROR_PATTERN", "TREND"}


# ================================================================== 5. 读取出口
def test_get_learning_dna_exposes_synthesis() -> None:
    """``get_learning_dna`` 出口带 ``synthesis`` 字段（家长端/报告直接消费）。"""
    from app.services.student_dna_service import consolidate_mastery, get_learning_dna

    db, engine = _make_iso_db()
    _create_l3_table(engine)
    sid = _add_student(db)
    kp = _add_kp(db)
    _emit(db, student_id=sid, kp_id=kp, event_type="ERROR", meta={"error_type": "CALC"})

    consolidate_mastery(db, student_id=sid, now=NOW)
    db.commit()

    read_db = _read_session(engine)
    dna = get_learning_dna(read_db, student_id=sid)
    assert "synthesis" in dna
    assert len(dna["synthesis"]) == 2
    for item in dna["synthesis"]:
        assert item["synthesis_type"] in ("ERROR_PATTERN", "WEAK_CHAIN", "TREND")
        assert isinstance(item["ref_kp_ids"], list)
        assert isinstance(item["ref_event_ids"], list)
        assert isinstance(item["payload"], dict)


def test_get_learning_dna_synthesis_empty_when_table_missing() -> None:
    """表不存在 → ``synthesis`` 为空列表（不报错，与「无数据返回默认值」一致）。"""
    from app.services.student_dna_service import get_learning_dna

    db, engine = _make_iso_db()  # 不建 L3 表
    sid = _add_student(db)

    dna = get_learning_dna(db, student_id=sid)
    assert dna["synthesis"] == []


# ================================================================== 8. 探测不得回滚未提交写入
def test_table_probe_does_not_rollback_pending_writes() -> None:
    """守卫：**表存在探测不得回滚调用方未提交的写入**。

    真实事故：用 ``inspect(db.bind).has_table()`` 探测表是否存在 → 从连接池
    **再借一条连接** → 内存库是 ``StaticPool``（全局单连接），借出即归还，
    归还时连接池默认 ``rollback`` → **同一事务里刚 flush 的掌握度行被静默
    回滚** → 下游前置缺口检测拿到空列表（断言 ``len(gaps)==1`` 变 0）。

    本例把探测放在「有未提交写入」的同一事务里跑，确保写入还在。
    """
    from app.services.student_dna_service import get_learning_dna

    db, engine = _make_iso_db()
    _create_l3_table(engine)
    sid = _add_student(db)
    kp = _add_kp(db)

    # 未提交的掌握度行（只 flush，不 commit）
    from app.models.dna import StudentKnowledgeMastery

    db.add(
        StudentKnowledgeMastery(
            student_id=sid,
            knowledge_point_id=kp,
            practice_count=5,
            correct_count=2,
            mastery=0.3,
        )
    )
    db.flush()

    # 触发「表存在探测」（L3 读出口 + L2 留存探测都在这条路径上）
    dna = get_learning_dna(db, student_id=sid)

    # 未提交的写入必须还在
    assert len(dna["mastery_summary"]) == 1
    rows = (
        db.execute(
            select(StudentKnowledgeMastery).where(
                StudentKnowledgeMastery.student_id == sid
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert int(rows[0].practice_count) == 5


# ================================================================== 6. schema 对账注册
def test_migration_registers_table_for_schema_reconcile() -> None:
    """迁移文件必须在 schema_reconcile 的期望表集里（否则生产库建不出该表）。"""
    import sys

    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from schema_reconcile import parse_migration_tables

    tables = parse_migration_tables()
    assert "student_mastery_synthesis" in tables
    assert tables["student_mastery_synthesis"].name == (
        "025_student_mastery_synthesis_sqlite.sql"
    )


# ================================================================== 7. cron 接线变异守卫
def test_daily_sync_wires_consolidate(db) -> None:
    """变异守卫：``daily_sync`` 必须真的调 ``consolidate_mastery``。

    做法：种子带 StudentProfile 的学生 + 练习事件，调真实 ``daily_sync()``，
    再开新会话读 L3 表，断言已写入综合行。**有人把 daily_sync 里的
    consolidate 调用删了，本例会直接红。**
    """
    from app.db import SessionLocal, engine
    from app.models.learning import StudentProfile
    from app.tasks.cron_jobs import daily_sync

    _create_l3_table(engine)

    user = User(id=uuid.uuid4(), phone=f"cron-l3-{uuid.uuid4()}", role="student")
    student = Student(id=uuid.uuid4(), user_id=user.id, grade=5)
    db.add(user)
    db.add(student)
    db.add(StudentProfile(student_id=student.id))
    kp = KnowledgePoint(id=uuid.uuid4(), code="KP_CRON_L3", name="知识点CronL3", sort=0)
    db.add(kp)
    db.commit()

    sid = student.id
    _emit(db, student_id=sid, kp_id=kp.id, event_type="ANSWER")
    _emit(db, student_id=sid, kp_id=kp.id, event_type="CORRECT")
    _emit(db, student_id=sid, kp_id=kp.id, event_type="ERROR", meta={"error_type": "CALC"})

    result = daily_sync()
    assert result["mastery_updated"] >= 1

    read_db = SessionLocal()
    try:
        rows = _rows(read_db, sid)
        # 有证据支撑的两类必在（WEAK_CHAIN 取决于图谱里是否真有缺口，不在此断言）
        assert {"ERROR_PATTERN", "TREND"} <= {r[0] for r in rows}
    finally:
        read_db.close()

"""V6.5 Sprint 2 · 学生 DNA 域契约测试（DNA-001/002/003/004/005）。

本文件是学生 DNA 三张落地表 + 服务的**结构护栏**，覆盖五组断言：

1. **双方言迁移**：``migrations/`` 下 019/020/021 的 PG 版与 SQLite 版必须
   同批存在、都建表、关键字段/约束齐全（缺任何一版，另一方言部署就挂）；
2. **SQLite DDL 可执行**：把 019/020/021 的 SQLite DDL 真跑一遍（含前置表），
   验证建表、CHECK 枚举、UNIQUE、索引、外键真实生效（语法错这里就炸）；
3. **DNA-002 画像写入**：八类学习目标枚举硬校验（非法值 ApiError 400）、
   hint_dependency 软枚举校验、JSON 字段类型校验、写入后读回一致、upsert 幂等；
4. **DNA-004 事件记录**：八类事件枚举校验（非法值 ApiError 400），
   meta 非对象拒绝，落库后可读回；
5. **DNA-005 聚合**：API 返回画像+掌握+事件三块结构、无数据学生返回默认值
   不报错（service 层）；HTTP 层鉴权（本人/绑定家长可见、他人/未绑定家长 403、
   不存在 404、未登录 401）。

红线（与 V6.5 开发总规划逐字对齐，勿漂移）：
    * 枚举值集合（LEARNING_GOAL_VALUES / LEARNING_EVENT_TYPE_VALUES）一个都不能改；
    * 画像表 ``student_learning_profiles.student_id`` 唯一（1:1 学生）；
    * 掌握表 ``(student_id, knowledge_point_id)`` 唯一；
    * 事件表 CHECK 八类枚举 + 聚合最近事件上限默认 10 条。
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import constants as C
from app.core.response import ApiError, CODE_BAD_REQUEST
from app.db import SessionLocal
from app.models.base import Base
from app.models.content import KnowledgePoint
from app.models.dna import (
    HINT_DEPENDENCY_VALUES,
    LEARNING_EVENT_TYPE_VALUES,
    LEARNING_GOAL_VALUES,
    StudentKnowledgeMastery,
    StudentLearningEvent,
    StudentLearningProfile,
)
from app.models.user import Student, User
from app.services.student_dna_service import (
    RECENT_EVENTS_LIMIT,
    get_learning_dna,
    get_student_profile,
    mastery_delta_for_event,
    record_learning_event,
    update_mastery_from_event,
    upsert_student_profile,
)

#: migrations 目录（tests/ 的上一级是 backend/）
MIGRATIONS_DIR: Path = Path(__file__).resolve().parents[1] / "migrations"

#: 三方言文件对：PG 版 / SQLite 版 必须同批存在
MIGRATION_PAIRS: tuple = (
    ("019_student_learning_profiles.sql", "019_student_learning_profiles_sqlite.sql"),
    ("020_student_knowledge_mastery.sql", "020_student_knowledge_mastery_sqlite.sql"),
    ("021_student_learning_events.sql", "021_student_learning_events_sqlite.sql"),
)


# ==================================================================== 公共夹具
@pytest.fixture()
def iso_db() -> Session:
    """独立内存库（与 conftest 的会话级共享库隔离，计数才确定）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    db = maker()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def add_student(db: Session, *, grade: int = 5) -> uuid.UUID:
    """造一个最小学生（含 User，满足 FK），返回 student.id。"""
    user = User(id=uuid.uuid4(), phone=f"dna-test-{uuid.uuid4()}", role="student")
    student = Student(id=uuid.uuid4(), user_id=user.id, grade=grade)
    db.add(user)
    db.add(student)
    db.commit()
    return student.id


def add_kp(db: Session, *, code: str = "KP_DNA_TEST") -> uuid.UUID:
    """造一个最小知识点，返回其 id。"""
    kp = KnowledgePoint(
        id=uuid.uuid4(),
        code=code,
        name=f"知识点{code}",
        sort=0,
    )
    db.add(kp)
    db.commit()
    return kp.id


# ==================================================================== ① 双方言迁移
@pytest.mark.parametrize("pg_name,sqlite_name", MIGRATION_PAIRS)
def test_migration_pair_exists_with_schema(
    pg_name: str, sqlite_name: str, request: Any
) -> None:
    """PG 与 SQLite 两份 migration 必须同批存在，且字段/约束齐全。"""
    for filename in (pg_name, sqlite_name):
        path = MIGRATIONS_DIR / filename
        assert path.exists(), f"缺失 migration：{filename}"
        sql = path.read_text(encoding="utf-8").lower()
        assert "create table" in sql, f"{filename} 未建表"
        assert "student_id" in sql, f"{filename} 缺 student_id"

    # 按表分别核对关键列（首版契约，改列名即触发）
    expect_tokens: Dict[str, tuple] = {
        "019": ("learning_goal", "learning_stage", "current_level", "hint_dependency"),
        "020": (
            "knowledge_point_id",
            "mastery",
            "confidence",
            "practice_count",
            "correct_count",
            "wrong_count",
            "concept_mastery",
        ),
        "021": (
            "event_type",
            "question_id",
            "knowledge_point_id",
            "meta",
            "created_at",
        ),
    }
    key = pg_name[:3]
    for filename in (pg_name, sqlite_name):
        sql = (MIGRATIONS_DIR / filename).read_text(encoding="utf-8").lower()
        for token in expect_tokens[key]:
            assert token in sql, f"{filename} 缺 {token}"


def test_migration_019_has_all_goal_values() -> None:
    """画像表 CHECK 必须覆盖八类学习目标（与模型枚举逐字一致）。"""
    for filename in (
        "019_student_learning_profiles.sql",
        "019_student_learning_profiles_sqlite.sql",
    ):
        sql = (MIGRATIONS_DIR / filename).read_text(encoding="utf-8")
        for goal in LEARNING_GOAL_VALUES:
            assert goal in sql, f"{filename} 的 CHECK 缺学习目标「{goal}」"


def test_migration_021_has_all_event_values() -> None:
    """事件表 CHECK 必须覆盖八类事件（与模型枚举逐字一致）。"""
    for filename in (
        "021_student_learning_events.sql",
        "021_student_learning_events_sqlite.sql",
    ):
        sql = (MIGRATIONS_DIR / filename).read_text(encoding="utf-8")
        for ev in LEARNING_EVENT_TYPE_VALUES:
            assert ev in sql, f"{filename} 的 CHECK 缺事件类型「{ev}」"


def test_sqlite_migrations_actually_create_tables() -> None:
    """把 019/020/021 的 SQLite DDL 真跑一遍：建表 + CHECK + UNIQUE + 索引 + 外键。"""
    ddl_files = (
        "019_student_learning_profiles_sqlite.sql",
        "020_student_knowledge_mastery_sqlite.sql",
        "021_student_learning_events_sqlite.sql",
    )
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        # 前置表（外键引用目标）
        conn.execute("CREATE TABLE students (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE knowledge_points (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE questions (id TEXT PRIMARY KEY, content TEXT)")
        for name in ddl_files:
            conn.executescript((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))

        # 表都存在
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for t in (
            "student_learning_profiles",
            "student_knowledge_mastery",
            "student_learning_events",
        ):
            assert t in tables, f"DDL 未建出表 {t}"

        # 019 画像表：UNIQUE(student_id) + CHECK 枚举
        rights, wrongs = str(uuid.uuid4()), str(uuid.uuid4())
        conn.execute(
            "INSERT INTO students (id) VALUES (?)",
            (rights,),
        )
        conn.execute(
            "INSERT INTO student_learning_profiles "
            "(id, student_id, learning_goal, created_at, updated_at) "
            "VALUES (?, ?, '期末', '2026-01-01', '2026-01-01')",
            (str(uuid.uuid4()), rights),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO student_learning_profiles "
                "(id, student_id, learning_goal, created_at, updated_at) "
                "VALUES (?, ?, '期末', '2026-01-01', '2026-01-01')",
                (str(uuid.uuid4()), rights),
            )  # student_id 重复 → UNIQUE
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO student_learning_profiles "
                "(id, student_id, learning_goal, created_at, updated_at) "
                "VALUES (?, ?, '非法目标', '2026-01-01', '2026-01-01')",
                (str(uuid.uuid4()), str(uuid.uuid4())),
            )  # 非法学习目标 → CHECK
        # 先补一个合法学生再验证非法目标不落库
        conn.execute(
            "INSERT INTO students (id) VALUES (?)",
            (wrongs,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO student_learning_profiles "
                "(id, student_id, learning_goal, created_at, updated_at) "
                "VALUES (?, ?, '非法目标', '2026-01-01', '2026-01-01')",
                (str(uuid.uuid4()), wrongs),
            )

        # 020 掌握表：UNIQUE(student_id, knowledge_point_id)
        kp_id = str(uuid.uuid4())
        conn.execute("INSERT INTO knowledge_points (id) VALUES (?)", (kp_id,))
        conn.execute(
            "INSERT INTO student_knowledge_mastery "
            "(id, student_id, knowledge_point_id, mastery, confidence, created_at, updated_at) "
            "VALUES (?, ?, ?, 0.5, 0.3, '2026-01-01', '2026-01-01')",
            (str(uuid.uuid4()), rights, kp_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO student_knowledge_mastery "
                "(id, student_id, knowledge_point_id, mastery, confidence, created_at, updated_at) "
                "VALUES (?, ?, ?, 0.6, 0.4, '2026-01-01', '2026-01-01')",
                (str(uuid.uuid4()), rights, kp_id),
            )  # (student_id, kp_id) 重复 → UNIQUE

        # 021 事件表：CHECK 八类枚举 + 索引存在
        conn.execute(
            "INSERT INTO student_learning_events "
            "(id, student_id, event_type, created_at) "
            "VALUES (?, ?, 'ANSWER', '2026-01-01')",
            (str(uuid.uuid4()), rights),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO student_learning_events "
                "(id, student_id, event_type, created_at) "
                "VALUES (?, ?, 'BAD_EVENT', '2026-01-01')",
                (str(uuid.uuid4()), rights),
            )  # 非法事件 → CHECK
        idx_names = {
            row[1] for row in conn.execute("PRAGMA index_list(student_learning_events)")
        }
        assert "idx_sle_student_created" in idx_names, "事件表缺 created_at 索引"
    finally:
        conn.close()


# ==================================================================== ② DNA-002 画像写入
def test_profile_write_and_read_back(iso_db: Session) -> None:
    """全字段写入后读回一致（含 JSON 字段与建议枚举）。"""
    sid = add_student(iso_db)
    back = upsert_student_profile(
        iso_db,
        student_id=sid,
        learning_goal="中考",
        learning_stage="九年级",
        current_level="基础巩固",
        learning_habits=[{"type": "每日练习", "minutes": 30}],
        common_errors=["粗心"],
        hint_dependency="medium",
        learning_trend={"30日": [0.5, 0.6]},
    )
    assert back["learning_goal"] == "中考"
    assert back["learning_stage"] == "九年级"
    assert back["current_level"] == "基础巩固"
    assert back["learning_habits"] == [{"type": "每日练习", "minutes": 30}]
    assert back["common_errors"] == ["粗心"]
    assert back["hint_dependency"] == "medium"
    assert back["learning_trend"] == {"30日": [0.5, 0.6]}

    # 独立读回（新查询）仍然一致；updated_at 因 SQLite 存 TEXT 丢时区制
    # 只比较业务字段，时间戳单独断言非空
    again = get_student_profile(iso_db, student_id=sid)
    base_keys = [k for k in back if k != "updated_at"]
    assert all(again[k] == back[k] for k in base_keys), (again, back)
    assert back["updated_at"] is not None and again["updated_at"] is not None


def test_profile_upsert_is_idempotent_single_row(iso_db: Session) -> None:
    """同一学生重复 upsert 只保留一行，且新值覆盖旧值。"""
    sid = add_student(iso_db)
    upsert_student_profile(iso_db, student_id=sid, learning_goal="期末")
    upsert_student_profile(
        iso_db, student_id=sid, learning_goal="中考", current_level="冲刺"
    )
    rows = (
        iso_db.execute(
            select(StudentLearningProfile).where(
                StudentLearningProfile.student_id == sid
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].learning_goal == "中考"
    assert rows[0].current_level == "冲刺"


@pytest.mark.parametrize("bad_goal", ["随便写", "", "期中考试", 123, None])
def test_profile_rejects_bad_learning_goal(iso_db: Session, bad_goal: Any) -> None:
    """学习目标非八类枚举（含空/None/错误类型）一律 400。"""
    sid = add_student(iso_db)
    with pytest.raises(ApiError) as exc:
        upsert_student_profile(iso_db, student_id=sid, learning_goal=bad_goal)
    assert exc.value.code == CODE_BAD_REQUEST
    assert "学习目标" in exc.value.msg


def test_profile_rejects_bad_hint_dependency(iso_db: Session) -> None:
    """hint_dependency 非 high/medium/low 拒绝；缺省或合法值通过。"""
    sid = add_student(iso_db)
    with pytest.raises(ApiError) as exc:
        upsert_student_profile(
            iso_db, student_id=sid, learning_goal="期末", hint_dependency="always"
        )
    assert exc.value.code == CODE_BAD_REQUEST

    back = upsert_student_profile(iso_db, student_id=sid, learning_goal="期末")
    assert back["hint_dependency"] is None

    for dep in HINT_DEPENDENCY_VALUES:
        back = upsert_student_profile(
            iso_db, student_id=sid, learning_goal="期末", hint_dependency=dep
        )
        assert back["hint_dependency"] == dep


def test_profile_rejects_bad_json_types(iso_db: Session) -> None:
    """JSON 字段只接受 list/dict/None，字符串/数字一律 400。"""
    sid = add_student(iso_db)
    for field, bad in (
        ("learning_habits", "习惯"),
        ("common_errors", 42),
        ("learning_trend", True),
    ):
        with pytest.raises(ApiError) as exc:
            upsert_student_profile(
                iso_db,
                student_id=sid,
                learning_goal="期末",
                **{field: bad},
            )
        assert exc.value.code == CODE_BAD_REQUEST, field


def test_profile_read_empty_defaults(iso_db: Session) -> None:
    """无画像学生读回空默认值（不报错）。"""
    sid = add_student(iso_db)
    profile = get_student_profile(iso_db, student_id=sid)
    assert profile["student_id"] == str(sid)
    assert profile["learning_goal"] is None
    assert profile["learning_stage"] is None
    assert profile["learning_trend"] is None
    assert profile["updated_at"] is None


# ==================================================================== ② DNA-003 掌握度增量更新
def _mastery_row(db: Session, sid: uuid.UUID, kp_id: uuid.UUID):
    """取掌握表当前行（未建行返回 None）。"""
    return db.execute(
        select(StudentKnowledgeMastery).where(
            StudentKnowledgeMastery.student_id == sid,
            StudentKnowledgeMastery.knowledge_point_id == kp_id,
        )
    ).scalar_one_or_none()


@pytest.mark.parametrize(
    "event_type,meta,expect_delta",
    [
        # 基础独立正确 +0.10
        ("CORRECT", None, C.MASTERY_DELTA_CORRECT_BASE),
        # 提示后正确（hint_used 或 hint_count>=1）→ +0.05
        ("CORRECT", {"hint_used": True}, C.MASTERY_DELTA_CORRECT_HINTED),
        ("CORRECT", {"hint_count": 1}, C.MASTERY_DELTA_CORRECT_HINTED),
        # 大量提示（hint_count>=3）→ +0.02
        ("CORRECT", {"hint_count": 3}, C.MASTERY_DELTA_CORRECT_HINTED_HEAVY),
        # 迁移/独立标记 → +0.12
        ("CORRECT", {"transfer": True}, C.MASTERY_DELTA_CORRECT_TRANSFER),
        ("CORRECT", {"independent": True}, C.MASTERY_DELTA_CORRECT_TRANSFER),
        # 核心错 → -0.08
        ("ERROR", None, -C.MASTERY_DELTA_WRONG_CORE),
        # 非驱动事件 → 0
        ("HINT", None, 0.0),
        ("REVIEW", None, 0.0),
        ("QUESTIONVIEW", None, 0.0),
    ],
)
def test_dna003_delta_rules(
    iso_db: Session, event_type: str, meta: dict, expect_delta: float
) -> None:
    """掌握度增量档位与规划 M-002 逐字对齐（0~100 制 ÷100 落表）。"""
    assert mastery_delta_for_event(event_type, meta) == pytest.approx(expect_delta)
    # 单次跳变上限 |delta| <= 0.15
    assert abs(mastery_delta_for_event(event_type, meta)) <= C.MASTERY_DELTA_MAX_ABS


def test_dna003_upsert_mapping_and_counts(iso_db: Session) -> None:
    """ANSWER/CORRECT/ERROR 事件→计数器/时间戳映射，upsert 幂等不双计。"""
    sid = add_student(iso_db)
    kp_id = add_kp(iso_db)

    # ANSWER + CORRECT（一次作答两条事件）→ practice 只 +1，correct +1
    record_learning_event(
        iso_db, student_id=sid, event_type="ANSWER", knowledge_point_id=kp_id
    )
    record_learning_event(
        iso_db, student_id=sid, event_type="CORRECT", knowledge_point_id=kp_id
    )
    iso_db.commit()

    row = _mastery_row(iso_db, sid, kp_id)
    assert row is not None
    assert row.practice_count == 1, "ANSWER+CORRECT 同作答，practice 不得双计"
    assert row.correct_count == 1
    assert row.wrong_count == 0
    assert row.last_practice_at is not None
    assert row.last_correct_at is not None
    assert row.last_wrong_at is None
    assert row.mastery == pytest.approx(C.MASTERY_DELTA_CORRECT_BASE)
    assert row.confidence == pytest.approx(0.5)  # 1 次作答

    # ERROR → wrong +1、mastery 回落、confidence 更新
    record_learning_event(
        iso_db, student_id=sid, event_type="ERROR", knowledge_point_id=kp_id
    )
    iso_db.commit()
    row = _mastery_row(iso_db, sid, kp_id)
    assert row.practice_count == 1, "ERROR 不驱动 practice_count"
    assert row.wrong_count == 1
    assert row.last_wrong_at is not None
    assert row.mastery == pytest.approx(
        C.MASTERY_DELTA_CORRECT_BASE - C.MASTERY_DELTA_WRONG_CORE
    )
    # confidence 只随 practice_count（ANSWER 次数）增长：本序列仅 1 次作答 → 0.5
    assert row.confidence == pytest.approx(0.5)
    assert row.concept_mastery is None, "预留五维列保持 NULL"


def test_dna003_answer_only_increments_practice(iso_db: Session) -> None:
    """重复 ANSWER 事件（不伴随作答结果）只累计 practice，不改 mastery/confidence。"""
    sid = add_student(iso_db)
    kp_id = add_kp(iso_db)
    for _ in range(3):
        record_learning_event(
            iso_db, student_id=sid, event_type="ANSWER", knowledge_point_id=kp_id
        )
    iso_db.commit()

    row = _mastery_row(iso_db, sid, kp_id)
    assert row.practice_count == 3
    assert row.correct_count == 0
    assert row.mastery == pytest.approx(0.0), "纯 ANSWER 不驱动掌握度"
    assert row.confidence == pytest.approx(1.0 - 0.5**3)  # 3 次 → 0.875


def test_dna003_clamp_and_bounded_delta(iso_db: Session) -> None:
    """mastery 夹紧 [0,1]；单次跳变上限 0.15（M-003 防跳变）。"""
    sid = add_student(iso_db)
    kp_id = add_kp(iso_db)

    # 多次正确逼近上限：1.0 封顶
    for _ in range(30):
        record_learning_event(
            iso_db, student_id=sid, event_type="CORRECT", knowledge_point_id=kp_id
        )
    iso_db.commit()
    row = _mastery_row(iso_db, sid, kp_id)
    assert row.mastery <= 1.0 + 1e-9
    assert row.mastery == pytest.approx(1.0)

    # 多次错误逼近下限：0.0 封底
    for _ in range(30):
        record_learning_event(
            iso_db, student_id=sid, event_type="ERROR", knowledge_point_id=kp_id
        )
    iso_db.commit()
    row = _mastery_row(iso_db, sid, kp_id)
    assert row.mastery >= 0.0 - 1e-9
    assert row.mastery == pytest.approx(0.0)

    # 单次事件 |delta| 不超 0.15
    assert (
        mastery_delta_for_event("CORRECT", {"transfer": True})
        <= C.MASTERY_DELTA_MAX_ABS
    )
    assert abs(mastery_delta_for_event("ERROR", None)) <= C.MASTERY_DELTA_MAX_ABS


def test_dna003_update_straight_call_contract(iso_db: Session) -> None:
    """直接调用 update_mastery_from_event：无 kp/非驱动事件返回 None 且不建行。"""
    sid = add_student(iso_db)
    kp_id = add_kp(iso_db)

    assert (
        update_mastery_from_event(
            iso_db, student_id=sid, event_type="HINT", knowledge_point_id=kp_id
        )
        is None
    )
    assert (
        update_mastery_from_event(
            iso_db, student_id=sid, event_type="CORRECT", knowledge_point_id=None
        )
        is None
    )
    iso_db.commit()
    assert _mastery_row(iso_db, sid, kp_id) is None, "非驱动调用不得建行"

    # 驱动事件返回更新后视图
    got = update_mastery_from_event(
        iso_db, student_id=sid, event_type="ANSWER", knowledge_point_id=kp_id
    )
    assert got is not None
    assert got["practice_count"] == 1
    assert got["mastery"] == pytest.approx(0.0)


def test_dna003_unique_constraint_holds(iso_db: Session) -> None:
    """掌握表 (student_id, knowledge_point_id) 唯一约束仍由 DB 兜底（M-001）。"""
    sid = add_student(iso_db)
    kp_id = add_kp(iso_db)
    record_learning_event(
        iso_db, student_id=sid, event_type="ANSWER", knowledge_point_id=kp_id
    )
    iso_db.commit()

    with pytest.raises(Exception) as exc:
        iso_db.add(
            StudentKnowledgeMastery(
                id=uuid.uuid4(), student_id=sid, knowledge_point_id=kp_id
            )
        )
        iso_db.commit()
    assert (
        "UNIQUE" in str(exc.value).upper()
        or "IntegrityError" in type(exc.value).__name__
    )


def test_dna003_event_linkage_no_double_count_via_practice(iso_db: Session) -> None:
    """链路级：一次 submit 的 ANSWER+CORRECT 双事件跨 commit 后仍不双计。"""
    sid = add_student(iso_db)
    kp_id = add_kp(iso_db)
    # 模拟 practice submit_answer 埋点序列：ANSWER 随提交，CORRECT 随判分
    record_learning_event(
        iso_db, student_id=sid, event_type="ANSWER", knowledge_point_id=kp_id
    )
    iso_db.commit()
    record_learning_event(
        iso_db, student_id=sid, event_type="CORRECT", knowledge_point_id=kp_id
    )
    iso_db.commit()
    # 另一学生同 kp 不串数
    sid2 = add_student(iso_db)
    record_learning_event(
        iso_db, student_id=sid2, event_type="ERROR", knowledge_point_id=kp_id
    )
    iso_db.commit()

    row = _mastery_row(iso_db, sid, kp_id)
    assert row.practice_count == 1 and row.correct_count == 1
    row2 = _mastery_row(iso_db, sid2, kp_id)
    assert row2.practice_count == 0 and row2.wrong_count == 1, (
        "学生间隔离，ERROR 不动 practice"
    )


# ==================================================================== ③ DNA-004 事件记录
@pytest.mark.parametrize("event_type", list(LEARNING_EVENT_TYPE_VALUES))
def test_event_all_types_accepted(iso_db: Session, event_type: str) -> None:
    """八类事件全部可落库且可读回。"""
    sid = add_student(iso_db)
    kp_id = add_kp(iso_db, code=f"KP_EVT_{event_type}")
    ev = record_learning_event(
        iso_db,
        student_id=sid,
        event_type=event_type,
        knowledge_point_id=kp_id,
        meta={"src": "test"},
    )
    assert ev["event_type"] == event_type
    assert ev["knowledge_point_id"] == str(kp_id)
    assert ev["meta"] == {"src": "test"}
    assert ev["created_at"] is not None

    row = iso_db.execute(
        select(StudentLearningEvent).where(
            StudentLearningEvent.id == uuid.UUID(ev["id"])
        )
    ).scalar_one()
    assert row.event_type == event_type


def test_event_rejects_bad_type_and_meta(iso_db: Session) -> None:
    """非法事件类型 400；meta 非 dict 400。"""
    sid = add_student(iso_db)
    with pytest.raises(ApiError) as exc:
        record_learning_event(iso_db, student_id=sid, event_type="CHEAT")
    assert exc.value.code == CODE_BAD_REQUEST
    assert "事件类型" in exc.value.msg

    with pytest.raises(ApiError) as exc:
        record_learning_event(
            iso_db, student_id=sid, event_type="ANSWER", meta="not-a-dict"
        )
    assert exc.value.code == CODE_BAD_REQUEST


# ==================================================================== ④ DNA-005 聚合（service 层）
def test_get_learning_dna_full_structure(iso_db: Session) -> None:
    """聚合返回画像 + 掌握汇总 + 最近事件三大块。

    DNA-003 联动后 ANSWER 事件会自动生成掌握行（practice_count=1）；
    手动写入的示例行改用第二个知识点点位，验证聚合能读出表内既有行。
    """
    sid = add_student(iso_db)
    kp_id = add_kp(iso_db)
    kp_manual = add_kp(iso_db, code="KP_DNA_MANUAL")
    upsert_student_profile(iso_db, student_id=sid, learning_goal="期末")
    record_learning_event(
        iso_db, student_id=sid, event_type="ANSWER", knowledge_point_id=kp_id
    )
    iso_db.add(
        StudentKnowledgeMastery(
            id=uuid.uuid4(),
            student_id=sid,
            knowledge_point_id=kp_manual,
            mastery=0.6,
            confidence=0.4,
            practice_count=3,
            correct_count=2,
            wrong_count=1,
        )
    )
    iso_db.commit()

    dna = get_learning_dna(iso_db, student_id=sid)
    assert dna["student_id"] == str(sid)
    assert dna["profile"]["learning_goal"] == "期末"
    assert len(dna["mastery_summary"]) == 2
    by_kp = {m["knowledge_point_id"]: m for m in dna["mastery_summary"]}
    m = by_kp[str(kp_manual)]
    assert m["mastery"] == pytest.approx(0.6)
    assert m["confidence"] == pytest.approx(0.4)
    assert m["practice_count"] == 3
    # 展示增强字段（DNA-005 → 家长端/学生端展示面契约）：
    # kp_name 关联知识库；level 复用 T04 同源 grade_level 口径；错误率直接给出
    assert m["kp_name"] == "知识点KP_DNA_MANUAL"
    assert m["level"] == 2  # mastery 0.6 且练习 3 次 → 基本掌握（与看板同规则）
    assert m["error_rate"] == pytest.approx(round(1 / 3, 4))
    assert m["updated_at"] is not None
    # DNA-003：ANSWER 事件驱动的掌握行只 +1 次 practice
    assert by_kp[str(kp_id)]["practice_count"] == 1
    assert len(dna["recent_events"]) == 1
    assert dna["recent_events"][0]["event_type"] == "ANSWER"


def test_get_learning_dna_empty_defaults(iso_db: Session) -> None:
    """无画像/无掌握/无事件学生：三块均为空默认值，不报错。"""
    sid = add_student(iso_db)
    dna = get_learning_dna(iso_db, student_id=sid)
    assert dna["student_id"] == str(sid)
    assert dna["profile"]["learning_goal"] is None
    assert dna["mastery_summary"] == []
    assert dna["recent_events"] == []


def test_recent_events_capped(iso_db: Session) -> None:
    """最近事件摘要不超过 RECENT_EVENTS_LIMIT。"""
    sid = add_student(iso_db)
    for i in range(RECENT_EVENTS_LIMIT + 3):
        record_learning_event(
            iso_db, student_id=sid, event_type="QUESTIONVIEW", meta={"i": i}
        )
    dna = get_learning_dna(iso_db, student_id=sid)
    assert len(dna["recent_events"]) == RECENT_EVENTS_LIMIT
    # 倒序：最新的在最前
    order = [ev["meta"]["i"] for ev in dna["recent_events"]]
    assert order == sorted(order, reverse=True), "事件未按时间倒序"


# ==================================================================== ⑤ DNA-005 HTTP 层契约（鉴权 + 完整结构）
def _register_dna_user(client, phone: str, role: str) -> Dict[str, Any]:
    """发码 → 注册 → 返回 data（与 test_family 同一套路）。"""
    r = client.post("/api/auth/code", json={"phone": phone})
    assert r.status_code == 200, r.text
    dev_code = r.json()["data"]["devCode"]
    r = client.post(
        "/api/auth/register",
        json={"phone": phone, "code": dev_code, "role": role},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _auth_token(raw: Dict[str, Any]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {raw['access']}"}


def _seed_dna_for_http(sid: uuid.UUID, *, kp_code: str = "KP_DNA_HTTP") -> uuid.UUID:
    """往共享测试库落一条 ANSWER+CORRECT 事件（形成掌握行），返回 kp id。"""
    s = SessionLocal()
    try:
        kp_id = add_kp(s, code=kp_code)
        record_learning_event(
            s, student_id=sid, event_type="ANSWER", knowledge_point_id=kp_id
        )
        record_learning_event(
            s, student_id=sid, event_type="CORRECT", knowledge_point_id=kp_id
        )
        s.commit()
        return kp_id
    finally:
        s.close()


def test_dna005_http_owner_sees_full_structure(client, new_phone) -> None:
    """学生本人可读聚合，返回画像+掌握（含展示增强字段）+事件三块。"""
    child = _register_dna_user(client, new_phone(), "student")
    sid = uuid.UUID(child["user"]["studentId"])
    kp_id = _seed_dna_for_http(sid)

    r = client.get(f"/api/students/{sid}/learning-dna", headers=_auth_token(child))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["student_id"] == str(sid)
    assert isinstance(data["profile"], dict)
    assert len(data["mastery_summary"]) == 1

    m = data["mastery_summary"][0]
    assert m["knowledge_point_id"] == str(kp_id)
    # 展示增强字段（DNA-005 → 家长端/学生端契约）：kp_name / level / error_rate / updated_at
    assert m["kp_name"] == "知识点KP_DNA_HTTP"
    assert m["mastery"] == pytest.approx(C.MASTERY_DELTA_CORRECT_BASE)
    assert m["level"] in (0, 1, 2, 3)
    assert m["error_rate"] == 0.0  # ANSWER+CORRECT：错 0 次
    assert m["practice_count"] == 1
    assert m["updated_at"] is not None
    assert m["last_practice_at"] is not None

    assert len(data["recent_events"]) == 2  # ANSWER + CORRECT


def test_dna005_http_authz_matrix(client, new_phone) -> None:
    """鉴权矩阵：未登录 401 / 他人学生 403 / 未绑定家长 403 / 绑定家长 200 / 不存在 404。"""
    child_a = _register_dna_user(client, new_phone(), "student")
    sid_a = uuid.UUID(child_a["user"]["studentId"])
    _seed_dna_for_http(sid_a, kp_code="KP_DNA_MATRIX")

    # 未登录 → 401
    r = client.get(f"/api/students/{sid_a}/learning-dna")
    assert r.status_code == 401

    # 他人学生（非本人）→ 403
    child_b = _register_dna_user(client, new_phone(), "student")
    r = client.get(f"/api/students/{sid_a}/learning-dna", headers=_auth_token(child_b))
    assert r.status_code == 403

    # 未绑定家长 → 403
    parent_free = _register_dna_user(client, new_phone(), "parent")
    r = client.get(
        f"/api/students/{sid_a}/learning-dna", headers=_auth_token(parent_free)
    )
    assert r.status_code == 403

    # 不存在学生 → 404（鉴权前先判存在性）
    r = client.get(
        f"/api/students/{uuid.uuid4()}/learning-dna",
        headers=_auth_token(child_a),
    )
    assert r.status_code == 404

    # 绑定家长 → 200
    parent_bound = _register_dna_user(client, new_phone(), "parent")
    r = client.post(
        "/api/family/bind",
        json={"phone": child_a["user"]["phone"], "relation": "father"},
        headers=_auth_token(parent_bound),
    )
    assert r.status_code == 200, r.text
    r = client.get(
        f"/api/students/{sid_a}/learning-dna", headers=_auth_token(parent_bound)
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["student_id"] == str(sid_a)

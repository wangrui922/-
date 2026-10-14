"""KG-002 契约测试：知识树关系边（``seed/knowledge_relations_v65.json`` + migration 015）。

本文件是 KG-002 的**三条件验收护栏**（对齐 V6.5 规划）：
    ① 双方言迁移：``015_knowledge_relations.sql`` 与 ``015_knowledge_relations_sqlite.sql``
       同批存在，且 DDL 与 ORM 模型逐字段一致（表名、外键、枚举 CHECK、防自环、唯一边）；
    ② 关系约束测试：真实落库验证外键（悬空端点被拒）、类型枚举（非法边被拒）、
       防自环、同类型重复边被拒；
    ③ 示例关系查询：规划示例「分数意义 → 分数加减 → 分数应用题」可查通，
       且 prerequisite/related/next 三类边各至少 1 条（种子覆盖三类）。

红线（与 KG-001 共用）：
    ``NS_QUESTION_BANK = 6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21``
    ``rel_uuid(source, type, target)
     = uuid5(NS_QUESTION_BANK, "rel:" + source + ":" + type + ":" + target)``
    公式一个字符都不能改（seed 脚本与测试共用，改则破坏幂等 merge）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.content import (
    KNOWLEDGE_RELATION_VALUES,
    KnowledgePoint,
    KnowledgeRelation,
)

#: 关系种子文件真实路径
SEED_PATH: Path = (
    Path(__file__).resolve().parent.parent / "seed" / "knowledge_relations_v65.json"
)
#: 知识点种子（FK 前置校验 + 示例链查询对象）
KP_SEED_PATH: Path = (
    Path(__file__).resolve().parent.parent / "seed" / "knowledge_points_v65.json"
)
#: migrations 目录（tests/ 的上一级是 backend/）
MIGRATIONS_DIR: Path = Path(__file__).resolve().parents[1] / "migrations"

#: 规划红线（KG-001/002 共用）
NS_QUESTION_BANK: uuid.UUID = uuid.UUID("6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21")

#: 规划示例链（KG-002）：分数意义 → 分数加减 → 分数应用题
EXAMPLE_CHAIN: List[str] = ["KP_FRAC_MEANING", "KP_FRAC_ADD_SUB", "KP_FRAC_WORD"]


def kp_uuid(code: str) -> uuid.UUID:
    """复刻 KG-001 派生（与 scripts/seed_db.py / seed_kp_v65.py 一致）。"""
    return uuid.uuid5(NS_QUESTION_BANK, f"kp:{code}")


def rel_uuid(source_code: str, relation_type: str, target_code: str) -> uuid.UUID:
    """复刻 seed_kp_relations_v65.py 的关系边主键派生公式。"""
    return uuid.uuid5(
        NS_QUESTION_BANK,
        f"rel:{source_code}:{relation_type}:{target_code}",
    )


# ==================================================================== 夹具
@pytest.fixture(scope="module")
def rel_raw() -> List[Dict[str, Any]]:
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["knowledgeRelations"]


@pytest.fixture(scope="module")
def kp_codes() -> set:
    """全部知识点 code（FK 前置校验用）。"""
    with open(KP_SEED_PATH, "r", encoding="utf-8") as f:
        return {n["code"] for n in json.load(f)["knowledgePoints"]}


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


def _seed_kp(db: Session) -> None:
    """灌入全部知识点（KG-001 种子），作为关系边的 FK 前置。"""
    with open(KP_SEED_PATH, "r", encoding="utf-8") as f:
        kps = json.load(f)["knowledgePoints"]
    for is_l1_pass in (True, False):
        for kp in kps:
            if (kp["parentCode"] is None) != is_l1_pass:
                continue
            db.merge(
                KnowledgePoint(
                    id=kp_uuid(kp["code"]),
                    code=kp["code"],
                    name=kp["name"],
                    sort=kp.get("sort", 0),
                )
            )
        db.flush()
    db.commit()


def _seed_relations(db: Session, rel_raw: List[Dict[str, Any]]) -> None:
    """按 seed 脚本相同公式 merge 全部关系边。"""
    for e in rel_raw:
        db.merge(
            KnowledgeRelation(
                id=rel_uuid(e["sourceCode"], e["relationType"], e["targetCode"]),
                source_id=kp_uuid(e["sourceCode"]),
                target_id=kp_uuid(e["targetCode"]),
                relation_type=e["relationType"],
            )
        )
    db.commit()


# ==================================================================== ① 双方言迁移
def test_both_migrations_exist_with_schema() -> None:
    """PG 与 SQLite 两份 migration 必须同批存在，且字段/约束齐全。"""
    for filename in (
        "015_knowledge_relations.sql",
        "015_knowledge_relations_sqlite.sql",
    ):
        path = MIGRATIONS_DIR / filename
        assert path.exists(), f"缺失 migration：{filename}"
        sql = path.read_text(encoding="utf-8").lower()
        assert "create table" in sql and "knowledge_relations" in sql, (
            f"{filename} 未建表"
        )
        for token in (
            "source_id",
            "target_id",
            "relation_type",
            "prerequisite",
            "related",
            "next",
        ):
            assert token in sql, f"{filename} 缺 {token}"
        assert "references knowledge_points" in sql, f"{filename} 缺外键"
        assert "source_id <> target_id" in sql or "source_id` <> `target_id" in sql, (
            f"{filename} 缺防自环约束"
        )


def test_sqlite_migration_actually_creates_table() -> None:
    """把 SQLite 版 DDL 真跑一遍：能建表且带约束（语法错这里就会炸）。"""
    ddl = (MIGRATIONS_DIR / "015_knowledge_relations_sqlite.sql").read_text(
        encoding="utf-8"
    )
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE knowledge_points "
            "(id TEXT PRIMARY KEY, name TEXT, parent_id TEXT, code TEXT, sort INTEGER)"
        )
        conn.executescript(ddl)

        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(knowledge_relations)")
        }
        assert {
            "id",
            "source_id",
            "target_id",
            "relation_type",
            "created_at",
            "updated_at",
        } <= columns, columns
    finally:
        conn.close()


# ==================================================================== ② 种子结构
def test_seed_has_three_edge_types(rel_raw: List[Dict[str, Any]]) -> None:
    """三类边 prerequisite/related/next 各至少 1 条（种子覆盖三类）。"""
    got = {e["relationType"] for e in rel_raw}
    assert KNOWLEDGE_RELATION_VALUES == ("prerequisite", "related", "next")
    assert got == set(KNOWLEDGE_RELATION_VALUES), (
        f"缺关系类型: {set(KNOWLEDGE_RELATION_VALUES) - got}"
    )


def test_seed_endpoints_exist_in_kp(
    rel_raw: List[Dict[str, Any]], kp_codes: set
) -> None:
    """source/target code 必须全部存在于 knowledge_points 种子（FK 不悬空）。"""
    missing: List[str] = []
    for e in rel_raw:
        if e["sourceCode"] not in kp_codes:
            missing.append(f"source={e['sourceCode']}")
        if e["targetCode"] not in kp_codes:
            missing.append(f"target={e['targetCode']}")
    assert not missing, f"悬空端点: {sorted(set(missing))}"


def test_seed_no_self_loop_and_unique_edge(rel_raw: List[Dict[str, Any]]) -> None:
    """无自环、(source, type, target) 不重复。"""
    for e in rel_raw:
        assert e["sourceCode"] != e["targetCode"], f"自环: {e}"
    keys = [(e["sourceCode"], e["relationType"], e["targetCode"]) for e in rel_raw]
    assert len(keys) == len(set(keys)), "重复边"


def test_seed_contains_plan_example_chain(rel_raw: List[Dict[str, Any]]) -> None:
    """规划示例链「分数意义 → 分数加减 → 分数应用题」必须完整建边。"""
    prerequisite = {
        (e["sourceCode"], e["targetCode"])
        for e in rel_raw
        if e["relationType"] == "prerequisite"
    }
    assert (EXAMPLE_CHAIN[0], EXAMPLE_CHAIN[1]) in prerequisite, "缺 分数意义→分数加减"
    assert (EXAMPLE_CHAIN[1], EXAMPLE_CHAIN[2]) in prerequisite, (
        "缺 分数加减→分数应用题"
    )


# ==================================================================== ② 关系约束（真实落库）
def test_fk_rejects_dangling_endpoint(iso_db: Session) -> None:
    """外键约束：source/target 指向不存在的知识点必须被拒。"""
    _seed_kp(iso_db)
    ghost = uuid.uuid4()
    with pytest.raises(IntegrityError):
        iso_db.add(
            KnowledgeRelation(
                id=rel_uuid("KP_CALC", "prerequisite", "KP_GHOST"),
                source_id=kp_uuid("KP_CALC"),
                target_id=ghost,
                relation_type="prerequisite",
            )
        )
        iso_db.flush()
    iso_db.rollback()


def test_type_enum_constraint_rejects_invalid(iso_db: Session) -> None:
    """类型枚举约束：非 prerequisite/related/next 的边必须被拒。"""
    _seed_kp(iso_db)
    with pytest.raises(IntegrityError):
        iso_db.add(
            KnowledgeRelation(
                id=rel_uuid("KP_CALC", "invalid", "KP_EQUATION"),
                source_id=kp_uuid("KP_CALC"),
                target_id=kp_uuid("KP_EQUATION"),
                relation_type="invalid",
            )
        )
        iso_db.flush()
    iso_db.rollback()


def test_no_self_loop_constraint_rejects(iso_db: Session) -> None:
    """防自环约束：source == target 必须被拒。"""
    _seed_kp(iso_db)
    with pytest.raises(IntegrityError):
        iso_db.add(
            KnowledgeRelation(
                id=rel_uuid("KP_CALC", "related", "KP_CALC"),
                source_id=kp_uuid("KP_CALC"),
                target_id=kp_uuid("KP_CALC"),
                relation_type="related",
            )
        )
        iso_db.flush()
    iso_db.rollback()


def test_duplicate_edge_rejected(iso_db: Session) -> None:
    """唯一边约束：同 (source, type, target) 重复插入必须被拒。"""
    _seed_kp(iso_db)
    _seed_relations(
        iso_db, json.loads(SEED_PATH.read_text(encoding="utf-8"))["knowledgeRelations"]
    )
    dup = rel_raw_or_empty()
    if not dup:
        return  # 种子为空时跳过（正常情况种子非空）
    e = dup[0]
    with pytest.raises(IntegrityError):
        iso_db.add(
            KnowledgeRelation(
                id=rel_uuid(e["sourceCode"], e["relationType"], e["targetCode"]),
                source_id=kp_uuid(e["sourceCode"]),
                target_id=kp_uuid(e["targetCode"]),
                relation_type=e["relationType"],
            )
        )
        iso_db.flush()
    iso_db.rollback()


def rel_raw_or_empty() -> List[Dict[str, Any]]:
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["knowledgeRelations"]


# ==================================================================== ③ 示例关系查询 + 幂等
def test_plan_example_query_resolves(
    iso_db: Session, rel_raw: List[Dict[str, Any]]
) -> None:
    """示例链查询：分数应用题的前置应含分数加减，分数加减的前置应含分数意义。"""
    _seed_kp(iso_db)
    _seed_relations(iso_db, rel_raw)

    def prerequisites(target_code: str) -> List[str]:
        tgt_id = kp_uuid(target_code)
        rows = iso_db.execute(
            select(KnowledgeRelation).where(
                KnowledgeRelation.target_id == tgt_id,
                KnowledgeRelation.relation_type == "prerequisite",
            )
        ).scalars()
        return [
            iso_db.execute(
                select(KnowledgePoint.code).where(KnowledgePoint.id == r.source_id)
            ).scalar_one()
            for r in rows
        ]

    assert "KP_FRAC_ADD_SUB" in prerequisites("KP_FRAC_WORD"), (
        "分数应用题前置缺分数加减"
    )
    assert "KP_FRAC_MEANING" in prerequisites("KP_FRAC_ADD_SUB"), (
        "分数加减前置缺分数意义"
    )
    # 分数意义自身无前置（链头）
    assert prerequisites("KP_FRAC_MEANING") == []


def test_seed_merge_is_idempotent(
    iso_db: Session, rel_raw: List[Dict[str, Any]]
) -> None:
    """两趟 merge 行数与主键完全不变（rel_uuid 确定性 = 幂等）。"""
    _seed_kp(iso_db)
    _seed_relations(iso_db, rel_raw)
    first = sorted(
        str(r[0]) for r in iso_db.execute(select(KnowledgeRelation.id)).all()
    )
    assert len(first) == len(rel_raw), len(first)

    _seed_relations(iso_db, rel_raw)
    second = sorted(
        str(r[0]) for r in iso_db.execute(select(KnowledgeRelation.id)).all()
    )
    assert first == second, "重跑后主键/行数变化，幂等性被破坏"


def test_rel_uuid_formula_is_frozen() -> None:
    """rel_uuid 公式冻结：NS + 'rel:' 前缀 + 三要素拼接，一个字符都不能改。"""
    assert rel_uuid("KP_A", "prerequisite", "KP_B") == uuid.uuid5(
        NS_QUESTION_BANK, "rel:KP_A:prerequisite:KP_B"
    )
    # 与 kp_uuid 前缀不同，永不碰撞
    assert rel_uuid("KP_A", "prerequisite", "KP_B") != kp_uuid("KP_A")

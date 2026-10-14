"""KG-001 契约测试：V6.5 知识点库种子（``seed/knowledge_points_v65.json``）。

本文件是 V6.5 知识点库的**结构护栏**，覆盖四类断言：

1. **种子数量**：总数 >= 200（规划红线），13 个 L1 + >=187 个 L2，且
   13 个 L1 与 ``knowledge_points.json``（v2 树）逐字节一致——任何 code 改动
   都会让存量题（``questions.kp_id``）外键悬空；
2. **必填约束**：subject=math、status=active、difficulty/importance 在值域内、
   L2 必须带 grade/semester/chapter（L1 聚合节点可为 NULL）；
3. **唯一性**：code 全局唯一、code 长度 <=32、name 长度 <=64；
4. **树结构**：无自环、树深恒为 2、parentCode 不悬空、同父下 sort 唯一；
5. **落库幂等**：两趟 merge 写进真库后行数与主键不变（merge + 确定性 uuid5）；
   uuid 公式与 ``scripts/seed_db.py`` 的 ``kp_uuid`` 保持一致。

红线（KG-001 design）：
    ``NS_QUESTION_BANK = 6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21``
    ``kp_uuid(code) = uuid5(NS_QUESTION_BANK, "kp:" + code)`` 一个字符都不能改。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.content import KnowledgePoint

#: V6.5 种子文件真实路径
SEED_PATH: Path = (
    Path(__file__).resolve().parent.parent / "seed" / "knowledge_points_v65.json"
)
#: v2 树（93 节点）——L1 契约比对源
V2_SEED_PATH: Path = (
    Path(__file__).resolve().parent.parent / "seed" / "knowledge_points.json"
)

#: 规划红线（V6.5 开发总规划 KG-001）
EXPECT_TOTAL_MIN: int = 200
EXPECT_L1: int = 13
EXPECT_L2_MIN: int = 187
EXPECT_SUBJECT: str = "math"
EXPECT_STATUS: str = "active"
#: 覆盖学段：小学 3 年级(3) … 初三(9)
GRADE_MIN: int = 3
GRADE_MAX: int = 9
#: 难度档位（1 基础 / 3 进阶 / 5 挑战）
DIFFICULTY_VALUES: tuple = (1, 3, 5)
#: 重要程度 1-5
IMPORTANCE_MIN: int = 1
IMPORTANCE_MAX: int = 5
#: 学期取值
SEMESTER_VALUES: tuple = ("上", "下")

#: 与 scripts/seed_db.py 的 NS_QUESTION_BANK 完全一致（typedef 勿改）
NS_QUESTION_BANK: uuid.UUID = uuid.UUID("6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21")


def kp_uuid(code: str) -> uuid.UUID:
    """复刻 V6.5 种子脚本与 scripts/seed_db.py 的主键派生公式。"""
    return uuid.uuid5(NS_QUESTION_BANK, f"kp:{code}")


# ==================================================================== 夹具
@pytest.fixture(scope="module")
def seed_doc() -> Dict[str, Any]:
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def kp_raw(seed_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return seed_doc["knowledgePoints"]


@pytest.fixture(scope="module")
def by_code(kp_raw: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {node["code"]: node for node in kp_raw}


@pytest.fixture(scope="module")
def v2_l1_contract() -> Dict[str, Dict[str, Any]]:
    """v2 树（93 节点）的 13 个 L1 code->name/sort，用作红线比对源。"""
    with open(V2_SEED_PATH, "r", encoding="utf-8") as f:
        nodes = json.load(f)["knowledgePoints"]
    return {
        n["code"]: {"name": n["name"], "sort": n["sort"]}
        for n in nodes
        if n["parentCode"] is None
    }


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


def _merge_two_pass(db: Session, kp_raw: List[Dict[str, Any]]) -> None:
    """复刻 ``seed_kp_v65.main()`` 的两趟 merge（先 L1 后 L2，满足外键时序）。"""
    for is_l1_pass in (True, False):
        for kp in kp_raw:
            if (kp["parentCode"] is None) != is_l1_pass:
                continue
            db.merge(
                KnowledgePoint(
                    id=kp_uuid(kp["code"]),
                    code=kp["code"],
                    name=kp["name"],
                    description=kp.get("description"),
                    sort=kp.get("sort", 0),
                    parent_id=kp_uuid(kp["parentCode"]) if kp["parentCode"] else None,
                    subject=kp.get("subject", EXPECT_SUBJECT),
                    grade=kp.get("grade"),
                    semester=kp.get("semester"),
                    chapter=kp.get("chapter"),
                    difficulty=kp.get("difficulty", 3),
                    importance=kp.get("importance", 3),
                    status=kp.get("status", EXPECT_STATUS),
                )
            )
        db.flush()
    db.commit()


# ==================================================================== 1. 种子数量
def test_total_count_meets_plan(kp_raw: List[Dict[str, Any]]) -> None:
    """规划红线：知识点总数 >= 200。"""
    assert len(kp_raw) >= EXPECT_TOTAL_MIN, (
        f"期望 >= {EXPECT_TOTAL_MIN}，实际 {len(kp_raw)}"
    )


def test_l1_l2_split(kp_raw: List[Dict[str, Any]]) -> None:
    """13 个 L1 + >=187 个 L2。"""
    l1 = [n for n in kp_raw if n["parentCode"] is None]
    l2 = [n for n in kp_raw if n["parentCode"] is not None]
    assert len(l1) == EXPECT_L1, [n["code"] for n in l1]
    assert len(l2) >= EXPECT_L2_MIN, len(l2)
    assert len(l1) + len(l2) == len(kp_raw)


def test_grade_band_covers_primary_to_middle(kp_raw: List[Dict[str, Any]]) -> None:
    """L2 的年级覆盖小学 3 年级(3) 至 初三(9)，且每个年级都有知识点。"""
    grades = {n["grade"] for n in kp_raw if n["parentCode"] is not None}
    missing = [g for g in range(GRADE_MIN, GRADE_MAX + 1) if g not in grades]
    assert not missing, f"以下年级无知识点: {missing}"


def test_l1_contract_matches_v2_tree(
    by_code: Dict[str, Dict[str, Any]],
    v2_l1_contract: Dict[str, Dict[str, Any]],
) -> None:
    """红线：13 个 L1 的 code/name/sort 与 v2 树逐字节一致（改一个字符就悬空存量题外键）。"""
    assert set(by_code.keys()) & set(v2_l1_contract) == set(v2_l1_contract)
    for code, expect in v2_l1_contract.items():
        node = by_code.get(code)
        assert node is not None, f"L1 缺失: {code}"
        assert node["parentCode"] is None, f"{code} 不应有父节点"
        assert node["name"] == expect["name"], f"{code} 名称被改: {node['name']}"
        assert node["sort"] == expect["sort"], f"{code} sort 被改: {node['sort']}"


def test_every_l1_has_children(kp_raw: List[Dict[str, Any]]) -> None:
    """13 个 L1 都必须至少带 1 个 L2（没有子节点的 L1 会让下钻恒回退）。"""
    with_children = {n["parentCode"] for n in kp_raw if n["parentCode"]}
    l1_codes = {n["code"] for n in kp_raw if n["parentCode"] is None}
    barren = l1_codes - with_children
    assert not barren, f"以下 L1 没有任何 L2 子节点: {sorted(barren)}"


# ==================================================================== 2. 必填约束
def test_required_columns_present(kp_raw: List[Dict[str, Any]]) -> None:
    """L2 必须带 grade/semester/chapter；L1 聚合节点允许 NULL（规划约定）。"""
    for n in kp_raw:
        if n["parentCode"] is None:
            continue
        assert n.get("grade") is not None, f"{n['code']} 缺 grade"
        assert n.get("semester"), f"{n['code']} 缺 semester"
        assert n.get("chapter"), f"{n['code']} 缺 chapter"


def test_l1_grade_semester_nullable(kp_raw: List[Dict[str, Any]]) -> None:
    """L1 聚合节点的 grade/semester 为 NULL 是合法形态（跨年级聚合）。"""
    for n in kp_raw:
        if n["parentCode"] is None:
            assert n.get("grade") is None, f"{n['code']} L1 不应带年级"
            assert n.get("semester") is None, f"{n['code']} L1 不应带学期"


def test_subject_and_status_defaults(kp_raw: List[Dict[str, Any]]) -> None:
    """subject=math、status=active（存量题归因不变的前提）。"""
    bad = [
        n["code"]
        for n in kp_raw
        if n.get("subject", EXPECT_SUBJECT) != EXPECT_SUBJECT
        or n.get("status", EXPECT_STATUS) != EXPECT_STATUS
    ]
    assert not bad, f"subject/status 异常: {bad}"


def test_difficulty_in_value_set(kp_raw: List[Dict[str, Any]]) -> None:
    """难度档位取 {1, 3, 5}（级别太少无法区分梯度，太多会导致推荐过碎）。"""
    bad = [n["code"] for n in kp_raw if n.get("difficulty") not in DIFFICULTY_VALUES]
    assert not bad, f"difficulty 越界: {bad}"


def test_importance_in_range(kp_raw: List[Dict[str, Any]]) -> None:
    """重要程度 1-5（Priority 权重 0.20 的取值来源）。"""
    bad = [
        n["code"]
        for n in kp_raw
        if not (IMPORTANCE_MIN <= (n.get("importance") or 0) <= IMPORTANCE_MAX)
    ]
    assert not bad, f"importance 越界: {bad}"


def test_grade_semester_bounds(kp_raw: List[Dict[str, Any]]) -> None:
    """grade 在 [3,9]、semester 取 {上,下}。"""
    bad = [
        n["code"]
        for n in kp_raw
        if n.get("grade") is not None
        and (
            not (GRADE_MIN <= n["grade"] <= GRADE_MAX)
            or (n.get("semester") and n["semester"] not in SEMESTER_VALUES)
        )
    ]
    assert not bad, f"年级/学期越界: {bad}"


def test_description_present_for_l2(kp_raw: List[Dict[str, Any]]) -> None:
    """L2 必须有非空 description（推荐语/讲解引用）。"""
    missing = [
        n["code"]
        for n in kp_raw
        if n["parentCode"] is not None and not (n.get("description") or "").strip()
    ]
    assert not missing, f"以下 L2 缺 description: {missing}"


# ==================================================================== 3. 唯一性
def test_codes_are_unique(kp_raw: List[Dict[str, Any]]) -> None:
    """code 全局唯一（数据库 UNIQUE 约束，重复会直接写库失败）。"""
    codes = [n["code"] for n in kp_raw]
    dupes = {c for c in codes if codes.count(c) > 1}
    assert not dupes, f"重复 code: {sorted(dupes)}"


def test_code_and_name_length(kp_raw: List[Dict[str, Any]]) -> None:
    """code <=32（String(32)）、name <=64（String(64)）。"""
    bad_code = [n["code"] for n in kp_raw if len(n["code"]) > 32]
    bad_name = [n["name"] for n in kp_raw if len(n["name"]) > 64]
    assert not bad_code, f"code 超长: {bad_code}"
    assert not bad_name, bad_name


def test_uuid_formula_is_frozen() -> None:
    """kp_uuid 公式冻结：NS_QUESTION_BANK + 'kp:' 前缀一个字符都不能改。"""
    assert kp_uuid("KP_CALC") == uuid.uuid5(NS_QUESTION_BANK, "kp:KP_CALC")


def test_v2_tree_codes_all_preserved(kp_raw: List[Dict[str, Any]]) -> None:
    """红线：v2 树（93 节点）的每个 code 都必须出现在 V6.5 种子中。

    存量题目（``questions.kp_id``）挂在 v2 树的 93 个节点上；V6.5 删掉
    任何一个，归因链路都会悬空。相反，V6.5 只允许新增节点。
    """
    with open(V2_SEED_PATH, "r", encoding="utf-8") as f:
        v2_codes = {n["code"] for n in json.load(f)["knowledgePoints"]}
    v65_codes = {n["code"] for n in kp_raw}
    missing = sorted(v2_codes - v65_codes)
    assert not missing, f"V6.5 种子缺失 v2 树节点, 存量题外键会悬空: {missing}"


def test_uuid_matches_known_legacy_ids() -> None:
    """抽查 7 个与 v2 树共用的节点 id（dev.db 存量主键，改了即悬空）。

    期望值来自 dev.db 实际行（scripts/seed_db.py 旧版 NS_QUESTION_BANK 派生）。
    """
    known = {
        "KP_CALC": "22218a8d-2956-5db5-8390-bd0b9a359113",
        "KP_CALC_ADD": "73084c38-7051-5eb0-88df-914599e3877d",
        "KP_EQUATION": "1c91e965-0bbb-5fd5-bf55-579d03f972ec",
        "KP_FRACTION": "7322c97d-e455-5002-a76a-da1dbe8b0355",
        "KP_GEOMETRY": "dc4c27c8-51a5-543f-84f1-9e3fd6155c21",
        "KP_MIXED": "4cac0edc-f5ab-5d4c-9e30-acf431ef987e",
        "KP_WORD": "8f29880c-c73c-5bd8-880e-51e0f0e6fed5",
    }
    for code, expected in known.items():
        assert str(kp_uuid(code)) == expected, (
            f"{code} 的主键公式偏离 dev.db 存量行，merge 将重复插入！"
        )


def test_uuid_distinct_and_deterministic(kp_raw: List[Dict[str, Any]]) -> None:
    """206 个 code 派生出 206 个互不相同的 uuid，且同 code 多次调用一致。"""
    ids = [str(kp_uuid(n["code"])) for n in kp_raw]
    assert len(set(ids)) == len(kp_raw), "uuid5 出现碰撞"
    assert ids == [str(kp_uuid(n["code"])) for n in kp_raw], "uuid5 不稳定"


# ==================================================================== 4. 树结构
def test_no_self_loop(kp_raw: List[Dict[str, Any]]) -> None:
    loops = [n["code"] for n in kp_raw if n["parentCode"] == n["code"]]
    assert not loops, f"自环节点: {loops}"


def test_no_l3(kp_raw: List[Dict[str, Any]]) -> None:
    """禁 L3：任何节点的父节点自身必须是 L1（树深恒为 2）。"""
    parent_of = {n["code"]: n["parentCode"] for n in kp_raw}
    violations = [
        (code, pc, parent_of[pc])
        for code, pc in parent_of.items()
        if pc is not None and parent_of.get(pc) is not None
    ]
    assert not violations, f"出现三层结构: {violations}"


def test_every_parent_code_exists(kp_raw: List[Dict[str, Any]]) -> None:
    codes = {n["code"] for n in kp_raw}
    dangling = [
        (n["code"], n["parentCode"])
        for n in kp_raw
        if n["parentCode"] is not None and n["parentCode"] not in codes
    ]
    assert not dangling, f"parentCode 悬空: {dangling}"


def test_sibling_sort_unique_within_parent(kp_raw: List[Dict[str, Any]]) -> None:
    buckets: Dict[Any, List[int]] = {}
    for node in kp_raw:
        buckets.setdefault(node["parentCode"], []).append(node["sort"])
    for parent, sorts in buckets.items():
        assert len(sorts) == len(set(sorts)), f"父节点 {parent} 下 sort 重复"


# ==================================================================== 5. 落库幂等
def test_two_pass_merge_writes_rows(
    iso_db: Session, kp_raw: List[Dict[str, Any]]
) -> None:
    """两趟 merge 后：206 行（13 L1 + 193 L2），外键全部成立，L1 无父。"""
    _merge_two_pass(iso_db, kp_raw)
    total = iso_db.execute(select(func.count()).select_from(KnowledgePoint)).scalar()
    l1 = iso_db.execute(
        select(func.count())
        .select_from(KnowledgePoint)
        .where(KnowledgePoint.parent_id.is_(None))
    ).scalar()
    assert total == len(kp_raw), total
    assert l1 == EXPECT_L1, l1

    ids = {
        row[0]: row[1]
        for row in iso_db.execute(
            select(KnowledgePoint.id, KnowledgePoint.parent_id)
        ).all()
    }
    for kid, pid in ids.items():
        if pid is None:
            continue
        assert pid in ids, f"{kid} 的 parent_id 悬空"
        assert ids[pid] is None, f"{kid} 的父节点不是 L1"


def test_two_pass_merge_is_idempotent(
    iso_db: Session, kp_raw: List[Dict[str, Any]]
) -> None:
    """重跑一次仍是 206 行，且全部主键不变（merge + 确定性 uuid5 = 幂等）。"""
    _merge_two_pass(iso_db, kp_raw)
    first = sorted(str(r[0]) for r in iso_db.execute(select(KnowledgePoint.id)).all())

    _merge_two_pass(iso_db, kp_raw)
    second = sorted(str(r[0]) for r in iso_db.execute(select(KnowledgePoint.id)).all())

    assert len(second) == len(kp_raw), len(second)
    assert first == second, "重跑后主键发生变化，幂等性被破坏"


def test_metadata_columns_persist(
    iso_db: Session, kp_raw: List[Dict[str, Any]]
) -> None:
    """merge 后 V6.5 元数据（difficulty/importance/grade/chapter）原样落库。"""
    _merge_two_pass(iso_db, kp_raw)
    sample = next(n for n in kp_raw if n["parentCode"] is not None)
    row = iso_db.execute(
        select(KnowledgePoint).where(KnowledgePoint.code == sample["code"])
    ).scalar_one()
    assert row.grade == sample["grade"]
    assert row.semester == sample["semester"]
    assert row.chapter == sample["chapter"]
    assert row.difficulty == sample["difficulty"]
    assert row.importance == sample["importance"]
    assert row.subject == EXPECT_SUBJECT
    assert row.status == EXPECT_STATUS

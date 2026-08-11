"""T01 契约测试：知识树种子数据 v2 + 加载器（``seed/knowledge_points.json`` / ``seed_db.py``）。

本文件是 KF 知识树 L2 的**结构护栏**，覆盖三类断言：

1. **种子文件契约**：93 节点 = 13 L1 + 80 L2、无 L3、无自环、code 长度 ≤32、
   13 个 L1 的 code/name/sort 与 v1 逐字节一致（改一个字符就有 2174 道题外键悬空）；
2. **校验函数**：``validate_kp_tree()`` 对三类畸形树（父不存在 / 自环 / 三层）必须 fail-fast；
3. **加载器幂等**：两趟 merge 写进真库后 93 行、外键成立，重跑一次仍是 93 行且 id 不变。

红线（design §K1）：
    ``kp_uuid(code) = uuid5(NAMESPACE_DNS, "aicoach:kp:" + code)`` 一个字符都不能改。
    本文件把 13 个 L1 的 uuid 硬编码成期望值，公式被改动会立刻炸测试。
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
from app.scripts.seed_db import kp_uuid, validate_kp_tree

#: 种子文件真实路径（design §K7：活动加载器是 app/scripts/seed_db.py，读 backend/seed/）
SEED_PATH: Path = Path(__file__).resolve().parent.parent / "seed" / "knowledge_points.json"

#: 期望的节点总数 / L1 数 / L2 数（PRD §3 拍板口径）
EXPECT_TOTAL: int = 93
EXPECT_L1: int = 13
EXPECT_L2: int = 80

#: 13 个 L1 的 code → (name, sort)，**与 v1 逐字节一致**，任何改动都会让存量题外键悬空
L1_CONTRACT: Dict[str, Any] = {
    "KP_CALC": ("计算", 10),
    "KP_EQUATION": ("方程", 20),
    "KP_WORD": ("应用题", 30),
    "KP_FRACTION": ("分数", 40),
    "KP_DECIMAL": ("小数", 50),
    "KP_PERCENT": ("百分数", 60),
    "KP_RATIO": ("比例", 70),
    "KP_GEOMETRY": ("几何", 80),
    "KP_SHAPE": ("图形", 90),
    "KP_STATISTICS": ("统计", 100),
    "KP_UNIT": ("单位换算", 110),
    "KP_NUMBER_THEORY": ("数论", 120),
    "KP_MIXED": ("综合", 900),
}

#: 每个 L1 期望的 L2 子节点数（合计 80）
EXPECT_CHILDREN: Dict[str, int] = {
    "KP_CALC": 7,
    "KP_EQUATION": 7,
    "KP_WORD": 7,
    "KP_FRACTION": 6,
    "KP_DECIMAL": 5,
    "KP_PERCENT": 5,
    "KP_RATIO": 5,
    "KP_GEOMETRY": 7,
    "KP_SHAPE": 8,
    "KP_STATISTICS": 5,
    "KP_UNIT": 6,
    "KP_NUMBER_THEORY": 7,
    "KP_MIXED": 5,
}


# ==================================================================== 夹具
@pytest.fixture(scope="module")
def seed_doc() -> Dict[str, Any]:
    """整份种子文件（含顶层 version）。"""
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def kp_raw(seed_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """种子文件里的 ``knowledgePoints`` 数组。"""
    return seed_doc["knowledgePoints"]


@pytest.fixture(scope="module")
def by_code(kp_raw: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """``{code: node}`` 索引。"""
    return {node["code"]: node for node in kp_raw}


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
    """复刻 ``seed_db.main()`` 的两趟 merge（先 L1 后 L2，满足外键时序）。"""
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
                )
            )
        db.flush()
    db.commit()


# ==================================================================== 1. 种子文件契约
def test_seed_version_is_v2(seed_doc: Dict[str, Any]) -> None:
    """种子文件必须声明 ``version = 2.0.0``（运维靠它判断库里是不是新树）。"""
    assert seed_doc.get("version") == "2.0.0", seed_doc.get("version")


def test_total_node_count_is_93(kp_raw: List[Dict[str, Any]]) -> None:
    """节点总数恒为 93。"""
    assert len(kp_raw) == EXPECT_TOTAL, f"期望 {EXPECT_TOTAL} 个节点，实际 {len(kp_raw)}"


def test_l1_and_l2_split_is_13_80(kp_raw: List[Dict[str, Any]]) -> None:
    """13 个 L1（``parentCode`` 为 None）+ 80 个 L2。"""
    l1 = [n for n in kp_raw if n["parentCode"] is None]
    l2 = [n for n in kp_raw if n["parentCode"] is not None]
    assert len(l1) == EXPECT_L1, [n["code"] for n in l1]
    assert len(l2) == EXPECT_L2, len(l2)


def test_codes_are_unique(kp_raw: List[Dict[str, Any]]) -> None:
    """code 全局唯一（数据库里是 UNIQUE 约束，重复会直接写库失败）。"""
    codes = [n["code"] for n in kp_raw]
    dupes = {c for c in codes if codes.count(c) > 1}
    assert not dupes, f"重复 code: {sorted(dupes)}"


def test_code_length_fits_column(kp_raw: List[Dict[str, Any]]) -> None:
    """code 长度 ≤32（``knowledge_points.code`` 是 ``String(32)``）。"""
    too_long = [n["code"] for n in kp_raw if len(n["code"]) > 32]
    assert not too_long, f"code 超过 32 字符: {too_long}"


def test_name_length_fits_column(kp_raw: List[Dict[str, Any]]) -> None:
    """name 长度 ≤64（``String(64)``）。"""
    too_long = [n["code"] for n in kp_raw if len(n["name"]) > 64]
    assert not too_long, too_long


def test_no_self_loop(kp_raw: List[Dict[str, Any]]) -> None:
    """禁自环：``parentCode != code``。"""
    loops = [n["code"] for n in kp_raw if n["parentCode"] == n["code"]]
    assert not loops, f"自环节点: {loops}"


def test_no_l3_tree_depth_is_exactly_two(kp_raw: List[Dict[str, Any]]) -> None:
    """禁 L3：任何节点的父节点自身必须是 L1（树深恒为 2，读侧才敢不用递归）。"""
    parent_of = {n["code"]: n["parentCode"] for n in kp_raw}
    violations = [
        (code, pc, parent_of[pc])
        for code, pc in parent_of.items()
        if pc is not None and parent_of.get(pc) is not None
    ]
    assert not violations, f"出现三层结构: {violations}"


def test_every_parent_code_exists(kp_raw: List[Dict[str, Any]]) -> None:
    """``parentCode`` 必须指向本文件里真实存在的 code（否则外键写不进去）。"""
    codes = {n["code"] for n in kp_raw}
    dangling = [
        (n["code"], n["parentCode"])
        for n in kp_raw
        if n["parentCode"] is not None and n["parentCode"] not in codes
    ]
    assert not dangling, f"parentCode 悬空: {dangling}"


def test_l1_contract_is_byte_identical_to_v1(by_code: Dict[str, Dict[str, Any]]) -> None:
    """**红线**：13 个 L1 的 code / name / sort 与 v1 完全一致。

    改动 code 会让 ``kp_uuid`` 变化 → 2174 道存量题的 ``kp_id`` 集体悬空；
    改动 sort 会让 ``_load_kp_list`` 的顺序变化 → 方舟 prompt 与归因结果漂移。
    """
    for code, (name, sort) in L1_CONTRACT.items():
        node = by_code.get(code)
        assert node is not None, f"L1 缺失: {code}"
        assert node["parentCode"] is None, f"{code} 不应有父节点"
        assert node["name"] == name, f"{code} 名称被改: {node['name']} != {name}"
        assert node["sort"] == sort, f"{code} sort 被改: {node['sort']} != {sort}"


def test_children_count_per_l1(kp_raw: List[Dict[str, Any]]) -> None:
    """每个 L1 的子节点数与 PRD §3.2–3.14 一致，合计 80。"""
    actual: Dict[str, int] = {code: 0 for code in L1_CONTRACT}
    for node in kp_raw:
        pc = node["parentCode"]
        if pc is not None:
            actual[pc] = actual.get(pc, 0) + 1
    assert actual == EXPECT_CHILDREN, actual
    assert sum(actual.values()) == EXPECT_L2


def test_every_l1_has_children(kp_raw: List[Dict[str, Any]]) -> None:
    """13 个 L1 都必须至少带 1 个 L2（没有子节点的 L1 会让下钻恒回退）。"""
    with_children = {n["parentCode"] for n in kp_raw if n["parentCode"]}
    barren = set(L1_CONTRACT) - with_children
    assert not barren, f"以下 L1 没有任何 L2 子节点: {sorted(barren)}"


def test_l2_code_is_prefixed_and_described(kp_raw: List[Dict[str, Any]]) -> None:
    """L2 必须有非空 description（方舟第二段 prompt 直接用它做选项说明）。"""
    missing = [
        n["code"]
        for n in kp_raw
        if n["parentCode"] is not None and not (n.get("description") or "").strip()
    ]
    assert not missing, f"以下 L2 缺 description: {missing}"


def test_sibling_sort_is_unique_within_parent(kp_raw: List[Dict[str, Any]]) -> None:
    """同一父节点下的 sort 不重复（重复会让排序在不同方言下不稳定）。"""
    buckets: Dict[Any, List[int]] = {}
    for node in kp_raw:
        buckets.setdefault(node["parentCode"], []).append(node["sort"])
    for parent, sorts in buckets.items():
        assert len(sorts) == len(set(sorts)), f"父节点 {parent} 下 sort 重复: {sorted(sorts)}"


# ==================================================================== 2. uuid5 公式红线
def test_kp_uuid_formula_is_frozen() -> None:
    """``kp_uuid`` 公式冻结：命名空间前缀 ``aicoach:kp:`` 一个字符都不能改。"""
    for code in L1_CONTRACT:
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, "aicoach:kp:" + code))
        assert kp_uuid(code) == expected, f"{code} 的 uuid 公式被改动了！"


def test_kp_uuid_matches_known_production_ids() -> None:
    """抽查 4 个 L1 的硬编码 uuid（这些值已写进生产库的 2174 道题里）。"""
    known = {
        "KP_CALC": "4ad902f9-5b87-55e6-bca4-767d6c20d027",
        "KP_EQUATION": "70d4d173-d59b-54d8-a7be-f84963cbf4bb",
        "KP_WORD": "5eb1a2e5-5886-5f61-8ce0-32db9d4687d9",
        "KP_MIXED": "67336ae8-bf30-5e1b-a636-c98da491a914",
    }
    for code, expected in known.items():
        assert kp_uuid(code) == expected, f"{code} 的主键变了，存量题外键会全部悬空！"


def test_kp_uuid_is_deterministic_and_distinct(kp_raw: List[Dict[str, Any]]) -> None:
    """93 个 code 派生出 93 个互不相同的 uuid，且同一 code 多次调用结果一致。"""
    ids = [kp_uuid(n["code"]) for n in kp_raw]
    assert len(set(ids)) == EXPECT_TOTAL, "uuid5 出现碰撞"
    assert ids == [kp_uuid(n["code"]) for n in kp_raw], "uuid5 不稳定"


# ==================================================================== 3. validate_kp_tree
def test_validate_accepts_real_seed(kp_raw: List[Dict[str, Any]]) -> None:
    """真实种子文件必须通过校验。"""
    validate_kp_tree(kp_raw)  # 不抛异常即通过


def test_validate_rejects_dangling_parent() -> None:
    """父 code 不存在 → fail-fast。"""
    bad = [
        {"code": "KP_A", "name": "A", "parentCode": None, "sort": 1},
        {"code": "KP_A_X", "name": "X", "parentCode": "KP_NOT_EXIST", "sort": 2},
    ]
    with pytest.raises(AssertionError, match="parentCode 不存在"):
        validate_kp_tree(bad)


def test_validate_rejects_self_loop() -> None:
    """自环 → fail-fast。"""
    bad = [{"code": "KP_A", "name": "A", "parentCode": "KP_A", "sort": 1}]
    with pytest.raises(AssertionError):
        validate_kp_tree(bad)


def test_validate_rejects_three_levels() -> None:
    """三层结构 → fail-fast。"""
    bad = [
        {"code": "KP_A", "name": "A", "parentCode": None, "sort": 1},
        {"code": "KP_B", "name": "B", "parentCode": "KP_A", "sort": 2},
        {"code": "KP_C", "name": "C", "parentCode": "KP_B", "sort": 3},
    ]
    with pytest.raises(AssertionError, match="树深超过 2 层"):
        validate_kp_tree(bad)


def test_validate_accepts_flat_v1_shape() -> None:
    """向后兼容：全部 ``parentCode=None`` 的老树（v1 的 13 桶）也必须通过。"""
    flat = [{"code": c, "name": n, "parentCode": None, "sort": s} for c, (n, s) in L1_CONTRACT.items()]
    validate_kp_tree(flat)


# ==================================================================== 4. 两趟 merge 落库
def test_two_pass_merge_writes_93_rows(iso_db: Session, kp_raw: List[Dict[str, Any]]) -> None:
    """两趟 merge 后库里 93 行，13 行 L1 + 80 行 L2，外键全部成立。"""
    _merge_two_pass(iso_db, kp_raw)

    total = iso_db.execute(select(func.count()).select_from(KnowledgePoint)).scalar()
    l2 = iso_db.execute(
        select(func.count()).select_from(KnowledgePoint).where(KnowledgePoint.parent_id.isnot(None))
    ).scalar()
    assert total == EXPECT_TOTAL, total
    assert l2 == EXPECT_L2, l2

    # 外键成立：每个 L2 的 parent_id 都能在表里找到，且父行自身是 L1
    ids = {row[0]: row[1] for row in iso_db.execute(
        select(KnowledgePoint.id, KnowledgePoint.parent_id)
    ).all()}
    for kid, pid in ids.items():
        if pid is None:
            continue
        assert pid in ids, f"{kid} 的 parent_id 悬空"
        assert ids[pid] is None, f"{kid} 的父节点不是 L1（出现三层）"


def test_two_pass_merge_is_idempotent(iso_db: Session, kp_raw: List[Dict[str, Any]]) -> None:
    """重跑一次仍是 93 行，且全部主键不变（merge + 确定性 uuid5 = 幂等）。"""
    _merge_two_pass(iso_db, kp_raw)
    first = sorted(str(r[0]) for r in iso_db.execute(select(KnowledgePoint.id)).all())

    _merge_two_pass(iso_db, kp_raw)
    second = sorted(str(r[0]) for r in iso_db.execute(select(KnowledgePoint.id)).all())

    assert len(second) == EXPECT_TOTAL, len(second)
    assert first == second, "重跑后主键发生变化，幂等性被破坏"


def test_l1_upgrade_keeps_existing_ids(iso_db: Session, kp_raw: List[Dict[str, Any]]) -> None:
    """升级场景：库里先有 v1 的 13 个 L1，灌 v2 后这 13 行的 id 必须原样保留。"""
    for code, (name, sort) in L1_CONTRACT.items():
        iso_db.merge(KnowledgePoint(id=kp_uuid(code), code=code, name=name, sort=sort))
    iso_db.commit()
    before = {
        str(r[1]): str(r[0])
        for r in iso_db.execute(select(KnowledgePoint.id, KnowledgePoint.code)).all()
    }

    _merge_two_pass(iso_db, kp_raw)

    after = {
        str(r[1]): str(r[0])
        for r in iso_db.execute(
            select(KnowledgePoint.id, KnowledgePoint.code).where(KnowledgePoint.parent_id.is_(None))
        ).all()
    }
    for code, old_id in before.items():
        assert after.get(code) == old_id, f"{code} 的主键在升级后变了！存量题外键会悬空"

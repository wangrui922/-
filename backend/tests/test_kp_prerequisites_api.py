"""KG-004 契约测试：``GET /api/knowledge-points/{kp_id}/prerequisites`` 前置查询。

规划验收（V6.5 · Sprint 1）：
    * 对「分数应用题」返回含「分数加减」（直接前置 prerequisite 边）；
    * **递归一层**：还应带出「分数意义」（分数加减的前置）；
    * **递归防环测试**：环状边（A→B→A）不死循环、不重复入列；
    * 空结果（无前置 / id 不存在 / id 非法）→ ``items=[]`` **恒 200**。

红线（与 KG-001/002 共用）：
    ``kp_uuid(code) = uuid5(NS_QUESTION_BANK, "kp:" + code)``
    ``rel_uuid(source, type, target) = uuid5(NS_QUESTION_BANK,
                                             "rel:" + source + ":" + type + ":" + target)``
    ``NS_QUESTION_BANK = 6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21``

数据语义：``KnowledgeRelation(source_id -> target_id, prerequisite)`` 表示
「source 是 target 的前置」；示例链「分数意义 → 分数加减 → 分数应用题」。
"""

from __future__ import annotations

import inspect
import uuid
from typing import Callable, List, Tuple

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import knowledge as knowledge_api
from app.db import get_db as _app_get_db
from app.main import app
from app.models.base import Base
from app.models.content import KnowledgePoint, KnowledgeRelation

NS_QUESTION_BANK = uuid.UUID("6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21")


def kp_uuid(code: str) -> uuid.UUID:
    """复刻 KG-001 派生（与 service / seed 脚本一致）。"""
    return uuid.uuid5(NS_QUESTION_BANK, f"kp:{code}")


def rel_uuid(source_code: str, relation_type: str, target_code: str) -> uuid.UUID:
    """复刻 KG-002 关系边主键派生（与 seed 脚本一致）。"""
    return uuid.uuid5(
        NS_QUESTION_BANK, f"rel:{source_code}:{relation_type}:{target_code}"
    )


#: (client, seed) —— seed 用共享 engine 灌数据，client 走同一库
@pytest.fixture()
def env() -> Tuple[TestClient, Callable[..., None]]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_db():
        db: Session = Maker()
        try:
            yield db
        finally:
            db.close()

    def _seed_kp(code: str, name: str) -> None:
        """造一个知识点节点。"""
        with Maker() as db:
            db.add(
                KnowledgePoint(
                    id=kp_uuid(code),
                    name=name,
                    code=code,
                    parent_id=None,
                    description=None,
                    subject="math",
                    grade=3,
                    semester="上",
                    chapter="分数",
                    difficulty=3,
                    importance=3,
                    status="active",
                    sort=0,
                )
            )
            db.commit()

    def _seed_edge(source_code: str, target_code: str) -> None:
        """造一条 prerequisite 边：“source 是 target 的前置”。"""
        with Maker() as db:
            db.add(
                KnowledgeRelation(
                    id=rel_uuid(source_code, "prerequisite", target_code),
                    source_id=kp_uuid(source_code),
                    target_id=kp_uuid(target_code),
                    relation_type="prerequisite",
                )
            )
            db.commit()

    def seed(
        *,
        kps: List[Tuple[str, str]] | None = None,
        edges: List[Tuple[str, str]] | None = None,
    ) -> None:
        """批量造节点 + prerequisite 边（幂等，可多次调用追加）。"""
        if kps:
            for code, name in kps:
                _seed_kp(code, name)
        if edges:
            for source_code, target_code in edges:
                _seed_edge(source_code, target_code)

    app.dependency_overrides[_app_get_db] = _override_db
    try:
        yield TestClient(app), seed
    finally:
        app.dependency_overrides.clear()


# ============================================================ 规划示例链
def test_plan_example_returns_add_sub_and_meaning(env) -> None:
    """规划验收：分数应用题 → 含分数加减（直接前置）+ 分数意义（递归一层）。"""
    client, seed = env
    seed(
        kps=[
            ("KP_FRAC_MEANING", "分数的意义"),
            ("KP_FRAC_ADD_SUB", "分数加减法"),
            ("KP_FRAC_WORD", "分数应用题"),
        ],
        edges=[
            ("KP_FRAC_MEANING", "KP_FRAC_ADD_SUB"),  # 意义 → 加减
            ("KP_FRAC_ADD_SUB", "KP_FRAC_WORD"),  # 加减 → 应用题
        ],
    )

    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_FRAC_WORD')}/prerequisites")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    items = body["data"]["items"]
    codes = [it["code"] for it in items]

    # 直接前置在前：分数加减是应用题的直接前置
    assert "KP_FRAC_ADD_SUB" in codes, f"缺直接前置: {codes}"
    assert codes[0] == "KP_FRAC_ADD_SUB", f"直接前置应排首位: {codes}"
    # 递归一层：分数意义是分数加减的前置，也应返回
    assert "KP_FRAC_MEANING" in codes, f"缺递归一层前置: {codes}"
    # 去重：不重复
    assert len(codes) == len(set(codes)), f"结果重复: {codes}"


def test_first_node_in_chain_returns_only_direct(env) -> None:
    """链头「分数意义」无前置 → 只返回空（自身不构成前置）。"""
    client, seed = env
    seed(
        kps=[
            ("KP_FRAC_MEANING", "分数的意义"),
            ("KP_FRAC_ADD_SUB", "分数加减法"),
        ],
        edges=[("KP_FRAC_MEANING", "KP_FRAC_ADD_SUB")],
    )
    resp = client.get(
        f"/api/knowledge-points/{kp_uuid('KP_FRAC_MEANING')}/prerequisites"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["items"] == []


# ============================================================ 递归防环
def test_cycle_does_not_loop_or_duplicate(env) -> None:
    """递归防环：A→B→A 的环状边不死循环、不重复入列。

    验证查询「A 的前置」：直接前置 = [B]；递归一层取 B 的直接前置 = [A]，
    但 A 已在结果中 → 被 seen 吸收，最终只返回 [B]。
    """
    client, seed = env
    seed(
        kps=[
            ("KP_CYC_A", "环A"),
            ("KP_CYC_B", "环B"),
        ],
        edges=[
            ("KP_CYC_A", "KP_CYC_B"),  # A 是 B 的前置
            ("KP_CYC_B", "KP_CYC_A"),  # B 是 A 的前置 → 环
        ],
    )
    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_CYC_A')}/prerequisites")
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    codes = [it["code"] for it in items]
    assert codes == ["KP_CYC_B"], f"环状边应只返回直接前置且不重复: {codes}"


def test_three_node_cycle_finite_and_unique(env) -> None:
    """三节点环 A→B→C→A：请求必然结束、无重复、无自引用。"""
    client, seed = env
    seed(
        kps=[("KP_CA", "环A"), ("KP_CB", "环B"), ("KP_CC", "环C")],
        edges=[("KP_CA", "KP_CB"), ("KP_CB", "KP_CC"), ("KP_CC", "KP_CA")],
    )
    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_CA')}/prerequisites")
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    codes = [it["code"] for it in items]
    assert len(codes) == len(set(codes)), f"重复: {codes}"
    assert "KP_CA" not in codes, f"不应包含自身: {codes}"
    assert set(codes) <= {"KP_CB", "KP_CC"}, f"越界节点: {codes}"


# ============================================================ 空结果恒 200
def test_no_prerequisite_returns_empty_items(env) -> None:
    """无前置边 → items=[] 恒 200（不报错）。"""
    client, seed = env
    seed(kps=[("KP_ALONE", "独立知识点")])
    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_ALONE')}/prerequisites")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["items"] == []


def test_unknown_id_returns_empty_items(env) -> None:
    """id 不存在 → items=[] 恒 200（前置查询是引导读，空即合法语义）。"""
    client, _ = env
    resp = client.get(f"/api/knowledge-points/{uuid.uuid4()}/prerequisites")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["items"] == []


def test_invalid_id_returns_empty_items(env) -> None:
    """id 非 UUID → items=[] 恒 200。"""
    client, _ = env
    resp = client.get("/api/knowledge-points/not-a-uuid/prerequisites")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["items"] == []


def test_route_signature_has_no_auth_dependency() -> None:
    """公开只读：函数签名不得出现 get_current_user / CurrentUser。"""
    sig = inspect.signature(knowledge_api.get_knowledge_point_prerequisites)
    for name, param in sig.parameters.items():
        default_repr = repr(param.default)
        assert "get_current_user" not in default_repr, f"参数 {name} 挂了鉴权依赖"
        assert "CurrentUser" not in str(param.annotation), (
            f"参数 {name} 标注了 CurrentUser"
        )
    assert set(sig.parameters) == {"kp_id", "db"}, (
        f"公开路由参数异常：{list(sig.parameters)}"
    )

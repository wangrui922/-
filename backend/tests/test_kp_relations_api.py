"""KG-005 契约测试：``GET /api/knowledge-points/{kp_id}/relations`` 相关知识查询。

规划验收（V6.5 · Sprint 1）：
    * 返回 prerequisite / related / next **三类分组**；
      - prerequisite：``prerequisite`` 边的 source（谁是我前置，直接关系）；
      - related：``related`` 边的对端（无向，双向都算）；
      - next：``next`` 边的 target（学完之后的下一个）；
    * **空关系返回空数组而非报错**：无关系边 / id 不存在 / id 非法 →
      三类分组均为 ``[]``，恒 200。

红线（与 KG-001/002 共用）：
    ``kp_uuid(code) = uuid5(NS_QUESTION_BANK, "kp:" + code)``
    ``rel_uuid(source, type, target) = uuid5(NS_QUESTION_BANK,
                                             "rel:" + source + ":" + type + ":" + target)``
    ``NS_QUESTION_BANK = 6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21``

数据语义示例（与种子一致）：
    ``分数意义 →(prerequisite) 分数加减 →(prerequisite) 分数应用题``；
    ``分数加减 →(related) 小数加减``；``分数加减 →(next) 分数乘除``。
"""

from __future__ import annotations

import inspect
import uuid
from typing import Callable, Dict, List, Tuple

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

    def _seed_edge(source_code: str, relation_type: str, target_code: str) -> None:
        """造一条关系边（prerequisite / related / next）。"""
        with Maker() as db:
            db.add(
                KnowledgeRelation(
                    id=rel_uuid(source_code, relation_type, target_code),
                    source_id=kp_uuid(source_code),
                    target_id=kp_uuid(target_code),
                    relation_type=relation_type,
                )
            )
            db.commit()

    def seed(
        *,
        kps: List[Tuple[str, str]] | None = None,
        edges: List[Tuple[str, str, str]] | None = None,
    ) -> None:
        """批量造节点 + 关系边（幂等，可多次调用追加）。

        Args:
            kps: ``(code, name)`` 列表。
            edges: ``(source_code, relation_type, target_code)`` 列表。
        """
        if kps:
            for code, name in kps:
                _seed_kp(code, name)
        if edges:
            for source_code, relation_type, target_code in edges:
                _seed_edge(source_code, relation_type, target_code)

    app.dependency_overrides[_app_get_db] = _override_db
    try:
        yield TestClient(app), seed
    finally:
        app.dependency_overrides.clear()


def _get_codes(body: Dict) -> Dict[str, List[str]]:
    """从响应 data 中提取三组 code 列表。"""
    data = body["data"]
    return {rel_type: [it["code"] for it in items] for rel_type, items in data.items()}


# ============================================================ 规划示例链
def test_plan_example_returns_three_groups(env) -> None:
    """示例链三级：应用题的前置=加减；加减的 related=小数加减、next=分数乘除。"""
    client, seed = env
    seed(
        kps=[
            ("KP_FRAC_MEANING", "分数的意义"),
            ("KP_FRAC_ADD_SUB", "分数加减法"),
            ("KP_FRAC_WORD", "分数应用题"),
            ("KP_DEC_ADD_SUB", "小数加减法"),
            ("KP_FRAC_MUL_DIV", "分数乘除法"),
        ],
        edges=[
            ("KP_FRAC_MEANING", "prerequisite", "KP_FRAC_ADD_SUB"),
            ("KP_FRAC_ADD_SUB", "prerequisite", "KP_FRAC_WORD"),
            ("KP_FRAC_ADD_SUB", "related", "KP_DEC_ADD_SUB"),
            ("KP_FRAC_ADD_SUB", "next", "KP_FRAC_MUL_DIV"),
        ],
    )

    # —— 分数应用题：前置有分数加减，related/next 为空 ——
    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_FRAC_WORD')}/relations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    codes = _get_codes(body)
    assert codes["prerequisite"] == ["KP_FRAC_ADD_SUB"], codes
    assert codes["related"] == [], codes
    assert codes["next"] == [], codes

    # —— 分数加减：三类分组齐全 ——
    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_FRAC_ADD_SUB')}/relations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    codes = _get_codes(body)
    assert codes["prerequisite"] == ["KP_FRAC_MEANING"], codes
    assert codes["related"] == ["KP_DEC_ADD_SUB"], codes
    assert codes["next"] == ["KP_FRAC_MUL_DIV"], codes


def test_related_is_bidirectional(env) -> None:
    """related 无向：查询对端（小数加减）也应返回分数加减。"""
    client, seed = env
    seed(
        kps=[("KP_FRAC_ADD_SUB", "分数加减法"), ("KP_DEC_ADD_SUB", "小数加减法")],
        edges=[("KP_FRAC_ADD_SUB", "related", "KP_DEC_ADD_SUB")],
    )
    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_DEC_ADD_SUB')}/relations")
    assert resp.status_code == 200, resp.text
    codes = _get_codes(resp.json())
    assert codes["related"] == ["KP_FRAC_ADD_SUB"], codes
    assert codes["prerequisite"] == [] and codes["next"] == [], codes


def test_dedup_when_related_self_adjacent(env) -> None:
    """同一相关节点不再重复（双向命中只返回一次）。"""
    client, seed = env
    seed(
        kps=[("KP_A", "A知识点"), ("KP_B", "B知识点")],
        edges=[("KP_A", "related", "KP_B")],
    )
    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_A')}/relations")
    assert resp.status_code == 200, resp.text
    codes = _get_codes(resp.json())
    assert codes["related"] == ["KP_B"], codes


# ============================================================ 空关系空数组恒 200
def test_no_relation_returns_empty_arrays(env) -> None:
    """无任何关系边 → 三类分组均为 [] 恒 200（不报错）。"""
    client, seed = env
    seed(kps=[("KP_ALONE", "独立知识点")])
    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_ALONE')}/relations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    for rel_type, items in _get_codes(body).items():
        assert items == [], f"{rel_type} 应为空数组: {items}"


def test_unknown_id_returns_empty_arrays(env) -> None:
    """id 不存在 → 三类分组均为 [] 恒 200。"""
    client, _ = env
    resp = client.get(f"/api/knowledge-points/{uuid.uuid4()}/relations")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    assert set(body["data"]) == {"prerequisite", "related", "next"}
    for items in body["data"].values():
        assert items == []


def test_invalid_id_returns_empty_arrays(env) -> None:
    """id 非 UUID → 三类分组均为 [] 恒 200。"""
    client, _ = env
    resp = client.get("/api/knowledge-points/not-a-uuid/relations")
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == 0
    assert all(v == [] for v in resp.json()["data"].values())


def test_route_signature_has_no_auth_dependency() -> None:
    """公开只读：函数签名不得出现 get_current_user / CurrentUser。"""
    sig = inspect.signature(knowledge_api.get_knowledge_point_relations)
    for name, param in sig.parameters.items():
        default_repr = repr(param.default)
        assert "get_current_user" not in default_repr, f"参数 {name} 挂了鉴权依赖"
        assert "CurrentUser" not in str(param.annotation), (
            f"参数 {name} 标注了 CurrentUser"
        )
    assert set(sig.parameters) == {"kp_id", "db"}, (
        f"公开路由参数异常：{list(sig.parameters)}"
    )

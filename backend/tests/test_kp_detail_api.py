"""KG-003 契约测试：``GET /api/knowledge-points/{kp_id}`` 详情接口。

规划验收（V6.5 · Sprint 1）：
    * 接口测试**返回完整字段**（含描述 description / 难度 difficulty /
      重要程度 importance，以及 id / name / code / parent_id / subject /
      grade / semester / chapter / status / sort / created_at / updated_at）；
    * **不存在 id 返回 404 语义**（HTTP 404 + code=404 + 统一文案，
      且与「非法 id」用同一句文案——防资源探测）。

红线（与 KG-001/002 共用）：
    ``kp_uuid(code) = uuid5(NS_QUESTION_BANK, "kp:" + code)``
    ``NS_QUESTION_BANK = 6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21``

本文件走独立内存库：``client`` 与 ``seed`` 共享 **同一个** engine
（StaticPool 内存库只在同 engine 内共享连接），保证试验数据隔离且可查。
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any, Callable, Tuple

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import knowledge as knowledge_api
from app.db import get_db as _app_get_db
from app.main import app
from app.models.base import Base
from app.models.content import KnowledgePoint

NS_QUESTION_BANK = uuid.UUID("6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21")


def kp_uuid(code: str) -> uuid.UUID:
    """复刻 KG-001 派生（与 service / seed 脚本一致）。"""
    return uuid.uuid5(NS_QUESTION_BANK, f"kp:{code}")


#: (client, seed) —— seed 用共享 engine 灌数据，client 走同一库
@pytest.fixture()
def env() -> Tuple[TestClient, Callable[[], None]]:
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

    def _seed_kp(
        *,
        code: str = "KP_FRAC_MEANING",
        name: str = "分数的意义",
        grade: int = 3,
        semester: str = "上",
        chapter: str = "分数初步认识",
        subject: str = "math",
    ) -> None:
        """造一个带全套学习元数据的知识点（对齐 KG-001 种子字段）。"""
        with Maker() as db:
            db.add(
                KnowledgePoint(
                    id=kp_uuid(code),
                    name=name,
                    code=code,
                    parent_id=None,
                    description="把一个整体平均分成若干份，表示这样的一份或几份的数",
                    subject=subject,
                    grade=grade,
                    semester=semester,
                    chapter=chapter,
                    difficulty=3,
                    importance=4,
                    status="active",
                    sort=10,
                )
            )
            db.commit()

    app.dependency_overrides[_app_get_db] = _override_db
    try:
        yield TestClient(app), _seed_kp
    finally:
        app.dependency_overrides.clear()


# ============================================================ 完整字段返回
def test_detail_returns_full_fields(env) -> None:
    """KG-003 验收①：返回完整字段，含描述/难度/重要程度。"""
    client, seed = env
    seed()

    resp = client.get(f"/api/knowledge-points/{kp_uuid('KP_FRAC_MEANING')}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]

    asserted_fields: dict = {
        "id": str(kp_uuid("KP_FRAC_MEANING")),
        "name": "分数的意义",
        "code": "KP_FRAC_MEANING",
        "parent_id": None,
        "description": "把一个整体平均分成若干份，表示这样的一份或几份的数",
        "subject": "math",
        "grade": 3,
        "semester": "上",
        "chapter": "分数初步认识",
        "difficulty": 3,
        "importance": 4,
        "status": "active",
        "sort": 10,
    }
    for field, expected in asserted_fields.items():
        assert field in data, f"缺字段 {field}"
        assert data[field] == expected, f"{field}: {data[field]!r} != {expected!r}"
    # 时间戳必须有值（TimestampMixin 默认填充）
    assert data["created_at"], "created_at 缺失"
    assert data["updated_at"], "updated_at 缺失"
    # 全量字段核对：出参键覆盖全部模型业务字段
    assert set(asserted_fields) | {"created_at", "updated_at"} <= set(data.keys())


def test_nonexistent_id_returns_404(env) -> None:
    """KG-003 验收②：不存在 id → HTTP 404 + code=404 + 统一文案。"""
    client, _ = env
    resp = client.get(f"/api/knowledge-points/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == 404
    assert body["msg"] == knowledge_api.KP_NOT_FOUND_MESSAGE
    assert body["data"] == {}


def test_invalid_id_format_returns_404(env) -> None:
    """非法 id（非 UUID）与不存在同句 404，防资源探测。"""
    client, _ = env
    resp = client.get("/api/knowledge-points/not-a-uuid")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == 404
    assert body["msg"] == knowledge_api.KP_NOT_FOUND_MESSAGE


def test_route_signature_has_no_auth_dependency() -> None:
    """公开只读：函数签名不得出现 get_current_user / CurrentUser。"""
    sig = inspect.signature(knowledge_api.get_knowledge_point_detail)
    for name, param in sig.parameters.items():
        default_repr = repr(param.default)
        assert "get_current_user" not in default_repr, f"参数 {name} 挂了鉴权依赖"
        assert "CurrentUser" not in str(param.annotation), (
            f"参数 {name} 标注了 CurrentUser"
        )
    assert set(sig.parameters) == {"kp_id", "db"}, (
        f"公开路由参数异常：{list(sig.parameters)}"
    )

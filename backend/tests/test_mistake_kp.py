"""T01 集成测试：错题→知识点深链（kp_id 列 + 快照 + 出参 + 筛选）。

覆盖 PRD §7 验收点 B-1 ~ B-4：
    * B-1：mistakes 表存在 kp_id 列，双方言迁移可重复执行不报错
    * B-2：GET /api/mistake/list 出参含 kpId/kpName/grade，支持 kp_id 筛选
    * B-3：record_mistake 建档时从 Question.kp_id 快照写入
    * B-4：kpId 为空的错题不影响出参（其他字段正常返回，kpId=None）

前置说明：
    * 测试库为内存 SQLite（conftest.py 设定），``Base.metadata.create_all``
      会按模型定义建好 ``mistakes.kp_id`` 列，迁移 SQL 的幂等性由
      ``schema_guard.ensure_mistake_kp_column`` 函数级测试覆盖。
    * ``app/db.py`` 已对 SQLite 打开 ``PRAGMA foreign_keys=ON``。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from sqlalchemy import inspect, text

from app.db import SessionLocal, engine
from app.models.content import KnowledgePoint, Question
from app.models.learning import Mistake
from app.services.importers.schema_guard import ensure_mistake_kp_column

from t03_helpers import assert_fail, assert_ok, patch_ark


# ------------------------------------------------------------------ 公共工具
def _login(client) -> Dict[str, Any]:
    """随机验证码登录，返回登录响应的 ``data``。"""
    from conftest import login_with_code

    return login_with_code(client)


def _auth(client) -> Tuple[Dict[str, str], uuid.UUID]:
    """登录并返回 ``(请求头, 学生 uuid)``。"""
    data = _login(client)
    student_id = data["user"]["studentId"]
    assert student_id, f"dev 登录应自动建学生档案，studentId 不应为空: {data}"
    return {"Authorization": f"Bearer {data['access']}"}, uuid.UUID(str(student_id))


def _make_kp(name: str = "计算") -> str:
    """往题库插一个知识点并返回其 id（字符串）。"""
    with SessionLocal() as db:
        kp = KnowledgePoint(name=name, code=f"KP_{uuid.uuid4().hex[:8]}")
        db.add(kp)
        db.commit()
        return str(kp.id)


def _make_question(
    content: str = "小明买 3 支笔花了 15 元，每支笔多少元？",
    kp_id: str | None = None,
    grade: int = 3,
) -> str:
    """往题库插一道题并返回其 id（字符串）。

    ``kp_id`` 非空时关联知识点，用于验证快照写入。
    """
    with SessionLocal() as db:
        question = Question(
            content=content,
            subject="数学",
            grade=grade,
            difficulty=3,
            type_="应用",
            kp_id=uuid.UUID(kp_id) if kp_id else None,
        )
        db.add(question)
        db.commit()
        return str(question.id)


def _list(client, headers: Dict[str, str], **params) -> Dict[str, Any]:
    """拉一次错题本列表，返回 ``data``。"""
    return assert_ok(client.get("/api/mistake/list", headers=headers, params=params))


def _find(items, mistake_id: str):
    """在列表里按 id 找出那条错题，找不到返回 ``None``。"""
    return next((item for item in items if item["id"] == mistake_id), None)


# ------------------------------------------------------------------ B-1 迁移幂等
def test_schema_guard_ensure_mistake_kp_column_idempotent():
    """B-1：``ensure_mistake_kp_column`` 可重复执行不报错。

    模拟 SQLite 兜底场景：表已有 kp_id 列时返回 ``added=False``，
    不执行任何 DDL。
    """
    # 内存测试库在 conftest._create_schema 已按模型建好全部列（含 kp_id）
    result1 = ensure_mistake_kp_column(engine)
    assert result1["added"] is False, result1

    # 重复调用仍然不报错
    result2 = ensure_mistake_kp_column(engine)
    assert result2["added"] is False, result2


def test_mistake_table_has_kp_id_column():
    """B-1：mistakes 表确实存在 kp_id 列。"""
    inspector = inspect(engine)
    cols = {col["name"] for col in inspector.get_columns("mistakes")}
    assert "kp_id" in cols, f"mistakes 表缺少 kp_id 列: {cols}"


def test_mistake_kp_id_index_exists():
    """B-1：idx_mistakes_kp 索引存在。"""
    inspector = inspect(engine)
    indexes = {idx["name"] for idx in inspector.get_indexes("mistakes")}
    assert "idx_mistakes_kp" in indexes, f"mistakes 表缺少 idx_mistakes_kp 索引: {indexes}"


# ------------------------------------------------------------------ B-3 快照写入
def test_record_mistake_snapshots_kp_id(client, monkeypatch):
    """B-3：``record_mistake`` 建档时从 ``Question.kp_id`` 快照写入。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    kp_id = _make_kp("计算")
    question_id = _make_question(kp_id=kp_id, grade=4)

    created = assert_ok(
        client.post(
            "/api/mistake",
            headers=headers,
            json={
                "questionId": question_id,
                "errorType": "计算错",
                "errorReason": "进位加法出错",
            },
        )
    )
    mistake_id = created["id"]

    # 直接查库验证快照
    with SessionLocal() as db:
        mistake = db.execute(
            text("SELECT kp_id FROM mistakes WHERE id = :mid"),
            {"mid": mistake_id},
        ).first()
        assert mistake is not None, "错题没落库"
        assert mistake[0] is not None, "kp_id 快照为空"
        assert mistake[0] == kp_id, f"kp_id 快照不匹配: {mistake[0]} != {kp_id}"


def test_record_mistake_kp_id_none_when_question_has_no_kp(client, monkeypatch):
    """B-3/B-4：题目没有 kp_id 时，错题的 kp_id 也为 None（不影响建档）。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    question_id = _make_question(kp_id=None, grade=3)

    created = assert_ok(
        client.post(
            "/api/mistake",
            headers=headers,
            json={"questionId": question_id, "errorType": "审题不清"},
        )
    )

    with SessionLocal() as db:
        mistake = db.execute(
            text("SELECT kp_id FROM mistakes WHERE id = :mid"),
            {"mid": created["id"]},
        ).first()
        assert mistake is not None
        assert mistake[0] is None, f"题目无 kp_id 时错题 kp_id 应为 NULL: {mistake[0]}"


# ------------------------------------------------------------------ B-2 出参 + 筛选
def test_to_item_contains_kp_fields(client, monkeypatch):
    """B-2：``_to_item`` 出参含 ``kpId`` / ``kpName`` / ``grade``。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    kp_id = _make_kp("分数")
    question_id = _make_question(kp_id=kp_id, grade=5)

    created = assert_ok(
        client.post(
            "/api/mistake",
            headers=headers,
            json={"questionId": question_id, "errorType": "概念不清"},
        )
    )

    data = _list(client, headers)
    item = _find(data["items"], created["id"])
    assert item is not None, f"新建的错题没出现在列表里: {data}"

    # 出参含 kpId / kpName / grade
    assert item["kpId"] == kp_id, item
    assert item["kpName"] == "分数", item
    assert item["grade"] == 5, item
    # 既有字段不丢
    assert item["questionId"] == question_id, item
    assert item["errorType"] == "概念不清", item


def test_list_mistakes_filter_by_kp_id(client, monkeypatch):
    """B-2：``GET /api/mistake/list?kp_id=xxx`` 按知识点筛选生效。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    kp_a = _make_kp("计算")
    kp_b = _make_kp("分数")

    q_a = _make_question(kp_id=kp_a, grade=3, content="3 + 5 = ?")
    q_b = _make_question(kp_id=kp_b, grade=4, content="1/2 + 1/3 = ?")

    mistake_a = assert_ok(
        client.post(
            "/api/mistake",
            headers=headers,
            json={"questionId": q_a, "errorType": "计算错"},
        )
    )
    mistake_b = assert_ok(
        client.post(
            "/api/mistake",
            headers=headers,
            json={"questionId": q_b, "errorType": "通分错"},
        )
    )

    # 筛 kp_a
    data_a = _list(client, headers, kp_id=kp_a)
    ids_a = {item["id"] for item in data_a["items"]}
    assert mistake_a["id"] in ids_a, f"kp_a 的错题应在筛选结果中: {data_a}"
    assert mistake_b["id"] not in ids_a, f"kp_b 的错题不应在 kp_a 筛选中: {data_a}"

    # 筛 kp_b
    data_b = _list(client, headers, kp_id=kp_b)
    ids_b = {item["id"] for item in data_b["items"]}
    assert mistake_b["id"] in ids_b, f"kp_b 的错题应在筛选结果中: {data_b}"
    assert mistake_a["id"] not in ids_b, f"kp_a 的错题不应在 kp_b 筛选中: {data_b}"


def test_list_mistakes_kp_id_filter_with_state(client, monkeypatch):
    """B-2：``kp_id`` 与 ``state`` 筛选可组合使用。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    kp_id = _make_kp("单位换算")
    q1 = _make_question(kp_id=kp_id, grade=3, content="3千米 = ?米")
    q2 = _make_question(kp_id=None, grade=4, content="5 + 3 = ?")

    m1 = assert_ok(
        client.post(
            "/api/mistake", headers=headers, json={"questionId": q1, "errorType": "换算错"}
        )
    )
    assert_ok(
        client.post(
            "/api/mistake", headers=headers, json={"questionId": q2, "errorType": "计算错"}
        )
    )

    # state=unsolved + kp_id 组合
    data = _list(client, headers, state="unsolved", kp_id=kp_id)
    ids = {item["id"] for item in data["items"]}
    assert m1["id"] in ids, data
    # 全部筛出来的都是这个 kp 的
    assert all(item["kpId"] == kp_id for item in data["items"]), data


def test_list_mistakes_invalid_kp_id_returns_400(client, monkeypatch):
    """B-2：``kp_id`` 不是合法 UUID → 400。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    assert_fail(
        client.get("/api/mistake/list", headers=headers, params={"kp_id": "not-a-uuid"}),
        400,
    )


# ------------------------------------------------------------------ B-4 kpId 为空不影响出参
def test_to_item_kp_id_none(client, monkeypatch):
    """B-4：``kpId`` 为空时其他字段正常返回，``kpId`` / ``kpName`` / ``grade`` 安全降级。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    # 题目无 kp_id
    question_id = _make_question(kp_id=None, grade=3)

    created = assert_ok(
        client.post(
            "/api/mistake",
            headers=headers,
            json={"questionId": question_id, "errorType": "审题不清"},
        )
    )

    data = _list(client, headers)
    item = _find(data["items"], created["id"])
    assert item is not None, data

    # kpId / kpName 为 None，grade 仍然返回（来自 question.grade）
    assert item["kpId"] is None, item
    assert item["kpName"] is None, item
    assert item["grade"] == 3, item
    # 其他字段正常
    assert item["questionId"] == question_id, item
    assert item["errorType"] == "审题不清", item
    assert item["solved"] is False, item


def test_to_item_all_fields_present(client, monkeypatch):
    """出参字段完整性：既有字段 + 新增字段都存在（不缺键）。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    kp_id = _make_kp("图形")
    question_id = _make_question(kp_id=kp_id, grade=6)

    created = assert_ok(
        client.post(
            "/api/mistake",
            headers=headers,
            json={"questionId": question_id, "errorType": "空间想象"},
        )
    )

    data = _list(client, headers)
    item = _find(data["items"], created["id"])
    assert item is not None, data

    expected_keys = {
        "id",
        "questionId",
        "errorType",
        "errorReason",
        "note",
        "solved",
        "solvedAt",
        "createdAt",
        "kpId",
            "kpName",
            "grade",
            "questionText",
            "originalImageUrl",
            "workImageUrls",
        }
    assert set(item.keys()) == expected_keys, f"出参键不匹配: {set(item.keys())}"

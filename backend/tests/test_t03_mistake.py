"""T03 错题本集成测试：记录错题 + 三态列表筛选。

覆盖：
    * ``POST /api/mistake``      建档（含幂等、外键校验、参数校验）
    * ``GET  /api/mistake/list`` 列表与 ``state`` 三态筛选

前置说明：
    ``mistakes.question_id`` 对 ``questions`` 有外键，内存测试库没有种子数据，
    所以每个用例自己往 ``questions`` 插一行（``_make_question``）。
    ``app/db.py`` 已对 SQLite 打开 ``PRAGMA foreign_keys=ON``，外键行为与 PG 对齐。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from app.db import SessionLocal
from app.models.content import Question

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


def _make_question(content: str = "小明买 3 支笔花了 15 元，每支笔多少元？") -> str:
    """往题库插一道题并返回其 id（字符串）。

    ``Question`` 只有 ``content`` 是必填（``subject`` / ``status`` 有默认值），
    这里顺带补齐 grade / difficulty / type 以贴近真实数据；
    ``difficulty`` 受 CHECK 约束只能取 1/3/5。
    """
    with SessionLocal() as db:
        question = Question(
            content=content,
            subject="数学",
            grade=3,
            difficulty=3,
            type_="应用",
        )
        db.add(question)
        db.commit()
        return str(question.id)


def _list(client, headers: Dict[str, str], state: str | None = None) -> Dict[str, Any]:
    """拉一次错题本列表，返回 ``data``。"""
    url = "/api/mistake/list" if state is None else f"/api/mistake/list?state={state}"
    return assert_ok(client.get(url, headers=headers))


def _find(items, mistake_id: str):
    """在列表里按 id 找出那条错题，找不到返回 ``None``。"""
    return next((item for item in items if item["id"] == mistake_id), None)


# ------------------------------------------------------------------ 建档 + 列表
def test_record_and_list_mistake(client, monkeypatch):
    """记录一道错题后，能在错题本列表里按契约字段完整读回。"""
    patch_ark(monkeypatch)  # 错题接口不该碰 AI，插桩兜底防止意外外呼
    headers, _ = _auth(client)
    question_id = _make_question()

    created = assert_ok(
        client.post(
            "/api/mistake",
            headers=headers,
            json={
                "questionId": question_id,
                "errorType": "审题不清",
                "errorReason": "没建立数量关系就下笔",
            },
        )
    )
    assert str(created["id"]).strip(), created
    uuid.UUID(str(created["id"]))
    assert created["solved"] is False, created

    data = _list(client, headers)
    assert data["total"] >= 1, data
    assert data["total"] == len(data["items"]), data

    item = _find(data["items"], created["id"])
    assert item is not None, f"新建的错题没出现在列表里: {data}"
    assert item["questionId"] == question_id, item
    assert item["errorType"] == "审题不清", item
    assert item["errorReason"] == "没建立数量关系就下笔", item
    assert item["solved"] is False, item
    assert item["solvedAt"] is None, item
    assert str(item["createdAt"] or "").strip(), item


def test_record_mistake_is_idempotent(client, monkeypatch):
    """同一题重复提交（且未攻克）复用同一条记录，不重复建档。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)
    question_id = _make_question("重复提交用题：7 + 8 = ?")

    payload = {"questionId": question_id, "errorType": "计算错"}
    first = assert_ok(client.post("/api/mistake", headers=headers, json=payload))
    second = assert_ok(client.post("/api/mistake", headers=headers, json=payload))

    assert first["id"] == second["id"], (first, second)

    items = _list(client, headers)["items"]
    same = [item for item in items if item["questionId"] == question_id]
    assert len(same) == 1, same


def test_record_mistake_requires_existing_question(client, monkeypatch):
    """questionId 指向不存在的题目 -> 400（外键校验兜底），且不落库。

    这里额外对比「合法 UUID 但题目不存在」与「UUID 格式非法」两种入参的报错文案：
    两者必须不同，否则说明用例是被 UUID 解析失败顺带挡下的，而不是真的走到了外键校验。
    """
    patch_ark(monkeypatch)
    headers, _ = _auth(client)
    ghost_id = str(uuid.uuid4())

    fk_body = assert_fail(
        client.post("/api/mistake", headers=headers, json={"questionId": ghost_id}), 400
    )
    parse_body = assert_fail(
        client.post("/api/mistake", headers=headers, json={"questionId": "not-a-uuid"}), 400
    )

    assert str(fk_body.get("msg") or "").strip(), fk_body
    assert fk_body["msg"] != parse_body["msg"], (fk_body, parse_body)

    items = _list(client, headers)["items"]
    assert all(item["questionId"] != ghost_id for item in items), items


def test_record_mistake_rejects_malformed_question_id(client, monkeypatch):
    """questionId 不是合法 UUID -> 400。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    assert_fail(client.post("/api/mistake", headers=headers, json={"questionId": "not-a-uuid"}), 400)


def test_mistake_requires_auth(client, monkeypatch):
    """错题本读写都需要登录 -> 401。"""
    patch_ark(monkeypatch)

    assert_fail(client.get("/api/mistake/list"), 401)
    assert_fail(client.post("/api/mistake", json={"questionId": str(uuid.uuid4())}), 401)


# ------------------------------------------------------------------ 三态筛选
def test_mistake_list_filter_state(client, monkeypatch):
    """三态筛选：新错题落在 unsolved；三态互斥且并集等于全量；非法 state -> 400。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)
    question_id = _make_question("三态筛选用题：12 ÷ 4 = ?")

    created = assert_ok(
        client.post("/api/mistake", headers=headers, json={"questionId": question_id})
    )

    unsolved = _list(client, headers, "unsolved")
    practicing = _list(client, headers, "practicing")
    mastered = _list(client, headers, "mastered")
    everything = _list(client, headers)

    # 1) 刚建的错题：未攻克 + 从没答对过 -> 属于 unsolved
    assert _find(unsolved["items"], created["id"]) is not None, unsolved
    assert _find(practicing["items"], created["id"]) is None, practicing
    assert _find(mastered["items"], created["id"]) is None, mastered

    # 2) 各态自身口径自洽
    assert all(item["solved"] is False for item in unsolved["items"]), unsolved
    assert all(item["solved"] is False for item in practicing["items"]), practicing
    assert all(item["solved"] is True for item in mastered["items"]), mastered
    assert all(item["solvedAt"] is None for item in unsolved["items"]), unsolved

    # 3) 三态互斥且并集 == 全量
    unsolved_ids = {item["id"] for item in unsolved["items"]}
    practicing_ids = {item["id"] for item in practicing["items"]}
    mastered_ids = {item["id"] for item in mastered["items"]}
    assert not (unsolved_ids & practicing_ids)
    assert not (unsolved_ids & mastered_ids)
    assert not (practicing_ids & mastered_ids)
    assert unsolved_ids | practicing_ids | mastered_ids == {item["id"] for item in everything["items"]}
    assert unsolved["total"] + practicing["total"] + mastered["total"] == everything["total"]

    # 4) 非法 state -> 400
    assert_fail(client.get("/api/mistake/list?state=whatever", headers=headers), 400)

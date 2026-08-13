"""学段（grade_band）区间过滤测试（#68 学段 UI）。

要点：
    * 内存 SQLite（conftest 注入 ``sqlite:///:memory:``，与 dev.db 隔离），
      本文件自带造数据 helper，不跨模块 import test_practice。
    * 验证 ``POST /api/practice/session`` 的 ``grade_band`` 参数：
      小学→grade∈[1,6]、初中→[7,9]、高中→[10,12]；
      不识别的 band 被忽略（等价于不限）。
    * 仅断言「抽到的题年级都落在对应区间」，不依赖具体题量（少给不凑数）。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.models.content import Answer, KnowledgePoint, Question
from t04_helpers import assert_ok, login


# ==================================================================== 造数据
def _make_kp(name: str) -> str:
    """插一个知识点，返回 ID 字符串。"""
    with SessionLocal() as db:
        kp = KnowledgePoint(name=name)
        db.add(kp)
        db.commit()
        return str(kp.id)


def _make_question(*, grade: int, kp_id: str, type_: str = "选择") -> str:
    """插一道 L1 安全的选择题（含答案），返回题目 ID 字符串。"""
    options = {"A": "1", "B": "2", "C": "3", "D": "4"}
    with SessionLocal() as db:
        q = Question(
            content=f"题 grade={grade}",
            subject="数学",
            grade=grade,
            difficulty=3,
            type_=type_,
            options=options,
            kp_id=uuid.UUID(kp_id),
            kp_verified=True,
            status="active",
        )
        db.add(q)
        db.flush()
        db.add(
            Answer(
                question_id=q.id,
                correct_answer="B",
                analysis="略",
                distractor_analysis={},
            )
        )
        db.commit()
        return str(q.id)


def _draw(client, headers: dict, **payload) -> dict:
    """调抽题接口并断言 200，返回 data。"""
    return assert_ok(client.post("/api/practice/session", json=payload, headers=headers))


def _grades_of(client, headers: dict, **payload) -> list:
    """抽题并返回所有题的 grade 列表。"""
    data = _draw(client, headers, **payload)
    return [q["grade"] for q in data["questions"]]


# ==================================================================== 用例
def test_grade_band_filters_by_range(client):
    """小学/初中/高中分别只回对应年级区间的题。"""
    headers, _ = login(client)
    kp = _make_kp("学段区间测试知识点")
    _make_question(grade=3, kp_id=kp)  # 小学
    _make_question(grade=8, kp_id=kp)  # 初中
    _make_question(grade=11, kp_id=kp)  # 高中

    # 小学 → 仅 1-6
    grades = _grades_of(client, headers, grade_band="小学", count=10)
    assert grades, "小学档应抽到至少 1 道"
    assert all(g is not None and 1 <= g <= 6 for g in grades)
    assert 8 not in grades and 11 not in grades

    # 初中 → 仅 7-9
    grades = _grades_of(client, headers, grade_band="初中", count=10)
    assert all(g is not None and 7 <= g <= 9 for g in grades)

    # 高中 → 仅 10-12
    grades = _grades_of(client, headers, grade_band="高中", count=10)
    assert all(g is not None and 10 <= g <= 12 for g in grades)


def test_grade_band_unknown_is_ignored(client):
    """传一个不识别的 band，后端忽略 → 等价于不限（能抽到存在的题）。"""
    headers, _ = login(client)
    kp = _make_kp("学段忽略测试知识点")
    _make_question(grade=5, kp_id=kp)

    grades = _grades_of(client, headers, grade_band="幼儿园", count=10)
    # 核心语义：未知学段被忽略 → 不施加年级过滤，应抽到题（非空）。
    # 注：conftest 共享同一份内存库，完整套件下其他测试插入的题也参与
    # 随机采样（count 被 MAX_COUNT 夹到 30），故不精确断言 grade=5 必现，
    # 只验证「未因未知 band 被过滤成空」（忽略而非清空/报错）。
    assert grades, "未知学段应被忽略并抽到题（非空），而非过滤成空集"


def test_grade_band_overlaps_with_grade(client):
    """grade_band 与单 grade 同时传：交集语义（初中 ∩ 5年级 = 空集 → 404）。"""
    headers, _ = login(client)
    kp = _make_kp("学段交集测试知识点")
    _make_question(grade=8, kp_id=kp)  # 仅一道初中题

    # 初中 + 5年级：区间[7,9] 与 grade==5 交集为空 → 抽不到（后端 404）
    resp = client.post(
        "/api/practice/session",
        json={"grade_band": "初中", "grade": 5, "count": 10},
        headers=headers,
    )
    assert resp.status_code == 404

    # 初中 + 8年级：交集命中 → 抽得到（可能有多个 grade=8 的题，全部须为 8）
    grades = _grades_of(client, headers, grade_band="初中", grade=8, count=10)
    assert grades and all(g == 8 for g in grades)

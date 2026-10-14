"""学生咨询原题自动沉淀到题库候选区。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.ai import AiSession
from app.models.content import Answer, Question
from app.models.learning import Mistake
from t03_helpers import FakeArk, assert_ok, patch_ark


def _auth(client, phone: str) -> tuple[dict[str, str], str]:
    from conftest import login_with_code

    data = login_with_code(client, phone)
    return {"Authorization": f"Bearer {data['access']}"}, str(data["user"]["studentId"])


def test_text_question_is_archived_as_complete_candidate(client, monkeypatch, new_phone):
    """纯文字咨询也要保存完整题目，但未经复核前不能进入正式抽题池。"""
    stem = "候选题归档测试：解方程 7x = 21"
    ark = FakeArk(correct_answer="x = 3", solution="等式两边同时除以 7。")
    patch_ark(monkeypatch, ark)
    headers, student_id = _auth(client, new_phone())

    started = assert_ok(client.post("/api/chat", headers=headers, json={"text": stem}))
    assert "correct_answer" not in started["analysis"]
    assert "solution" not in started["analysis"]

    with SessionLocal() as db:
        question = db.execute(
            select(Question).where(
                Question.content == stem,
                Question.source_doc == "student_asked",
            )
        ).scalar_one()
        answer = db.execute(
            select(Answer).where(Answer.question_id == question.id)
        ).scalar_one()
        mistake = db.execute(
            select(Mistake).where(
                Mistake.student_id == student_id,
                Mistake.question_id == question.id,
            )
        ).scalar_one()
        session = db.execute(
            select(AiSession).where(AiSession.id == started["session_id"])
        ).scalar_one()

    assert question.status == "archived"
    assert question.source_tag == "red"
    assert question.ship_ready is False
    assert question.kp_verified is False
    assert question.type_ == ark.qtype
    assert answer.correct_answer == ark.correct_answer
    assert answer.analysis == ark.solution
    assert mistake.error_reason == "咨询求助题"
    assert session.question_id == question.id


def test_same_question_is_deduplicated_but_each_student_gets_a_mistake(
    client, monkeypatch, new_phone
):
    """重复问同一道题只保留一份题库记录，每名学生仍保留自己的学习记录。"""
    stem = "候选题去重测试：一个数的三倍是 24，这个数是多少？"
    patch_ark(monkeypatch, FakeArk(correct_answer="8", solution="24 除以 3 等于 8。"))
    first_headers, _ = _auth(client, new_phone())
    second_headers, _ = _auth(client, new_phone())

    assert_ok(client.post("/api/chat", headers=first_headers, json={"text": stem}))
    assert_ok(client.post("/api/chat", headers=first_headers, json={"text": f"  {stem}  "}))
    assert_ok(client.post("/api/chat", headers=second_headers, json={"text": stem}))

    with SessionLocal() as db:
        question_ids = list(
            db.execute(
                select(Question.id).where(
                    Question.content == stem,
                    Question.source_doc == "student_asked",
                )
            ).scalars()
        )
        mistake_count = db.execute(
            select(func.count()).select_from(Mistake).where(
                Mistake.question_id.in_(question_ids)
            )
        ).scalar_one()
        linked_session_count = db.execute(
            select(func.count()).select_from(AiSession).where(
                AiSession.question_id.in_(question_ids)
            )
        ).scalar_one()

    assert len(question_ids) == 1
    assert mistake_count == 2
    assert linked_session_count == 3

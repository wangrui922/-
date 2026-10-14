"""题库消费层 MVP 测试（B1~B4）。

覆盖：

* ``judge_engine`` 纯函数：选择题 dict/list 双形态 options、判断题归一、
  复合脏数据兜底 L4（无夹具，参考 ``test_quality_gate.py`` 的写法）；
* ``judge_engine.is_l1_safe`` / ``is_compound``：L1 安全池的**唯一真相源**，
  含 V3 全库不变式与 V3b 复合题反例；
* ``judge_engine.leaks_answer`` / ``practice_service.sanitize_guide_reply``：
  引导语 no-leak 代码层兜底（V1 铁律 / C11+C12）；
* ``/api/practice/answer`` 的 no-leak：答错时出参不得带出标准答案
  （``TestAnswerNoLeak``，断言对象是整个响应体而不是单个字段）；
* ``/api/practice/session``：抽题闸门（只抽 选择/判断 + 有答案 + active +
  非复合多小问 + 选择题必须有选项）、两级回退与 ``unmapped_count`` 诚实披露；
* ``/api/practice/answer``：L1 判分 + ``attempts`` 落库 + ``duration_sec``
  后端 ``min(d,600)`` 兜底 + ``client_event_id`` 幂等；
* ``/api/practice/reveal``：做对的题不给答案，做错的题给；
* ``/api/practice/finish``：错题自动入本（幂等）+ 统计口径来自 attempts；
* 越权：不能提交 / 结算别人的会话（一律 404）。

跨方言：本文件不写任何裸 SQL（V3 全库不变式那条除外——它**只读**核对真实
``dev.db``，库文件不存在时自动 skip，不影响 CI 主流程）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.content import Answer, KnowledgePoint, Question
from app.models.learning import Attempt, LearningTask, Mistake
from app.schemas.practice import SubmitResponse
from app.services import judge_engine, practice_service
from app.services.judge_engine import LEVEL_FALLBACK, LEVEL_RULE
from t04_helpers import assert_fail, assert_ok, login


def _as_async(fn):
    """把同步 ``**kwargs`` 函数包装成 async 版本，用于同一逻辑同时打桩
    ``ark_respond`` 与 ``ark_respond_async``（调用方只传关键字参数）。"""

    async def _wrapped(**kwargs):
        return fn(**kwargs)

    return _wrapped


# ==================================================================== 造数据
def _make_kp(name: str) -> str:
    """插一个知识点，返回 ID 字符串。"""
    with SessionLocal() as db:
        kp = KnowledgePoint(name=name)
        db.add(kp)
        db.commit()
        return str(kp.id)


def make_bank_question(
    *,
    type_: str = "选择",
    content: str = "3 + 4 = ?",
    options: Any = None,
    correct_answer: Optional[str] = "B",
    analysis: str = "先算加法。",
    kp_id: Optional[str] = None,
    kp_verified: bool = True,
    grade: int = 3,
    edition: Optional[str] = None,
    section: Optional[str] = None,
    difficulty: Optional[int] = 3,
    status: str = "active",
    with_answer: bool = True,
) -> str:
    """插一道题库题（含标准答案），返回题目 ID 字符串。

    Args:
        type_: 题型。
        content: 题干。
        options: 选项（dict/list/None）。
        correct_answer: 标准答案；``with_answer=False`` 时不建 Answer 行。
        analysis: 解析。
        kp_id: 知识点 ID；``None`` 表示不可归因（模拟 docx 新题）。
        kp_verified: 知识点是否人工复核通过。
        grade: 年级。
        edition: 教材版本。
        section: 册内分区。
        difficulty: 难度。
        status: active/archived。
        with_answer: 是否建 ``answers`` 行。

    Returns:
        题目 ID 字符串。
    """
    if options is None and type_ == "选择":
        options = {"A": "6", "B": "7", "C": "8", "D": "9"}
    with SessionLocal() as db:
        question = Question(
            content=content,
            subject="数学",
            grade=grade,
            difficulty=difficulty,
            type_=type_,
            options=options,
            kp_id=uuid.UUID(kp_id) if kp_id else None,
            kp_verified=kp_verified,
            edition=edition,
            section=section,
            status=status,
        )
        db.add(question)
        db.flush()
        if with_answer:
            db.add(
                Answer(
                    question_id=question.id,
                    correct_answer=correct_answer,
                    analysis=analysis,
                    distractor_analysis={},
                )
            )
        db.commit()
        return str(question.id)


def _draw(client, headers: Dict[str, str], **payload: Any) -> Dict[str, Any]:
    """调抽题接口并断言成功，返回 data。"""
    return assert_ok(client.post("/api/practice/session", json=payload, headers=headers))


def _attempts_of(student_id: uuid.UUID, question_id: str) -> List[Attempt]:
    """读该学生对某题的全部作答流水。"""
    with SessionLocal() as db:
        return list(
            db.execute(
                select(Attempt).where(
                    Attempt.student_id == student_id,
                    Attempt.question_id == uuid.UUID(question_id),
                )
            ).scalars()
        )


def _mistakes_of(student_id: uuid.UUID, question_id: str) -> List[Mistake]:
    """读该学生对某题的错题记录。"""
    with SessionLocal() as db:
        return list(
            db.execute(
                select(Mistake).where(
                    Mistake.student_id == student_id,
                    Mistake.question_id == uuid.UUID(question_id),
                )
            ).scalars()
        )


# ==================================================================== B2 判分引擎
class TestJudgeEngineOptions:
    """``normalize_options`` 的双形态兼容。"""

    def test_dict_options(self) -> None:
        items = judge_engine.normalize_options({"B": "7", "A": "6"})
        assert [item.key for item in items] == ["A", "B"]
        assert items[0].text == "6"

    def test_list_options_by_index(self) -> None:
        items = judge_engine.normalize_options(["6", "7", "8", "9"])
        assert [item.key for item in items] == ["A", "B", "C", "D"]
        assert items[1].text == "7"

    def test_list_options_with_prefix(self) -> None:
        items = judge_engine.normalize_options(["A. 6", "B、7", "C）8"])
        assert [item.key for item in items] == ["A", "B", "C"]
        assert items[2].text == "8"

    def test_list_of_dict_options(self) -> None:
        items = judge_engine.normalize_options([{"key": "A", "text": "6"}, {"key": "B", "text": "7"}])
        assert [item.text for item in items] == ["6", "7"]

    def test_empty_options(self) -> None:
        assert judge_engine.normalize_options(None) == []
        assert judge_engine.normalize_options([]) == []
        assert judge_engine.normalize_options("坏数据") == []


class TestJudgeEngineChoice:
    """选择题 L1 判分。"""

    OPTIONS = {"A": "6", "B": "7", "C": "8", "D": "9"}

    def test_correct(self) -> None:
        outcome = judge_engine.judge("选择", self.OPTIONS, "B", "B")
        assert outcome.is_correct is True
        assert outcome.level == LEVEL_RULE

    def test_wrong(self) -> None:
        outcome = judge_engine.judge("选择", self.OPTIONS, "B", "C")
        assert outcome.is_correct is False
        assert outcome.level == LEVEL_RULE
        # ⚠️ 这条以前断言的是 `"B" in outcome.reason` —— 断的正是泄题行为本身。
        # 现在职责拆开：对外文案不许带标准答案，完整依据退到 audit_reason。
        assert "B" not in outcome.reason
        assert "B" in outcome.audit_reason

    def test_chinese_prefixed_answer(self) -> None:
        outcome = judge_engine.judge("选择", self.OPTIONS, "选B", "选 b")
        assert outcome.is_correct is True
        assert outcome.level == LEVEL_RULE

    def test_bare_answer_with_punctuation(self) -> None:
        outcome = judge_engine.judge("选择", self.OPTIONS, "B．", "B、")
        assert outcome.is_correct is True

    def test_list_options_still_judgeable(self) -> None:
        outcome = judge_engine.judge("选择", ["6", "7", "8", "9"], "B", "B")
        assert outcome.is_correct is True
        assert outcome.level == LEVEL_RULE

    def test_missing_options_falls_back(self) -> None:
        outcome = judge_engine.judge("选择", None, "B", "B")
        assert outcome.level == LEVEL_FALLBACK
        assert outcome.is_correct is False

    def test_multi_part_answer_falls_back(self) -> None:
        """复合题「(1)选A (2)选B」不判，兜底 L4。"""
        outcome = judge_engine.judge("选择", self.OPTIONS, "(1)选A (2)选B", "A")
        assert outcome.level == LEVEL_FALLBACK
        assert outcome.is_correct is False

    def test_unparseable_student_answer_falls_back(self) -> None:
        outcome = judge_engine.judge("选择", self.OPTIONS, "B", "我不会")
        assert outcome.level == LEVEL_FALLBACK
        assert outcome.is_correct is False

    def test_answer_text_match(self) -> None:
        """学生直接抄了选项正文也能判。"""
        outcome = judge_engine.judge("选择", self.OPTIONS, "B", "7")
        assert outcome.is_correct is True


class TestJudgeEngineTrueFalse:
    """判断题 L1 判分。"""

    @pytest.mark.parametrize(
        ("standard", "picked", "expected"),
        [
            ("√", "√", True),
            ("√", "对", True),
            ("√", "×", False),
            ("×", "错误", True),
            ("×", "√", False),
            ("对", "正确", True),
            ("错", "×", True),
        ],
    )
    def test_normalized_pairs(self, standard: str, picked: str, expected: bool) -> None:
        outcome = judge_engine.judge("判断", None, standard, picked)
        assert outcome.level == LEVEL_RULE
        assert outcome.is_correct is expected

    def test_dirty_multi_symbol_answer_falls_back(self) -> None:
        """题库脏数据「(1)× ×1...」多符号 → 不猜，兜底 L4。"""
        outcome = judge_engine.judge("判断", None, "(1)× ×1（2）√", "√")
        assert outcome.level == LEVEL_FALLBACK
        assert outcome.is_correct is False

    def test_unsupported_type_falls_back(self) -> None:
        outcome = judge_engine.judge("应用", None, "x = 4", "x = 4")
        assert outcome.level == LEVEL_FALLBACK
        assert outcome.is_correct is False

    def test_blank_answer_falls_back(self) -> None:
        outcome = judge_engine.judge("判断", None, "√", "   ")
        assert outcome.level == LEVEL_FALLBACK
        assert outcome.is_correct is False


# ==================================================================== B4 抽题
class TestDraw:
    """``POST /api/practice/session``。"""

    def test_draw_requires_login(self, client) -> None:
        assert_fail(client.post("/api/practice/session", json={}), 401)

    def test_draw_returns_normalized_questions(self, client, new_phone) -> None:
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("抽题-加法")
        make_bank_question(kp_id=kp_id, grade=4, edition="青岛版", content="抽题用题 1")

        data = _draw(client, headers, grade=4, edition="青岛版", count=5)

        assert data["session_id"]
        assert len(data["questions"]) == 1
        item = data["questions"][0]
        assert item["type"] == "选择"
        assert [opt["key"] for opt in item["options"]] == ["A", "B", "C", "D"]
        assert data["pool_size"] >= 1
        assert data["auto_relaxed"] is False
        assert data["unmapped_count"] == 0

    def test_draw_excludes_non_l1_and_archived(self, client, new_phone) -> None:
        """闸门：填空题 / 归档题 / 无答案题都不进池。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("抽题-闸门")
        good = make_bank_question(kp_id=kp_id, grade=5, edition="苏教版", content="闸门-合格题")
        make_bank_question(kp_id=kp_id, grade=5, edition="苏教版", type_="填空", content="闸门-填空题")
        make_bank_question(kp_id=kp_id, grade=5, edition="苏教版", content="闸门-归档题", status="archived")
        make_bank_question(kp_id=kp_id, grade=5, edition="苏教版", content="闸门-无答案题", with_answer=False)

        data = _draw(client, headers, grade=5, edition="苏教版", count=10)

        ids = [q["question_id"] for q in data["questions"]]
        assert ids == [good]
        assert data["pool_size"] == 1

    def test_draw_auto_relaxes_and_discloses_unmapped(self, client, new_phone) -> None:
        """筛选条件只能命中新题时自动放开，并披露 unmapped_count（C6）。"""
        headers, _ = login(client, new_phone())
        make_bank_question(kp_id=None, grade=6, edition="青岛版", section="预习", content="新题-无知识点 1")
        make_bank_question(kp_id=None, grade=6, edition="青岛版", section="预习", content="新题-无知识点 2")

        data = _draw(client, headers, grade=6, edition="青岛版", section="预习", count=5)

        assert data["auto_relaxed"] is True
        assert data["pool_size"] == 2
        assert data["unmapped_count"] == 2
        assert len(data["questions"]) == 2
        assert all(q["kp_id"] is None for q in data["questions"])

    def test_draw_without_candidates_returns_404(self, client, new_phone) -> None:
        headers, _ = login(client, new_phone())
        assert_fail(
            client.post("/api/practice/session", json={"grade": 2, "edition": "不存在的版本"}, headers=headers),
            404,
        )

    def test_draw_creates_learning_task_with_snapshot(self, client, new_phone) -> None:
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp("抽题-快照")
        make_bank_question(kp_id=kp_id, grade=3, section="温故", content="快照题")

        data = _draw(client, headers, grade=3, section="温故", count=3)

        with SessionLocal() as db:
            task = db.execute(
                select(LearningTask).where(LearningTask.id == uuid.UUID(data["session_id"]))
            ).scalar_one()
        assert task.student_id == student_id
        # 裁决①：不新增 task_type 枚举，复用 daily
        assert task.task_type == "daily"
        assert task.status == "doing"
        assert task.items[0]["status"] == "pending"
        assert task.items[0]["source"] == "practice"


# ==================================================================== B4 判分
class TestSubmit:
    """``POST /api/practice/answer``。"""

    def test_submit_correct_writes_attempt(self, client, new_phone) -> None:
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp("判分-正确")
        qid = make_bank_question(kp_id=kp_id, grade=3, section="自测", content="判分-正确题")
        session_id = _draw(client, headers, grade=3, section="自测")["session_id"]

        data = assert_ok(
            client.post(
                "/api/practice/answer",
                json={
                    "session_id": session_id,
                    "question_id": qid,
                    "selected_answer": "B",
                    "duration_sec": 42,
                    "client_event_id": "practice-ok-1",
                },
                headers=headers,
            )
        )

        assert data["is_correct"] is True
        assert data["level"] == LEVEL_RULE
        attempts = _attempts_of(student_id, qid)
        assert len(attempts) == 1
        assert attempts[0].is_correct is True
        assert attempts[0].duration_sec == 42
        assert attempts[0].selected_answer == "B"

    def test_submit_clamps_duration(self, client, new_phone) -> None:
        """C8：后端 min(d, 600) 兜底。"""
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp("判分-耗时")
        qid = make_bank_question(kp_id=kp_id, grade=3, section="专项", content="判分-耗时题")
        session_id = _draw(client, headers, grade=3, section="专项")["session_id"]

        data = assert_ok(
            client.post(
                "/api/practice/answer",
                json={
                    "session_id": session_id,
                    "question_id": qid,
                    "selected_answer": "B",
                    "duration_sec": 99999,
                    "client_event_id": "practice-clamp-1",
                },
                headers=headers,
            )
        )

        assert data["duration_sec"] == 600
        assert _attempts_of(student_id, qid)[0].duration_sec == 600

    def test_submit_is_idempotent(self, client, new_phone) -> None:
        """C2：同一 client_event_id 重发只写一条 attempt。"""
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp("判分-幂等")
        qid = make_bank_question(kp_id=kp_id, grade=3, section="摸底", content="判分-幂等题")
        session_id = _draw(client, headers, grade=3, section="摸底")["session_id"]
        payload = {
            "session_id": session_id,
            "question_id": qid,
            "selected_answer": "B",
            "duration_sec": 10,
            "client_event_id": "practice-idem-1",
        }

        first = assert_ok(client.post("/api/practice/answer", json=payload, headers=headers))
        second = assert_ok(client.post("/api/practice/answer", json=payload, headers=headers))

        assert first["is_correct"] is second["is_correct"] is True
        assert len(_attempts_of(student_id, qid)) == 1

    def test_submit_unparseable_student_answer_falls_back_to_l4(self, client, new_phone) -> None:
        """C7：学生作答无法识别时判 L4，不误判为对，但照样落 attempts。

        ⚠️ 这条以前造的是「题库脏数据」题（标准答案 ``(1)× ×1（2）√``），
        现在这类题已被 ``is_l1_safe()`` 挡在抽题池外、**根本抽不到**了
        （见 ``TestL1SafePool::test_draw_excludes_compound_and_optionless``），
        所以这里改测另一条仍然会走 L4 的真实路径：学生作答无法解析。
        """
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp("判分-作答不可解析")
        qid = make_bank_question(
            kp_id=kp_id,
            type_="判断",
            options=None,
            correct_answer="√",
            grade=2,
            section="温故",
            content="判分-作答不可解析题",
        )
        session_id = _draw(client, headers, grade=2, section="温故")["session_id"]

        data = assert_ok(
            client.post(
                "/api/practice/answer",
                json={"session_id": session_id, "question_id": qid, "selected_answer": "我拿不准"},
                headers=headers,
            )
        )

        assert data["level"] == LEVEL_FALLBACK
        assert data["is_correct"] is False
        # L4 也要如实落库，后续人工复核靠它
        assert len(_attempts_of(student_id, qid)) == 1

    def test_submit_question_not_in_session(self, client, new_phone) -> None:
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("判分-越题")
        make_bank_question(kp_id=kp_id, grade=4, section="提优", content="越题-会话内题")
        other = make_bank_question(kp_id=kp_id, grade=1, section="自测", content="越题-会话外题")
        session_id = _draw(client, headers, grade=4, section="提优")["session_id"]

        assert_fail(
            client.post(
                "/api/practice/answer",
                json={"session_id": session_id, "question_id": other, "selected_answer": "B"},
                headers=headers,
            ),
            400,
        )

    def test_submit_other_students_session_is_404(self, client, new_phone) -> None:
        """越权：别人的会话一律 404，不泄露存在性。"""
        owner_headers, _ = login(client, new_phone())
        intruder_headers, _ = login(client, new_phone())
        kp_id = _make_kp("判分-越权")
        qid = make_bank_question(kp_id=kp_id, grade=5, section="专项", content="越权题")
        session_id = _draw(client, owner_headers, grade=5, section="专项")["session_id"]

        assert_fail(
            client.post(
                "/api/practice/answer",
                json={"session_id": session_id, "question_id": qid, "selected_answer": "B"},
                headers=intruder_headers,
            ),
            404,
        )


# ==================================================================== B4 看答案
class TestReveal:
    """``POST /api/practice/reveal``（Q3 分级解锁）。"""

    def _prepare(self, client, new_phone, picked: str):
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp(f"看答案-{uuid.uuid4().hex[:6]}")
        qid = make_bank_question(kp_id=kp_id, grade=6, section="摸底", content=f"看答案题-{picked}")
        session_id = _draw(client, headers, grade=6, section="摸底")["session_id"]
        assert_ok(
            client.post(
                "/api/practice/answer",
                json={"session_id": session_id, "question_id": qid, "selected_answer": picked},
                headers=headers,
            )
        )
        return headers, student_id, session_id, qid

    def test_reveal_after_wrong_answer(self, client, new_phone) -> None:
        headers, _, session_id, qid = self._prepare(client, new_phone, "C")
        data = assert_ok(
            client.post(
                "/api/practice/reveal",
                json={"session_id": session_id, "question_id": qid},
                headers=headers,
            )
        )
        assert data["correct_answer"] == "B"
        assert data["analysis"]

    def test_reveal_blocked_when_correct(self, client, new_phone) -> None:
        headers, _, session_id, qid = self._prepare(client, new_phone, "B")
        assert_fail(
            client.post(
                "/api/practice/reveal",
                json={"session_id": session_id, "question_id": qid},
                headers=headers,
            ),
            403,
        )

    def test_reveal_blocked_before_answering(self, client, new_phone) -> None:
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("看答案-未作答")
        qid = make_bank_question(kp_id=kp_id, grade=1, section="预习", content="未作答题")
        session_id = _draw(client, headers, grade=1, section="预习")["session_id"]

        assert_fail(
            client.post(
                "/api/practice/reveal",
                json={"session_id": session_id, "question_id": qid},
                headers=headers,
            ),
            400,
        )


# ==================================================================== B4 结算
class TestFinish:
    """``POST /api/practice/finish``。"""

    def test_finish_counts_and_creates_mistakes(self, client, new_phone) -> None:
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp("结算-知识点")
        right = make_bank_question(kp_id=kp_id, grade=4, section="自测", content="结算-做对题")
        wrong = make_bank_question(kp_id=kp_id, grade=4, section="自测", content="结算-做错题")
        session_id = _draw(client, headers, grade=4, section="自测", count=10)["session_id"]

        for qid, picked in ((right, "B"), (wrong, "A")):
            assert_ok(
                client.post(
                    "/api/practice/answer",
                    json={"session_id": session_id, "question_id": qid, "selected_answer": picked},
                    headers=headers,
                )
            )

        data = assert_ok(
            client.post("/api/practice/finish", json={"session_id": session_id}, headers=headers)
        )

        assert data["total"] == 2
        assert data["correct"] == 1
        assert data["wrong"] == 1
        assert data["mistakes_created"] == 1
        assert data["accuracy"] == 0.5
        assert data["unmapped_count"] == 0
        assert data["mastery_refreshed"] is True
        assert [wp["kp_name"] for wp in data["weak_points"]] == ["结算-知识点"]
        assert len(_mistakes_of(student_id, wrong)) == 1
        assert _mistakes_of(student_id, right) == []

    def test_finish_is_idempotent(self, client, new_phone) -> None:
        """C4：重复结算不重复建错题。"""
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp("结算-幂等")
        wrong = make_bank_question(kp_id=kp_id, grade=2, section="提优", content="结算-幂等错题")
        session_id = _draw(client, headers, grade=2, section="提优")["session_id"]
        assert_ok(
            client.post(
                "/api/practice/answer",
                json={"session_id": session_id, "question_id": wrong, "selected_answer": "A"},
                headers=headers,
            )
        )

        first = assert_ok(
            client.post("/api/practice/finish", json={"session_id": session_id}, headers=headers)
        )
        second = assert_ok(
            client.post("/api/practice/finish", json={"session_id": session_id}, headers=headers)
        )

        assert first["mistakes_created"] == 1
        assert second["mistakes_created"] == 0
        assert len(_mistakes_of(student_id, wrong)) == 1

    def test_finish_skips_cache_for_unmapped_questions(self, client, new_phone) -> None:
        """裁决②：kp_id IS NULL 的新题跳过 invalidate_cache_for_question。"""
        headers, student_id = login(client, new_phone())
        qid = make_bank_question(kp_id=None, grade=6, section="专项", edition="苏教版", content="结算-新题")
        session_id = _draw(client, headers, grade=6, section="专项", edition="苏教版")["session_id"]
        assert_ok(
            client.post(
                "/api/practice/answer",
                json={"session_id": session_id, "question_id": qid, "selected_answer": "A"},
                headers=headers,
            )
        )

        data = assert_ok(
            client.post("/api/practice/finish", json={"session_id": session_id}, headers=headers)
        )

        assert data["unmapped_count"] == 1
        assert data["mastery_refreshed"] is False
        assert data["weak_points"] == []
        # 错题本仍然照建（C4 只看 is_correct）
        assert len(_mistakes_of(student_id, qid)) == 1

    def test_finish_other_students_session_is_404(self, client, new_phone) -> None:
        owner_headers, _ = login(client, new_phone())
        intruder_headers, _ = login(client, new_phone())
        kp_id = _make_kp("结算-越权")
        make_bank_question(kp_id=kp_id, grade=1, section="摸底", content="结算-越权题")
        session_id = _draw(client, owner_headers, grade=1, section="摸底")["session_id"]

        assert_fail(
            client.post("/api/practice/finish", json={"session_id": session_id}, headers=intruder_headers),
            404,
        )


# ==================================================================== B4 引导
class TestGuide:
    """``POST /api/practice/guide``（AI 过程引导）。"""

    def test_guide_returns_reply(self, client, new_phone, monkeypatch) -> None:
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("引导-知识点")
        qid = make_bank_question(kp_id=kp_id, grade=5, section="预习", content="引导题")
        session_id = _draw(client, headers, grade=5, section="预习")["session_id"]

        captured: Dict[str, Any] = {}

        def _fake_ark(*, messages, system=None, image_b64=None, timeout=60.0) -> str:
            captured["messages"] = messages
            return '{"reply":"先看看题目给了哪些条件？","askFinalConfirm":false}'

        async def _fake_ark_async(*, messages, system=None, image_b64=None, timeout=60.0) -> str:
            return _fake_ark(
                messages=messages, system=system, image_b64=image_b64, timeout=timeout
            )

        monkeypatch.setattr("app.ai.ark_client.ark_respond", _fake_ark)
        monkeypatch.setattr("app.ai.ark_client.ark_respond_async", _fake_ark_async)

        data = assert_ok(
            client.post(
                "/api/practice/guide",
                json={"session_id": session_id, "question_id": qid, "work": "我先算了 3+4"},
                headers=headers,
            )
        )

        assert data["reply"] == "先看看题目给了哪些条件？"
        assert data["ask_final_confirm"] is False
        assert data["prompt_version"]
        prompt = captured["messages"][0]["content"]
        assert "引导题" in prompt
        assert "我先算了 3+4" in prompt
        assert "{{QUESTION}}" not in prompt
        assert "{{WORK}}" not in prompt

    def test_guide_tolerates_plain_text_reply(self, client, new_phone, monkeypatch) -> None:
        """模型没给 JSON 时按纯文本处理，不 500。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("引导-纯文本")
        qid = make_bank_question(kp_id=kp_id, grade=5, section="温故", content="引导-纯文本题")
        session_id = _draw(client, headers, grade=5, section="温故")["session_id"]

        monkeypatch.setattr(
            "app.ai.ark_client.ark_respond",
            lambda **kwargs: "这一步先别急，想想单位统一了吗？",
        )
        monkeypatch.setattr(
            "app.ai.ark_client.ark_respond_async",
            _as_async(lambda **kwargs: "这一步先别急，想想单位统一了吗？"),
        )

        data = assert_ok(
            client.post(
                "/api/practice/guide",
                json={"session_id": session_id, "question_id": qid},
                headers=headers,
            )
        )

        assert data["reply"] == "这一步先别急，想想单位统一了吗？"
        assert data["ask_final_confirm"] is False

    def test_guide_discards_leaked_answer(self, client, new_phone, monkeypatch) -> None:
        """C11/C12：模型抖出答案时整条丢弃，改用兜底话术（代码层兜底，不靠提示词）。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("引导-泄题")
        qid = make_bank_question(kp_id=kp_id, grade=4, section="提优", content="引导-泄题题")
        session_id = _draw(client, headers, grade=4, section="提优")["session_id"]

        monkeypatch.setattr(
            "app.ai.ark_client.ark_respond",
            lambda **kwargs: '{"reply":"这题答案是B，因为3加4等于7。","askFinalConfirm":false}',
        )
        monkeypatch.setattr(
            "app.ai.ark_client.ark_respond_async",
            _as_async(
                lambda **kwargs: '{"reply":"这题答案是B，因为3加4等于7。","askFinalConfirm":false}'
            ),
        )

        data = assert_ok(
            client.post(
                "/api/practice/guide",
                json={"session_id": session_id, "question_id": qid},
                headers=headers,
            )
        )

        assert data["reply"] == practice_service.GUIDE_FALLBACK_REPLY
        assert "B" not in data["reply"]


# ==================================================================== C11/C12 no-leak
class TestGuideNoLeak:
    """``judge_engine.leaks_answer`` / ``practice_service.sanitize_guide_reply``。"""

    OPTIONS = {"A": "6", "B": "7", "C": "8", "D": "9"}
    LONG_OPTIONS = {"A": "12 米", "B": "15 米", "C": "18 米", "D": "20 米"}

    @pytest.mark.parametrize(
        "reply",
        [
            "这题答案是B，因为3加4等于7。",
            "答案：B",
            "所以应该选 B 哦。",
            "选B就对了",
            "（B）才是正确的",
            "正确答案是 b。",
        ],
    )
    def test_choice_leaks_detected(self, reply: str) -> None:
        assert judge_engine.leaks_answer(reply, "选择", self.OPTIONS, "B") is True

    @pytest.mark.parametrize(
        "reply",
        [
            "我们一步步来：先说说这道题告诉了我们什么条件？",
            "别急，先想想 3 和 4 分别表示什么。",
            "你是怎么算出来的？把过程说给我听听。",
            "题目里有几个数？它们之间是什么关系呢？",
        ],
    )
    def test_clean_guide_replies_pass(self, reply: str) -> None:
        assert judge_engine.leaks_answer(reply, "选择", self.OPTIONS, "B") is False

    def test_choice_option_text_leak_detected(self) -> None:
        """标准选项正文（≥2 字）被原样抄进回复也算泄题。"""
        assert judge_engine.leaks_answer("所以结果就是 15 米。", "选择", self.LONG_OPTIONS, "B") is True

    def test_judge_symbol_leak_detected(self) -> None:
        assert judge_engine.leaks_answer("这句话应该打 √ 哦。", "判断", None, "√") is True
        assert judge_engine.leaks_answer("这句话应该打 × 哦。", "判断", None, "×") is True

    def test_judge_verdict_word_leak_detected(self) -> None:
        assert judge_engine.leaks_answer("答案是正确的。", "判断", None, "√") is True
        assert judge_engine.leaks_answer("所以这句话是错误的。", "判断", None, "×") is True

    def test_judge_clean_reply_passes(self) -> None:
        reply = "先想想：这句话里的两个量，单位一样吗？"
        assert judge_engine.leaks_answer(reply, "判断", None, "√") is False

    def test_leak_check_skipped_without_answer(self) -> None:
        """没有标准答案时无从判泄题，一律放行（交给提示词层）。"""
        assert judge_engine.leaks_answer("答案是B", "选择", self.OPTIONS, None) is False
        assert judge_engine.leaks_answer("", "选择", self.OPTIONS, "B") is False

    def test_sanitize_replaces_leaked_reply(self) -> None:
        safe = practice_service.sanitize_guide_reply(
            "答案是B，别再纠结啦。",
            q_type="选择",
            options=self.OPTIONS,
            correct_answer="B",
        )
        assert safe == practice_service.GUIDE_FALLBACK_REPLY

    def test_sanitize_keeps_clean_reply(self) -> None:
        reply = "先说说这道题里已知了哪些条件？"
        safe = practice_service.sanitize_guide_reply(
            reply, q_type="选择", options=self.OPTIONS, correct_answer="B"
        )
        assert safe == reply

    def test_sanitize_empty_reply_uses_fallback(self) -> None:
        safe = practice_service.sanitize_guide_reply(
            "   ", q_type="选择", options=self.OPTIONS, correct_answer="B"
        )
        assert safe == practice_service.GUIDE_FALLBACK_REPLY


# ==================================================================== /answer no-leak
class TestAnswerNoLeak:
    """``POST /api/practice/answer`` 在**任何分支**下都**不得**把标准答案透给前端。

    Q3 拍板的口径是「默认不给答案，主按钮是让老师带我想」——想看标准答案必须
    显式走 ``/reveal``（且只对做错的题开放）。前端不回显只是第一层，**接口本身
    不吐**才是纵深防御，否则 F12 一开就穿帮。

    ⚠️ **无条件不变量**：``reason`` 在**答对 / 答错 / L4 兜底**全部分支都不得含
    标准答案。守卫测试因此**无条件覆盖全分支**，不做答对豁免、不写解释性注释——
    规则只有一句话：「``reason`` 任何分支都不泄题」，新增分支只要带答案就会被拦下。

    ⚠️ 断言对象刻意是**整个响应体**，不是 ``reason`` 单个字段：

    1. ``data`` —— 路由真正吐出去的那个 dict（``api/practice.py`` 是把 service
       的返回值原样 ``ok(data)``，**不经过** ``SubmitResponse`` 过滤，所以它才是
       真实攻击面）；
    2. ``SubmitResponse`` 过滤后的 JSON —— 对外契约声明的字段集。

    这样以后谁再往任何一层加带答案的字段，都会被这条测试抓住，而不是只盯着
    ``reason`` 一处。
    """

    #: 标准选项正文刻意用 ≥2 字的（``MIN_LEAK_OPTION_TEXT``），
    #: 这样「把选项正文原样抄进出参」也在 ``leaks_answer`` 的检测范围内
    CHOICE_OPTIONS = {"A": "12 米", "B": "15 米", "C": "18 米", "D": "20 米"}

    @staticmethod
    def _payloads(data: Dict[str, Any]) -> List[str]:
        """把出参序列化成两份待检字符串：真实响应体 + 契约模型过滤后的。

        用 ``ensure_ascii=False`` 是必须的：泄题检测规则大量依赖中文（``正确``
        ``所以``）与 ``√``/``×`` 符号，一旦被转义成 ``\\uXXXX``，检测会全部落空，
        测试就变成了永远为真的空壳。
        """
        contract = SubmitResponse(**data).model_dump()
        return [
            json.dumps(data, ensure_ascii=False, default=str),
            json.dumps(contract, ensure_ascii=False, default=str),
        ]

    def _answer(self, client, headers, session_id: str, qid: str, picked: str) -> Dict[str, Any]:
        """提交一次作答并断言接口成功，返回 data。"""
        return assert_ok(
            client.post(
                "/api/practice/answer",
                json={"session_id": session_id, "question_id": qid, "selected_answer": picked},
                headers=headers,
            )
        )

    def test_choice_wrong_answer_does_not_leak(self, client, new_phone) -> None:
        """选择题答错：出参里既不许出现标准键位，也不许出现标准选项正文。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("泄题守卫-选择")
        qid = make_bank_question(
            kp_id=kp_id,
            options=self.CHOICE_OPTIONS,
            correct_answer="B",
            grade=6,
            edition="泄题版",
            section="选择泄题",
            content="泄题守卫-选择题",
        )
        session_id = _draw(client, headers, grade=6, edition="泄题版", section="选择泄题")["session_id"]

        data = self._answer(client, headers, session_id, qid, "C")
        assert data["is_correct"] is False
        assert data["level"] == LEVEL_RULE
        assert "audit_reason" not in data, "审计字段绝不能进 API 响应体"

        for payload in self._payloads(data):
            assert (
                judge_engine.leaks_answer(payload, "选择", self.CHOICE_OPTIONS, "B") is False
            ), f"/answer 出参泄题：{payload}"
            assert "15 米" not in payload, f"标准选项正文被带出：{payload}"

    def test_choice_correct_answer_does_not_leak(self, client, new_phone) -> None:
        """选择题答对：出参不得含标准键位，也不含标准选项正文。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("泄题守卫-选择-答对")
        qid = make_bank_question(
            kp_id=kp_id,
            options=self.CHOICE_OPTIONS,
            correct_answer="B",
            grade=6,
            edition="泄题版",
            section="选择泄题",
            content="泄题守卫-选择题-答对",
        )
        session_id = _draw(client, headers, grade=6, edition="泄题版", section="选择泄题")["session_id"]

        data = self._answer(client, headers, session_id, qid, "B")
        assert data["is_correct"] is True
        assert data["level"] == LEVEL_RULE
        assert "audit_reason" not in data, "审计字段绝不能进 API 响应体"

        for payload in self._payloads(data):
            assert (
                judge_engine.leaks_answer(payload, "选择", self.CHOICE_OPTIONS, "B") is False
            ), f"/answer 出参泄题：{payload}"
            assert "15 米" not in payload, f"标准选项正文被带出：{payload}"

    def test_choice_fallback_does_not_leak(self, client, new_phone) -> None:
        """选择题 L4 兜底（作答无法识别）：出参不得含标准键位。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("泄题守卫-选择-L4")
        qid = make_bank_question(
            kp_id=kp_id,
            options=self.CHOICE_OPTIONS,
            correct_answer="B",
            grade=6,
            edition="泄题版",
            section="选择泄题",
            content="泄题守卫-选择题-L4",
        )
        session_id = _draw(client, headers, grade=6, edition="泄题版", section="选择泄题")["session_id"]

        data = self._answer(client, headers, session_id, qid, "这题我瞎蒙的")
        assert data["level"] == LEVEL_FALLBACK
        assert "audit_reason" not in data, "审计字段绝不能进 API 响应体"

        for payload in self._payloads(data):
            assert (
                judge_engine.leaks_answer(payload, "选择", self.CHOICE_OPTIONS, "B") is False
            ), f"/answer 出参泄题：{payload}"

    def test_judge_wrong_answer_does_not_leak(self, client, new_phone) -> None:
        """判断题答错：出参里不许出现 √ / × 结论。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("泄题守卫-判断")
        qid = make_bank_question(
            kp_id=kp_id,
            type_="判断",
            options=None,
            correct_answer="√",
            grade=6,
            edition="泄题版",
            section="判断泄题",
            content="泄题守卫-判断题",
        )
        session_id = _draw(client, headers, grade=6, edition="泄题版", section="判断泄题")["session_id"]

        data = self._answer(client, headers, session_id, qid, "×")
        assert data["is_correct"] is False
        assert data["level"] == LEVEL_RULE
        assert "audit_reason" not in data, "审计字段绝不能进 API 响应体"

        for payload in self._payloads(data):
            assert (
                judge_engine.leaks_answer(payload, "判断", None, "√") is False
            ), f"/answer 出参泄题：{payload}"
            assert "√" not in payload, f"标准答案符号被带出：{payload}"

    def test_judge_correct_answer_does_not_leak(self, client, new_phone) -> None:
        """判断题答对：出参不得含 √ / × 结论。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("泄题守卫-判断-答对")
        qid = make_bank_question(
            kp_id=kp_id,
            type_="判断",
            options=None,
            correct_answer="√",
            grade=6,
            edition="泄题版",
            section="判断泄题",
            content="泄题守卫-判断题-答对",
        )
        session_id = _draw(client, headers, grade=6, edition="泄题版", section="判断泄题")["session_id"]

        data = self._answer(client, headers, session_id, qid, "√")
        assert data["is_correct"] is True
        assert data["level"] == LEVEL_RULE
        assert "audit_reason" not in data, "审计字段绝不能进 API 响应体"

        for payload in self._payloads(data):
            assert (
                judge_engine.leaks_answer(payload, "判断", None, "√") is False
            ), f"/answer 出参泄题：{payload}"
            assert "√" not in payload, f"标准答案符号被带出：{payload}"

    def test_judge_fallback_does_not_leak(self, client, new_phone) -> None:
        """判断题 L4 兜底（作答无法识别）：出参不得含 √ / × 结论。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("泄题守卫-判断-L4")
        qid = make_bank_question(
            kp_id=kp_id,
            type_="判断",
            options=None,
            correct_answer="√",
            grade=6,
            edition="泄题版",
            section="判断泄题",
            content="泄题守卫-判断题-L4",
        )
        session_id = _draw(client, headers, grade=6, edition="泄题版", section="判断泄题")["session_id"]

        data = self._answer(client, headers, session_id, qid, "我拿不准")
        assert data["level"] == LEVEL_FALLBACK
        assert "audit_reason" not in data, "审计字段绝不能进 API 响应体"

        for payload in self._payloads(data):
            assert (
                judge_engine.leaks_answer(payload, "判断", None, "√") is False
            ), f"/answer 出参泄题：{payload}"
            assert "√" not in payload, f"标准答案符号被带出：{payload}"

    def test_audit_reason_keeps_standard_answer(self) -> None:
        """审计信息不能被一起阉掉——答对 / 答错分支都必须带答案，供排查判分口径。"""
        choice_wrong = judge_engine.judge("选择", self.CHOICE_OPTIONS, "B", "C")
        assert "B" in choice_wrong.audit_reason, choice_wrong.audit_reason
        assert "C" in choice_wrong.audit_reason, choice_wrong.audit_reason
        assert judge_engine.leaks_answer(choice_wrong.reason, "选择", self.CHOICE_OPTIONS, "B") is False

        choice_correct = judge_engine.judge("选择", self.CHOICE_OPTIONS, "B", "B")
        assert "B" in choice_correct.audit_reason, "答对分支审计也必须带答案"
        assert judge_engine.leaks_answer(choice_correct.reason, "选择", self.CHOICE_OPTIONS, "B") is False

        verdict_wrong = judge_engine.judge("判断", None, "√", "×")
        assert "√" in verdict_wrong.audit_reason, verdict_wrong.audit_reason
        assert "√" not in verdict_wrong.reason, verdict_wrong.reason

        verdict_correct = judge_engine.judge("判断", None, "√", "√")
        assert "√" in verdict_correct.audit_reason, "答对分支审计也必须带答案"
        assert "√" not in verdict_correct.reason, verdict_correct.reason

    def test_audit_reason_defaults_to_reason(self) -> None:
        """L4 兜底分支不显式传 audit_reason，应回落为 reason（同样无答案）。"""
        fallback = judge_engine.judge("选择", self.CHOICE_OPTIONS, "B", "我不会")
        assert fallback.level == LEVEL_FALLBACK
        assert fallback.audit_reason == fallback.reason

    def test_to_dict_excludes_audit_reason(self) -> None:
        """``to_dict`` 是对外出参的唯一出口，必须过滤掉审计字段。"""
        outcome = judge_engine.judge("选择", self.CHOICE_OPTIONS, "B", "C")
        assert set(outcome.to_dict()) == {"is_correct", "level", "reason"}


# ==================================================================== L1 安全池口径
class TestL1SafePool:
    """``is_l1_safe`` / ``is_compound``：抽题池与判分池的唯一真相源。"""

    OPTIONS = {"A": "6", "B": "7", "C": "8", "D": "9"}

    def test_plain_choice_is_safe(self) -> None:
        assert judge_engine.is_l1_safe("选择", self.OPTIONS, "B") is True

    def test_plain_judge_is_safe(self) -> None:
        assert judge_engine.is_l1_safe("判断", None, "√") is True

    def test_unsupported_type_is_unsafe(self) -> None:
        assert judge_engine.is_l1_safe("填空", None, "7") is False
        assert judge_engine.is_l1_safe(None, None, "7") is False

    def test_blank_answer_is_unsafe(self) -> None:
        assert judge_engine.is_l1_safe("选择", self.OPTIONS, None) is False
        assert judge_engine.is_l1_safe("选择", self.OPTIONS, "   ") is False

    def test_choice_without_options_is_unsafe(self) -> None:
        """P0-1：库里真实存在「选择题但 options 为空」的脏题，绝不能进池。"""
        assert judge_engine.is_l1_safe("选择", None, "② 12×2=24(袋)") is False
        assert judge_engine.is_l1_safe("选择", [], "B") is False
        assert judge_engine.is_l1_safe("选择", {}, "B") is False

    def test_answer_key_outside_options_is_unsafe(self) -> None:
        """真实脏数据：答案 ``'C'`` 但选项只有 A/B —— 孩子怎么选都判不出对错。"""
        assert judge_engine.is_l1_safe("选择", ["林", "上"], "C") is False
        assert judge_engine.is_l1_safe("选择", ["3", "+0.8", "-5"], "D") is False

    def test_answer_contaminated_by_analysis_is_unsafe(self) -> None:
        """真实脏数据：docx 导入把解析串进了答案格（``'A提示在直角三角形中…'``）。"""
        dirty = "A提示在直角三角形中，斜边最长，所以高小于5cm。"
        assert judge_engine.is_l1_safe("选择", ["4", "5", "6", "7"], dirty) is False

    def test_unresolvable_judge_answer_is_unsafe(self) -> None:
        """判断题标准答案归一不出布尔（多符号 / 自由文本）→ 不进池。"""
        assert judge_engine.is_l1_safe("判断", None, "视情况而定") is False
        assert judge_engine.is_l1_safe("判断", None, "× ×1 √") is False

    @pytest.mark.parametrize(
        ("q_type", "options", "answer"),
        [
            ("选择", {"A": "6", "B": "7"}, "B"),
            ("选择", ["6", "7", "8", "9"], "选C"),
            ("选择", ["A. 6", "B、7"], "B．"),
            ("判断", None, "√"),
            ("判断", None, "错误"),
        ],
    )
    def test_safe_implies_l1_judgeable(self, q_type: str, options: Any, answer: str) -> None:
        """核心性质：``is_l1_safe`` 为真 ⟹ 喂标准答案必然 L1 判对。

        这条把「进池」和「可判分」焊死；有它在，池子规模就是规则的派生值，
        不会再出现文档数字和实际口径对不上的情况。
        """
        assert judge_engine.is_l1_safe(q_type, options, answer) is True
        outcome = judge_engine.judge(q_type, options, answer, answer)
        assert outcome.level == LEVEL_RULE
        assert outcome.is_correct is True

    @pytest.mark.parametrize(
        "raw",
        ["(1)C (2)B", "（1）√ （2）×", "(1)× ×1（2）√", "(1)A(2)B(3)C"],
    )
    def test_compound_answers_detected(self, raw: str) -> None:
        assert judge_engine.is_compound(raw) is True

    @pytest.mark.parametrize("raw", ["B", "√", "(1)C", "12×2=24(袋)", "", None])
    def test_non_compound_answers(self, raw: Optional[str]) -> None:
        assert judge_engine.is_compound(raw) is False

    def test_v3b_compound_counterexample(self) -> None:
        """V3b 反例：``'(1)C (2)B'`` 既不进 L1 池，判分时也必须走 L4。

        这是把口径焊死的关键一条——抽题层与判分层必须对同一道题给出一致
        结论，否则孩子会抽到注定只能自评的题（进不了掌握度闭环）。
        """
        raw = "(1)C (2)B"
        assert judge_engine.is_l1_safe("选择", self.OPTIONS, raw) is False
        outcome = judge_engine.judge("选择", self.OPTIONS, raw, "C")
        assert outcome.level == LEVEL_FALLBACK
        assert outcome.is_correct is False

    def test_draw_excludes_compound_and_optionless(self, client, new_phone) -> None:
        """P0-1 + P0-2：抽题层实际把这两类脏题挡在池外。"""
        headers, _ = login(client, new_phone())
        kp_id = _make_kp("抽题-脏题闸门")
        good = make_bank_question(
            kp_id=kp_id, grade=9, edition="闸门版", section="脏题", content="闸门-干净选择题"
        )
        make_bank_question(
            kp_id=kp_id,
            grade=9,
            edition="闸门版",
            section="脏题",
            content="闸门-复合多小问",
            correct_answer="(1)C (2)B",
        )
        make_bank_question(
            kp_id=kp_id,
            grade=9,
            edition="闸门版",
            section="脏题",
            content="闸门-选择题无选项",
            options={},
            correct_answer="② 12×2=24(袋)",
        )

        data = _draw(client, headers, grade=9, edition="闸门版", section="脏题", count=10)

        assert [q["question_id"] for q in data["questions"]] == [good]
        assert data["pool_size"] == 1


# ==================================================================== V3 全库不变式
#: 真实题库（开发用 SQLite）。不存在时 V3 不变式自动 skip。
_DEV_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dev.db")


def _parse_options(raw: Any) -> Any:
    """把 sqlite 里存的 options 原始值解析成 dict/list（解析不了就原样返回）。"""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    text = str(raw).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _real_bank_rows() -> List[Tuple[Any, Any, Any]]:
    """只读拉取真实题库里 ``active`` 且有标准答案的题。"""
    uri = f"file:{_DEV_DB.replace(os.sep, '/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return conn.execute(
            "SELECT q.type, q.options, a.correct_answer "
            "FROM questions q JOIN answers a ON a.question_id = q.id "
            "WHERE q.status = 'active'"
        ).fetchall()
    finally:
        conn.close()


@pytest.mark.skipif(not os.path.exists(_DEV_DB), reason="真实题库 dev.db 不存在，跳过 V3 全库不变式")
class TestL1PoolInvariantOnRealBank:
    """V3 全库不变式：**不硬编码池子规模**，只断言口径自洽。

    历史教训：L1 池规模在文档里漂过 595→591→589→594 四轮，每次都是靠临时
    脚本人肉重算。所以这里一个数字都不写死，只焊死几条不变式——
    规则一改，断言自动跟着走，数字自己派生。
    """

    def test_pool_is_non_empty(self) -> None:
        """池子不能空（空了说明口径写崩了，比数字对不上更严重）。"""
        rows = _real_bank_rows()
        pool = [r for r in rows if judge_engine.is_l1_safe(r[0], _parse_options(r[1]), r[2])]
        assert pool, "L1 安全池为空——is_l1_safe 口径异常"

    def test_every_pooled_question_is_l1_judgeable(self) -> None:
        """**核心不变式**：池内每一道题都必须能被 L1 判分（喂标准答案必判对、level=1）。

        用 ``is_l1_safe()``（抽题口径）与真实 ``judge()``（判分口径）交叉验证：
        只要有一道题进了池却落到 L4，说明两层口径脱节，孩子会抽到注定只能
        自评的题。
        """
        offenders: List[Tuple[Any, Any, Any]] = []
        for q_type, raw_options, correct_answer in _real_bank_rows():
            options = _parse_options(raw_options)
            if not judge_engine.is_l1_safe(q_type, options, correct_answer):
                continue
            outcome = judge_engine.judge(q_type, options, correct_answer, correct_answer)
            if outcome.level != LEVEL_RULE or outcome.is_correct is not True:
                offenders.append((q_type, correct_answer, outcome.reason))

        assert not offenders, f"L1 池内有 {len(offenders)} 道题无法被 L1 判分，样例：{offenders[:5]}"

    def test_compound_questions_never_enter_pool(self) -> None:
        """复合多小问一道都不许进池（铁律 C2），且判分层同样兜到 L4。"""
        leaked: List[Any] = []
        for q_type, raw_options, correct_answer in _real_bank_rows():
            if not judge_engine.is_compound(correct_answer):
                continue
            options = _parse_options(raw_options)
            if judge_engine.is_l1_safe(q_type, options, correct_answer):
                leaked.append(correct_answer)
            outcome = judge_engine.judge(q_type, options, correct_answer, correct_answer)
            assert outcome.level == LEVEL_FALLBACK, f"复合题被 L1 判分了：{correct_answer!r}"

        assert not leaked, f"有 {len(leaked)} 道复合多小问混进了 L1 池，样例：{leaked[:5]}"

    def test_pooled_choice_questions_always_have_options(self) -> None:
        """P0-1 全库版：池内选择题必须都有可用选项，不许出现光秃秃的题干。"""
        naked: List[Any] = []
        for q_type, raw_options, correct_answer in _real_bank_rows():
            options = _parse_options(raw_options)
            if not judge_engine.is_l1_safe(q_type, options, correct_answer):
                continue
            if q_type == judge_engine.TYPE_CHOICE and not judge_engine.option_keys(options):
                naked.append(correct_answer)

        assert not naked, f"有 {len(naked)} 道无选项的选择题混进了 L1 池，样例：{naked[:5]}"

    def test_excluded_questions_are_never_l1_judged(self) -> None:
        """反向不变式：被踢出池的题，判分层也绝不会给出 L1 结论（不会误判为对）。

        方向上和 ``test_every_pooled_question_is_l1_judgeable`` 互补，两条合起来
        就是「进池 ⟺ 可判分」这条等价关系的全库验证。
        """
        offenders: List[Tuple[Any, Any]] = []
        for q_type, raw_options, correct_answer in _real_bank_rows():
            if q_type not in judge_engine.SUPPORTED_TYPES:
                continue
            options = _parse_options(raw_options)
            if judge_engine.is_l1_safe(q_type, options, correct_answer):
                continue
            outcome = judge_engine.judge(q_type, options, correct_answer, correct_answer)
            if outcome.level == LEVEL_RULE:
                offenders.append((correct_answer, outcome.reason))

        assert not offenders, f"有 {len(offenders)} 道被踢出池的题仍被 L1 判分，样例：{offenders[:5]}"

    def test_no_choice_question_with_missing_options(self) -> None:
        """P0 全库不变式：**active 选择题必须含完整 A-D 四选项**。

        与设计 §3.4 口径同构（严格相等 ``{A,B,C,D}``），既不退化成 ``>= 4``、
        也不退化成 ``⊇``。用 ``quality_gate.validate_choice_options`` 作单一真相源，
        不各自写 SQL ``LIKE '%"D"%'``。

        这是 P0 治理（N0.1 补 D + N0.2 归档）后的验收核心：治理前全库有 312 道
        残缺 active 选择题，治理后应归零。本断言只核对口径自洽，不硬编码任何数字。
        """
        from app.services.importers.quality_gate import validate_choice_options

        naked: List[Any] = []
        for q_type, raw_options, _correct_answer in _real_bank_rows():
            if q_type != "选择":
                continue
            options = _parse_options(raw_options)
            if not validate_choice_options(options):
                naked.append(_parse_options(raw_options))

        assert not naked, f"有 {len(naked)} 道 active 选择题仍缺 A-D 完整选项，样例：{naked[:5]}"

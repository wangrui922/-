"""内测指标数据采集测试（60 人内测第一梯队 · 独立完成率可计算）。

与任务书验收四条逐条对应：

1. **hint 上下文**：无提示答对 → CORRECT meta 含 ``independent=true``；
   提示后答对 → meta 含 ``hint_count>0`` 且 ``independent=false``；
   看答案后答对 → ``answer_peeked=true`` 且 ``independent=false``；
   顺手钉死掌握度增量口径（独立正确 +0.10 / 提示后 +0.05 / 大量提示 +0.02），
   防止作答级 ``independent`` 标志误触发动机的迁移档（+0.12）；
2. **chat 归参**：HINT/REVIEW 事件带 ``question_id``/``kp_id``；
   会话未绑定题目时 meta 标 ``question_id_unknown``；
3. **三类新事件**：QUESTIONVIEW / SESSIONSTART / SESSIONEND 能正常 emit
   且不破坏主流程（含埋点整体故障时的旁路容错）；
4. **旧键兼容**：ANSWER/CORRECT/ERROR 的旧 meta 键仍在、语义不变。

另覆盖 AI 练习链路的 QUESTIONVIEW（出题展示）与 SESSIONEND（学习小结收尾）。

口径备注：本文件**不设** ``DATABASE_URL``，走 conftest 的会话级共享内存
SQLite（团队回归基线闸门，见 conftest 顶部注释）。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.ai import orchestrator
from app.core import constants as C
from app.db import SessionLocal
from app.models.ai import AiSession
from app.models.content import Answer, KnowledgePoint, Question
from app.models.dna import StudentKnowledgeMastery, StudentLearningEvent
from app.services.judge_engine import LEVEL_RULE
from t03_helpers import assert_ok, patch_ark
from t04_helpers import login
from test_ai_exercise import VerdictFakeArk

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
    section: Optional[str] = None,
    difficulty: Optional[int] = 3,
    status: str = "active",
    with_answer: bool = True,
) -> str:
    """插一道题库题（含标准答案），返回题目 ID 字符串。

    与 ``test_practice.make_bank_question`` 同口径；本文件自持一份，
    避免测试模块互相 import 造数据 helper（只 import 打桩/断言类）。
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


def _uniq(prefix: str) -> str:
    """全局唯一后缀（隔离共享内存库里的抽题池）。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _draw(client, headers: Dict[str, str], **payload: Any) -> Dict[str, Any]:
    """调抽题接口并断言成功，返回 data。"""
    return assert_ok(client.post("/api/practice/session", json=payload, headers=headers))


def _answer(
    client,
    headers: Dict[str, str],
    *,
    session_id: str,
    question_id: str,
    selected_answer: str,
    client_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    """提交单题作答并断言成功，返回 data。"""
    return assert_ok(
        client.post(
            "/api/practice/answer",
            json={
                "session_id": session_id,
                "question_id": question_id,
                "selected_answer": selected_answer,
                "client_event_id": client_event_id or f"ev-{uuid.uuid4().hex[:8]}",
            },
            headers=headers,
        )
    )


def _patch_practice_ark(monkeypatch) -> None:
    """给练习引导打桩（同步 + 异步两个引用点，回复不泄题）。"""
    reply = '{"reply":"我们先把题目里的条件理一理，你觉得该先求什么？","askFinalConfirm":false}'

    def _fake(*, messages, system=None, image_b64=None, timeout=60.0) -> str:
        return reply

    async def _fake_async(*, messages, system=None, image_b64=None, timeout=60.0) -> str:
        return reply

    monkeypatch.setattr("app.ai.ark_client.ark_respond", _fake)
    monkeypatch.setattr("app.ai.ark_client.ark_respond_async", _fake_async)


def _events(student_id: uuid.UUID, event_type: Optional[str] = None) -> List[StudentLearningEvent]:
    """读该学生的学习行为事件（按时间升序）。"""
    with SessionLocal() as db:
        db.rollback()  # 先结束旧事务，确保读到接口刚提交的数据
        stmt = select(StudentLearningEvent).where(
            StudentLearningEvent.student_id == student_id
        )
        if event_type is not None:
            stmt = stmt.where(StudentLearningEvent.event_type == event_type)
        return list(
            db.execute(stmt.order_by(StudentLearningEvent.created_at.asc())).scalars()
        )


def _mastery_row(student_id: uuid.UUID, kp_id: str) -> Optional[StudentKnowledgeMastery]:
    """读该学生某知识点的掌握度行。"""
    with SessionLocal() as db:
        db.rollback()
        return db.execute(
            select(StudentKnowledgeMastery).where(
                StudentKnowledgeMastery.student_id == student_id,
                StudentKnowledgeMastery.knowledge_point_id == uuid.UUID(kp_id),
            )
        ).scalar_one_or_none()


# ==================================================================== ① hint 上下文
class TestJudgeEventHintContext:
    """判分事件（CORRECT/ERROR）meta 的 hint 上下文与独立完成标志。"""

    def test_correct_without_hint_is_independent(self, client, new_phone) -> None:
        """无提示、未看答案、非引导 → independent=true，掌握度走基础档 +0.10。"""
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp(_uniq("独立-知识点"))
        section = _uniq("独立区")
        qid = make_bank_question(kp_id=kp_id, grade=3, section=section, content="独立题")
        session_id = _draw(client, headers, grade=3, section=section)["session_id"]

        result = _answer(
            client,
            headers,
            session_id=session_id,
            question_id=qid,
            selected_answer="B",
        )
        assert result["is_correct"] is True

        correct = _events(student_id, "CORRECT")
        assert len(correct) == 1
        event = correct[0]
        # 新键：hint 上下文 + 独立标志
        assert event.meta["hint_count"] == 0
        assert event.meta["answer_peeked"] is False
        assert event.meta["guided"] is False
        assert event.meta["independent"] is True
        assert event.meta["hint_context_unknown"] is False
        # 旧键：语义不变（向后兼容）
        assert event.meta["session_id"] == session_id
        assert event.meta["level"] == LEVEL_RULE
        # 事件列归因
        assert str(event.question_id) == qid
        assert str(event.knowledge_point_id) == kp_id
        # 掌握度：独立正确 = 基础档 +0.10（independent 未误触 +0.12 迁移档）
        row = _mastery_row(student_id, kp_id)
        assert row is not None
        assert abs(float(row.mastery) - C.MASTERY_DELTA_CORRECT_BASE) < 1e-9

    def test_correct_after_guide_is_hinted(self, client, new_phone, monkeypatch) -> None:
        """引导 2 轮后答对 → hint_count=2 / guided=true / independent=false / +0.05。"""
        _patch_practice_ark(monkeypatch)
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp(_uniq("提示-知识点"))
        section = _uniq("提示区")
        qid = make_bank_question(kp_id=kp_id, grade=4, section=section, content="提示题")
        session_id = _draw(client, headers, grade=4, section=section)["session_id"]

        for _ in range(2):
            guided = assert_ok(
                client.post(
                    "/api/practice/guide",
                    json={
                        "session_id": session_id,
                        "question_id": qid,
                        "work": "我先想了想前面的条件",
                    },
                    headers=headers,
                )
            )
            assert guided["reply"]

        _answer(
            client, headers, session_id=session_id, question_id=qid, selected_answer="B"
        )

        correct = _events(student_id, "CORRECT")
        assert len(correct) == 1
        meta = correct[0].meta
        assert meta["hint_count"] == 2
        assert meta["guided"] is True
        assert meta["answer_peeked"] is False
        assert meta["independent"] is False
        assert meta["hint_context_unknown"] is False
        # 掌握度：提示后正确 = +0.05（断点1 修复的直接证据）
        row = _mastery_row(student_id, kp_id)
        assert row is not None
        assert abs(float(row.mastery) - C.MASTERY_DELTA_CORRECT_HINTED) < 1e-9

    def test_heavy_hint_correct_uses_heavy_delta(self, client, new_phone, monkeypatch) -> None:
        """引导 3 轮后答对 → hint_count=3，掌握度走大量提示档 +0.02。"""
        _patch_practice_ark(monkeypatch)
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp(_uniq("重提示-知识点"))
        section = _uniq("重提示区")
        qid = make_bank_question(kp_id=kp_id, grade=4, section=section, content="重提示题")
        session_id = _draw(client, headers, grade=4, section=section)["session_id"]

        for _ in range(3):
            assert_ok(
                client.post(
                    "/api/practice/guide",
                    json={"session_id": session_id, "question_id": qid, "work": "还是不会"},
                    headers=headers,
                )
            )
        _answer(
            client, headers, session_id=session_id, question_id=qid, selected_answer="B"
        )

        meta = _events(student_id, "CORRECT")[0].meta
        assert meta["hint_count"] == 3
        assert meta["independent"] is False
        row = _mastery_row(student_id, kp_id)
        assert row is not None
        assert abs(float(row.mastery) - C.MASTERY_DELTA_CORRECT_HINTED_HEAVY) < 1e-9

    def test_correct_after_reveal_is_peeked(self, client, new_phone) -> None:
        """答错 → 看答案 → 再答对：answer_peeked=true / independent=false。"""
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp(_uniq("看答案-知识点"))
        section = _uniq("看答案区")
        qid = make_bank_question(kp_id=kp_id, grade=5, section=section, content="看答案题")
        session_id = _draw(client, headers, grade=5, section=section)["session_id"]

        # 先答错（选 A）
        _answer(
            client,
            headers,
            session_id=session_id,
            question_id=qid,
            selected_answer="A",
        )
        # 看参考答案（只能对做错的题）
        revealed = assert_ok(
            client.post(
                "/api/practice/reveal",
                json={"session_id": session_id, "question_id": qid},
                headers=headers,
            )
        )
        assert revealed["correct_answer"] == "B"
        # 再答对（新 client_event_id，重判一次）
        _answer(
            client,
            headers,
            session_id=session_id,
            question_id=qid,
            selected_answer="B",
        )

        correct = _events(student_id, "CORRECT")
        assert len(correct) == 1
        meta = correct[0].meta
        assert meta["answer_peeked"] is True
        assert meta["hint_count"] == 0
        assert meta["guided"] is False
        assert meta["independent"] is False
        assert meta["hint_context_unknown"] is False
        # 答错那次照旧发 ERROR，旧键不变；答错时还未看答案 → 当时是独立作答
        wrong = _events(student_id, "ERROR")
        assert len(wrong) == 1
        assert wrong[0].meta["session_id"] == session_id
        assert wrong[0].meta["level"] == LEVEL_RULE
        assert wrong[0].meta["independent"] is True  # 首轮作答无提示未看答案

    def test_error_and_answer_meta_backward_compatible(self, client, new_phone) -> None:
        """旧事件 meta 键值兼容：ANSWER/CORRECT/ERROR 旧键仍在、语义不变。"""
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp(_uniq("兼容-知识点"))
        section = _uniq("兼容区")
        qid = make_bank_question(kp_id=kp_id, grade=5, section=section, content="兼容题")
        session_id = _draw(client, headers, grade=5, section=section)["session_id"]

        _answer(
            client,
            headers,
            session_id=session_id,
            question_id=qid,
            selected_answer="C",
        )

        answer = _events(student_id, "ANSWER")
        assert len(answer) == 1
        assert answer[0].meta["session_id"] == session_id
        assert answer[0].meta["is_correct"] is False
        assert answer[0].meta["level"] == LEVEL_RULE
        assert answer[0].meta["duration_sec"] is None  # 未传耗时的旧行为

        error = _events(student_id, "ERROR")
        assert len(error) == 1
        assert error[0].meta["session_id"] == session_id
        assert error[0].meta["level"] == LEVEL_RULE
        # 新键只增不改：ERROR 也带同样的 hint 上下文字段集
        assert set(error[0].meta) >= {
            "session_id",
            "level",
            "hint_count",
            "answer_peeked",
            "guided",
            "independent",
            "hint_context_unknown",
        }

    def test_hint_context_unit_unknown_paths(self) -> None:
        """_hint_context 单测：快照缺失/脏数据 → None + unknown，严禁编造。"""
        from app.services.practice_service import _hint_context

        # 无法判定的输入：快照缺失 / 无 revealed 键（旧快照）/ 脏引导历史
        for bad in (None, {}, {"guide_history": "脏数据", "revealed": False}):
            ctx = _hint_context(bad)
            assert ctx["hint_count"] is None
            assert ctx["answer_peeked"] is None
            assert ctx["guided"] is None
            assert ctx["independent"] is None
            assert ctx["hint_context_unknown"] is True

        # 看过答案但无引导轮次：可判定（非独立），不是 unknown
        peeked_only = _hint_context({"revealed": True})
        assert peeked_only["hint_count"] == 0
        assert peeked_only["answer_peeked"] is True
        assert peeked_only["independent"] is False
        assert peeked_only["hint_context_unknown"] is False

        # 有引导轮次但缺 revealed 键（旧快照）：计数可得、看过答案不可考 → 未知
        ctx = _hint_context({"guide_history": [{"work": "a", "reply": "b"}]})
        assert ctx["hint_context_unknown"] is True
        assert ctx["independent"] is None

        # 完整快照：零提示 + 未看答案 → independent
        assert _hint_context({"guide_history": [], "revealed": False}) == {
            "hint_count": 0,
            "answer_peeked": False,
            "guided": False,
            "independent": True,
            "hint_context_unknown": False,
        }

        # 完整快照：看过答案 → 非独立
        peeked = _hint_context({"guide_history": [], "revealed": True})
        assert peeked["answer_peeked"] is True
        assert peeked["independent"] is False


# ==================================================================== ③ 三类新事件
class TestSessionLifecycleEvents:
    """QUESTIONVIEW / SESSIONSTART / SESSIONEND 的 emit 与旁路容错。"""

    def test_draw_emits_sessionstart_and_questionview(self, client, new_phone) -> None:
        """抽题开 session：1 条 SESSIONSTART + 每题 1 条 QUESTIONVIEW（带归因）。"""
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp(_uniq("展示-知识点"))
        section = _uniq("展示区")
        qids = [
            make_bank_question(
                kp_id=kp_id, grade=6, section=section, content=f"展示题-{index}"
            )
            for index in range(3)
        ]

        data = _draw(client, headers, grade=6, section=section, count=3)
        assert data["actual_count"] == 3

        starts = _events(student_id, "SESSIONSTART")
        assert len(starts) == 1
        assert starts[0].meta["channel"] == "practice"
        assert starts[0].meta["question_count"] == 3
        assert starts[0].question_id is None  # 会话级事件不带题目归因

        views = _events(student_id, "QUESTIONVIEW")
        assert len(views) == 3
        assert {str(view.question_id) for view in views} == set(qids)
        assert all(str(view.knowledge_point_id) == kp_id for view in views)
        assert all(view.meta["session_id"] == data["session_id"] for view in views)
        assert all(view.meta["channel"] == "practice" for view in views)

    def test_finish_emits_sessionend_once(self, client, new_phone) -> None:
        """结算发 1 条 SESSIONEND；重复 finish 不重复发（幂等）。"""
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp(_uniq("收尾-知识点"))
        section = _uniq("收尾区")
        qid = make_bank_question(kp_id=kp_id, grade=5, section=section, content="收尾题")
        session_id = _draw(client, headers, grade=5, section=section)["session_id"]
        _answer(
            client, headers, session_id=session_id, question_id=qid, selected_answer="B"
        )

        finished = assert_ok(
            client.post(
                "/api/practice/finish",
                json={"session_id": session_id},
                headers=headers,
            )
        )
        assert finished["correct"] == 1

        ends = _events(student_id, "SESSIONEND")
        assert len(ends) == 1
        assert ends[0].meta["channel"] == "practice"
        assert ends[0].meta["session_id"] == session_id
        assert ends[0].meta["total"] == 1
        assert ends[0].meta["correct"] == 1
        assert ends[0].meta["wrong"] == 0
        assert ends[0].meta["accuracy"] == 1.0

        # 重复结算（幂等链路）：SESSIONEND 不重复发
        again = assert_ok(
            client.post(
                "/api/practice/finish",
                json={"session_id": session_id},
                headers=headers,
            )
        )
        assert again["correct"] == 1
        assert len(_events(student_id, "SESSIONEND")) == 1

    def test_emit_failure_does_not_break_main_flow(self, client, new_phone, monkeypatch) -> None:
        """埋点整体故障（含三类新事件）：抽题/判分主流程照常，异常被旁路吞掉。"""
        def _boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("埋点故障（测试注入）")

        monkeypatch.setattr(
            "app.services.practice_service.record_learning_event", _boom
        )
        headers, student_id = login(client, new_phone())
        kp_id = _make_kp(_uniq("容错-知识点"))
        section = _uniq("容错区")
        qid = make_bank_question(kp_id=kp_id, grade=5, section=section, content="容错题")

        data = _draw(client, headers, grade=5, section=section)  # 抽题照常
        result = _answer(
            client, headers, session_id=data["session_id"], question_id=qid,
            selected_answer="B",
        )
        assert result["is_correct"] is True
        assert result["level"] == LEVEL_RULE
        # 故障期间一个事件都没落，但 attempts 等主链路数据不受影响
        assert _events(student_id) == []

    def test_new_event_types_pass_enum_guard(self) -> None:
        """三类新事件类型在八类枚举硬校验内（DB CHECK + 服务层双保险）。"""
        from app.models.dna import LEARNING_EVENT_TYPE_VALUES

        for event_type in ("QUESTIONVIEW", "SESSIONSTART", "SESSIONEND"):
            assert event_type in LEARNING_EVENT_TYPE_VALUES


# ==================================================================== ② chat 归参
class TestChatEventAttribution:
    """chat 侧 HINT/REVIEW 事件的 question_id/kp_id 归参与 unknown 标记。"""

    def test_chat_hint_event_has_question_and_kp_attribution(
        self, client, new_phone, monkeypatch
    ) -> None:
        """HINT 事件带 question_id/kp_id（断点2 修复）+ 开场发 SESSIONSTART。"""
        kp_name = _uniq("归参知识点")
        kp_id = _make_kp(kp_name)
        patch_ark(monkeypatch, VerdictFakeArk(verdict="correct", knowledge=kp_name))
        headers, student_id = login(client, new_phone())

        started = assert_ok(
            client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"})
        )
        session_id = started["session_id"]

        replied = assert_ok(
            client.post(
                f"/api/chat/{session_id}/message",
                headers=headers,
                json={"text": "这一步我想不出来，给我一点提示", "mode": "HINT"},
            )
        )
        assert replied["mode"] == "HINT"

        with SessionLocal() as db:
            db.rollback()
            session = db.execute(
                select(AiSession).where(AiSession.id == uuid.UUID(session_id))
            ).scalar_one()
            session_question_id = session.question_id
        assert session_question_id is not None  # 开场已把咨询题沉淀归档

        hints = _events(student_id, "HINT")
        assert len(hints) == 1
        assert hints[0].question_id == session_question_id
        assert hints[0].knowledge_point_id == uuid.UUID(kp_id)
        assert "question_id_unknown" not in (hints[0].meta or {})
        assert "kp_id_unknown" not in (hints[0].meta or {})
        assert hints[0].meta["session_id"] == session_id  # 旧键不变

        starts = _events(student_id, "SESSIONSTART")
        assert len(starts) == 1
        assert starts[0].meta["channel"] == "ai_chat"
        assert starts[0].question_id == session_question_id
        assert starts[0].knowledge_point_id == uuid.UUID(kp_id)

    def test_chat_hint_event_marks_unknown_without_question(
        self, client, new_phone, monkeypatch
    ) -> None:
        """会话未绑定题目：HINT 事件 question_id 为空 + meta 标 question_id_unknown。"""
        patch_ark(monkeypatch, VerdictFakeArk(verdict="correct"))
        headers, student_id = login(client, new_phone())

        with SessionLocal() as db:
            db.rollback()
            session = AiSession(student_id=student_id, status="active")
            db.add(session)
            db.commit()
            session_id = session.id
            result = orchestrator.continue_session(
                db=db,
                session_id=session_id,
                user_text="给我一点提示",
                mode="HINT",
            )
        assert result["mode"] == "HINT"

        hints = _events(student_id, "HINT")
        assert len(hints) == 1
        assert hints[0].question_id is None
        assert hints[0].knowledge_point_id is None
        assert hints[0].meta["question_id_unknown"] is True

    def test_chat_emit_failure_does_not_break_conversation(
        self, client, new_phone, monkeypatch
    ) -> None:
        """chat 埋点故障：辅导对话照常返回（旁路容错）。"""
        def _boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("埋点故障（测试注入）")

        monkeypatch.setattr(
            "app.services.student_dna_service.record_learning_event", _boom
        )
        patch_ark(monkeypatch, VerdictFakeArk(verdict="correct"))
        headers, student_id = login(client, new_phone())

        started = assert_ok(
            client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"})
        )
        replied = assert_ok(
            client.post(
                f"/api/chat/{started['session_id']}/message",
                headers=headers,
                json={"text": "给我一点提示", "mode": "HINT"},
            )
        )
        assert replied["reply"]
        assert replied["mode"] == "HINT"
        assert _events(student_id, "HINT") == []


# ==================================================================== AI 练习链路
class TestAiExerciseEvents:
    """AI 练习链路：出题展示 QUESTIONVIEW + 学习小结收尾 SESSIONEND。"""

    def test_ai_exercise_questionview_and_sessionend(
        self, client, new_phone, monkeypatch
    ) -> None:
        """出题发 QUESTIONVIEW（channel=ai_exercise）；小结发 SESSIONEND。"""
        patch_ark(monkeypatch, VerdictFakeArk(verdict="correct"))
        headers, student_id = login(client, new_phone())

        session_id = assert_ok(
            client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"})
        )["session_id"]
        exercises = assert_ok(
            client.post("/api/exercise/gen", headers=headers, json={"sessionId": session_id})
        )["exercises"]
        assert len(exercises) == 2

        views = [
            view
            for view in _events(student_id, "QUESTIONVIEW")
            if (view.meta or {}).get("channel") == "ai_exercise"
        ]
        assert len(views) == 2
        assert {str(view.question_id) for view in views} == {
            str(item["question_id"]) for item in exercises
        }

        for item in exercises:
            judged = assert_ok(
                client.post(
                    "/api/exercise/judge",
                    headers=headers,
                    json={
                        "sessionId": session_id,
                        "questionId": item["question_id"],
                        "answer": item["a"],
                        "finalConfirmed": True,
                    },
                )
            )
            assert judged["correct"] is True

        summarized = assert_ok(
            client.post(
                "/api/exercise/summary", headers=headers, json={"sessionId": session_id}
            )
        )
        assert summarized["summary"]

        ends = _events(student_id, "SESSIONEND")
        assert len(ends) == 1
        assert ends[0].meta["channel"] == "ai_exercise"
        assert ends[0].meta["session_id"] == session_id
        assert ends[0].meta["exercise_count"] == 2
        assert ends[0].question_id is not None

        # 小结缓存命中不会重复发 SESSIONEND（天然幂等）
        assert_ok(
            client.post(
                "/api/exercise/summary", headers=headers, json={"sessionId": session_id}
            )
        )
        assert len(_events(student_id, "SESSIONEND")) == 1

"""AI 练习判题链路 · SymPy 数学验证层测试（T07-sympy）。

为什么要这个文件
----------------
AI 生成题的标准答案由 LLM 自产、无第三方数学校验；判题又长期依赖「模型
verdict + 文本比对」双信号——模型「幻觉判对」时孩子白捡一个会，文本比对
又把 ``1/2`` vs ``0.5`` 这类等价形式误判成错。本文件守护新增的第三方
数学校验层：

1. ``judge_and_record`` 判题前先过 ``sympy_judge.judge``（**仲裁层**）：
   题型在 ``SYMPY_SUPPORTED_TYPES`` 且两侧答案均可解析时**按 SymPy 结果判**——
   模型 verdict 说错也算对、说对也算错（专防幻觉）；
2. SymPy 不可判（主观题 / 答案不可解析）时回落原逻辑，不抛错；
3. AI 生成题落库前的轻量合法性检查：``correct_answer`` 为空 / 不可解析时
   只记 warning + 事件 meta 标记，**不中断**出题主链路。

零网络铁律：方舟一律经 ``t03_helpers.patch_ark`` 打桩（netguard 兜底拦
外部 HTTP）；判题本身纯本地 SymPy 计算，不连任何外部服务。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import func, select

from app.ai.orchestrator import EXERCISE_PREFIX
from app.db import SessionLocal
from app.models.ai import AiMessage
from app.models.content import QUESTION_STATUS_ARCHIVED, Answer, Question
from app.services.ai_exercise_service import (
    AI_SOURCE_DOC,
    ANSWER_CHECK_EMPTY,
    ANSWER_CHECK_UNPARSEABLE,
    _sympy_arbitration,
)

from t03_helpers import FakeArk, patch_ark
from test_ai_exercise import VerdictFakeArk

#: 判题用例统一使用的题干（与 FakeArk 默认 OCR 文本保持一致）
SAMPLE_QUESTION: str = "解方程：2x + 3 = 7"

#: 数值题：标准答案 0.5（学生可答等价形式 1/2，也可答错值 0.4）
NUMBER_EXERCISES: List[Dict[str, Any]] = [
    {
        "q": "计算：把 1/2 化成小数是多少？",
        "a": "0.5",
        "type": "计算",
        "role": "fix",
        "tag": "分数化小数",
    },
    {
        "q": "计算：0.25 + 0.25 等于多少？",
        "a": "0.5",
        "type": "计算",
        "role": "transfer",
        "tag": "小数加法",
    },
]

#: 表达式题：标准答案 x+1（学生可答等价形式 1+x）
EXPRESSION_EXERCISES: List[Dict[str, Any]] = [
    {
        "q": "化简：x + 1",
        "a": "x+1",
        "type": "计算",
        "role": "fix",
        "tag": "加法交换律",
    },
    {
        "q": "计算：2 + 3 等于多少？",
        "a": "5",
        "type": "计算",
        "role": "transfer",
        "tag": "基础加法",
    },
]

#: 解方程题：标准答案写成 x = 4（学生可只答 4）
EQUATION_EXERCISES: List[Dict[str, Any]] = [
    {
        "q": "解方程：3x - 5 = 7",
        "a": "x = 4",
        "type": "计算",
        "role": "fix",
        "tag": "移项求解",
    },
    {
        "q": "解方程：2x + 1 = 5",
        "a": "x = 2",
        "type": "计算",
        "role": "transfer",
        "tag": "移项求解",
    },
]

#: 文字应用题（type=应用）：SymPy 闸门不可判，必须回落模型 verdict
WORD_PROBLEM_EXERCISES: List[Dict[str, Any]] = [
    {
        "q": "小明买 3 支笔花了 15 元，每支笔多少元？列方程求解。",
        "a": "3x = 15，x = 5",
        "type": "应用",
        "role": "fix",
        "tag": "迁移练习",
    },
    {
        "q": "小红有 10 元钱，买本子花了 4 元，还剩多少元？",
        "a": "6 元",
        "type": "应用",
        "role": "transfer",
        "tag": "剩余问题",
    },
]

#: 计算题但标准答案不可被 SymPy 解析（模拟 LLM 产出脏答案）
UNPARSEABLE_STD_EXERCISES: List[Dict[str, Any]] = [
    {
        "q": "计算：先阅读理解再作答",
        "a": "答案见讲解",
        "type": "计算",
        "role": "fix",
        "tag": "异常标准答案",
    },
    {
        "q": "计算：1 + 1 等于多少？",
        "a": "2",
        "type": "计算",
        "role": "transfer",
        "tag": "基础加法",
    },
]

#: 落库合法性检查：第一题答案不可解析、第二题正常
BAD_ANSWER_EXERCISES: List[Dict[str, Any]] = [
    {
        "q": "计算：先阅读理解再作答",
        "a": "答案见讲解",
        "type": "计算",
        "role": "fix",
        "tag": "不可解析答案",
    },
    {
        "q": "计算：1 + 1 等于多少？",
        "a": "2",
        "type": "计算",
        "role": "transfer",
        "tag": "可解析答案",
    },
]

#: 落库合法性检查：第一题答案为空、第二题正常
EMPTY_ANSWER_EXERCISES: List[Dict[str, Any]] = [
    {
        "q": "计算：先阅读理解再作答",
        "a": "",
        "type": "计算",
        "role": "fix",
        "tag": "空答案",
    },
    {
        "q": "计算：1 + 1 等于多少？",
        "a": "2",
        "type": "计算",
        "role": "transfer",
        "tag": "可解析答案",
    },
]


# ==================================================================== 公共工具
def _login(client, phone: str):
    """随机验证码登录，返回 ``(请求头, 学生 uuid)``。"""
    from conftest import login_with_code

    data = login_with_code(client, phone)
    student_id = data["user"]["studentId"]
    assert student_id, f"dev 登录应自动建学生档案，studentId 不应为空: {data}"
    return {"Authorization": f"Bearer {data['access']}"}, uuid.UUID(str(student_id))


def _new_session(client, headers: Dict[str, str], text: str = SAMPLE_QUESTION) -> str:
    """开一个辅导会话，返回 sessionId。"""
    resp = client.post("/api/chat", headers=headers, json={"text": text})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0, body
    session_id = body["data"].get("sessionId") or body["data"].get("session_id")
    assert session_id, body
    return str(session_id)


def _gen(client, headers: Dict[str, str], session_id: str) -> List[Dict[str, Any]]:
    """生成练习题，返回 exercises 列表（含 question_id）。"""
    resp = client.post("/api/exercise/gen", headers=headers, json={"sessionId": session_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0, body
    exercises = body["data"]["exercises"]
    assert isinstance(exercises, list) and len(exercises) == 2, exercises
    return exercises


def _judge(
    client, headers: Dict[str, str], session_id: str, question_id: str, answer: str
) -> Dict[str, Any]:
    """提交最终答案（finalConfirmed=True），返回判题 data。"""
    resp = client.post(
        "/api/exercise/judge",
        headers=headers,
        json={
            "sessionId": session_id,
            "questionId": question_id,
            "answer": answer,
            "finalConfirmed": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def _setup_math_question(
    client,
    monkeypatch,
    phone: str,
    exercises: List[Dict[str, Any]],
    *,
    verdict: str,
    error_type: str = "CALC",
):
    """打桩方舟 → 登录 → 开会话 → 出题，返回 ``(请求头, session_id, 第一题 question_id)``。"""
    patch_ark(
        monkeypatch,
        VerdictFakeArk(verdict=verdict, error_type=error_type, exercises=exercises),
    )
    headers, _ = _login(client, phone)
    session_id = _new_session(client, headers)
    first = _gen(client, headers, session_id)[0]
    qid = first.get("question_id")
    assert qid, f"第一题应带 question_id: {first}"
    return headers, session_id, str(qid)


def _count_ai_archived() -> int:
    """``questions`` 中 ``source_doc='ai_gen'`` 且 ``status='archived'`` 的总条数。

    注意：测试库是会话级共享的内存 SQLite，先取基线再取差值，避免被其他
    测试文件已落库的 AI 题干扰。
    """
    with SessionLocal() as db:
        db.rollback()
        return int(
            db.execute(
                select(func.count())
                .select_from(Question)
                .where(
                    Question.source_doc == AI_SOURCE_DOC,
                    Question.status == QUESTION_STATUS_ARCHIVED,
                )
            ).scalar_one()
        )


def _load_answer_checks(session_id: str) -> List[Optional[str]]:
    """从会话的练习题元数据（``EXERCISE_PREFIX`` 消息）里回捞 answer_check 标记。"""
    with SessionLocal() as db:
        db.rollback()
        rows = db.execute(
            select(AiMessage.content)
            .where(
                AiMessage.session_id == uuid.UUID(str(session_id)),
                AiMessage.role == "assistant",
            )
            .order_by(AiMessage.created_at.asc())
        ).scalars()
    checks: List[Optional[str]] = []
    for text in rows:
        text = text or ""
        if not text.startswith(EXERCISE_PREFIX):
            continue
        try:
            payload = json.loads(text[len(EXERCISE_PREFIX):])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, list):
            checks.extend(
                item.get("answer_check") for item in payload if isinstance(item, dict)
            )
    return checks


def _load_std_answer(question_id: str) -> Optional[str]:
    """读回落库的标准答案原文（校验合法性检查不改写、不拦截）。"""
    with SessionLocal() as db:
        db.rollback()
        row = db.execute(
            select(Answer).where(Answer.question_id == uuid.UUID(str(question_id)))
        ).scalar_one()
    return row.correct_answer


# ============================================================ SymPy 仲裁层（判题）
class TestSympyArbitrationDecidesMathAnswers:
    """数值/表达式题：SymPy 能判定时按它判——模型 verdict 说错也算对、说对也算错。"""

    def test_equivalent_form_judged_correct_despite_model_wrong(
        self, client, monkeypatch, new_phone
    ):
        """学生答 1/2、标准答案 0.5：等价形式判对，即使模型 verdict=wrong。"""
        headers, session_id, qid = _setup_math_question(
            client, monkeypatch, new_phone(), NUMBER_EXERCISES, verdict="wrong"
        )
        result = _judge(client, headers, session_id, qid, "1/2")
        assert result["correct"] is True, result
        assert result["mistake_id"] is None, result

    def test_wrong_number_judged_wrong_despite_model_correct(
        self, client, monkeypatch, new_phone
    ):
        """学生答 0.4、标准答案 0.5：判错并入本，即使模型 verdict=correct（防幻觉判对）。"""
        headers, session_id, qid = _setup_math_question(
            client, monkeypatch, new_phone(), NUMBER_EXERCISES, verdict="correct"
        )
        result = _judge(client, headers, session_id, qid, "0.4")
        assert result["correct"] is False, result
        assert result["mistake_id"], result

    def test_expression_commutative_form_judged_correct(
        self, client, monkeypatch, new_phone
    ):
        """学生答 1+x、标准答案 x+1：等价表达式判对，即使模型 verdict=wrong。"""
        headers, session_id, qid = _setup_math_question(
            client, monkeypatch, new_phone(), EXPRESSION_EXERCISES, verdict="wrong"
        )
        result = _judge(client, headers, session_id, qid, "1+x")
        assert result["correct"] is True, result
        assert result["mistake_id"] is None, result

    def test_equation_answer_without_variable_judged_correct(
        self, client, monkeypatch, new_phone
    ):
        """解方程题标准答案写成 x = 4，学生只答 4：判对（SymPy 抽取数值后比较）。"""
        headers, session_id, qid = _setup_math_question(
            client, monkeypatch, new_phone(), EQUATION_EXERCISES, verdict="wrong"
        )
        result = _judge(client, headers, session_id, qid, "4")
        assert result["correct"] is True, result
        assert result["mistake_id"] is None, result


# ============================================================ 回落（SymPy 判不了）
class TestFallbackWhenSympyCannotJudge:
    """SymPy 不可判（主观题 / 答案不可解析）→ 回落原逻辑且不抛错。"""

    def test_word_problem_falls_back_to_model_verdict(
        self, client, monkeypatch, new_phone
    ):
        """应用题（type=应用）：SymPy 闸门不可判 → 以模型 verdict=correct 为准。"""
        headers, session_id, qid = _setup_math_question(
            client, monkeypatch, new_phone(), WORD_PROBLEM_EXERCISES, verdict="correct"
        )
        result = _judge(client, headers, session_id, qid, "每支笔 5 元")
        assert result["correct"] is True, result
        assert result["mistake_id"] is None, result

    def test_unparseable_standard_answer_falls_back_to_model_verdict(
        self, client, monkeypatch, new_phone
    ):
        """计算题但标准答案不可解析（"答案见讲解"）：SymPy 不可判 → 以模型 verdict=wrong 为准。"""
        headers, session_id, qid = _setup_math_question(
            client, monkeypatch, new_phone(), UNPARSEABLE_STD_EXERCISES, verdict="wrong"
        )
        result = _judge(client, headers, session_id, qid, "1/2")
        assert result["correct"] is False, result
        assert result["mistake_id"], result


# ============================================================ 仲裁层单元口径
class TestSympyArbitrationHelper:
    """``_sympy_arbitration`` 单元级口径（无 DB / 无 LLM，纯函数）。"""

    def test_unsupported_type_returns_none(self) -> None:
        """主观题型（应用/图形/None）一律不可判——正则与黑名单不放开。"""
        assert (
            _sympy_arbitration(
                q_type="应用", content="小明买笔", correct_answer="5", selected_answer="5"
            )
            is None
        )
        assert (
            _sympy_arbitration(
                q_type="图形", content="如图", correct_answer="30°", selected_answer="30"
            )
            is None
        )
        assert (
            _sympy_arbitration(
                q_type=None, content=None, correct_answer="5", selected_answer="5"
            )
            is None
        )

    def test_missing_answer_returns_none(self) -> None:
        """任一侧答案缺失即不可判（回落信号①/②）。"""
        assert (
            _sympy_arbitration(
                q_type="计算", content=None, correct_answer="", selected_answer="1"
            )
            is None
        )
        assert (
            _sympy_arbitration(
                q_type="计算", content=None, correct_answer="1", selected_answer=""
            )
            is None
        )
        assert (
            _sympy_arbitration(
                q_type="计算", content=None, correct_answer=None, selected_answer=None
            )
            is None
        )

    def test_unparseable_answer_returns_none(self) -> None:
        """标准答案或学生作答不可解析时不可判，绝不猜测式抽取。"""
        assert (
            _sympy_arbitration(
                q_type="计算",
                content="计算：先阅读理解再作答",
                correct_answer="答案见讲解",
                selected_answer="1/2",
            )
            is None
        )
        assert (
            _sympy_arbitration(
                q_type="计算",
                content="计算：先阅读理解再作答",
                correct_answer="0.5",
                selected_answer="不会做",
            )
            is None
        )

    @pytest.mark.parametrize(
        ("correct_answer", "selected_answer", "expected"),
        [
            ("0.5", "1/2", True),      # 分数 vs 小数
            ("0.5", "0.4", False),     # 数值不等
            ("x+1", "1+x", True),      # 加法交换律
            ("x = 4", "4", True),      # 方程形式 vs 裸数值
            ("x = 4", "x = 99", False),  # 方程解不同
        ],
    )
    def test_parseable_pair_returns_verdict(
        self, correct_answer: str, selected_answer: str, expected: bool
    ) -> None:
        """两侧可解析时给出明确的布尔结论（仲裁层不是哑巴）。"""
        assert (
            _sympy_arbitration(
                q_type="计算",
                content=None,
                correct_answer=correct_answer,
                selected_answer=selected_answer,
            )
            is expected
        )


# ============================================================ 落库合法性检查
class TestGeneratedAnswerLegalityCheck:
    """AI 生成题落库前合法性检查：只记 warning + 事件 meta，不中断出题主链路。"""

    def test_unparseable_answer_warns_and_persists(
        self, client, monkeypatch, new_phone, caplog
    ):
        """标准答案不可解析：warning 留痕 + meta 标记，两道题照常落库、原文不改写。"""
        patch_ark(monkeypatch, FakeArk(exercises=BAD_ANSWER_EXERCISES))
        headers, _ = _login(client, new_phone())

        before = _count_ai_archived()
        with caplog.at_level(logging.WARNING, logger="app.services.ai_exercise"):
            session_id = _new_session(client, headers)
            exercises = _gen(client, headers, session_id)

        # 不中断落库：两道题照常返回 question_id 并入库
        assert _count_ai_archived() == before + 2
        for item in exercises:
            assert item.get("question_id"), item

        # warning 留痕（只记录，不拦截）
        assert "无法被 SymPy 解析" in caplog.text, caplog.text

        # 事件 meta 标记：不可解析 + 正常各一道
        assert _load_answer_checks(session_id) == [ANSWER_CHECK_UNPARSEABLE, "ok"]

        # 原文落库不被校验层改写
        assert _load_std_answer(str(exercises[0]["question_id"])) == "答案见讲解"

    def test_empty_answer_warns_and_persists(
        self, client, monkeypatch, new_phone, caplog
    ):
        """标准答案为空：warning 留痕 + meta 标记，落库不中断（Answer 留空）。"""
        patch_ark(monkeypatch, FakeArk(exercises=EMPTY_ANSWER_EXERCISES))
        headers, _ = _login(client, new_phone())

        before = _count_ai_archived()
        with caplog.at_level(logging.WARNING, logger="app.services.ai_exercise"):
            session_id = _new_session(client, headers)
            exercises = _gen(client, headers, session_id)

        assert _count_ai_archived() == before + 2
        assert "标准答案为空" in caplog.text, caplog.text
        assert _load_answer_checks(session_id) == [ANSWER_CHECK_EMPTY, "ok"]

        # 空答案按既有口径落 ``NULL``（判题时信号①不可用，靠模型 verdict 兜底）
        assert _load_std_answer(str(exercises[0]["question_id"])) is None


__all__ = [
    "TestSympyArbitrationDecidesMathAnswers",
    "TestFallbackWhenSympyCannotJudge",
    "TestSympyArbitrationHelper",
    "TestGeneratedAnswerLegalityCheck",
]

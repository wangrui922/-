"""T10 · T6 落库 / T7 判错即时入本 验收测试（端到端，零网络）。

被测链路（全部经真实 API + 内存 SQLite，方舟一律打桩）：

    POST /api/chat                 开场：读题分析 + 落库会话（供出题回捞分析结论）
    POST /api/exercise/gen        基于会话分析结论出两道练习题 + D1 落库为归档题
    POST /api/exercise/judge      提交最终答案：判对错 + 判错即时入本（D3-b）
    GET  /api/mistake/list        错题本列表（camelCase 出参，含 errorType）

覆盖要求（来自 team-lead T10 子任务 3）：
    * T6 落库：/exercise/gen 后 ``questions`` 中 ``source_doc='ai_gen'``
      且 ``status='archived'`` 恰好 2 条，且返回的 exercises 含 ``questionId``；
    * T7 判错入本：judge 判错 → ``GET /api/mistake/list`` 查得到
      ，``errorType`` ∈ {READ, METHOD, CALC, KNOWLEDGE, CHECK}；
    * 幂等：同一题重复判错不产生第二条未攻克记录（mistake_id 复用）。

零网络铁律：所有方舟调用经 ``t03_helpers.patch_ark`` 换成 ``FakeArk`` 假实现，
绝不发真实 HTTPS（沙箱出网被代理拦截，真实请求会 502 并把测试拖死）。

字段命名：``/exercise/gen`` 的 data 是 snake_case（``question_id``），
``/mistake/list`` 是 camelCase（``questionId`` / ``errorType``）——本文件分别按
实际键取值，不绑死某一种写法。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.content import QUESTION_STATUS_ARCHIVED, Question
from app.services.ai_exercise_service import AI_SOURCE_DOC

from t03_helpers import FakeArk, assert_ok, patch_ark

#: 错因五类机器码（与 ``app.core.error_categories.ERROR_CATEGORIES`` 对齐，
#: 此处独立一份用于断言，避免反向依赖导致环形 import）。
VALID_ERROR_TYPES: Tuple[str, ...] = ("READ", "METHOD", "CALC", "KNOWLEDGE", "CHECK")

#: 测试统一使用的题面（与 FakeArk 默认 OCR 文本保持一致，便于人工比对）
SAMPLE_QUESTION: str = "解方程：2x + 3 = 7"


# ==================================================================== 打桩
class VerdictFakeArk(FakeArk):
    """``ark_respond`` 假实现：给讲解/评判（practice_guide）调用返回结构化 verdict JSON。

    ``ai_exercise_service._ask_guide_model`` 调用方舟时**不传** ``system``，
    默认 ``FakeArk`` 会把它归类为 ``tutor`` 并返回一句纯文字引导语（非 JSON），
    那样 ``extract_json`` 会失败 → ``verdict='pending'`` → 判错链路拿不到
    ``errorType``。本子类把 ``tutor`` 类调用改写为返回合规的 verdict JSON，
    其余种类（analyze / exercise / ocr / diagnose）沿用父类行为。
    """

    def __init__(
        self,
        *,
        verdict: str = "wrong",
        error_type: str = "CALC",
        guide_reply: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.verdict = verdict
        self.error_type = error_type
        self.guide_reply = guide_reply or "这一步我们先对齐一下思路，你说说看哪里卡住了？"

    def _reply(self, kind: str) -> str:
        if kind == "tutor":
            payload = {
                "reply": self.guide_reply,
                "askFinalConfirm": False,
                "verdict": self.verdict,
                # 只有 verdict=wrong 才带 errorType；correct/pending 一律 null
                "errorType": self.error_type if self.verdict == "wrong" else None,
            }
            return json.dumps(payload, ensure_ascii=False)
        return super()._reply(kind)


# ==================================================================== 公共工具
def _login(client, phone: str) -> Dict[str, Any]:
    """随机验证码登录，返回登录响应的 ``data``（首次登录自动建号 + 建学生档案）。"""
    from conftest import login_with_code

    return login_with_code(client, phone)


def _auth(client, phone: str) -> Tuple[Dict[str, str], uuid.UUID]:
    """用给定手机号登录并返回 ``(请求头, 学生 uuid)``。"""
    data = _login(client, phone)
    student_id = data["user"]["studentId"]
    assert student_id, f"dev 登录应自动建学生档案，studentId 不应为空: {data}"
    return {"Authorization": f"Bearer {data['access']}"}, uuid.UUID(str(student_id))


def _pick(data: Dict[str, Any], *names: str) -> Any:
    """按候选名依次取值（兼容 camelCase / snake_case 两种出参命名）。"""
    for name in names:
        if name in data:
            return data[name]
    raise AssertionError(f"响应缺少字段 {names}，实际键为 {sorted(data)}")


def _start_session(client, headers: Dict[str, str], text: str = SAMPLE_QUESTION) -> str:
    """开一个辅导会话，返回 sessionId（供出题/判错用例复用）。"""
    resp = client.post("/api/chat", headers=headers, json={"text": text})
    data = assert_ok(resp)
    return str(_pick(data, "sessionId", "session_id"))


def _gen_exercises(client, headers: Dict[str, str], session_id: str) -> List[Dict[str, Any]]:
    """生成练习题，返回 exercises 列表（含 question_id）。"""
    data = assert_ok(
        client.post("/api/exercise/gen", headers=headers, json={"sessionId": session_id})
    )
    exercises = data["exercises"]
    assert isinstance(exercises, list) and len(exercises) == 2, data
    return exercises


def _count_ai_archived_questions(student_id: Optional[uuid.UUID] = None) -> int:
    """``questions`` 中 ``source_doc='ai_gen'`` 且 ``status='archived'`` 的条数。"""
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


# ==================================================================== T6 落库
def test_generate_exercise_persists_two_archived_ai_questions(client, monkeypatch, new_phone):
    """T6：/exercise/gen 后恰好两道题落库为 ``ai_gen`` + ``archived`` 归档题。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client, new_phone())

    session_id = _start_session(client, headers)
    exercises = _gen_exercises(client, headers, session_id)

    # 落库断言：D1 隔离位 —— 2 道归档 AI 题已进 questions 表
    assert _count_ai_archived_questions() == 2

    # 返回的每条都带合法 questionId（供判错入本挂外键）
    for index, item in enumerate(exercises):
        qid = item.get("question_id")
        assert qid, f"第 {index + 1} 题缺少 question_id: {item}"
        uuid.UUID(str(qid))  # 必须是合法 UUID


def test_generate_exercise_question_id_resolves_to_archived_row(client, monkeypatch, new_phone):
    """T6：返回的 questionId 在库中确为 ``ai_gen`` + ``archived`` 的归档题。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client, new_phone())

    session_id = _start_session(client, headers)
    exercises = _gen_exercises(client, headers, session_id)
    qid = uuid.UUID(str(exercises[0]["question_id"]))

    with SessionLocal() as db:
        db.rollback()
        row = db.execute(select(Question).where(Question.id == qid)).scalar_one_or_none()
        assert row is not None, f"question_id={qid} 在 questions 表中查不到"
        assert row.source_doc == AI_SOURCE_DOC, row.source_doc
        assert row.status == QUESTION_STATUS_ARCHIVED, row.status
        # 归档题绝不能污染题库抽题池
        assert row.source_doc != "", row.source_doc


def test_generate_exercise_uses_same_then_slightly_harder_difficulty(
    client, monkeypatch, new_phone
):
    """两道相似题固定为原题同难度、下一档略难，不采信模型随意给的等级。"""
    patch_ark(
        monkeypatch,
        FakeArk(
            difficulty=3,
            exercises=[
                {
                    "q": "同考点基础题",
                    "a": "1",
                    "difficulty": 1,
                    "role": "fix",
                    "tag": "同难度练习",
                },
                {
                    "q": "同考点迁移题",
                    "a": "2",
                    "difficulty": 1,
                    "role": "transfer",
                    "tag": "略难迁移",
                },
            ],
        ),
    )
    headers, _ = _auth(client, new_phone())

    exercises = _gen_exercises(client, headers, _start_session(client, headers))

    assert [item["difficulty"] for item in exercises] == [3, 5], exercises


def test_judge_requires_explicit_final_answer_confirmation(client, monkeypatch, new_phone):
    """没有回答“确定是最终答案”时，任何中间步骤都不能直接进入最终判题。"""
    ark = patch_ark(monkeypatch, VerdictFakeArk(verdict="wrong", error_type="CALC"))
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)
    qid = str(_gen_exercises(client, headers, session_id)[0]["question_id"])

    response = client.post(
        "/api/exercise/judge",
        headers=headers,
        json={"sessionId": session_id, "questionId": qid, "answer": "2x=4"},
    )

    assert response.status_code == 400, response.text
    assert ark.count("tutor") == 1, "未确认时不应调用最终判题模型"


def test_summary_requires_both_exercises_and_is_idempotent(client, monkeypatch, new_phone):
    """两道练习都最终判定后才生成小结，重复请求复用已保存的小结。"""
    ark = patch_ark(monkeypatch, VerdictFakeArk(verdict="correct"))
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)
    exercises = _gen_exercises(client, headers, session_id)

    early = client.post(
        "/api/exercise/summary",
        headers=headers,
        json={"sessionId": session_id},
    )
    assert early.status_code == 400, early.text

    guided = client.post(
        "/api/exercise/guide",
        headers=headers,
        json={
            "sessionId": session_id,
            "questionId": exercises[0]["question_id"],
            "work": "第一步我先找出题目里的数量关系",
        },
    )
    assert guided.status_code == 200, guided.text

    for item in exercises:
        judged = client.post(
            "/api/exercise/judge",
            headers=headers,
            json={
                "sessionId": session_id,
                "questionId": item["question_id"],
                "answer": item["a"],
                "work": "我写出了完整过程，这是最终答案。",
                "finalConfirmed": True,
            },
        )
        assert judged.status_code == 200, judged.text

    first = assert_ok(
        client.post("/api/exercise/summary", headers=headers, json={"sessionId": session_id})
    )
    second = assert_ok(
        client.post("/api/exercise/summary", headers=headers, json={"sessionId": session_id})
    )
    assert first["summary"].startswith("【今天学习】"), first
    assert second["summary"] == first["summary"], second
    assert ark.count("summary") == 1, ark.kinds()
    summary_call = next(call for call in ark.calls if call["kind"] == "summary")
    assert "第一步我先找出题目里的数量关系" in summary_call["messages"][0]["content"]


def test_new_exercise_batch_clears_old_summary(client, monkeypatch, new_phone):
    """重新生成一组练习后，不得继续返回上一组已经缓存的小结。"""
    ark = patch_ark(monkeypatch, VerdictFakeArk(verdict="correct"))
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)
    first_batch = _gen_exercises(client, headers, session_id)

    for item in first_batch:
        response = client.post(
            "/api/exercise/judge",
            headers=headers,
            json={
                "sessionId": session_id,
                "questionId": item["question_id"],
                "answer": item["a"],
                "finalConfirmed": True,
            },
        )
        assert response.status_code == 200, response.text

    first_summary = client.post(
        "/api/exercise/summary", headers=headers, json={"sessionId": session_id}
    )
    assert first_summary.status_code == 200, first_summary.text
    assert ark.count("summary") == 1

    _gen_exercises(client, headers, session_id)
    second_summary = client.post(
        "/api/exercise/summary", headers=headers, json={"sessionId": session_id}
    )

    assert second_summary.status_code == 400, second_summary.text
    assert ark.count("summary") == 1, "新一组题未完成时不应生成或返回小结"


# ==================================================================== T7 判错入本
def test_judge_wrong_records_mistake_with_valid_error_type(client, monkeypatch, new_phone):
    """T7：judge 判错 → 错题本查得到，且 errorType 属五类机器码之一。"""
    patch_ark(monkeypatch, VerdictFakeArk(verdict="wrong", error_type="CALC"))
    headers, student_id = _auth(client, new_phone())

    session_id = _start_session(client, headers)
    exercises = _gen_exercises(client, headers, session_id)
    qid = str(exercises[0]["question_id"])

    # 交一个明显错误的答案（标准答案在 exercises[0]["a"]）
    judge = assert_ok(
        client.post(
            "/api/exercise/judge",
            headers=headers,
            json={
                "sessionId": session_id,
                "questionId": qid,
                "answer": "x = 99",
                "finalConfirmed": True,
            },
        )
    )
    assert judge["correct"] is False, judge
    assert judge["mistake_id"], judge

    # 错题本查得到这道错题
    listing = assert_ok(client.get("/api/mistake/list", headers=headers))
    items = [m for m in listing["items"] if m["questionId"] == qid]
    assert items, f"错题本未收录该判错题；全部 items={listing['items']}"
    assert len(items) == 1, items
    # errorType 必须是五类机器码之一
    assert items[0]["errorType"] == "CALC", items[0]
    assert items[0]["errorType"] in VALID_ERROR_TYPES, items[0]


@pytest.mark.parametrize("error_type", list(VALID_ERROR_TYPES))
def test_judge_wrong_covers_all_five_error_categories(
    client, monkeypatch, new_phone, error_type
):
    """T7 暴力覆盖：五类错因机器码（READ/METHOD/CALC/KNOWLEDGE/CHECK）经 judge 入本后均可被查到。"""
    patch_ark(monkeypatch, VerdictFakeArk(verdict="wrong", error_type=error_type))
    headers, _ = _auth(client, new_phone())

    session_id = _start_session(client, headers)
    exercises = _gen_exercises(client, headers, session_id)
    qid = str(exercises[0]["question_id"])

    assert_ok(
        client.post(
            "/api/exercise/judge",
            headers=headers,
            json={
                "sessionId": session_id,
                "questionId": qid,
                "answer": "x = 99",
                "finalConfirmed": True,
            },
        )
    )

    listing = assert_ok(client.get("/api/mistake/list", headers=headers))
    items = [m for m in listing["items"] if m["questionId"] == qid]
    assert items, f"错题本未收录 errorType={error_type} 的错题；items={listing['items']}"
    assert items[0]["errorType"] == error_type, items[0]


# ==================================================================== 幂等
def test_judge_idempotent_same_question_no_duplicate(client, monkeypatch, new_phone):
    """幂等：同一题重复判错不产生第二条未攻克记录，mistake_id 复用。"""
    patch_ark(monkeypatch, VerdictFakeArk(verdict="wrong", error_type="CALC"))
    headers, _ = _auth(client, new_phone())

    session_id = _start_session(client, headers)
    exercises = _gen_exercises(client, headers, session_id)
    qid = str(exercises[0]["question_id"])

    first = assert_ok(
        client.post(
            "/api/exercise/judge",
            headers=headers,
            json={
                "sessionId": session_id,
                "questionId": qid,
                "answer": "x = 99",
                "finalConfirmed": True,
            },
        )
    )
    first_mid = first["mistake_id"]
    assert first_mid, first

    # 同题、同错误答案，再判一次
    second = assert_ok(
        client.post(
            "/api/exercise/judge",
            headers=headers,
            json={
                "sessionId": session_id,
                "questionId": qid,
                "answer": "x = 99",
                "finalConfirmed": True,
            },
        )
    )
    # 必须复用同一条未攻克记录
    assert second["mistake_id"] == first_mid, (first_mid, second["mistake_id"])
    assert second["correct"] is False, second

    # 错题本里该学生针对此题只有 1 条记录
    listing = assert_ok(client.get("/api/mistake/list", headers=headers))
    items = [m for m in listing["items"] if m["questionId"] == qid]
    assert len(items) == 1, items


# ==================================================================== 边界 / 负向
def test_judge_correct_does_not_record_mistake(client, monkeypatch, new_phone):
    """负向：judge 判对（模型 verdict=correct）不应写入错题本。"""
    patch_ark(monkeypatch, VerdictFakeArk(verdict="correct"))
    headers, _ = _auth(client, new_phone())

    session_id = _start_session(client, headers)
    exercises = _gen_exercises(client, headers, session_id)
    qid = str(exercises[0]["question_id"])
    correct_answer = str(exercises[0]["a"]).strip()  # 标准答案，用作正确作答

    judge = assert_ok(
        client.post(
            "/api/exercise/judge",
            headers=headers,
            json={
                "sessionId": session_id,
                "questionId": qid,
                "answer": correct_answer,
                "finalConfirmed": True,
            },
        )
    )
    assert judge["correct"] is True, judge
    assert judge["mistake_id"] is None, judge

    listing = assert_ok(client.get("/api/mistake/list", headers=headers))
    items = [m for m in listing["items"] if m["questionId"] == qid]
    assert items == [], f"判对不应入本，但查到：{listing['items']}"


def test_judge_rejects_unknown_session(client, monkeypatch, new_phone):
    """边界：sessionId 指向不存在的会话 → 404，且不产生错题。"""
    patch_ark(monkeypatch, VerdictFakeArk(verdict="wrong", error_type="CALC"))
    headers, _ = _auth(client, new_phone())

    # 先出题拿一个真实 question_id（来自别的会话也不影响本题校验）
    real_session = _start_session(client, headers)
    exercises = _gen_exercises(client, headers, real_session)
    qid = str(exercises[0]["question_id"])

    resp = client.post(
        "/api/exercise/judge",
        headers=headers,
        json={
            "sessionId": str(uuid.uuid4()),
            "questionId": qid,
            "answer": "x = 99",
            "finalConfirmed": True,
        },
    )
    # 会话不存在 → 404（见 ai_exercise_service._load_ai_session）
    assert resp.status_code == 404, resp.text

    # 不应因这次非法请求产生错题
    listing = assert_ok(client.get("/api/mistake/list", headers=headers))
    items = [m for m in listing["items"] if m["questionId"] == qid]
    assert items == [], listing["items"]


def test_generate_exercise_requires_auth(client, monkeypatch):
    """边界：未带 Authorization 出练习题 → 401，且不调用出题 Agent。"""
    ark = patch_ark(monkeypatch)
    resp = client.post("/api/exercise/gen", json={"sessionId": str(uuid.uuid4())})
    assert resp.status_code == 401, resp.text
    assert ark.count("exercise") == 0, ark.kinds()


__all__ = [
    "VerdictFakeArk",
    "test_generate_exercise_persists_two_archived_ai_questions",
    "test_generate_exercise_question_id_resolves_to_archived_row",
    "test_judge_wrong_records_mistake_with_valid_error_type",
    "test_judge_wrong_covers_all_five_error_categories",
    "test_judge_idempotent_same_question_no_duplicate",
    "test_judge_correct_does_not_record_mistake",
    "test_judge_rejects_unknown_session",
    "test_generate_exercise_requires_auth",
]

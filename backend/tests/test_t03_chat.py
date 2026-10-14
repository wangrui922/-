"""T03 AI 教学闭环集成测试：拍题开场 → 多轮引导 → 生成练习题。

覆盖三个接口的端到端链路（含落库校验）：
    * ``POST /api/chat``                      开场：读题分析 + 首轮引导
    * ``POST /api/chat/{session_id}/message`` 多轮：带历史继续对话
    * ``POST /api/exercise/gen``              基于会话分析结论出两道练习题

隔离策略：
    所有方舟调用一律用 ``t03_helpers.patch_ark`` 换成 ``FakeArk``，
    **不产生任何真实网络请求**；数据库用 conftest 提供的内存 SQLite。

字段命名说明（重要）：
    ``/api/chat`` 与 ``/api/exercise/gen`` 的 data 由 ``orchestrator`` 原样透传，
    当前是 **snake_case**（``session_id`` / ``first_question`` / ``question_text``），
    与 docs/v4-db-api.md §B.2 的示例一致；而 ``/api/mistake/list`` 是 camelCase。
    为了不把测试绑死在某一种写法上（前端 ``student-web/src/api/chat.ts`` 两种都兼容），
    这里统一用 ``_pick`` 取值，两种命名都算通过。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from app.db import SessionLocal

from t03_helpers import assert_fail, assert_ok, count_sessions, load_messages, patch_ark

#: 测试统一使用的题面（与 FakeArk 默认的 OCR 文本保持一致，便于人工比对）
SAMPLE_QUESTION: str = "解方程：2x + 3 = 7"


# ------------------------------------------------------------------ 公共工具
def _login(client) -> Dict[str, Any]:
    """随机验证码登录，返回登录响应的 ``data``（自动建号 + 建学生档案）。"""
    from conftest import login_with_code

    return login_with_code(client)


def _auth(client) -> Tuple[Dict[str, str], uuid.UUID]:
    """登录并返回 ``(请求头, 学生 uuid)``。

    Returns:
        ``({"Authorization": "Bearer ..."}, uuid.UUID)``。
    """
    data = _login(client)
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
    """开一个辅导会话，返回 sessionId（供多轮/出题用例复用）。"""
    resp = client.post("/api/chat", headers=headers, json={"text": text})
    data = assert_ok(resp)
    return str(_pick(data, "sessionId", "session_id"))


# ------------------------------------------------------------------ 开场
def test_start_chat_creates_session(client, monkeypatch):
    """拍题开场：返回合法 sessionId / 分析结论 / 首轮引导，并落库一条会话与两条消息。"""
    ark = patch_ark(monkeypatch)
    headers, student_id = _auth(client)

    with SessionLocal() as db:
        before = count_sessions(db, student_id)

    data = assert_ok(client.post("/api/chat", headers=headers, json={"text": SAMPLE_QUESTION}))

    # 1) sessionId 必须是合法 UUID
    session_id = str(_pick(data, "sessionId", "session_id") or "").strip()
    assert session_id, f"sessionId 不能为空: {data}"
    uuid.UUID(session_id)

    # 2) analysis 必须带上知识点/难度/题型，且值来自被 mock 的分析 JSON
    analysis = data["analysis"]
    assert isinstance(analysis, dict), data
    assert str(analysis.get("knowledge") or "").strip(), analysis
    assert analysis["knowledge"] == ark.knowledge, analysis
    assert analysis["difficulty"] == ark.difficulty, analysis
    assert analysis["type"] == ark.qtype, analysis
    assert str(_pick(analysis, "questionText", "question_text") or "").strip() == SAMPLE_QUESTION

    # 3) 首轮引导非空
    first_question = str(_pick(data, "firstQuestion", "first_question") or "").strip()
    assert first_question, data

    # 4) 落库：ai_sessions 恰好多一条；ai_messages 落了「分析 + 首问」两条 assistant
    with SessionLocal() as db:
        assert count_sessions(db, student_id) == before + 1
        messages = load_messages(db, session_id)

    assert len(messages) == 2, [m.content for m in messages]
    assert {m.role for m in messages} == {"assistant"}
    contents = [m.content for m in messages]
    assert any(c.startswith("分析：") for c in contents), contents
    assert first_question in contents, contents

    # 5) 只走了「分析 + 引导」两次 AI 调用，纯文字输入不应触发 OCR
    assert ark.count("analyze") == 1, ark.kinds()
    assert ark.count("tutor") == 1, ark.kinds()
    assert ark.count("ocr") == 0, ark.kinds()


def test_start_chat_requires_text_or_image(client, monkeypatch):
    """题目文字与图片同时为空 -> 400，且不应触发任何 AI 调用。"""
    ark = patch_ark(monkeypatch)
    headers, _ = _auth(client)

    body = assert_fail(client.post("/api/chat", headers=headers, json={}), 400)
    assert str(body.get("msg") or "").strip(), body
    assert ark.calls == [], ark.kinds()

    # 只给空白字符同样视为空
    assert_fail(client.post("/api/chat", headers=headers, json={"text": "   "}), 400)
    assert ark.calls == [], ark.kinds()


def test_start_chat_requires_auth(client, monkeypatch):
    """未带 Authorization -> 401，且不应触发任何 AI 调用。"""
    ark = patch_ark(monkeypatch)

    assert_fail(client.post("/api/chat", json={"text": SAMPLE_QUESTION}), 401)
    assert ark.calls == [], ark.kinds()


# ------------------------------------------------------------------ 多轮引导
def test_continue_session(client, monkeypatch):
    """多轮引导：返回非空 reply，并把 user / assistant 两条消息追加进会话。"""
    ark = patch_ark(monkeypatch)
    headers, _ = _auth(client)
    session_id = _start_session(client, headers)

    child_says = "我先把 3 移到右边，得到 2x = 4"
    data = assert_ok(
        client.post(f"/api/chat/{session_id}/message", headers=headers, json={"text": child_says})
    )

    reply = str(data["reply"] or "").strip()
    assert reply, data

    with SessionLocal() as db:
        messages = load_messages(db, session_id)

    assert len(messages) == 4, [(m.role, m.content) for m in messages]
    assert sorted(m.role for m in messages) == ["assistant", "assistant", "assistant", "user"]
    assert any(m.role == "user" and m.content == child_says for m in messages)
    assert any(m.role == "assistant" and m.content == reply for m in messages)

    # 开场 2 次（analyze + tutor）+ 本轮 1 次 tutor
    assert ark.count("tutor") == 2, ark.kinds()


def test_continue_session_rejects_empty_text(client, monkeypatch):
    """孩子这一轮什么都没说 -> 400，且不追加消息、不调用 AI。"""
    ark = patch_ark(monkeypatch)
    headers, _ = _auth(client)
    session_id = _start_session(client, headers)
    calls_after_start = len(ark.calls)

    assert_fail(client.post(f"/api/chat/{session_id}/message", headers=headers, json={"text": "  "}), 400)

    assert len(ark.calls) == calls_after_start, ark.kinds()
    with SessionLocal() as db:
        assert len(load_messages(db, session_id)) == 2


def test_continue_session_rejects_unknown_session(client, monkeypatch):
    """会话不存在 -> 400。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)

    assert_fail(
        client.post(f"/api/chat/{uuid.uuid4()}/message", headers=headers, json={"text": "在吗"}),
        400,
    )


# ------------------------------------------------------------------ 出练习题
def test_generate_exercise(client, monkeypatch):
    """出题：恰好两道，字段齐全，分工为 fix + transfer，知识点沿用会话分析结论。"""
    ark = patch_ark(monkeypatch)
    headers, _ = _auth(client)
    session_id = _start_session(client, headers)

    data = assert_ok(
        client.post("/api/exercise/gen", headers=headers, json={"sessionId": session_id})
    )

    exercises = data["exercises"]
    assert isinstance(exercises, list), data
    assert len(exercises) == 2, exercises

    for index, item in enumerate(exercises):
        for key in ("q", "a", "type", "knowledge", "difficulty", "role", "tag"):
            assert key in item, f"第 {index + 1} 题缺字段 {key}: {item}"
        assert str(item["q"]).strip(), item
        assert str(item["a"]).strip(), item
        assert str(item["role"]).strip(), item

    assert [item["role"] for item in exercises] == ["fix", "transfer"], exercises
    # 出题时会回捞开场落库的分析 JSON，知识点/难度应与之一致
    assert all(item["knowledge"] == ark.knowledge for item in exercises), exercises
    expected_harder = next(
        (level for level in (1, 3, 5) if level > ark.difficulty), ark.difficulty
    )
    assert [item["difficulty"] for item in exercises] == [ark.difficulty, expected_harder], exercises
    assert ark.count("exercise") == 1, ark.kinds()


def test_generate_exercise_rejects_unknown_session(client, monkeypatch):
    """sessionId 指向不存在的会话 -> 400，且不调用出题 Agent。"""
    ark = patch_ark(monkeypatch)
    headers, _ = _auth(client)

    assert_fail(
        client.post("/api/exercise/gen", headers=headers, json={"sessionId": str(uuid.uuid4())}),
        400,
    )
    assert ark.count("exercise") == 0, ark.kinds()

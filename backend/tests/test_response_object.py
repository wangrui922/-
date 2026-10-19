"""TASK-002 学生回答结构化测试（V1 规则 analyzer + response_object 列）。

覆盖设计文档 §7 清单（12 组 + 补充）：
    1. user 消息落库时带上结构化对象（FINAL_ANSWER + answer 抽取）；
    2. 八枚举参数化覆盖（含 UNCLEAR 兜底）；
    3. 抽不到的字段为 None（不编造）；
    4. 「有且只有一个数字」守卫（多个数字不猜答案）；
    5. 老消息（response_object NULL）读取降级不炸；
    6. assistant 消息结构化列恒 NULL；
    7. **mode 逐字不变回归铁律**（结构化是旁路，不改教学策略）；
    8. raw content 逐字保留（重分析前提）；
    9. 脏 JSON 容错；
    10. analyzer 纯函数单测（零 DB）；
    11. analyzer 内部失败 → UNCLEAR 契约兜底（旁路容错）；
    12. analyzer 整体抛异常 → 列落 NULL、聊天照常（双保险）；
    13. 低置信度闸门回归（与 #29 共存不互扰）；
    14. latest_response_objects 顺序与 limit；
    15. 错因自报 + step_state 信号。

测试铁律：**绝不设置 DATABASE_URL**（conftest 默认 :memory:）。方舟调用一律
``t03_helpers.patch_ark`` 换成 ``FakeArk``，无真实网络请求。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import pytest

from app.ai import intent, orchestrator
from app.ai import _num_utils
from app.ai import response_analyzer
from app.ai.response_analyzer import RESPONSE_TYPE_VALUES
from app.db import SessionLocal
from app.models.ai import AiMessage, AiSession
from app.models.user import Student, User
from t03_helpers import FakeArk, assert_ok, load_messages, patch_ark

#: 开一场会话并返回 (headers, student_id, session_id)
SAMPLE_QUESTION: str = "解方程：2x + 3 = 7"


# ====================================================================  fixtures
@pytest.fixture
def confidence_threshold(monkeypatch):
    """低置信度阈值夹具（回归 #29 闸门用）：替换 system_config._cache。"""

    def _apply(value: float) -> None:
        from app.core import system_config

        monkeypatch.setattr(system_config, "_cache", {"CONFIDENCE_RETHRESHOLD": value})

    yield _apply


# ====================================================================  helpers
def _auth(client, phone: str) -> Tuple[Dict[str, str], uuid.UUID]:
    """指定手机号登录（每个用例一个新号 = 一个干净学生）。"""
    from conftest import login_with_code

    data = login_with_code(client, phone)
    student_id = data["user"]["studentId"]
    assert student_id, f"dev 登录应自动建学生档案: {data}"
    return {"Authorization": f"Bearer {data['access']}"}, uuid.UUID(str(student_id))


def _make_student(session) -> uuid.UUID:
    """直接造一个干净学生（测试库 FK 已开，AiSession.student_id 必须真实存在）。"""
    user = User(id=uuid.uuid4(), phone=f"13{uuid.uuid4().hex[:9]}", nickname="结构化测试")
    session.add(user)
    session.flush()
    student = Student(id=uuid.uuid4(), user_id=user.id, grade=4)
    session.add(student)
    session.flush()
    return student.id


def _start_session(client, headers: Dict[str, str], text: str = SAMPLE_QUESTION) -> str:
    """开一场辅导会话，返回 session_id。"""
    data = assert_ok(client.post("/api/chat", headers=headers, json={"text": text}))
    session_id = str(data.get("session_id") or "")
    assert session_id, data
    return session_id


def _continue(client, headers: Dict[str, str], session_id: str, text: str) -> Dict[str, Any]:
    """多轮引导一句，返回 data。"""
    return assert_ok(
        client.post(
            f"/api/chat/{session_id}/message", headers=headers, json={"text": text}
        )
    )


def _user_messages(db, session_id: str) -> List[AiMessage]:
    """该会话的 user 消息（按时间升序）。"""
    db.rollback()
    return [m for m in load_messages(db, session_id) if m.role == "user"]


# ====================================================================  ① 写入
def test_user_message_gets_response_object(client, monkeypatch, new_phone):
    """① 学生说「我算出来是 x = 2，对不对」→ user 消息带结构化对象。"""
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)

    _continue(client, headers, session_id, "我算出来是 x = 2，对不对")

    with SessionLocal() as db:
        user_messages = _user_messages(db, session_id)
    assert len(user_messages) == 1
    obj = user_messages[0].response_object
    assert isinstance(obj, dict), obj
    assert obj["response_type"] == "FINAL_ANSWER", obj
    assert obj["answer"] == "x=2", obj
    assert obj["evidence"] == ["x=2"], obj
    assert obj["confidence"] == 0.8, obj  # 多规则命中
    # 7 键恒存在
    assert set(obj) == {
        "response_type",
        "answer",
        "reasoning",
        "evidence",
        "error",
        "confidence",
        "step_state",
    }, obj


# ====================================================================  ② 枚举覆盖
@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("我算出来是 x = 2，对不对", "FINAL_ANSWER"),
        ("因为两边都要除以 2，所以先移项", "REASONING"),
        ("这道题求什么呀", "QUESTION"),
        ("给点提示吧", "HINT_REQUEST"),
        ("我不会做", "I_DONT_KNOW"),
        ("我算到 2x=4 了，后面不会", "PARTIAL_ANSWER"),
        ("不对，我改一下，应该是 5", "CORRECTION"),
        ("……", "UNCLEAR"),
    ],
)
def test_response_type_enum_coverage(client, monkeypatch, new_phone, text, expected_type):
    """② 八枚举参数化：每组语料落对应枚举（UNCLEAR 兜底）。"""
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)

    _continue(client, headers, session_id, text)

    with SessionLocal() as db:
        user_messages = _user_messages(db, session_id)
    obj = user_messages[0].response_object
    assert obj["response_type"] == expected_type, (text, obj)
    assert obj["response_type"] in RESPONSE_TYPE_VALUES


# ====================================================================  ③ 缺省 None
def test_missing_fields_are_none(client, monkeypatch, new_phone):
    """③ 「给点提示吧」→ 抽不到的字段为 None，不编造。"""
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)

    _continue(client, headers, session_id, "给点提示吧")

    with SessionLocal() as db:
        user_messages = _user_messages(db, session_id)
    obj = user_messages[0].response_object
    assert obj["response_type"] == "HINT_REQUEST", obj
    assert obj["answer"] is None, obj
    assert obj["evidence"] is None, obj
    assert obj["error"] is None, obj
    assert obj["step_state"] is None, obj
    assert obj["confidence"] == 0.6, obj  # 单规则命中


# ====================================================================  ④ 数字守卫
def test_sole_number_guard(client, monkeypatch, new_phone):
    """④ 多个数字不猜答案：「先把 3 移到右边，再除以 2」answer=None。"""
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)

    _continue(client, headers, session_id, "先把 3 移到右边，再除以 2")

    with SessionLocal() as db:
        user_messages = _user_messages(db, session_id)
    obj = user_messages[0].response_object
    assert obj["response_type"] == "REASONING", obj
    assert obj["answer"] is None, obj  # 3 和 2 两个数字，拒绝误抽


# ====================================================================  ⑤ 老数据兼容
def test_old_messages_without_column_compat():
    """⑤ 老消息（response_object NULL）→ 回捞降级为 None，不炸。"""
    with SessionLocal() as session:
        session.rollback()
        ai_session = AiSession(student_id=_make_student(session))
        session.add(ai_session)
        session.flush()
        old = AiMessage(session_id=ai_session.id, role="user", content="老消息")
        old.response_object = None  # 老数据：无结构化列值
        session.add(old)
        session.flush()

        objects = orchestrator.latest_response_objects([old])
        assert objects == [None], objects
        # 混合新老消息：老的不炸，新的正常返回
        new = AiMessage(session_id=ai_session.id, role="user", content="新消息")
        new.response_object = response_analyzer.analyze_response(text="给点提示")
        session.add(new)
        session.flush()
        objects = orchestrator.latest_response_objects([old, new], limit=2)
        assert objects[0] is None
        assert objects[1]["response_type"] == "HINT_REQUEST"


# ====================================================================  ⑥ assistant 不结构化
def test_assistant_messages_not_structured(client, monkeypatch, new_phone):
    """⑥ assistant 消息结构化列恒 NULL（只结构化学生消息）。"""
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)
    _continue(client, headers, session_id, "给点提示吧")

    with SessionLocal() as db:
        messages = load_messages(db, session_id)
    assistants = [m for m in messages if m.role == "assistant"]
    assert len(assistants) == 3  # 开场 2 条 + 本轮 1 条
    assert all(m.response_object is None for m in assistants), assistants


# ====================================================================  ⑦ mode 不变
def test_mode_unaffected_by_structuring(client, monkeypatch, new_phone):
    """⑦ 回归铁律：加结构化前后 mode 逐字不变（mode 仍由 intent 唯一定稿）。"""
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)

    child_says = "给点提示吧，我想再自己试试"
    data = _continue(client, headers, session_id, child_says)

    expected_mode = intent.detect_mode(text=child_says)
    expected_next = "GUIDE" if expected_mode == "DEMO" else expected_mode
    assert data["mode"] == expected_mode, data
    assert data["next_mode"] == expected_next, data
    assert str(data["reply"] or "").strip(), data

    # 结构化列确实写了（证明旁路在工作），但 mode 口径未变
    with SessionLocal() as db:
        user_messages = _user_messages(db, session_id)
    assert user_messages[0].response_object is not None


# ====================================================================  ⑧ raw 保留
def test_raw_content_preserved(client, monkeypatch, new_phone):
    """⑧ content 与学生原话逐字相等（raw_response = 重分析前提）。"""
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)

    child_says = "我算出来是 12，对不对"
    _continue(client, headers, session_id, child_says)

    with SessionLocal() as db:
        user_messages = _user_messages(db, session_id)
    assert user_messages[0].content == child_says


# ====================================================================  ⑨ 脏 JSON 容错
def test_dirty_json_tolerated():
    """⑨ 脏 JSON 文本 → 读出非 dict → 回捞按 None 处理，不炸。"""
    with SessionLocal() as session:
        session.rollback()
        ai_session = AiSession(student_id=_make_student(session))
        session.add(ai_session)
        session.flush()
        dirty = AiMessage(session_id=ai_session.id, role="user", content="脏数据")
        dirty.response_object = "{not-valid-json"  # 模拟历史脏数据
        session.add(dirty)
        session.commit()

        loaded = [m for m in load_messages(session, str(ai_session.id)) if m.role == "user"]
        objects = orchestrator.latest_response_objects(loaded)
        assert objects == [None], objects


# ====================================================================  ⑩ 纯函数
def test_analyzer_unit():
    """⑩ analyzer 纯函数：空串/None history/纯符号/超长输入均返回合法契约。"""
    for text in ("", "   ", "？??？", "啊" * 500):
        obj = response_analyzer.analyze_response(text=text, history=None)
        assert isinstance(obj, dict), text
        assert obj["response_type"] in RESPONSE_TYPE_VALUES, text
        assert set(obj) == {
            "response_type",
            "answer",
            "reasoning",
            "evidence",
            "error",
            "confidence",
            "step_state",
        }, text
    # 空串兜底 UNCLEAR + 最低置信度
    assert response_analyzer.analyze_response(text="")["confidence"] == 0.3


def test_num_utils_unit():
    """⑩b _num_utils 共享件：sole_number 守卫 + 等式抽取 + 答案抽取。"""
    assert _num_utils.sole_number("12") == 12.0
    assert _num_utils.sole_number("12元") == 12.0
    assert _num_utils.sole_number("7 和 17") is None  # 两个数字不抽
    assert _num_utils.sole_number("") is None
    assert _num_utils.extract_equalities("2x + 3 = 7") == ["2x+3=7"]
    assert _num_utils.extract_equalities("没有等式") == []
    assert _num_utils.extract_answer("我算出来是 x = 2") == "x=2"
    assert _num_utils.extract_answer("答案是 12") == "12"
    assert _num_utils.extract_answer("我算到 2x=4 了") is None  # 多数字不猜


# ====================================================================  ⑪ 内部失败兜底
def test_analyzer_internal_failure_falls_back_to_unclear(client, monkeypatch, new_phone):
    """⑪ analyzer 内部抽取函数抛异常 → UNCLEAR 契约兜底，聊天照常。"""

    def _boom(_text: str):  # noqa: ANN001 - 测试替身
        raise RuntimeError("simulated extractor failure")

    monkeypatch.setattr(_num_utils, "extract_answer", _boom)
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)

    data = _continue(client, headers, session_id, "我算出来是 x = 2，对不对")
    assert str(data["reply"] or "").strip(), data  # 主链路不受影响

    with SessionLocal() as db:
        user_messages = _user_messages(db, session_id)
    obj = user_messages[0].response_object
    assert obj is not None and obj["response_type"] == "UNCLEAR", obj
    assert obj["confidence"] == 0.3, obj


# ====================================================================  ⑫ 整体异常旁路
def test_orchestrator_bypass_when_analyzer_raises(client, monkeypatch, new_phone):
    """⑫ 双保险：analyzer 整体抛异常 → 列落 NULL，消息照常落库。"""

    def _boom(*_args, **_kwargs):  # noqa: ANN001 - 测试替身
        raise RuntimeError("simulated analyzer crash")

    monkeypatch.setattr(response_analyzer, "analyze_response", _boom)
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)

    data = _continue(client, headers, session_id, "给点提示吧")
    assert str(data["reply"] or "").strip(), data

    with SessionLocal() as db:
        user_messages = _user_messages(db, session_id)
    assert len(user_messages) == 1
    assert user_messages[0].response_object is None  # 列落 NULL，不拖垮主链路


# ====================================================================  ⑬ 闸门回归
def test_confidence_gate_regression(client, monkeypatch, new_phone, confidence_threshold):
    """⑬ 低置信度重拍闸门与结构化共存：#29 行为不回归。"""
    confidence_threshold(0.6)
    patch_ark(monkeypatch, FakeArk(confidence=0.3))
    headers, student_id = _auth(client, new_phone())

    with SessionLocal() as db:
        db.rollback()
        before = db.execute(
            orchestrator.select(AiSession).where(AiSession.student_id == student_id)
        ).scalars().all()

    data = assert_ok(client.post("/api/chat", headers=headers, json={"text": SAMPLE_QUESTION}))
    assert data.get("need_rephoto") is True, data
    assert data.get("confidence") == 0.3, data

    with SessionLocal() as db:
        db.rollback()
        after = db.execute(
            orchestrator.select(AiSession).where(AiSession.student_id == student_id)
        ).scalars().all()
    assert len(after) == len(before) == 0  # 闸门拦截，什么都不建


# ====================================================================  ⑭ 顺序与 limit
def test_latest_response_objects_ordering_and_limit():
    """⑭ 回捞 helper：按时间升序、limit 生效、assistant 跳过。"""
    base = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
    with SessionLocal() as session:
        session.rollback()
        ai_session = AiSession(student_id=_make_student(session))
        session.add(ai_session)
        session.flush()
        rows: List[AiMessage] = []
        for idx in range(3):
            msg = AiMessage(
                session_id=ai_session.id,
                role="user",
                content=f"第{idx}句",
                created_at=base + timedelta(seconds=idx),
            )
            msg.response_object = {"response_type": "REASONING", "index": idx}
            session.add(msg)
            rows.append(msg)
        session.add(
            AiMessage(
                session_id=ai_session.id,
                role="assistant",
                content="老师回复",
                created_at=base + timedelta(seconds=10),
            )
        )
        session.flush()

        all_objects = orchestrator.latest_response_objects(rows, limit=10)
        assert [obj["index"] for obj in all_objects] == [0, 1, 2]
        latest = orchestrator.latest_response_objects(rows, limit=1)
        assert latest == [all_objects[-1]]
        assert orchestrator.latest_response_objects(rows, limit=0) == []


# ====================================================================  ⑮ 错因/步骤
@pytest.mark.parametrize(
    ("text", "expected_error_type", "expected_step"),
    [
        ("我移项忘了变号，算错了", "METHOD", None),
        ("我看错题目了", "READ", None),
        ("我漏写了一步", "CHECK", None),
        ("不会用公式", "KNOWLEDGE", None),
        ("我做完了，答案是 12", None, "done"),
        ("我卡在第二步了", None, "stuck"),
        ("从第一步开始想", None, "in_progress"),
    ],
)
def test_self_reported_error_and_step_state(
    client, monkeypatch, new_phone, text, expected_error_type, expected_step
):
    """⑮ 学生明示自报错因 / 步骤进度信号（不猜、无信号为 None）。"""
    patch_ark(monkeypatch, FakeArk())
    headers, _ = _auth(client, new_phone())
    session_id = _start_session(client, headers)

    _continue(client, headers, session_id, text)

    with SessionLocal() as db:
        user_messages = _user_messages(db, session_id)
    obj = user_messages[0].response_object
    if expected_error_type is None:
        assert obj["error"] is None, obj
    else:
        assert obj["error"]["type"] == expected_error_type, obj
    if expected_step is None:
        assert obj["step_state"] is None, obj
    else:
        assert obj["step_state"]["status"] == expected_step, obj

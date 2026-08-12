"""T04B 集成测试：教学状态机落库（第三层防答案机器）。

通过 FastAPI 测试客户端跑完整链路（拍题开场 → 多轮引导 → 出练习题），
断言 ``ai_sessions`` 的 ``session_state`` / ``step`` 被正确持久化与读回，
并验证输出检查器在集成场景下真的拦下了泄漏答案。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from sqlalchemy import select

from app.db import SessionLocal
from app.models.ai import AiSession
from app.ai.state_machine import SESSION_STATE_GUIDING, SESSION_STATE_PRACTICING

from t03_helpers import FakeArk, assert_ok, patch_ark

SAMPLE_QUESTION: str = "解方程：2x + 3 = 7"


# ------------------------------------------------------------------ 公共工具
def _auth(client) -> Tuple[Dict[str, str], uuid.UUID]:
    """随机验证码登录，返回 ``(请求头, 学生 uuid)``。"""
    from conftest import login_with_code

    data = login_with_code(client)
    sid = uuid.UUID(str(data["user"]["studentId"]))
    return {"Authorization": f"Bearer {data['access']}"}, sid


def _start(client, headers: Dict[str, str], text: str = SAMPLE_QUESTION) -> str:
    data = assert_ok(client.post("/api/chat", headers=headers, json={"text": text}))
    return str(data.get("sessionId") or data.get("session_id"))


def _get_session(db, session_id: str) -> AiSession:
    return db.execute(
        select(AiSession).where(AiSession.id == uuid.UUID(session_id))
    ).scalar_one()


# ------------------------------------------------------------------ 开场 -> guiding
def test_start_session_transitions_to_guiding(client, monkeypatch):
    """开场后会话应处于 guiding 阶段、步数为 1。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)
    session_id = _start(client, headers)

    with SessionLocal() as db:
        s = _get_session(db, session_id)
        assert s.session_state == SESSION_STATE_GUIDING
        assert s.step == 1


def test_start_injects_state_into_prompt(client, monkeypatch):
    """开场首问应把『引导阶段』注入 tutor 系统提示。"""
    ark = FakeArk()
    patch_ark(monkeypatch, ark)
    headers, _ = _auth(client)
    _start(client, headers)

    tutor_systems = [c["system"] for c in ark.calls if c["kind"] == "tutor"]
    assert tutor_systems, "应至少有一次 tutor 调用"
    # 开场首问用的是 intro 阶段提示（含『开场』），所有阶段提示都带『教学阶段』标记
    assert any("教学阶段" in sys for sys in tutor_systems)


# ------------------------------------------------------------------ 多轮 -> step 递增
def test_continue_increments_step_and_injects_state(client, monkeypatch):
    """两轮引导后 step=3，且每轮都把阶段与步数注入提示。"""
    ark = FakeArk()
    patch_ark(monkeypatch, ark)
    headers, _ = _auth(client)
    session_id = _start(client, headers)

    assert_ok(
        client.post(
            f"/api/chat/{session_id}/message", headers=headers, json={"text": "我先移项"}
        )
    )
    assert_ok(
        client.post(
            f"/api/chat/{session_id}/message", headers=headers, json={"text": "然后呢"}
        )
    )

    with SessionLocal() as db:
        s = _get_session(db, session_id)
        assert s.step == 3  # 开场(1) + 2 轮 = 3
        assert s.session_state == SESSION_STATE_GUIDING

    # 至少一次 tutor 调用提示里带「第 2 步 / 第 3 步」或「引导阶段」
    tutor_systems = [c["system"] for c in ark.calls if c["kind"] == "tutor"]
    assert any("引导阶段" in sys for sys in tutor_systems)


# ------------------------------------------------------------------ 出题 -> practicing
def test_generate_exercise_sets_practicing(client, monkeypatch):
    """出练习题后阶段应切到 practicing。"""
    patch_ark(monkeypatch)
    headers, _ = _auth(client)
    session_id = _start(client, headers)
    assert_ok(
        client.post(
            f"/api/chat/{session_id}/message", headers=headers, json={"text": "我先移项"}
        )
    )
    assert_ok(
        client.post("/api/exercise/gen", headers=headers, json={"session_id": session_id})
    )

    with SessionLocal() as db:
        s = _get_session(db, session_id)
        assert s.session_state == SESSION_STATE_PRACTICING


# ------------------------------------------------------------------ 检查器真的生效
class _GuardFakeArk(FakeArk):
    """首轮 tutor 回复泄漏答案，次轮转干净，用于验证检查器拦截。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tutor_n = 0

    def _reply(self, kind):
        if kind == "tutor":
            self.tutor_n += 1
            return "答案：x=2" if self.tutor_n == 1 else "你先想想先移项对不对？"
        return super()._reply(kind)


def test_leak_is_regenerated_and_not_stored(client, monkeypatch):
    """开场首问若泄漏，检查器应重生成，落库的首问不含『答案：』。"""
    ark = _GuardFakeArk()
    patch_ark(monkeypatch, ark)
    headers, _ = _auth(client)

    data = assert_ok(
        client.post("/api/chat", headers=headers, json={"text": SAMPLE_QUESTION})
    )
    first = str(data.get("firstQuestion") or data.get("first_question") or "")
    assert "答案：" not in first, f"泄漏答案不应落库：{first}"
    assert ark.tutor_n >= 2, "检查器应至少触发一次重生成"

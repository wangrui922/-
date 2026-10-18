"""低置信度重拍机制测试（60 人内测第二梯队 · 灰度开关默认关）。

覆盖（对应任务书 5 条 + 补充）：
    ① confidence=0.3 + 阈值 0.6 → need_rephoto=true，且 session/mistake/question
       一个都不建（查库计数为 0）；
    ② confidence=0.9 + 阈值 0.6 → 正常开场（session 建、首轮消息在）；
    ③ confidence 缺失 + 阈值 0.6 → 放行正常开场（不误伤）；
    ④ 阈值 -1（关闭）+ confidence=0.3 → 放行正常开场；
    补充：同步 ``start_session`` 与异步版同一闸门、配置默认值（默认关闭）、
    ``_normalize_confidence`` 脏输入收敛、``analysis.confidence`` 透出。

测试铁律：**绝不设置 DATABASE_URL**（conftest 默认 :memory:，设了会触发文件库
闸门直接退出）。阈值用 monkeypatch 替换 ``system_config._cache`` 实现——不写
DB、用例结束自动还原、零跨用例污染（与 test_consent_gate.py 同一套路）。
方舟调用一律 ``t03_helpers.patch_ark`` 换成 ``FakeArk``，无真实网络请求。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

import pytest
from sqlalchemy import func, select

from app.ai import orchestrator
from app.ai.agents import question as question_agent
from app.core.system_config import DEFAULTS
from app.db import SessionLocal
from app.models.ai import AiSession
from app.models.content import Question
from app.models.learning import Mistake
from t03_helpers import FakeArk, assert_ok, load_messages, patch_ark


# ====================================================================  fixtures
@pytest.fixture
def confidence_threshold(monkeypatch):
    """低置信度阈值夹具：替换 ``system_config._cache``，用例结束自动还原。"""

    def _apply(value: float) -> None:
        from app.core import system_config

        monkeypatch.setattr(
            system_config, "_cache", {"CONFIDENCE_RETHRESHOLD": value}
        )

    yield _apply


# ====================================================================  helpers
def _auth(client, phone: str) -> Tuple[Dict[str, str], uuid.UUID]:
    """指定手机号登录（每个用例一个新号 = 一个干净学生），返回 ``(headers, student_id)``。"""
    from conftest import login_with_code

    data = login_with_code(client, phone)
    student_id = data["user"]["studentId"]
    assert student_id, f"dev 登录应自动建学生档案，studentId 不应为空: {data}"
    return {"Authorization": f"Bearer {data['access']}"}, uuid.UUID(str(student_id))


def _counts(db, student_id: uuid.UUID) -> Dict[str, int]:
    """该学生的 session / mistake 数 + 全库 student_asked 题目数。

    重拍拦截场景要求：新学生 sessions/mistakes 绝对为 0，questions 与拦截前
    持平（全局表可能有其他用例的数据，故用前后对比）。
    """
    db.rollback()
    sessions = int(
        db.execute(
            select(func.count())
            .select_from(AiSession)
            .where(AiSession.student_id == student_id)
        ).scalar_one()
    )
    mistakes = int(
        db.execute(
            select(func.count())
            .select_from(Mistake)
            .where(Mistake.student_id == student_id)
        ).scalar_one()
    )
    questions = int(
        db.execute(
            select(func.count())
            .select_from(Question)
            .where(Question.source_doc == orchestrator.STUDENT_QUESTION_SOURCE_DOC)
        ).scalar_one()
    )
    return {"sessions": sessions, "mistakes": mistakes, "questions": questions}


# ====================================================================  ① 拦截
def test_low_confidence_blocks_and_creates_nothing(
    client, monkeypatch, new_phone, confidence_threshold
):
    """① confidence=0.3 < 阈值 0.6 → need_rephoto，session/mistake/question 全不建。"""
    confidence_threshold(0.6)
    ark = patch_ark(monkeypatch, FakeArk(confidence=0.3))
    headers, student_id = _auth(client, new_phone())

    with SessionLocal() as db:
        before = _counts(db, student_id)

    resp = client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"})
    # HTTP 200 + code 0：正常业务结果，不是 4xx/5xx 错误码（前端不走错误分支）
    data = assert_ok(resp)

    assert data.get("need_rephoto") is True, data
    assert data.get("confidence") == 0.3, data
    message = str(data.get("message") or "")
    assert "重新拍" in message, message  # 引导重拍语义（温和、无 emoji）
    assert not data.get("session_id") and not data.get("sessionId"), data

    with SessionLocal() as db:
        after = _counts(db, student_id)
    assert after["sessions"] == 0, after
    assert after["mistakes"] == 0, after
    assert after["questions"] == before["questions"], (before, after)

    # 只走了「分析」一次 AI 调用，没有进入引导（tutor 零调用）
    assert ark.count("analyze") == 1, ark.kinds()
    assert ark.count("tutor") == 0, ark.kinds()


# ====================================================================  ② 放行
def test_high_confidence_starts_normally(
    client, monkeypatch, new_phone, confidence_threshold
):
    """② confidence=0.9 ≥ 阈值 0.6 → 正常开场：session 建、首轮消息在。"""
    confidence_threshold(0.6)
    ark = patch_ark(monkeypatch, FakeArk(confidence=0.9))
    headers, student_id = _auth(client, new_phone())

    with SessionLocal() as db:
        before = _counts(db, student_id)

    data = assert_ok(
        client.post("/api/chat", headers=headers, json={"text": "计算：25 × 4 = ?"})
    )

    assert not data.get("need_rephoto"), data
    session_id = str(data.get("session_id") or "")
    uuid.UUID(session_id)
    # analyze 的 confidence 自评经 public_analysis 透出
    assert data["analysis"]["confidence"] == 0.9, data["analysis"]
    first_question = str(data.get("first_question") or "").strip()
    assert first_question, data

    with SessionLocal() as db:
        after = _counts(db, student_id)
        messages = load_messages(db, session_id)
    assert after["sessions"] == before["sessions"] + 1, (before, after)
    assert after["mistakes"] == before["mistakes"] + 1, (before, after)
    assert after["questions"] == before["questions"] + 1, (before, after)
    assert len(messages) == 2, [m.content for m in messages]
    assert ark.count("analyze") == 1, ark.kinds()
    assert ark.count("tutor") == 1, ark.kinds()


# ====================================================================  ③ 缺失放行
def test_missing_confidence_passes_through(
    client, monkeypatch, new_phone, confidence_threshold
):
    """③ confidence 缺失（模型没给 / 解析失败）→ 放行正常开场，不误伤。"""
    confidence_threshold(0.6)
    ark = patch_ark(monkeypatch, FakeArk())  # 默认分析 JSON 不带 confidence 字段
    headers, student_id = _auth(client, new_phone())

    data = assert_ok(
        client.post(
            "/api/chat", headers=headers, json={"text": "应用题：甲乙两地相距 300 千米"}
        )
    )

    assert not data.get("need_rephoto"), data
    uuid.UUID(str(data.get("session_id") or ""))
    assert data["analysis"].get("confidence") is None, data["analysis"]

    with SessionLocal() as db:
        assert _counts(db, student_id)["sessions"] == 1
    assert ark.count("tutor") == 1, ark.kinds()


# ====================================================================  ④ 开关关闭
def test_threshold_disabled_allows_low_confidence(
    client, monkeypatch, new_phone, confidence_threshold
):
    """④ 阈值 -1（默认关闭）→ confidence=0.3 也放行正常开场。"""
    confidence_threshold(-1)
    ark = patch_ark(monkeypatch, FakeArk(confidence=0.3))
    headers, student_id = _auth(client, new_phone())

    data = assert_ok(
        client.post(
            "/api/chat", headers=headers, json={"text": "几何：求三角形 ABC 的面积"}
        )
    )

    assert not data.get("need_rephoto"), data
    uuid.UUID(str(data.get("session_id") or ""))

    with SessionLocal() as db:
        assert _counts(db, student_id)["sessions"] == 1
    assert ark.count("tutor") == 1, ark.kinds()


# ====================================================================  补充
def test_sync_start_session_gate(client, monkeypatch, new_phone, confidence_threshold):
    """同步 ``start_session`` 与异步版同一闸门：低置信度同样拦截且不落库。"""
    confidence_threshold(0.6)
    patch_ark(monkeypatch, FakeArk(confidence=0.2))
    _headers, student_id = _auth(client, new_phone())

    with SessionLocal() as db:
        before = _counts(db, student_id)
        result = orchestrator.start_session(
            student_id=student_id,
            image_b64=None,
            text="证明：勾股定理",
            db=db,
        )
        after = _counts(db, student_id)

    assert result["need_rephoto"] is True, result
    assert result["confidence"] == 0.2, result
    assert "重新拍" in result["message"], result
    assert after == before, (before, after)


def test_default_threshold_is_disabled():
    """灰度定位：``CONFIDENCE_RETHRESHOLD`` 默认 -1（关闭），运营验证后按需开启。"""
    default_str, cast = DEFAULTS["CONFIDENCE_RETHRESHOLD"]
    assert cast is float
    assert float(default_str) < 0  # 默认关闭：不确定性默认不挡用户


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.3, 0.3),
        ("0.85", 0.85),
        ("0", 0.0),
        (1, 1.0),
        (1.7, 1.0),  # 越界收敛到上限
        (-0.2, 0.0),  # 越界收敛到下限
        (None, None),  # 缺失 → 放行
        ("", None),
        ("abc", None),
        (True, None),  # bool 脏值不信任
    ],
)
def test_normalize_confidence(raw: Any, expected: Any):
    """``_normalize_confidence``：0~1 收敛 + 缺失/脏值按 None（放行）处理。"""
    assert question_agent._normalize_confidence(raw) == expected

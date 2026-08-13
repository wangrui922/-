"""讲解质量闸门集成测试（#67→生产流）。

验证 ``orchestrator.continue_session`` 在 **DEMO 讲解**（学生点「不会做，给我讲」）
路径上对 ``ensure_explanation_quality`` 的接入：

* 开关关闭（默认）→ 不评测、不重讲，回复即原始讲解，只消耗 1 次 tutor 调用；
* 开关开启 → 讲解低于阈值时自动重讲一次，回复为高质量版本，
  共消耗 2 次 tutor + 2 次 judge 调用。

隔离策略：沿用 ``t03_helpers.patch_ark``，并补 ``QualityFakeArk`` 让裁判（judge）
调用返回可控质量分；数据库用 conftest 内存 SQLite，无真实网络。
"""
from __future__ import annotations

import json

from t03_helpers import (
    FakeArk,
    assert_ok,
    patch_ark,
)

from app.core.config import settings


class QualityFakeArk(FakeArk):
    """在 FakeArk 基础上识别「评测裁判」调用，并返回可控质量分。

    判定依据：system 提示词含「评估专家」（讲解质量评测系统的特征串）。
    tutor 调用按次数返回 V1（首讲，质量差）/ V2（重讲，质量好）。
    """

    def _classify(self, messages, system):
        if system and "评估专家" in system:
            return "judge"
        return super()._classify(messages, system)

    def _reply(self, kind: str) -> str:
        if kind == "judge":
            # 第一次评测给低分（触发重讲），其后给高分（达标）
            overall = 2 if self.count("judge") == 1 else 5
            return json.dumps(
                {
                    "guidance": overall,
                    "correctness": overall,
                    "clarity": overall,
                    "relevance": overall,
                    "warmth": overall,
                    "overall": overall,
                    "suggestion": "保持" if overall == 5 else "需改进",
                },
                ensure_ascii=False,
            )
        if kind == "tutor":
            # 已进入「重讲阶段」（至少做过一次评测）→ 返回高质量讲解；
            # 否则返回首讲（质量差）。用 judge 调用次数判断，避免依赖绝对计数。
            return "讲解V2（质量好）" if self.count("judge") >= 1 else "讲解V1（质量差）"
        return super()._reply(kind)


def _auth(client):
    from conftest import login_with_code

    data = login_with_code(client)
    student_id = data["user"]["studentId"]
    return {"Authorization": f"Bearer {data['access']}"}, student_id


def _start_session(client, headers: dict) -> str:
    resp = client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"})
    data = assert_ok(resp)
    return str(data.get("sessionId") or data.get("session_id"))


def test_demo_gate_enabled_regenerates(client, monkeypatch):
    """开关开启 + DEMO 讲解不达标 → 自动重讲一次，回复为高质量版本。"""
    ark = QualityFakeArk()
    patch_ark(monkeypatch, fake=ark)
    monkeypatch.setattr(settings, "explanation_quality_gate_enabled", True)
    monkeypatch.setattr(settings, "explanation_quality_gate_threshold", 3)

    headers, _ = _auth(client)
    session_id = _start_session(client, headers)

    data = assert_ok(
        client.post(
            f"/api/chat/{session_id}/message",
            headers=headers,
            json={"text": "不会做，给我讲", "mode": "DEMO"},
        )
    )

    assert data["mode"] == "DEMO"
    assert data["reply"] == "讲解V2（质量好）"
    # 进入重讲阶段必经：首讲评测 1 次 + 重讲后评测 1 次
    assert ark.count("judge") == 2, ark.kinds()
    # tutor 至少被调 2 次（首讲 + 重讲一次）
    assert ark.count("tutor") >= 2, ark.kinds()


def test_demo_gate_disabled_no_evaluation(client, monkeypatch):
    """开关关闭（默认）→ 不评测、不重讲，回复即原始讲解。"""
    ark = QualityFakeArk()
    patch_ark(monkeypatch, fake=ark)
    monkeypatch.setattr(settings, "explanation_quality_gate_enabled", False)

    headers, _ = _auth(client)
    session_id = _start_session(client, headers)

    data = assert_ok(
        client.post(
            f"/api/chat/{session_id}/message",
            headers=headers,
            json={"text": "不会做，给我讲", "mode": "DEMO"},
        )
    )

    assert data["mode"] == "DEMO"
    assert data["reply"] == "讲解V1（质量差）"
    assert ark.count("judge") == 0, ark.kinds()
    assert ark.count("tutor") >= 1, ark.kinds()


def test_non_demo_mode_skips_gate(client, monkeypatch):
    """非 DEMO 模式（GUIDE 引导）即使开关开启也不触发评测。"""
    ark = QualityFakeArk()
    patch_ark(monkeypatch, fake=ark)
    monkeypatch.setattr(settings, "explanation_quality_gate_enabled", True)

    headers, _ = _auth(client)
    session_id = _start_session(client, headers)

    data = assert_ok(
        client.post(
            f"/api/chat/{session_id}/message",
            headers=headers,
            json={"text": "我先把 3 移到右边", "mode": "GUIDE"},
        )
    )

    assert data["mode"] == "GUIDE"
    assert ark.count("judge") == 0, ark.kinds()
    assert ark.count("tutor") >= 1, ark.kinds()

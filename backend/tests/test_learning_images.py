"""拍题与手写过程图片的持久化闭环验收。"""

from __future__ import annotations

import base64
from typing import Any, Dict

from t03_helpers import assert_ok, patch_ark
from test_ai_exercise import VerdictFakeArk


def _auth(client, phone: str) -> Dict[str, str]:
    from conftest import login_with_code

    data = login_with_code(client, phone)
    return {"Authorization": f"Bearer {data['access']}"}


def _pick(data: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in data:
            return data[name]
    raise AssertionError(f"响应缺少字段 {names}，实际键为 {sorted(data)}")


def test_photo_question_is_saved_to_mistake_bank(client, monkeypatch, new_phone):
    """拍题开场后，错题库能回看题干与原题图。"""
    patch_ark(monkeypatch)
    headers = _auth(client, new_phone())

    assert_ok(
        client.post(
            "/api/chat",
            headers=headers,
            json={
                "text": "解方程：2x + 3 = 7",
                "imageKey": "source-question.jpg",
            },
        )
    )

    listing = assert_ok(client.get("/api/mistake/list", headers=headers))
    item = listing["items"][0]
    assert item["questionText"] == "解方程：2x + 3 = 7"
    assert item["originalImageUrl"] == "/api/file/source-question.jpg"


def test_handwritten_work_image_is_judged_and_saved(client, monkeypatch, new_phone):
    """练习判错时，模型能读手写图，错题库也能回看该过程图。"""
    ark = patch_ark(monkeypatch, VerdictFakeArk(verdict="wrong", error_type="CALC"))
    headers = _auth(client, new_phone())

    started = assert_ok(
        client.post("/api/chat", headers=headers, json={"text": "解方程：2x + 3 = 7"})
    )
    session_id = str(_pick(started, "sessionId", "session_id"))
    generated = assert_ok(
        client.post("/api/exercise/gen", headers=headers, json={"sessionId": session_id})
    )
    question_id = str(generated["exercises"][0]["question_id"])

    raw_image = b"student-handwritten-work"
    work_image = base64.b64encode(raw_image).decode("ascii")
    judged = assert_ok(
        client.post(
            "/api/exercise/judge",
            headers=headers,
            json={
                "sessionId": session_id,
                "questionId": question_id,
                "answer": "x = 99",
                "finalConfirmed": True,
                "work": "先移项",
                "workImage": work_image,
                "workImageKey": "handwritten-step.jpg",
            },
        )
    )
    assert judged["correct"] is False
    judge_calls = [call for call in ark.calls if call["kind"] == "tutor"]
    assert judge_calls[-1]["image_b64"] == work_image

    listing = assert_ok(client.get("/api/mistake/list", headers=headers))
    item = next(row for row in listing["items"] if row["questionId"] == question_id)
    assert item["workImageUrls"] == ["/api/file/handwritten-step.jpg"]

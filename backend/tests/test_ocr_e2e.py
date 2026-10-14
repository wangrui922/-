"""OCR 拍照识题端到端测试（#5 OCR 全链路）。

覆盖真实用户路径：
    1. 上传照片 ``POST /api/file/upload?ocr=1``  ->  落盘 + 方舟 OCR 返回 ocrText；
    2. 带图开讲 ``POST /api/chat``（base64 data URL）->  OCR -> 分析 -> 引导，
       且 ``analysis.question_text`` 正是 OCR 转写出的题面；
    3. 先传图再开讲（image_key + image 组合，对应前端「先拍到题再开始对话」的交互）。

链路约束：
    所有方舟调用统一用 ``t03_helpers.patch_ark`` 换成 ``FakeArk``，不产生真实网络请求；
    ``FakeArk`` 对 OCR 提示词返回 ``ocr_text``（默认 ``解方程：2x + 3 = 7``）。
    文件落盘路径用 ``tmp_path`` 隔离，绝不动真实 ``backend/storage/``。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict

from app.api import file as file_module
from app.db import SessionLocal

from t03_helpers import assert_ok, load_messages, patch_ark

#: 与 FakeArk 默认 ocr_text 一致的题面（便于断言 OCR 结果被正确透传）
OCR_QUESTION: str = "解方程：2x + 3 = 7"

#: 一张 1x1 透明 PNG 的最小合法字节（仅用于触发上传链路的文件头校验）
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _pick(data: Dict[str, Any], *names: str) -> Any:
    """按候选名依次取值（兼容 camelCase / snake_case 两种出参命名）。"""
    for name in names:
        if name in data:
            return data[name]
    raise AssertionError(f"响应缺少字段 {names}，实际键为 {sorted(data)}")


def _login(client) -> Dict[str, Any]:
    from conftest import login_with_code

    return login_with_code(client)


def _auth(client) -> Dict[str, str]:
    data = _login(client)
    return {"Authorization": f"Bearer {data['access']}"}


def _use_tmp_storage(monkeypatch, tmp_path: Path) -> Path:
    """把上传存储目录指到 pytest 的临时目录，返回该目录路径。"""
    monkeypatch.setattr(file_module, "_storage_dir", lambda: tmp_path)
    return tmp_path


# ------------------------------------------------------------------ 1. 上传 + OCR
def test_upload_with_ocr_returns_text(client, monkeypatch, tmp_path):
    """``?ocr=1`` 上传：文件真实落盘，且返回 key / url / ocrText（OCR 文本）。"""
    ark = patch_ark(monkeypatch)
    _use_tmp_storage(monkeypatch, tmp_path)
    headers = _auth(client)

    resp = client.post(
        "/api/file/upload?ocr=1",
        headers=headers,
        files={"file": ("question.png", PNG_BYTES, "image/png")},
    )
    data = assert_ok(resp)

    # 1) key / url 齐全
    key = str(data.get("key") or "")
    url = str(data.get("url") or "")
    assert key.endswith(".png"), data
    assert url == f"/api/file/{key}", data

    # 2) ocrText 来自方舟 OCR 调用
    assert data["ocrText"] == OCR_QUESTION, data
    assert ark.count("ocr") == 1, ark.kinds()

    # 3) 文件真实落盘且可读回
    saved = tmp_path / key
    assert saved.is_file() and saved.read_bytes() == PNG_BYTES
    fetched = client.get(url)
    assert fetched.status_code == 200 and fetched.content == PNG_BYTES


def test_upload_without_ocr_skips_vision(client, monkeypatch, tmp_path):
    """不带 ``ocr=1`` 时只落盘，不触发任何方舟调用（省额度）。"""
    ark = patch_ark(monkeypatch)
    _use_tmp_storage(monkeypatch, tmp_path)
    headers = _auth(client)

    data = assert_ok(
        client.post(
            "/api/file/upload",
            headers=headers,
            files={"file": ("plain.png", PNG_BYTES, "image/png")},
        )
    )
    assert "ocrText" not in data, data
    assert ark.calls == [], ark.kinds()


# ------------------------------------------------------------------ 2. 带图开讲
def test_chat_with_image_runs_ocr_then_analyze(client, monkeypatch):
    """带图 ``/api/chat``：OCR -> 分析 -> 引导，question_text 取 OCR 结果并落库。"""
    ark = patch_ark(monkeypatch)
    headers, student_id = _auth_with_student(client)

    data = assert_ok(
        client.post(
            "/api/chat",
            headers=headers,
            json={"image": "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode()},
        )
    )

    # 1) AI 调用顺序：OCR 一次、分析一次、引导一次
    assert ark.kinds() == ["ocr", "analyze", "tutor"], ark.kinds()

    # 2) 分析里透传的题面就是 OCR 转写文本
    analysis = data["analysis"]
    assert analysis["question_text"] == OCR_QUESTION, analysis
    assert str(_pick(analysis, "questionText", "question_text")).strip() == OCR_QUESTION

    # 3) 落库：一条新会话 + 两条 assistant 消息
    session_id = str(_pick(data, "sessionId", "session_id"))
    with SessionLocal() as db:
        messages = load_messages(db, session_id)
    assert len(messages) == 2, [m.content for m in messages]
    assert any(OCR_QUESTION in m.content for m in messages)
    assert ark.count("ocr") == 1, ark.kinds()


# ------------------------------------------------------------------ 3. 先传图再开讲
def test_chat_with_uploaded_image_key(client, monkeypatch, tmp_path):
    """前端典型交互：先上传（拿 key）再带图开讲（image 走 OCR，image_key 落库）。"""
    ark = patch_ark(monkeypatch)
    _use_tmp_storage(monkeypatch, tmp_path)
    headers, _ = _auth_with_student(client)

    # 先传图拿 key
    uploaded = assert_ok(
        client.post(
            "/api/file/upload?ocr=1",
            headers=headers,
            files={"file": ("math.png", PNG_BYTES, "image/png")},
        )
    )
    image_key = uploaded["key"]
    # 上传本身已消耗 1 次 OCR（前端核对题面用）
    assert ark.count("ocr") == 1, ark.kinds()

    # 再带图开讲
    data = assert_ok(
        client.post(
            "/api/chat",
            headers=headers,
            json={
                "image": "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode(),
                "image_key": image_key,
            },
        )
    )
    assert ark.kinds() == ["ocr", "ocr", "analyze", "tutor"], ark.kinds()
    assert data["analysis"]["question_text"] == OCR_QUESTION, data["analysis"]


def _auth_with_student(client):
    """登录并返回 ``(headers, student_id)``；带图开讲需要真实学生档案落库。"""
    data = _login(client)
    student_id = data["user"]["studentId"]
    assert student_id, f"dev 登录应自动建学生档案: {data}"
    return {"Authorization": f"Bearer {data['access']}"}, str(student_id)
"""OCR 契约测试（#66）。

聚焦 `_run_ocr` 的两条核心契约：
1. 视觉服务正常 → 返回转写文本，且图片确实被 base64 传给方舟；
2. 视觉服务抛 ``ArkError`` → 降级为空串，绝不能把上传链路带崩。
"""
from __future__ import annotations

from unittest.mock import patch

from app.ai.ark_client import ArkError
from app.api import file as file_module


def test_run_ocr_returns_transcript():
    with patch.object(file_module, "ark_respond", return_value="解：设 x=3，则…") as fake:
        out = file_module._run_ocr(b"fake-image-bytes")

    assert out == "解：设 x=3，则…"
    # 图片必须被 base64 后传给视觉模型
    _, kwargs = fake.call_args
    assert kwargs.get("image_b64")


def test_run_ocr_degrades_on_ark_error():
    with patch.object(file_module, "ark_respond", side_effect=ArkError("vision down")):
        out = file_module._run_ocr(b"fake-image-bytes")

    # 视觉挂了也不能影响上传本身：降级为空串
    assert out == ""


def test_run_ocr_handles_empty_bytes():
    with patch.object(file_module, "ark_respond", return_value="ignored") as fake:
        file_module._run_ocr(b"")
    # 空字节也要能走进调用（不至于崩），base64 后仍是合法串
    assert fake.called

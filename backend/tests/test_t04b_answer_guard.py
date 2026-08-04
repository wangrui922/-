"""T04B 单元测试：输出检查器（第二层防答案机器）。

只测纯逻辑，不触 DB、不触方舟（直接 monkeypatch ark_respond）。
"""

from __future__ import annotations

import pytest

from app.ai.answer_guard import (
    LEAK_RETRY_HINT,
    generate_guarded_reply,
    has_answer_leak,
)


# ----------------------------------------------------------------- 命中判定
def test_has_answer_leak_positive():
    """典型「直接给答案」表述都应被识破。"""
    assert has_answer_leak("答案：x=5")
    assert has_answer_leak("最终答案是 12")
    assert has_answer_leak("所以 x = 5")
    assert has_answer_leak("解得 y=3")
    assert has_answer_leak("解为 4")


def test_has_answer_leak_negative():
    """正常引导语不应误报。"""
    assert not has_answer_leak("你先想想第一步该处理哪部分？")
    assert not has_answer_leak("分母代表什么？分成4份和5份是一种大小吗？")
    assert not has_answer_leak("很好，再试一次")
    assert not has_answer_leak("你的答案对了，思路很清晰")  # 无冒号/等号


def test_loose_mode_allows_so_equals():
    """宽松模式（练习/总结阶段）只拦最露骨表述，允许「所以 x=5」这类。"""
    assert not has_answer_leak("所以 x=5", strict=False)
    assert has_answer_leak("答案：x=5", strict=False)


# ----------------------------------------------------------------- 重生成流程
def _patch_ark(monkeypatch, fake_call):
    """把 ark_respond 在定义处与 answer_guard 命名空间都换成 fake_call。"""
    monkeypatch.setattr("app.ai.ark_client.ark_respond", fake_call)
    monkeypatch.setattr("app.ai.answer_guard.ark_respond", fake_call)


def test_generate_guarded_reply_retries_on_leak(monkeypatch):
    """首次泄漏 -> 追加 LEAK_RETRY_HINT 重新生成 -> 第二次干净返回。"""
    calls = []

    def fake(*, messages, system=None, image_b64=None, timeout=60.0):
        calls.append(list(messages))
        return "答案：x=5" if len(calls) == 1 else "你先想想先移项对不对？"

    _patch_ark(monkeypatch, fake)
    out = generate_guarded_reply(
        messages=[{"role": "user", "content": "这题咋做"}], system="SYS", strict=True
    )
    assert out == "你先想想先移项对不对？"
    assert len(calls) == 2  # 重生成了一次
    # 重生成那次应带 LEAK_RETRY_HINT
    assert any(LEAK_RETRY_HINT in m.get("content", "") for m in calls[1])


def test_generate_guarded_reply_exhausted_falls_back(monkeypatch):
    """始终泄漏 -> 重试耗尽 -> 退回兜底语，绝不把答案给孩子。"""
    calls = []

    def fake(*, messages, system=None, image_b64=None, timeout=60.0):
        calls.append(1)
        return "答案：x=5"

    _patch_ark(monkeypatch, fake)
    out = generate_guarded_reply(
        messages=[{"role": "user", "content": "x"}],
        system="SYS",
        strict=True,
        fallback="兜底引导语",
    )
    assert out == "兜底引导语"
    # 首次 + 2 次重试 = 3 次（MAX_GUARD_RETRIES=2）
    assert len(calls) == 3

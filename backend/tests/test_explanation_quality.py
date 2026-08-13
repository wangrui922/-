"""讲解质量评测 harness 单元测试（#67）。

重点验证：
1. 解析器对「标准 JSON / 围栏 JSON / 畸形输入」的鲁棒性；
2. 评测主链路：mock 方舟裁判 → evaluate_explanation 正确返回结构化结果；
3. 方舟调用失败（ArkError）原样透传，不静默吞掉。
全程不触网、不依赖真实 Key（monkeypatch ``ark_respond``）。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.ai.ark_client import ArkError
from app.services import explanation_quality as eq_module
from app.services.explanation_quality import (
    DIMENSION_KEYS,
    ExplanationQuality,
    evaluate_explanation,
    parse_quality,
)

_GOOD_JSON = (
    '{"guidance":5,"correctness":5,"clarity":4,"relevance":4,"warmth":5,'
    '"overall":5,"suggestion":"可以再缩小提示颗粒度"}'
)


def test_parse_valid_json():
    parsed = parse_quality(_GOOD_JSON)
    assert parsed["parsed_ok"] is True
    assert parsed["dimensions"]["guidance"] == 5
    assert parsed["dimensions"]["warmth"] == 5
    assert parsed["overall"] == 5
    assert parsed["suggestion"] == "可以再缩小提示颗粒度"


def test_parse_fenced_json():
    fenced = f"```json\n{_GOOD_JSON}\n```"
    parsed = parse_quality(fenced)
    assert parsed["parsed_ok"] is True
    assert parsed["overall"] == 5


def test_parse_missing_overall_uses_mean():
    raw = '{"guidance":5,"correctness":3,"clarity":3,"relevance":3,"warmth":3}'
    parsed = parse_quality(raw)
    assert parsed["parsed_ok"] is True
    # 均值四舍五入：(5+3+3+3+3)/5 = 3.4 → 3
    assert parsed["overall"] == 3


def test_parse_malformed_downgrades_gracefully():
    # 不是 JSON、找不到对象 → parsed_ok=False，维度给默认值 3，不抛异常
    parsed = parse_quality("抱歉我无法评估这段文字")
    assert parsed["parsed_ok"] is False
    assert parsed["error"] is not None
    assert all(v == 3 for v in parsed["dimensions"].values())
    assert parsed["overall"] == 3


def test_parse_clamps_out_of_range():
    raw = '{"guidance":99,"correctness":-2,"clarity":3,"relevance":3,"warmth":3,"overall":50}'
    parsed = parse_quality(raw)
    assert parsed["dimensions"]["guidance"] == 5
    assert parsed["dimensions"]["correctness"] == 1
    assert parsed["overall"] == 5


def test_evaluate_happy_path():
    with patch.object(eq_module, "ark_respond", return_value=_GOOD_JSON) as fake:
        result = evaluate_explanation(
            question="解方程 2x+3=11",
            student_attempt="孩子写了 x=4",
            ai_explanation="等号两边先减 3，会变成什么？",
            error_type="移项符号错误",
        )

    assert isinstance(result, ExplanationQuality)
    assert result.overall == 5
    assert result.grade == "优秀"
    assert result.passes(4) is True
    assert result.parsed_ok is True
    # 裁判确实收到了题目与讲解文本
    _, kwargs = fake.call_args
    assert kwargs.get("system")
    sent_messages = kwargs["messages"]
    assert any("解方程 2x+3=11" in m["content"] for m in sent_messages)
    assert any("等号两边先减 3" in m["content"] for m in sent_messages)


def test_evaluate_ark_error_propagates():
    with patch.object(eq_module, "ark_respond", side_effect=ArkError("judge down")):
        with pytest.raises(ArkError):
            evaluate_explanation(
                question="q",
                student_attempt="a",
                ai_explanation="e",
            )


def test_dimension_keys_stable():
    # rubric 字段名是裁判 JSON 契约，必须稳定存在
    assert DIMENSION_KEYS == [
        "guidance",
        "correctness",
        "clarity",
        "relevance",
        "warmth",
    ]


# ------------------------------------------------------------------ 在线闸门
class _FakeJudge:
    """可控的方舟裁判 mock：按 ``scores`` 顺序返回综合分。"""

    def __init__(self, scores: list[int]) -> None:
        self.scores = scores
        self.calls = 0

    def __call__(self, *, messages, system=None, image_b64=None, timeout=60.0):
        score = self.scores[min(self.calls, len(self.scores) - 1)]
        self.calls += 1
        return (
            f'{{"guidance":{score},"correctness":{score},"clarity":{score},'
            f'"relevance":{score},"warmth":{score},"overall":{score},"suggestion":"x"}}'
        )


def test_ensure_skips_regenerate_when_quality_ok(monkeypatch):
    """讲解已达标 → 不调用 regenerate，直接返回原讲解。"""
    judge = _FakeJudge([5])
    monkeypatch.setattr(eq_module, "ark_respond", judge)
    spy = {"called": False}

    def _regen():
        spy["called"] = True
        return "重讲版本"

    text, quality = eq_module.ensure_explanation_quality(
        question="q",
        student_attempt="a",
        ai_explanation="原讲解",
        regenerate=_regen,
        threshold=3,
    )
    assert text == "原讲解"
    assert quality.overall == 5
    assert spy["called"] is False


def test_ensure_regenerates_once_when_low(monkeypatch):
    """讲解不达标 + 提供 regenerate → 重讲一次，返回重讲后的高质量讲解。"""
    judge = _FakeJudge([2, 5])  # 首次低分触发重讲，重讲后高分达标
    monkeypatch.setattr(eq_module, "ark_respond", judge)
    regen_calls = []

    def _regen():
        regen_calls.append(1)
        return "重讲版本"

    text, quality = eq_module.ensure_explanation_quality(
        question="q",
        student_attempt="a",
        ai_explanation="原讲解",
        regenerate=_regen,
        threshold=3,
    )
    assert text == "重讲版本"
    assert quality.overall == 5
    assert len(regen_calls) == 1


def test_ensure_arkerror_returns_original(monkeypatch):
    """评测调用抛 ArkError → 返回原讲解，不触发重讲（best-effort）。"""

    def _boom(*, messages, system=None, image_b64=None, timeout=60.0):
        raise ArkError("key missing")

    monkeypatch.setattr(eq_module, "ark_respond", _boom)
    spy = {"called": False}

    def _regen():
        spy["called"] = True
        return "重讲版本"

    text, quality = eq_module.ensure_explanation_quality(
        question="q",
        student_attempt="a",
        ai_explanation="原讲解",
        regenerate=_regen,
        threshold=3,
    )
    assert text == "原讲解"
    assert quality.parsed_ok is False
    assert spy["called"] is False


def test_ensure_regenerate_raises_returns_original(monkeypatch):
    """重讲回调自身抛异常 → 捕获并回落原讲解，不崩溃。"""
    judge = _FakeJudge([2])  # 首次低分，会尝试重讲
    monkeypatch.setattr(eq_module, "ark_respond", judge)

    def _regen():
        raise RuntimeError("重讲失败")

    text, quality = eq_module.ensure_explanation_quality(
        question="q",
        student_attempt="a",
        ai_explanation="原讲解",
        regenerate=_regen,
        threshold=3,
    )
    assert text == "原讲解"

"""残缺选择题判定内核 + 入库卡口测试（T01 · 2026-08-12）。

覆盖：

* :func:`app.services.importers.quality_gate.validate_choice_options` —— 单点真相纯函数：
  dict / list / list-of-dict / 带前缀 list 各形态；``{A,B,C}`` 缺 D、``{A,B,C,E}``
  （4 键但缺 D）、``{A,B,C,D,E}``（5 选项）均判残缺；``{A,B,C,D}`` 通过；
  ``correct_answer`` 命中 / 不命中两种用法。
* :func:`app.services.importers.quality_gate.choice_options_reasons` —— 原因列表（空=通过）。
* 入库卡口集成测：构造缺 D 选择题走 ``loader.load_outcome``，断言落 ``status='archived'``
  且 ``stats.gated_review + 1``；完整 A-D 题落 ``status='active'``。

判定口径严格对齐设计 §1.4，不硬编码任何数字（与 312 道存量同口径）。
"""

from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.models.content import Question
from app.services.importers.loader import load_outcome, question_uuid
from app.services.importers.quality_gate import (
    REQUIRED_CHOICE_KEYS,
    STRICT_ABCD,
    choice_options_reasons,
    classify_question,
    validate_choice_options,
)
from app.services.importers.schema import LoadStats, ParseOutcome, ParsedQuestion


# ==================================================================== 纯函数单测
class TestValidateChoiceOptions:
    """四种物理形态 + 严格相等 / 子集语义 + 两种用法。"""

    # --- dict 形态 ---
    def test_dict_complete_abcd_passes(self) -> None:
        assert validate_choice_options({"A": "50", "B": "55", "C": "35", "D": "65"}) is True

    def test_dict_missing_d_fails(self) -> None:
        assert validate_choice_options({"A": "50", "B": "55", "C": "35"}) is False

    # --- list 形态（按下标映射 A/B/C/D）---
    def test_list_missing_d_fails(self) -> None:
        assert validate_choice_options(["50", "55", "35"]) is False

    def test_list_complete_abcd_passes(self) -> None:
        assert validate_choice_options(["50", "55", "35", "65"]) is True

    # --- list-of-dict 形态 ---
    def test_list_of_dict_missing_d_fails(self) -> None:
        opts = [{"key": "A", "text": "50"}, {"key": "B", "text": "55"}, {"key": "C", "text": "35"}]
        assert validate_choice_options(opts) is False

    # --- 带前缀 list 形态（"A. 50"）---
    def test_prefixed_list_missing_d_fails(self) -> None:
        opts = ["A. 50", "B. 55", "C. 35"]
        assert validate_choice_options(opts) is False

    # --- 严格相等：4 键但缺 D 仍判残缺 ---
    def test_four_keys_but_missing_d_fails(self) -> None:
        assert validate_choice_options({"A": "1", "B": "2", "C": "3", "E": "4"}) is False

    # --- 严格相等：5 选项也判残缺 ---
    def test_five_keys_fails_under_strict(self) -> None:
        assert (
            validate_choice_options({"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"}) is False
        )

    # --- 子集语义开关：5 选项在 STRICT_ABCD=False 时放行 ---
    def test_strict_abcd_false_allows_extra_key(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.services.importers.quality_gate.STRICT_ABCD", False
        )
        assert (
            validate_choice_options({"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"}) is True
        )

    # --- None / 空 ---
    def test_none_options_fails(self) -> None:
        assert validate_choice_options(None) is False

    def test_empty_dict_fails(self) -> None:
        assert validate_choice_options({}) is False

    # --- JSON 字符串形态（裸 SQL 查询返回）---
    def test_json_string_missing_d_fails(self) -> None:
        assert validate_choice_options('{"A": "50", "B": "55", "C": "35"}') is False

    def test_json_string_complete_abcd_passes(self) -> None:
        assert validate_choice_options('{"A": "50", "B": "55", "C": "35", "D": "65"}') is True

    # --- correct_answer 用法（入库卡口 P1.1）---
    def test_correct_answer_hit_passes(self) -> None:
        assert (
            validate_choice_options(
                {"A": "50", "B": "55", "C": "35", "D": "65"}, correct_answer="B"
            )
            is True
        )

    def test_correct_answer_miss_fails(self) -> None:
        # 选项完整但答案命中不进选项键 → 卡口口径判残缺
        assert (
            validate_choice_options(
                {"A": "50", "B": "55", "C": "35", "D": "65"}, correct_answer="F"
            )
            is False
        )

    def test_keys_are_exactly_abcd(self) -> None:
        assert REQUIRED_CHOICE_KEYS == frozenset({"A", "B", "C", "D"})
        assert STRICT_ABCD is True


# ==================================================================== 原因列表
class TestChoiceOptionsReasons:
    """``choice_options_reasons`` 返回原因列表（空=合法）。"""

    def test_complete_returns_empty(self) -> None:
        assert choice_options_reasons({"A": "1", "B": "2", "C": "3", "D": "4"}) == []

    def test_missing_d_reports(self) -> None:
        reasons = choice_options_reasons({"A": "1", "B": "2", "C": "3"})
        assert any("缺选项键" in r for r in reasons)

    def test_extra_key_reports(self) -> None:
        reasons = choice_options_reasons({"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"})
        assert any("非法选项键" in r for r in reasons)

    def test_no_options_reports(self) -> None:
        reasons = choice_options_reasons(None)
        assert reasons == ["选择题无选项"]

    def test_correct_answer_not_hit_reports(self) -> None:
        reasons = choice_options_reasons(
            {"A": "1", "B": "2", "C": "3", "D": "4"}, correct_answer="F"
        )
        assert any("正确答案未命中" in r for r in reasons)


# ==================================================================== classify_question 向后兼容
class TestClassifyQuestionBackwardCompat:
    """新增 q_type/options kwargs 带默认值，既有调用零改动。"""

    def test_plain_content_clean(self) -> None:
        # 含汉字的普通题 → clean（纯算式 "3+4=?" 会被占位判定拦，故用含汉字题干）
        verdict, reasons = classify_question("计算 3 + 4 等于几？", "7")
        assert verdict == "clean"
        assert reasons == []

    def test_choice_with_missing_d_review(self) -> None:
        verdict, reasons = classify_question(
            "选最大的", "B", q_type="选择", options=["50", "55", "35"]
        )
        assert verdict == "review"
        assert any("缺选项键" in r for r in reasons)

    def test_choice_complete_clean(self) -> None:
        verdict, reasons = classify_question(
            "选最大的", "B", q_type="选择", options=["50", "55", "35", "65"]
        )
        assert verdict == "clean"
        assert reasons == []

    def test_non_choice_options_ignored(self) -> None:
        # 非选择题传入残缺 options 不应触发选择题卡口
        verdict, reasons = classify_question(
            "计算题", "7", q_type="计算", options=["50", "55", "35"]
        )
        assert verdict == "clean"


# ==================================================================== 入库卡口集成测
def _run_loader(options, correct_answer) -> tuple[str, LoadStats]:
    """构造一道新选择题走 load_outcome，返回 (status, stats)。"""
    parsed = ParsedQuestion(
        stable_key="docx:test_residual_gate.docx|p|一|1",
        content="选最大的",
        q_type="选择",
        correct_answer=correct_answer,
        options=options,
    )
    outcome = ParseOutcome(
        source_key="test_residual_gate",
        label="测试",
        edition="青岛版",
        source_type="衔接",
        grade=3,
        source_doc="test_residual_gate.docx",
        questions=[parsed],
    )
    with SessionLocal() as session:
        stats = load_outcome(session, outcome)
        session.commit()
        qid = question_uuid(parsed.stable_key)
        status = session.get(Question, qid).status
    return status, stats


class TestLoaderGateIntegration:
    """loader._upsert_question 把残缺选择题挡进 archived。"""

    def test_residual_choice_goes_archived(self) -> None:
        status, stats = _run_loader(["50", "55", "35"], "B")
        assert status == "archived"
        assert stats.gated_review == 1
        assert stats.gated_clean == 0

    def test_complete_choice_goes_active(self) -> None:
        status, stats = _run_loader(["50", "55", "35", "65"], "B")
        assert status == "active"
        assert stats.gated_clean == 1
        assert stats.gated_review == 0

    def test_residual_choice_unparseable_answer_archived(self) -> None:
        # 防新增死题卡口：选择题答案若根本解析不出合法键位（如 "F" 不在 A-D 内），
        # 入库即判 review（落 archived），不让学生刷到无法判分的题。
        # 这比「只校验选项」更贴合「有问题的题都剔除」的要求。
        status, stats = _run_loader(["50", "55", "35", "65"], "F")
        assert status == "archived"
        assert stats.gated_review == 1
        assert stats.gated_clean == 0
        # 全量口径校验该题同样判残缺（答案未命中选项键），单点真相一致
        from app.services.importers.quality_gate import validate_choice_options

        assert validate_choice_options(["50", "55", "35", "65"], correct_answer="F") is False

    def test_choice_valid_answer_still_active(self) -> None:
        # 合法答案（"B" 命中 A-D）不应被完整性卡口误杀
        status, stats = _run_loader(["50", "55", "35", "65"], "B")
        assert status == "active"
        assert stats.gated_clean == 1

# -*- coding: utf-8 -*-
"""方向八 · 四层状态机升级：MathDial 诊断式追问链路单测。

覆盖：
    1. MathDial 话术表完整性：五错因 × 三环节全覆盖、非空；
    2. L2 注入点 ``state_machine.diagnostic_tactic_prompt``：
       各 mode 语义、错因标签行、None/非法输入向后兼容；
    3. L3 话术生成 ``tutor._build_request``：system 拼接链含诊断后缀，
       且 ``error_type=None`` 时与改造前等价；
    4. 错因收敛 ``normalize_error``：五码 / 中文标签 / 特征词 / 兜底；
    5. 练习引导 ``practice_guide`` v1.3.0 含【先诊断再教学】。
"""
from __future__ import annotations

import pytest

from app.ai import intent
from app.ai.agents import tutor
from app.ai.prompts import mathdial
from app.ai.prompts import practice_guide
from app.ai.state_machine import diagnostic_tactic_prompt
from app.core import error_categories as ec

ALL_ERRORS = (ec.ERROR_READ, ec.ERROR_METHOD, ec.ERROR_CALC, ec.ERROR_KNOWLEDGE, ec.ERROR_CHECK)
ALL_TACTICS = (mathdial.TACTIC_DIAGNOSE, mathdial.TACTIC_HINT, mathdial.TACTIC_ELABORATE)


# ---------------------------------------------------------------- 1. 话术表完整性
class TestMathDialTable:
    @pytest.mark.parametrize("code", ALL_ERRORS)
    @pytest.mark.parametrize("tactic", ALL_TACTICS)
    def test_cell_non_empty(self, code: str, tactic: str) -> None:
        """五错因 × 三环节每个格子都有行为指令与参考问句。"""
        assert mathdial.tactic_instruction(code, tactic).strip()
        assert mathdial.pick_phrase(code, tactic, variant=0).strip()
        assert mathdial.pick_phrase(code, tactic, variant=1).strip()

    def test_unclassified_fallback(self) -> None:
        """取不到错因回退到通用诊断问句，仍非空。"""
        phrase = mathdial.pick_phrase(None, mathdial.TACTIC_DIAGNOSE)
        assert phrase.strip()
        assert mathdial.tactic_instruction("", mathdial.TACTIC_HINT).strip()

    def test_tactic_label(self) -> None:
        assert mathdial.tactic_label(mathdial.TACTIC_DIAGNOSE) == "诊断"
        assert mathdial.tactic_label(mathdial.TACTIC_HINT) == "提示"
        assert mathdial.tactic_label(mathdial.TACTIC_ELABORATE) == "讲解"
        assert mathdial.tactic_label("nonsense") == "诊断"  # 非法回退

    def test_diagnose_first_instruction(self) -> None:
        assert "先诊断再教学" in mathdial.DIAGNOSE_FIRST_INSTRUCTION


# ---------------------------------------------------------------- 2. L2 注入点语义
class TestDiagnosticTacticPrompt:
    def test_all_modes_contain_general_rule(self) -> None:
        """所有模式都带「先诊断再教学」总则与教学动作行。"""
        for mode in intent.MODE_VALUES:
            text = diagnostic_tactic_prompt(mode, error_type=ec.ERROR_READ)
            assert mathdial.DIAGNOSE_FIRST_INSTRUCTION in text, mode
            assert "本轮教学动作" in text, mode

    def test_error_label_line(self) -> None:
        """有明确错因时注入错因中文标签行（内部参考）。"""
        text = diagnostic_tactic_prompt("HINT", error_type=ec.ERROR_CALC)
        assert "计算错" in text and "不要念给孩子" in text

    def test_error_label_hidden_when_unclassified(self) -> None:
        """无错因 / UNCLASSIFIED 时不显示具体错因标签行。"""
        text = diagnostic_tactic_prompt("HINT", error_type=ec.ERROR_UNCLASSIFIED)
        labels = [ec.ERROR_LABELS[c] for c in ALL_ERRORS]
        assert not any(label in text for label in labels), text

    def test_mode_to_tactic_mapping(self) -> None:
        """GUIDE→诊断、HINT→提示、DEMO→讲解（JUDGE 回退诊断）。"""
        assert "诊断" in diagnostic_tactic_prompt("GUIDE", error_type=ec.ERROR_READ)
        assert "提示" in diagnostic_tactic_prompt("HINT", error_type=ec.ERROR_READ)
        assert "讲解" in diagnostic_tactic_prompt(intent.MODE_DEMO, error_type=ec.ERROR_READ)

    @pytest.mark.parametrize(
        ("mode", "error_type", "expected"),
        [
            (None, ec.ERROR_READ, ""),  # mode None → 空串
            ("nonsense", ec.ERROR_READ, ""),  # 非法 mode → 空串
        ],
    )
    def test_backward_compat_edges(self, mode, error_type, expected) -> None:
        """向后兼容边界：非法/缺失 mode 一律空串，不注入任何指令。"""
        text = diagnostic_tactic_prompt(mode, error_type=error_type)
        if text != expected:
            pytest.fail(f"diagnostic_tactic_prompt({mode}, {error_type}) = {text!r}")

    def test_none_error_type_gives_generic_instruction(self) -> None:
        """error_type=None 时给通用「先诊断再教学」指令，但不绑定具体错因。"""
        text = diagnostic_tactic_prompt("HINT", error_type=None)
        assert mathdial.DIAGNOSE_FIRST_INSTRUCTION in text
        labels = [ec.ERROR_LABELS[c] for c in ALL_ERRORS]
        assert not any(label in text for label in labels), text

    def test_reference_phrase_present(self) -> None:
        text = diagnostic_tactic_prompt("HINT", error_type=ec.ERROR_CHECK)
        assert "参考问法" in text


# ---------------------------------------------------------------- 3. L3 拼接链路
class TestTutorBuildRequest:
    BASE = [
        {"role": "user", "content": "题目：3 + 4 = ?"},
        {"role": "assistant", "content": "你觉得先算哪一步？"},
    ]

    def test_system_contains_diagnostic_suffix(self) -> None:
        """提供 error_type 时 system 尾部追加诊断式追问（含错因标签）。"""
        _, system, _ = tutor._build_request(
            session_messages=self.BASE,
            current_error=None,
            state="guiding",
            mode="GUIDE",
            error_type=ec.ERROR_READ,
        )
        assert "【诊断式追问】" in system
        assert mathdial.DIAGNOSE_FIRST_INSTRUCTION in system
        assert "读题错" in system

    def test_no_error_type_equals_pre_change(self) -> None:
        """error_type=None 时 system 与改造前一致（不含诊断式追问区块）。"""
        _, system, _ = tutor._build_request(
            session_messages=self.BASE,
            current_error=None,
            state="guiding",
            mode="GUIDE",
            error_type=None,
        )
        assert "【诊断式追问】" not in system
        assert mathdial.DIAGNOSE_FIRST_INSTRUCTION not in system
        assert "本轮错因" not in system

    def test_error_type_through_next_question_signature(self) -> None:
        """next_question / next_question_async 接受 error_type 关键字参数。"""
        import inspect

        sig = inspect.signature(tutor.next_question)
        assert "error_type" in sig.parameters and sig.parameters["error_type"].default is None
        sig_async = inspect.signature(tutor.next_question_async)
        assert "error_type" in sig_async.parameters and sig_async.parameters["error_type"].default is None


# ---------------------------------------------------------------- 4. 错因收敛
class TestNormalizeError:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("READ", ec.ERROR_READ),
            ("CALC", ec.ERROR_CALC),
            ("读题错", ec.ERROR_READ),
            ("计算错", ec.ERROR_CALC),
            ("他主要是读题错误，漏看了条件", ec.ERROR_READ),
            ("计算失误", ec.ERROR_CALC),
            ("没验算", ec.ERROR_CHECK),
            (None, ec.ERROR_UNCLASSIFIED),
            ("", ec.ERROR_UNCLASSIFIED),
            ("完全没法归类的描述", ec.ERROR_UNCLASSIFIED),
        ],
    )
    def test_normalize(self, raw, expected) -> None:
        assert ec.normalize_error(raw) == expected


# ---------------------------------------------------------------- 5. 练习引导模板
class TestPracticeGuide:
    def test_version_and_diagnose_section(self) -> None:
        """v1.3.0 起练习引导模板含【先诊断再教学】小节与占位符。"""
        assert practice_guide.VERSION >= "1.3.0"
        assert "先诊断再教学" in practice_guide.PROMPT
        for placeholder in practice_guide.PLACEHOLDERS:
            assert placeholder in practice_guide.PROMPT
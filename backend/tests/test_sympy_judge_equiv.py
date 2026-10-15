"""判题 L3 层（math_verify 数学等价判定）测试。

为什么要这个文件
----------------
升级前判题只有「字符串归一（L1）+ SymPy（L0）」两层，``1/2`` vs ``0.5``、
``x+1`` vs ``1+x``、``\\frac{1}{2}`` vs ``0.5`` 这类**等价但字面不同**的写法
会被误判成错，学生拿到错误的错题反馈。L3 层用 math_verify 做数学等价判定兜底。

用例组织
--------
* ``EQUIV_CASES_L0`` —— L0（SymPy）自身就能判对的等价形式，用来守住
  **升级不回退**：math_verify 装没装都必须判对；
* ``EQUIV_CASES_L3`` —— 只有 L3 才能救回来的形式（百分号 / 单位 / 单位大小写），
  math_verify 未安装时整组 **skip**（L3 降级，行为回到升级前的两层路径）；
* ``NOT_EQUIV_CASES`` —— 应该判错的用例（必须覆盖「反向」，不能只写正向）。

⚠️ math_verify 是**可选依赖**：未安装时相关用例 skip 而不是 fail
（L3 内部 lazy import 捕获 ImportError → 降级回 L0/L1，应用照常工作）。
"""

from __future__ import annotations

import sys
import time
from typing import List, Tuple

import pytest

from app.services import sympy_judge
from app.services.judge_engine import LEVEL_FALLBACK, LEVEL_RULE

# math_verify 未安装 → 依赖 L3 的用例跳过（不 fail）
try:
    import math_verify  # noqa: F401

    HAS_MATH_VERIFY: bool = True
except ImportError:  # pragma: no cover - 取决于运行环境是否装了可选依赖
    HAS_MATH_VERIFY = False

requires_math_verify = pytest.mark.skipif(
    not HAS_MATH_VERIFY,
    reason="math_verify 未安装：L3 层自动降级回 L0/L1，依赖 L3 的用例跳过",
)

#: 判题用例统一用的题型（L0/L3 都支持的纯计算题）
Q_TYPE_CALC: str = "计算"


# ------------------------------------------------------------------ 用例表
#: 等价 → 判对（L0 SymPy 已能覆盖，math_verify 装没装都必须判对）
EQUIV_CASES_L0: List[Tuple[str, str]] = [
    ("1/2", "0.5"),                     # 分数 vs 小数
    ("0.5", "1/2"),                     # 小数 vs 分数（反向）
    ("x+1", "1+x"),                     # 加法交换律
    ("2x", "2*x"),                      # 隐式乘法
    ("x*2", "2*x"),                     # 乘法交换律
    (r"\frac{1}{2}", "0.5"),            # LaTeX 分数 vs 小数
    ("0.5", r"\frac{1}{2}"),            # 小数 vs LaTeX 分数（反向）
    (r"\dfrac{3}{4}", "0.75"),          # \dfrac vs 小数
    ("3.0", "3"),                       # 小数尾零
    ("3", "3.0"),                       # 整数 vs 小数尾零（反向）
    ("0.50", "0.5"),                    # 小数尾零（右侧）
    ("2/4", "1/2"),                     # 未约分分数
    ("3/6", "1/2"),                     # 未约分分数
    ("1÷2", "1/2"),                     # 除法式 vs 分数
    ("1 + 1", "2"),                     # 算式 vs 结果
    ("3-1", "1+1"),                     # 两个不同算式同值
    ("2^2", "4"),                       # 幂 vs 结果
    ("-0.5", "-1/2"),                   # 负数分数 vs 小数
    ("-（1/2）", "-0.5"),                # 全角括号 + 正负号位置
    ("+3", "3"),                        # 正号冗余
    ("  1/2  ", "0.5"),                 # 多余空格
    ("x  +  1", "1 + x"),               # 多余空格（表达式内部）
    ("（x+1）", "(x+1)"),                # 全角括号 vs 半角括号
    ("１/２", "0.5"),                    # 全角数字
    ("√ (4)", "2"),                     # 根号 vs 结果
    ("12米", "12"),                     # 中文单位（L0 的结果片段抽取已能处理）
]

#: 等价 → 判对（**只有 L3 能救回来**：百分号 / 拉丁单位 / 单位大小写）
EQUIV_CASES_L3: List[Tuple[str, str]] = [
    ("50%", "0.5"),                     # 百分号 vs 小数
    ("0.5", "50%"),                     # 小数 vs 百分号（反向）
    ("0.25", "25%"),                    # 百分号 vs 小数
    ("100%", "1"),                      # 百分号 vs 整数
    ("2.5", "250%"),                    # 大于 1 的百分数
    ("5cm", "5"),                       # 单侧带单位
    ("5", "5cm"),                       # 单侧带单位（反向）
    ("5 cm", "5"),                      # 数字与单位之间有空格
    ("5CM", "5cm"),                     # 单位大小写
    ("5cm", "5CM"),                     # 单位大小写（反向）
    ("5", "5CM"),                       # 单侧带单位 + 大写单位
]

#: 不等价 → 判错（**必须覆盖反向用例**，否则判题「全判对」也能全绿）
NOT_EQUIV_CASES: List[Tuple[str, str]] = [
    ("1/2", "0.6"),                     # 分数 vs 邻近小数
    ("0.5", "1/3"),                     # 小数 vs 不同分数
    ("x+1", "x+2"),                     # 常数项不同
    ("x+1", "x-1"),                     # 符号不同
    ("2x", "3*x"),                      # 系数不同
    ("3", "4"),                         # 整数不同
    (r"\frac{1}{2}", r"\frac{1}{3}"),   # LaTeX 分数不同
    ("0.5", "0.05"),                    # 小数点位置
    ("2^2", "5"),                       # 幂运算结果不同
    ("1/2", "2"),                       # 分数 vs 倒数
    ("3/4", "0.34"),                    # 分数 vs 近似小数
    ("5cm", "5m"),                      # **单位不同**：绝不能被剥成 5 == 5 判对
    ("5m", "5cm"),                      # 单位不同（反向）
    ("1", "1+x"),                       # **漏写项**：math_verify 会把 1+x 截断成 1
    ("2", "2x"),                        # **漏写项**：math_verify 会把 2x 截断成 2
]


# ------------------------------------------------------------------ 主流程：等价 → 判对
class TestEquivalentAnswersAreCorrect:
    """等价写法必须判对（升级的核心目标）。"""

    @pytest.mark.parametrize(("correct_answer", "selected_answer"), EQUIV_CASES_L0)
    def test_l0_equivalence(self, correct_answer: str, selected_answer: str) -> None:
        """L0（SymPy）已能覆盖的等价形式：不依赖 math_verify，装没装都必须判对。"""
        outcome = sympy_judge.layered_judge(
            Q_TYPE_CALC, "", None, correct_answer, selected_answer
        )
        assert outcome.is_correct is True, (
            f"等价答案被误判为错：{selected_answer} ≡ {correct_answer}"
            f" | level={outcome.level} audit={outcome.audit_reason}"
        )

    @requires_math_verify
    @pytest.mark.parametrize(("correct_answer", "selected_answer"), EQUIV_CASES_L3)
    def test_l3_equivalence(self, correct_answer: str, selected_answer: str) -> None:
        """只有 L3 能救回来的等价形式（百分号 / 单位 / 单位大小写）。"""
        outcome = sympy_judge.layered_judge(
            Q_TYPE_CALC, "", None, correct_answer, selected_answer
        )
        assert outcome.is_correct is True, (
            f"L3 未把等价答案救回：{selected_answer} ≡ {correct_answer}"
            f" | level={outcome.level} audit={outcome.audit_reason}"
        )


# ------------------------------------------------------------------ 主流程：不等价 → 判错
class TestDifferentAnswersAreWrong:
    """不等价写法必须判错（反向覆盖，防止「全判对」也能过测）。"""

    @pytest.mark.parametrize(("correct_answer", "selected_answer"), NOT_EQUIV_CASES)
    def test_not_equivalent(self, correct_answer: str, selected_answer: str) -> None:
        """不等价的答案一律判错；L3 只能「救回判对」，不能把判错改成判对。"""
        outcome = sympy_judge.layered_judge(
            Q_TYPE_CALC, "", None, correct_answer, selected_answer
        )
        assert outcome.is_correct is False, (
            f"不等价答案被误判为对：{selected_answer} ≠ {correct_answer}"
            f" | level={outcome.level} audit={outcome.audit_reason}"
        )


# ------------------------------------------------------------------ L3 层级与降级路径
class TestL3LayerRouting:
    """L3 的生效条件与降级路径（这部分不依赖 math_verify 是否真的装上）。"""

    @requires_math_verify
    def test_l3_used_when_l0_abstains(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """L0 判不出（返回 None）时，L3 应接管并给出 level=3 的判对结论。"""
        monkeypatch.setattr(sympy_judge, "judge", lambda *args, **kwargs: None)
        outcome = sympy_judge.layered_judge(Q_TYPE_CALC, "", None, "1/2", "0.5")
        assert outcome.level == sympy_judge.LEVEL_MATH_VERIFY
        assert outcome.is_correct is True

    def test_l3_off_switch_keeps_two_layers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``judge_l3=off``：完全不进 L3，回到原来的 L0 + L1 两层路径。

        这里把 L0 也强制成「判不出」，于是应当落到 L1 规则引擎 —— 而 ``计算``
        不在 L1 支持的题型里，所以是 level=4 兜底。断言 level=4 即证明
        **L3 没有偷跑**。
        """
        monkeypatch.setattr(sympy_judge, "judge", lambda *args, **kwargs: None)
        monkeypatch.setattr(sympy_judge, "_load_l3_config", lambda: (False, 500))
        outcome = sympy_judge.layered_judge(Q_TYPE_CALC, "", None, "1/2", "0.5")
        assert outcome.level == LEVEL_FALLBACK
        assert outcome.is_correct is False

    def test_l3_skipped_for_choice_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """选择/判断题由 L1 规则引擎负责，L3 不得抢在它前面下结论。"""
        called: List[str] = []

        def _spy(correct: str, selected: str, timeout_ms: int = 500) -> bool:
            called.append(f"{correct}|{selected}")
            return True  # 即使 L3 说「等价」，也不该影响选择题判定

        monkeypatch.setattr(sympy_judge, "_math_verify_equivalent", _spy)
        outcome = sympy_judge.layered_judge(
            "选择",
            "",
            {"A": "0.5", "B": "0.6"},
            "A",
            "B",
        )
        assert outcome.level == LEVEL_RULE
        assert outcome.is_correct is False
        assert called == [], "选择题不应调用 L3 等价判定"

    def test_import_error_degrades_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """math_verify 未安装（ImportError）时 L3 返回 None，不抛异常、不误判。"""
        # sys.modules 里塞 None → ``from math_verify import ...`` 抛 ImportError
        monkeypatch.setitem(sys.modules, "math_verify", None)
        assert sympy_judge._math_verify_equivalent("1/2", "0.5") is None

        monkeypatch.setattr(sympy_judge, "judge", lambda *args, **kwargs: None)
        outcome = sympy_judge.layered_judge(Q_TYPE_CALC, "", None, "1/2", "0.5")
        assert outcome.level == LEVEL_FALLBACK

    @requires_math_verify
    def test_timeout_forces_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """单题判定超时 → 强制降级，返回 None 而不是卡住判题主流程。

        这里刻意把「判定本体」换成慢函数（而不是只把超时压到 1ms 去赌机器快慢），
        保证用例在任何机器上都是确定性的。
        """

        def _slow_parse(parse_fn: Any, text: str) -> list:
            time.sleep(0.3)
            return ["stub"]

        monkeypatch.setattr(sympy_judge, "_math_verify_parse", _slow_parse)
        assert sympy_judge._math_verify_equivalent("1/2", "0.5", 50) is None

    def test_l3_never_flips_correct_to_wrong(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L3 只能「救回判对」：L0 已判对时必须原样返回，L3 不参与。"""
        called: List[str] = []
        monkeypatch.setattr(
            sympy_judge,
            "_math_verify_equivalent",
            lambda *args, **kwargs: called.append("x") or False,
        )
        outcome = sympy_judge.layered_judge(Q_TYPE_CALC, "", None, "1/2", "0.5")
        assert outcome.is_correct is True
        assert outcome.level == sympy_judge.LEVEL_SYMPY
        assert called == [], "L0 已判对时不应再调用 L3"


# ------------------------------------------------------------------ 超时工具
class TestRunWithTimeout:
    """``_run_with_timeout``：守护线程 + join 超时（跨平台，不用 signal/asyncio）。"""

    def test_completes(self) -> None:
        completed, value, error = sympy_judge._run_with_timeout(lambda: 42, 1.0)
        assert completed is True
        assert value == 42
        assert error is None

    def test_expired(self) -> None:
        completed, value, error = sympy_judge._run_with_timeout(
            lambda: time.sleep(0.5), 0.05
        )
        assert completed is False
        assert value is None
        assert error is None

    def test_captures_error(self) -> None:
        def _boom() -> None:
            raise ValueError("math_verify 内部炸了")

        completed, value, error = sympy_judge._run_with_timeout(_boom, 1.0)
        assert completed is True
        assert value is None
        assert isinstance(error, ValueError)


# ------------------------------------------------------------------ 纯函数：归一助手
class TestNormalizeHelpers:
    """百分号归一与单位剥离（纯函数，不依赖 math_verify）。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("50%", "(50/100)"),
            ("25%", "(25/100)"),
            ("100%", "(100/100)"),
            ("0.5", "0.5"),          # 无百分号：原样返回
            ("2.5", "2.5"),
        ],
    )
    def test_normalize_percent(self, raw: str, expected: str) -> None:
        assert sympy_judge._normalize_percent(raw) == expected

    @pytest.mark.parametrize(
        ("correct_text", "selected_text", "expected"),
        [
            ("5cm", "5", ("5", "5")),            # 单侧带单位 → 剥
            ("5", "5cm", ("5", "5")),            # 单侧带单位（反向）→ 剥学生侧
            ("5 cm", "5", ("5", "5")),           # 数字与单位间有空格
            ("5CM", "5cm", ("5", "5")),          # 单位相同（大小写不同）→ 一起剥
            ("5cm", "5m", ("5cm", "5m")),        # 单位不同 → 一个都不剥
            ("12米", "12", ("12", "12")),        # 中文单位
            ("3", "3", ("3", "3")),              # 都不带单位 → 原样
            ("x+1", "1+x", ("x+1", "1+x")),      # 表达式不受影响
        ],
    )
    def test_strip_unit_pair(
        self, correct_text: str, selected_text: str, expected: Tuple[str, str]
    ) -> None:
        assert sympy_judge._strip_unit_pair(correct_text, selected_text) == expected

    @pytest.mark.parametrize(
        ("parsed", "text", "expected"),
        [
            ([5, "5"], "5", True),          # 原文完整命中 → 可以判
            ([5, "5"], "5cm", False),       # 单位被吞（"5cm" 只抽到 "5"）→ 拒绝
            ([1, "1"], "1+x", False),       # 表达式被截断 → 拒绝
            ([], "1/2", False),                 # 空抽取 → 拒绝
        ],
    )
    def test_extraction_is_complete(
        self, parsed: object, text: str, expected: bool
    ) -> None:
        """math_verify 的抽取必须完整覆盖原文，否则拒绝判定（防「错的判成对」）。"""
        assert sympy_judge._extraction_is_complete(parsed, text) is expected


# ------------------------------------------------------------------ 配置开关
class TestJudgeL3Setting:
    """``judge_l3`` 开关：默认 on，off 时完全走原两层路径。"""

    def test_default_is_on(self) -> None:
        from app.core.config import Settings

        cfg = Settings()
        assert cfg.judge_l3 == "on"
        assert cfg.judge_l3_enabled is True
        assert cfg.judge_l3_timeout_ms == 500

    @pytest.mark.parametrize("raw", ["off", "OFF", "0", "false", "no", "关闭"])
    def test_off_values(self, raw: str) -> None:
        from app.core.config import Settings

        assert Settings(judge_l3=raw).judge_l3_enabled is False

    @pytest.mark.parametrize("raw", ["on", "ON", "1", "true", "yes", "开启"])
    def test_on_values(self, raw: str) -> None:
        from app.core.config import Settings

        assert Settings(judge_l3=raw).judge_l3_enabled is True


# ------------------------------------------------------------------ no-leak 铁律
class TestL3NoLeak:
    """L3 的结论文案同样遵守「reason 绝不泄题」铁律。"""

    def test_reason_has_no_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sympy_judge, "judge", lambda *args, **kwargs: None)
        monkeypatch.setattr(sympy_judge, "_l3_equivalent", lambda *args, **kwargs: True)
        outcome = sympy_judge.layered_judge(Q_TYPE_CALC, "", None, "0.5", "1/2")
        assert outcome.is_correct is True
        assert "0.5" not in outcome.reason
        assert "1/2" not in outcome.reason
        # 审计字段才允许带答案（只进日志）
        assert "0.5" in outcome.audit_reason

"""T04 · /guide 泄题回归测试。

覆盖范围（≥12 条用例）：
    * 五类错因各 ≥1 条：读题错 / 方法错 / 计算错 / 知识点缺 / 检查漏
    * 沉默场景（在写中间步骤）1 条
    * 最终确认场景（已完整）1 条
    * 泄题红线 ≥4 条：选择题键位泄露 / 选项正文泄露 / 判断题符号泄露 / 结论词泄露

设计要点：
    * **不依赖真实方舟调用**——用固定回复字符串模拟模型输出，断言
      ``judge_engine.leaks_answer()`` 对候选回复的判定结果。
    * **不碰数据库**——全部是纯函数断言，无夹具、无 HTTP 请求。
    * 用 ``leaks_answer(reply, q_type, options, correct_answer)`` 做硬断言：
      - 正常引导语 → ``leaks_answer is False``（不泄题）
      - 泄题回复 → ``leaks_answer is True``（被代码闸门拦截）
    * 额外断言引导回复的结构与节奏：句数 2-4、问号数量 ≤1（每轮 1 个问题上限）。

与 ``test_practice.py::TestGuideNoLeak`` 的分工：
    * ``TestGuideNoLeak`` 覆盖 ``leaks_answer`` 的基础正/反例（参数化批量）；
    * 本文件按 **T04 五类错因** 场景化组织，每条用例都带题干 + 孩子作答 +
      期望引导行为，便于教研对照 ``docs/guide-prompt-optimization.md`` 第 5 节。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

import pytest

from app.services import judge_engine, practice_service


# ==================================================================== 工具
def _count_sentences(text: str) -> int:
    """粗略统计句数：按中文句末标点（。！？）切分，忽略空串。"""
    parts = re.split(r"[。！？\.\!\?]", text.strip())
    return len([p for p in parts if p.strip()])


def _count_questions(text: str) -> int:
    """统计问号数量（中文 ？ + 英文 ?），用于断言「每轮 1 个问题上限」。"""
    return text.count("？") + text.count("?")


def _has_leak_symbol(reply: str, symbols: tuple[str, ...]) -> bool:
    """回复里是否出现了判断题泄题符号（√/× 等）。"""
    return any(sym in reply for sym in symbols)


# ==================================================================== 测试数据
#: 选择题选项（选项正文含空格，与 leaks_answer 的 option_text 子串匹配保持一致）
CHOICE_OPTIONS_1: Dict[str, str] = {"A": "8元", "B": "15元", "C": "10元", "D": "2元"}
CHOICE_OPTIONS_2: Dict[str, str] = {"A": "4.2 米", "B": "1.8 米", "C": "3.6 米", "D": "1.2 米"}
CHOICE_OPTIONS_3: Dict[str, str] = {"A": "100", "B": "90", "C": "80", "D": "110"}
CHOICE_OPTIONS_4: Dict[str, str] = {"A": "15元", "B": "17元", "C": "20元", "D": "7元"}


class TestGuideRegression:
    """T04 /guide 泄题回归。

    每个测试方法对应 ``docs/guide-prompt-optimization.md`` 第 5 节的一条用例。
    用固定回复字符串模拟方舟输出，断言 ``leaks_answer()`` 判定 + 结构节奏。
    """

    # ====================================================================
    # 五类错因 × 正常引导（leaks_answer 必须返回 False + 结构断言）
    # ====================================================================

    def test_read_error_choice(self) -> None:
        """用例 1 · 读题错（选择题）：引导重读题目，不泄题。"""
        reply = (
            "我们先把题目再读一遍：它问的是'一共花了多少'，"
            "每本 5 元买了 3 本。你觉得应该用什么方法？"
        )
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_1, "B") is False
        assert _count_questions(reply) == 1
        assert 2 <= _count_sentences(reply) <= 4

    def test_read_error_judge(self) -> None:
        """用例 2 · 读题错（判断题）：引导回到条件，不泄题。"""
        reply = "你想想看：有没有哪个偶数比较特殊，它只能被 1 和自己整除？"
        assert judge_engine.leaks_answer(reply, "判断", None, "×") is False
        assert _count_questions(reply) == 1
        # 判断题泄题符号不能出现在回复里
        assert not _has_leak_symbol(reply, ("√", "✔", "✓", "×", "✗", "✘"))

    def test_method_error_choice(self) -> None:
        """用例 3 · 方法错（选择题）：抛矛盾问题不纠正，不泄题。"""
        reply = (
            "你算得很快！不过想想看：剪去一段绳子后，"
            "剩下的应该比原来的 3 米长还是短呢？"
        )
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_2, "B") is False
        assert _count_questions(reply) == 1
        assert 2 <= _count_sentences(reply) <= 4

    def test_calc_error_choice(self) -> None:
        """用例 4 · 计算错（选择题）：只陪查计算这一步，不泄题。"""
        reply = "你的方法对了，是乘法。我们一起来查这一步：5 乘 4 等于多少？"
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_3, "A") is False
        assert _count_questions(reply) == 1
        assert 2 <= _count_sentences(reply) <= 4

    def test_knowledge_gap_judge(self) -> None:
        """用例 5 · 知识点缺（判断题）：补简短讲解再引导，不泄题。"""
        reply = (
            "没关系。π 是一个特殊的小数，它的小数部分永远写不完。"
            "你觉得它是有限小数还是无限小数？"
        )
        assert judge_engine.leaks_answer(reply, "判断", None, "√") is False
        assert _count_questions(reply) == 1
        # 判断题泄题符号不能出现
        assert not _has_leak_symbol(reply, ("√", "✔", "✓", "×", "✗", "✘"))

    def test_check_miss_choice(self) -> None:
        """用例 6 · 检查漏（选择题）：提醒回头检查遗漏，不泄题。"""
        reply = "你算书的部分对了，15 元。不过题目里还提到了一样东西，你看看有没有漏掉的？"
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_4, "B") is False
        assert _count_questions(reply) == 1
        assert 2 <= _count_sentences(reply) <= 4

    # ====================================================================
    # 沉默 & 确认场景
    # ====================================================================

    def test_silence_writing_steps(self) -> None:
        """用例 7 · 在写中间步骤：只肯定不推进，不泄题。"""
        reply = "这一步的乘法你选对了，接着往下算，个位 6 乘 7 是多少？"
        # 用 CHOICE_OPTIONS_3 模拟选择题场景（标准答案 A=100）
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_3, "A") is False
        assert _count_questions(reply) == 1
        # 沉默场景句数更短（1-2 句）
        assert 1 <= _count_sentences(reply) <= 2

    def test_ask_final_confirm(self) -> None:
        """用例 8 · 已完整：问最终确认，不泄题。"""
        reply = "你算完了吗？确定这是你的最终答案吗？"
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_3, "A") is False
        assert _count_questions(reply) == 2  # 确认场景 2 个问句是合理的

    # ====================================================================
    # 泄题红线（leaks_answer 必须返回 True）
    # ====================================================================

    def test_leak_choice_key(self) -> None:
        """用例 9 · 泄题红线（选择题 · 键位泄露）：回复含「答案是B」。"""
        reply = "答案是B，因为3乘5等于15。"
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_1, "B") is True

    def test_leak_choice_option_text(self) -> None:
        """用例 10 · 泄题红线（选择题 · 选项正文泄露）：回复含标准选项正文。"""
        reply = "所以结果就是 1.8 米。"
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_2, "B") is True

    def test_leak_judge_symbol(self) -> None:
        """用例 11 · 泄题红线（判断题 · 符号泄露）：回复含 √。"""
        reply = "这句话应该打 √ 哦。"
        assert judge_engine.leaks_answer(reply, "判断", None, "√") is True

    def test_leak_judge_verdict_word(self) -> None:
        """用例 12 · 泄题红线（判断题 · 结论词泄露）：回复含「所以是正确的」。"""
        reply = "所以这个判断是正确的。"
        assert judge_engine.leaks_answer(reply, "判断", None, "√") is True

    def test_leak_choice_select_prefix(self) -> None:
        """用例 12b · 泄题红线（选择题 · 选B 前缀泄露）。"""
        reply = "所以应该选 B 哦。"
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_1, "B") is True

    # ====================================================================
    # sanitize_guide_reply 集成断言（代码闸门完整链路）
    # ====================================================================

    def test_sanitize_replaces_leak_with_fallback(self) -> None:
        """泄题回复被 ``sanitize_guide_reply`` 拦截，返回兜底话术。"""
        leaking_reply = "答案是B，因为3乘5等于15。"
        safe = practice_service.sanitize_guide_reply(
            leaking_reply,
            q_type="选择",
            options=CHOICE_OPTIONS_1,
            correct_answer="B",
        )
        assert safe == practice_service.GUIDE_FALLBACK_REPLY
        assert "B" not in safe

    def test_sanitize_passes_clean_reply(self) -> None:
        """正常引导语被 ``sanitize_guide_reply`` 原样放行。"""
        clean_reply = "我们先把题目再读一遍：它问的是一共花了多少。你觉得应该用什么方法？"
        safe = practice_service.sanitize_guide_reply(
            clean_reply,
            q_type="选择",
            options=CHOICE_OPTIONS_1,
            correct_answer="B",
        )
        assert safe == clean_reply

    def test_sanitize_empty_reply_uses_fallback(self) -> None:
        """空回复被 ``sanitize_guide_reply`` 替换为兜底话术。"""
        safe = practice_service.sanitize_guide_reply(
            "",
            q_type="选择",
            options=CHOICE_OPTIONS_1,
            correct_answer="B",
        )
        assert safe == practice_service.GUIDE_FALLBACK_REPLY

    def test_sanitize_leak_judge_with_symbol(self) -> None:
        """判断题泄题（含 √）被 ``sanitize_guide_reply`` 拦截。"""
        leaking_reply = "这句话应该打 √ 哦。"
        safe = practice_service.sanitize_guide_reply(
            leaking_reply,
            q_type="判断",
            options=None,
            correct_answer="√",
        )
        assert safe == practice_service.GUIDE_FALLBACK_REPLY
        assert "√" not in safe

    # ====================================================================
    # 额外正例：正常引导语不误杀（leaks_answer 保守优先但不误伤）
    # ====================================================================

    @pytest.mark.parametrize(
        "reply",
        [
            "我们一步步来：先说说这道题告诉了我们什么条件？",
            "别急，先想想 3 和 4 分别表示什么。",
            "你是怎么算出来的？把过程说给我听听。",
            "题目里有几个数？它们之间是什么关系呢？",
            "你的方法对了，我们一起来查 5 乘 4 等于多少？",
        ],
    )
    def test_clean_guide_not_flagged(self, reply: str) -> None:
        """正常引导语不被 ``leaks_answer`` 误判为泄题。"""
        assert judge_engine.leaks_answer(reply, "选择", CHOICE_OPTIONS_3, "A") is False

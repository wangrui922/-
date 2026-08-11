"""T2/T10 单元测试：学生意图识别 ``app.ai.intent``（mode 的唯一定稿处）。

被测契约来自设计文档：

* §3.2 规则优先级表：``override`` → ``JUDGE`` → ``DEMO`` → ``HINT`` → ``GUIDE``（兜底）；
* §3.2 反例保护：``我不会``(DEMO) 与 ``不会吧``(非 DEMO) 必须用词边界区分；
* §3.2 冷却回切（PRD P1-1）：连续 ``COOLDOWN_ROUNDS`` 轮同一非 GUIDE mode，
  第 3 轮强制回 ``GUIDE``，防止一句「不会」把整场对话锁死在 DEMO；
* §3.1 ``UNGUARDED_MODES``：``DEMO``/``JUDGE`` 完全跳过泄漏检查。

零依赖铁律
----------
``intent.py`` 是**纯函数模块**（零 DB / 零网络 / 零新依赖），因此本文件
**不用任何 fixture、不建库、不打桩**——一旦将来有人往 ``intent`` 里塞了
DB 或方舟调用，这些用例会立刻因为 import/调用副作用而暴露。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.ai import intent
from app.ai.intent import (
    COOLDOWN_ROUNDS,
    DEFAULT_MODE,
    MODE_DEMO,
    MODE_GUIDE,
    MODE_HINT,
    MODE_JUDGE,
    MODE_VALUES,
    UNGUARDED_MODES,
    detect,
    detect_mode,
    normalize_mode,
)


# ==================================================================== 工具
def _history(*texts: str, role: str = "user") -> List[Dict[str, Any]]:
    """造一段会话历史（默认全是学生原话，供冷却判定使用）。"""
    return [{"role": role, "content": text} for text in texts]


# ==================================================================== 枚举契约
class TestModeEnum:
    """mode 枚举是跨三层的唯一契约，先把它钉死。"""

    def test_mode_values_are_exactly_four(self) -> None:
        """四个 mode 一个不多一个不少，且 GUIDE 是默认值。"""
        assert set(MODE_VALUES) == {MODE_GUIDE, MODE_HINT, MODE_DEMO, MODE_JUDGE}
        assert len(MODE_VALUES) == 4
        assert DEFAULT_MODE == MODE_GUIDE

    def test_unguarded_modes_are_demo_and_judge_only(self) -> None:
        """只有 DEMO/JUDGE 跳过泄漏闸门；GUIDE/HINT 必须仍受管束（§3.1）。"""
        assert set(UNGUARDED_MODES) == {MODE_DEMO, MODE_JUDGE}
        assert MODE_GUIDE not in UNGUARDED_MODES
        assert MODE_HINT not in UNGUARDED_MODES


# ==================================================================== normalize
class TestNormalizeMode:
    """``normalize_mode``：任意脏输入 → 合法 mode 或 None。"""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("DEMO", MODE_DEMO),
            ("demo", MODE_DEMO),
            ("  Judge ", MODE_JUDGE),
            ("hint", MODE_HINT),
            ("guide", MODE_GUIDE),
        ],
    )
    def test_accepts_dirty_but_legal_input(self, raw: Any, expected: str) -> None:
        """大小写 / 首尾空白都要能收敛。"""
        assert normalize_mode(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "讲解", "DEMOS", "TEACH", 0, [], {}])
    def test_rejects_illegal_input_with_none(self, raw: Any) -> None:
        """收敛不了必须返回 ``None``（语义是「未指定」，不是 GUIDE）。

        这条很关键：如果这里回落成 GUIDE，``detect`` 就会把「没传 override」
        误当成「显式指定了 GUIDE」，意图识别整个失效。
        """
        assert normalize_mode(raw) is None


# ==================================================================== 优先级 1：override
class TestOverridePriority:
    """优先级 1：前端快捷按钮显式传值 → 直接采用，不做任何识别。"""

    def test_override_beats_regex(self) -> None:
        """文本明明是 DEMO 特征，override=HINT 时也必须听 override。"""
        result = detect(text="我不会，你给我讲一遍", override="HINT")
        assert result.mode == MODE_HINT
        assert result.source == "override"
        assert result.confidence == pytest.approx(1.0)

    @pytest.mark.parametrize("raw", ["demo", "DEMO", " Demo "])
    def test_override_is_case_insensitive(self, raw: str) -> None:
        """脏大小写的显式 mode 同样 100% 命中。"""
        assert detect_mode(text="随便说点什么", override=raw) == MODE_DEMO

    def test_illegal_override_falls_back_to_detection(self) -> None:
        """非法 override 静默降级为自动识别，**不报错**（旧版前端零感知）。"""
        result = detect(text="给点提示", override="不存在的模式")
        assert result.mode == MODE_HINT
        assert result.source == "regex"

    def test_override_ignores_cooldown(self) -> None:
        """显式 > 猜测：override 不受冷却回切影响。"""
        history = _history("我不会", "我不会做")
        assert detect_mode(text="我不会", history=history, override="DEMO") == MODE_DEMO


# ==================================================================== 优先级 2：JUDGE
class TestJudgeRules:
    """优先级 2：学生给出了结论 / 主动要评判 → JUDGE。"""

    @pytest.mark.parametrize(
        "text",
        [
            "答案是 12",
            "结果为 3.5",
            "我算出来是 42",
            "我得到 15",
            "我觉得是 B",
            "应该是 7 元",
            "我做完了",
            "我算完了",
            "这样对不对",
            "对吗",
            "是不是这样",
            "帮我看看",
            "帮我检查一下",
            "我这样做",
        ],
    )
    def test_judge_positive(self, text: str) -> None:
        """§3.2 JUDGE 规则行的正向语料全部命中。"""
        assert detect_mode(text=text) == MODE_JUDGE, text

    def test_judge_beats_demo_when_both_hit(self) -> None:
        """「我算出来是 12，对不对」同含数值与「对不对」→ 诉求是评判不是重讲。

        这是 §3.2 明写的优先级理由，也是最容易被实现顺序写反的一条。
        """
        assert detect_mode(text="我算出来是 12，对不对") == MODE_JUDGE

    @pytest.mark.parametrize("text", ["你告诉我答案吧", "直接给我答案", "答案是多少"])
    def test_judge_negative_guard_hands_over_to_demo(self, text: str) -> None:
        """「要答案」不是「给答案」：被 JUDGE 反向否决后应落到 DEMO。

        反例保护——``答案\\s*\\S+`` 会把「你告诉我答案吧」误伤成 JUDGE，
        但学生诉求显然是要讲解。
        """
        assert detect_mode(text=text) == MODE_DEMO, text

    @pytest.mark.parametrize("text", ["结果是什么", "结果是多少", "我不知道答案", "我不知道结果"])
    def test_judge_negative_guard_never_yields_judge(self, text: str) -> None:
        """反向否决表命中的语料**一律不得**判成 JUDGE（这是它唯一的硬契约）。

        ⚠️ 已知落差（QA 记录，非阻塞）：``_JUDGE_NEGATIVE_PATTERNS`` 的
        ``不知道(答案|结果)`` 与 ``结果是(多少|什么|啥|几)`` 两条在 ``_DEMO_PATTERNS``
        里**没有对应项**，所以这几句实际落到 GUIDE，而模块自己的注释写的是
        「交给 DEMO 处理」。设计文档 §3.2 未逐字列举这两种说法，故本用例只断言
        「不判 JUDGE」这条一定成立的契约，不把测试绑死在未定义行为上；
        补 DEMO 正则的建议已单独反馈工程师（详见 QA 报告「已知落差」）。
        """
        assert detect_mode(text=text) != MODE_JUDGE, text


# ==================================================================== 优先级 3：DEMO
class TestDemoRules:
    """优先级 3：明确说不会 / 明确请老师讲 → DEMO。"""

    @pytest.mark.parametrize(
        "text",
        [
            "我不会",
            "我真的不会",
            "我完全不会",
            "不会做",
            "不会算",
            "不知道怎么做",
            "不知道咋下手",
            "完全看不懂",
            "你给我讲一遍",
            "老师讲一下",
            "你解释一下",
            "直接告诉我",
            "把这道题给我讲讲",
            "我想看完整过程",
            "完整步骤",
            "看演示",
            "演示一下",
            "教我怎么做",
        ],
    )
    def test_demo_positive(self, text: str) -> None:
        """§3.2 DEMO 规则行的正向语料全部命中。"""
        assert detect_mode(text=text) == MODE_DEMO, text

    def test_prd_p0_1_golden_sentence(self) -> None:
        """PRD P0-1 的验收原句必须判成 DEMO（§9 验收对照表）。"""
        assert detect_mode(text="我不会，你给我讲一遍") == MODE_DEMO

    def test_prd_complaint_sentence(self) -> None:
        """PRD 1.2 真实投诉语料：「我不知道，你把这道题给我解释下吧」。"""
        assert detect_mode(text="我不知道，你把这道题给我解释下吧") == MODE_DEMO

    # ------------------------------------------------------- ★ 反例保护
    @pytest.mark.parametrize("text", ["不会吧", "不会吧？", "不会真的吧", "不会呗", "不会嘛"])
    def test_bu_hui_ba_is_not_demo(self, text: str) -> None:
        """★「不会吧」是纯感叹，**不是** DEMO（§3.2 反例保护，本次核心用例）。

        没有词边界保护的实现会用 ``不会`` 裸匹配，把一句语气词判成「请你讲」，
        整场对话瞬间被锁进讲解模式——这正是要防的事故。
        """
        assert detect_mode(text=text) != MODE_DEMO, text
        assert detect_mode(text=text) == MODE_GUIDE, text


# ==================================================================== 优先级 4：HINT
class TestHintRules:
    """优先级 4：要方向、要线索，但没要完整解答 → HINT。"""

    @pytest.mark.parametrize(
        "text",
        [
            "给点提示",
            "给个提示",
            "提示一下",
            "提醒我一下",
            "给个思路",
            "从哪里开始",
            "从哪儿下手",
            "第一步是什么",
            "第一步怎么走",
            "有没有提示",
            "能不能提示一下",
            "我卡住了",
        ],
    )
    def test_hint_positive(self, text: str) -> None:
        """§3.2 HINT 规则行的正向语料全部命中。"""
        assert detect_mode(text=text) == MODE_HINT, text

    def test_hint_does_not_swallow_demo(self) -> None:
        """「不会做，给点提示」里 DEMO 优先级更高，先命中 DEMO。"""
        assert detect_mode(text="不会做，给点提示") == MODE_DEMO


# ==================================================================== 优先级 5：GUIDE 兜底
class TestGuideFallback:
    """优先级 5：什么都没命中 → GUIDE（默认引导）。"""

    @pytest.mark.parametrize(
        "text",
        ["懂了", "我先把 3 移到右边", "嗯", "然后呢", "老师你好", "这道题挺有意思的"],
    )
    def test_guide_fallback(self, text: str) -> None:
        """常规对话一律走引导，不许乱切模式。"""
        result = detect(text=text)
        assert result.mode == MODE_GUIDE, text
        assert result.source == "default"

    @pytest.mark.parametrize("text", ["", "   ", "\n\t "])
    def test_empty_text_is_guide(self, text: str) -> None:
        """空输入不炸、不猜，一律兜底 GUIDE。"""
        assert detect_mode(text=text) == MODE_GUIDE


# ==================================================================== 冷却回切
class TestCooldown:
    """PRD P1-1：连续 2 轮同一非 GUIDE mode → 第 3 轮强制回 GUIDE。"""

    def test_cooldown_forces_guide_on_third_round(self) -> None:
        """★ 前两轮都是 DEMO，第 3 轮再说「我不会」→ 强制拉回 GUIDE。"""
        history = _history("我不会", "我还是不会做")
        result = detect(text="我不会", history=history)

        assert result.mode == MODE_GUIDE
        assert result.source == "cooldown"
        assert MODE_DEMO in result.matched, "冷却日志应记下被回切的原 mode，便于排障"

    def test_cooldown_also_applies_to_hint(self) -> None:
        """冷却不是 DEMO 专属：连续 2 轮 HINT 同样回切。"""
        history = _history("给点提示", "再给个提示")
        assert detect_mode(text="提示一下", history=history) == MODE_GUIDE

    def test_one_round_is_not_enough_to_cool_down(self) -> None:
        """只有 1 轮同 mode 时**不能**回切——否则 DEMO 永远讲不完一次。"""
        history = _history("我不会")
        assert detect_mode(text="我不会", history=history) == MODE_DEMO

    def test_different_modes_do_not_accumulate(self) -> None:
        """前两轮 mode 不同（HINT + DEMO）→ 不构成「连续同一 mode」，不回切。"""
        history = _history("给点提示", "我不会")
        assert detect_mode(text="我不会", history=history) == MODE_DEMO

    def test_guide_round_breaks_the_streak(self) -> None:
        """中间插了一轮普通对话就断连，冷却计数清零。"""
        history = _history("我不会", "懂了一点")
        assert detect_mode(text="我不会做", history=history) == MODE_DEMO

    def test_assistant_messages_are_not_counted(self) -> None:
        """冷却只数**学生原话**，老师的回复里出现「我不会」不算数。"""
        history = [
            {"role": "user", "content": "我不会"},
            {"role": "assistant", "content": "别急，我们一起看看。你不会的是哪一步？"},
        ]
        assert detect_mode(text="我不会", history=history) == MODE_DEMO

    def test_cooldown_never_fires_for_guide(self) -> None:
        """本轮是 GUIDE 时冷却无意义，不该被触发（GUIDE 回切 GUIDE 是死循环）。"""
        history = _history("懂了", "嗯嗯")
        result = detect(text="然后呢", history=history)
        assert result.mode == MODE_GUIDE
        assert result.source == "default"

    def test_cooldown_rounds_constant_matches_behavior(self) -> None:
        """常量与行为一致：恰好 ``COOLDOWN_ROUNDS`` 轮触发，少一轮不触发。"""
        streak = ["我不会"] * COOLDOWN_ROUNDS
        assert detect_mode(text="我不会", history=_history(*streak)) == MODE_GUIDE
        if COOLDOWN_ROUNDS > 1:
            short = ["我不会"] * (COOLDOWN_ROUNDS - 1)
            assert detect_mode(text="我不会", history=_history(*short)) == MODE_DEMO


# ==================================================================== 健壮性
class TestRobustness:
    """脏输入不许把识别打挂——它在请求主链路上，抛异常就是 500。"""

    @pytest.mark.parametrize(
        "history",
        [
            None,
            [],
            ["不是字典"],
            [{"role": "user"}],
            [{"role": None, "content": None}],
            [{"content": "没有 role"}],
            [{"role": "user", "content": ""}],
        ],
    )
    def test_dirty_history_is_tolerated(self, history: Any) -> None:
        """脏历史直接跳过脏元素，识别结果仍然合法。"""
        assert detect_mode(text="我不会", history=history) in MODE_VALUES

    def test_state_param_is_accepted_but_not_decisive(self) -> None:
        """``state`` 入参保留但当前不参与判定（签名向后兼容用）。"""
        for state in (None, "intro", "guiding", "practicing", "summarizing", "closed"):
            assert detect_mode(text="我不会", state=state) == MODE_DEMO

    def test_detect_result_is_always_legal(self) -> None:
        """任意输入下 ``DetectResult`` 的四个字段都必须合法可用。"""
        for text in ["我不会", "对不对", "给点提示", "懂了", "", "!!!???"]:
            result = detect(text=text)
            assert result.mode in MODE_VALUES, text
            assert 0.0 <= result.confidence <= 1.0, text
            assert result.source in {"override", "regex", "cooldown", "default"}, text
            assert isinstance(result.matched, str), text

    def test_detect_mode_equals_detect_dot_mode(self) -> None:
        """``detect_mode`` 只是 ``detect`` 的收敛版，两者不许漂移。"""
        for text in ["我不会", "我算出来是 12", "给点提示", "懂了"]:
            assert detect_mode(text=text) == detect(text=text).mode

    def test_mode_label_falls_back_to_guide(self) -> None:
        """展示标签：未知 mode 一律回落「引导中」，前端不硬编码中文。"""
        assert intent.mode_label(MODE_DEMO) == "讲解中"
        assert intent.mode_label("不存在") == intent.mode_label(MODE_GUIDE)
        assert intent.mode_label(None) == "引导中"


# ==================================================================== G1 准确率
#: G1 验收语料：20 条真实口吻的学生原话 → 期望 mode（设计文档 §5 T10）
G1_SAMPLES = (
    ("我不会，你给我讲一遍", MODE_DEMO),
    ("我不知道，你把这道题给我解释下吧", MODE_DEMO),
    ("这题我不会做", MODE_DEMO),
    ("你直接告诉我吧", MODE_DEMO),
    ("教我怎么做", MODE_DEMO),
    ("看演示", MODE_DEMO),
    ("我想看完整过程", MODE_DEMO),
    ("我算出来是 42", MODE_JUDGE),
    ("我算出来是 12，对不对", MODE_JUDGE),
    ("答案是 B", MODE_JUDGE),
    ("我做完了", MODE_JUDGE),
    ("帮我检查一下", MODE_JUDGE),
    ("我这样做对不对", MODE_JUDGE),
    ("给点提示", MODE_HINT),
    ("给个思路", MODE_HINT),
    ("第一步是什么", MODE_HINT),
    ("我卡住了", MODE_HINT),
    ("懂了", MODE_GUIDE),
    ("我先把 3 移到右边", MODE_GUIDE),
    ("不会吧", MODE_GUIDE),
)


def test_g1_intent_accuracy_meets_90_percent() -> None:
    """G1 业务目标：20 条抽样语料的意图识别准确率 ≥ 90%（设计文档 §5 T10）。

    低于 90% 时的正确动作是**补正则**，不是改架构（§8.2 A4）。
    """
    assert len(G1_SAMPLES) == 20, "G1 口径要求恰好 20 条抽样"

    misses = [
        (text, expected, detect_mode(text=text))
        for text, expected in G1_SAMPLES
        if detect_mode(text=text) != expected
    ]
    accuracy = (len(G1_SAMPLES) - len(misses)) / len(G1_SAMPLES)

    assert accuracy >= 0.9, f"G1 准确率 {accuracy:.0%} < 90%，未命中：{misses}"

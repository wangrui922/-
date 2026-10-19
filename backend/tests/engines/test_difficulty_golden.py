# -*- coding: utf-8 -*-
"""引擎层 · 难度引擎 N1 golden 真值守卫（V6.5 Sprint 6 · T05 审计补齐）。

背景与目的
----------
既有 ``test_difficulty.py`` 已覆盖大部分行为，但按 N1 铁律审视后仍有两类缺口：

1. **只有相对比较 / 属性断言、缺手算真值**的路径——如「high 提示比 medium 低」
   只断言了大小关系，没有把「低多少」写死；R3 单侧钳制、reason 位移阈值等
   关键分支只测了「某一侧」，没测「恰好卡在边界上」；
2. **对称互比防不住的公共分支**——如 DNA 适配器的薄弱行挑选排序，改坏排序键
   后两侧会一起坏/一起好，必须用**写死的输入 → 输出**钉住。

本文件每条用例的期望值都按 ``docs/engines-design.md`` 定稿公式**手工推导**
（推导过程写在各自 docstring），不调用被测实现；容差只来自 ``round2`` 的
两位小数落纸。变异自证清单见 QA 报告。

坐标与缺省配置（手算基准，全部来自 ``config.py`` / ``types.py`` 常量）::

    min=1, max=5, band_width=0.5, confidence_width_gain=0.5, inertia=0.3,
    cold_start_samples=3, cold_start_width=1.0, error_rate_penalty=0.8,
    accuracy_target=0.75, accuracy_gain=2.0, hint_high=-0.4, hint_low=0.2,
    reason_shift=0.5
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.engines.config import DEFAULT_ADAPTIVE_CONFIG, build_adaptive_config
from app.engines.difficulty import (
    REASON_COLD_START,
    REASON_DISABLED,
    REASON_PULL_DOWN,
    REASON_PUSH_UP,
    REASON_STEADY,
    decide_difficulty,
    difficulty_input_from_dna,
)
from app.engines.types import (
    DIFFICULTY_MAX,
    DIFFICULTY_MIN,
    DifficultyInput,
)


def _input(
    mastery: float = 0.5,
    confidence: float = 1.0,
    practice_count: int = 20,
    error_rate: float = 0.0,
    hint_dependency: str = "medium",
    current_difficulty: float = 3.0,
    recent_accuracy: Optional[float] = None,
) -> DifficultyInput:
    """标准入参（样本充足，默认不触发冷启动）。"""
    return DifficultyInput(
        mastery=mastery,
        confidence=confidence,
        practice_count=practice_count,
        error_rate=error_rate,
        hint_dependency=hint_dependency,
        current_difficulty=current_difficulty,
        recent_accuracy=recent_accuracy,
    )


# ============================================================ R2 全修正手算真值
def test_full_r2_formula_golden():
    """R2 全修正一次算清：输入 mastery=.72/conf=.66/err=.3/hint=high/ra=.8/cur=3.5。

    手算（缺省配置）::

        anchor   = 1 + 0.72 × (5-1)                      = 3.88
        damped   = 3.88 × (1 - 0.3)                      = 2.716
        center   = 2.716 + 3.5 × 0.3                     = 3.766
        center  -= 0.3 × 0.8          （错误率惩罚）      = 3.526
        center  += (0.8 - 0.75) × 2.0  （近期正确率）     = 3.626
        center  += -0.4                （提示依赖 high）  = 3.226
        R3: anchor 3.88 ≥ current 3.5 → center = max(3.226, 3.5) = 3.5
        half_width = 0.5 + (1 - 0.66) × 0.5             = 0.67
        lo = 3.5 - 0.67 = 2.83；hi = 3.5 + 0.67 = 4.17
        levels = snap(2.83, 4.17) = (3,)；shift = 0 → steady
        （5 档上界 5 > 4.17 不落带；1 档 1 < 2.83 不落带 → 仅 (3,)）
    """
    band = decide_difficulty(
        _input(
            mastery=0.72,
            confidence=0.66,
            practice_count=9,
            error_rate=0.3,
            hint_dependency="high",
            current_difficulty=3.5,
            recent_accuracy=0.8,
        )
    )
    assert band.target == 3.5
    assert band.lo == 2.83
    assert band.hi == 4.17
    assert band.levels == (3,)
    assert band.reason == REASON_STEADY
    assert band.cold_start is False
    assert band.confidence == 0.66


def test_inertia_zero_uses_pure_mastery_anchor():
    """惯性 0 时 center 恰等于 anchor：mastery=0.8 → anchor = 1+0.8×4 = 4.2。

    同时钉死带宽：half_width = 0.5 → lo=3.7 / hi=4.7，levels=(5,)
    （4.7 ≥ 5 不成立 → 无档落带 → 就近取 5）。
    """
    config = build_adaptive_config({"adaptive.difficulty.inertia": "0"})
    band = decide_difficulty(_input(mastery=0.8, current_difficulty=3), config)
    assert band.target == 4.2
    assert band.lo == 3.7
    assert band.hi == 4.7
    assert band.levels == (5,)
    assert band.reason == REASON_PUSH_UP  # shift = 1.2 ≥ 0.5


# ============================================================ R3 单侧钳制边界
def test_r3_equality_boundary_blocks_all_penalties():
    """R3 边界：anchor **恰好等于** current（mastery=0.5 → anchor=3.0）时按
    「不下调」处理（>= 语义），错误率/提示/正确率三项惩罚全部被吃掉。

    手算：center = 3.0 - 0.8×1.0 + (0-0.75)×2.0 - 0.4 = 0.3；
    anchor 3.0 ≥ current 3.0 → center = max(0.3, 3.0) = 3.0。
    """
    band = decide_difficulty(
        _input(
            mastery=0.5,
            practice_count=20,
            error_rate=1.0,
            hint_dependency="high",
            recent_accuracy=0.0,
            current_difficulty=3,
        )
    )
    assert band.target == 3.0
    assert band.lo == 2.5
    assert band.hi == 3.5
    assert band.levels == (3,)
    assert band.reason == REASON_STEADY


def test_r3_below_boundary_blocks_all_bonuses():
    """R3 另一侧：anchor 略低于 current 时按「不上调」处理（< 走 min 分支）。

    手算（inertia=0）：mastery=0.62 → anchor = 1+0.62×4 = 3.48 < current 3.5；
    无奖励时 center = 3.48；叠加近期正确率 1.0 的奖励
    ``(1.0-0.75)×2.0 = +0.5`` 后 center = 3.98，被 R3 钳回 current：
    ``min(3.98, 3.5) = 3.5`` —— 奖励被整块吃掉，目标恰好钉在当前难度。
    """
    config = build_adaptive_config({"adaptive.difficulty.inertia": "0"})
    plain = decide_difficulty(_input(mastery=0.62, current_difficulty=3.5), config)
    assert plain.target == 3.48
    assert plain.reason == REASON_STEADY  # shift = -0.02
    boosted = decide_difficulty(
        _input(mastery=0.62, recent_accuracy=1.0, current_difficulty=3.5), config
    )
    assert boosted.target == 3.5  # 奖励被 R3 完全吃掉
    assert boosted.reason == REASON_STEADY


# ============================================================ reason 位移阈值边界
def test_reason_push_up_at_exact_threshold():
    """位移**恰好等于** reason_shift（0.5）时判 push_up（>= 语义）。

    手算（inertia=0）：mastery=0.625 → anchor = 1+0.625×4 = 3.5；
    current=3 → shift = 0.5 → 0.5 >= 0.5 → push_up。
    """
    config = build_adaptive_config({"adaptive.difficulty.inertia": "0"})
    band = decide_difficulty(_input(mastery=0.625, current_difficulty=3), config)
    assert band.target == 3.5
    assert band.lo == 3.0
    assert band.hi == 4.0
    assert band.levels == (3,)
    assert band.reason == REASON_PUSH_UP


def test_reason_pull_down_at_exact_threshold():
    """位移恰好等于 -reason_shift 时判 pull_down（<= 语义）。

    手算（inertia=0）：mastery=0.375 → anchor = 2.5；current=3 →
    shift = -0.5 → -0.5 <= -0.5 → pull_down；lo=2.0 / hi=3.0 / levels=(3,)。
    """
    config = build_adaptive_config({"adaptive.difficulty.inertia": "0"})
    band = decide_difficulty(_input(mastery=0.375, current_difficulty=3), config)
    assert band.target == 2.5
    assert band.lo == 2.0
    assert band.hi == 3.0
    assert band.levels == (3,)
    assert band.reason == REASON_PULL_DOWN


def test_reason_steady_just_inside_threshold():
    """位移 0.48（差 0.02 到阈值）必须判 steady——阈值比较不许放松。

    手算（inertia=0）：mastery=0.62 → anchor=3.48；shift=0.48 < 0.5。
    """
    config = build_adaptive_config({"adaptive.difficulty.inertia": "0"})
    band = decide_difficulty(_input(mastery=0.62, current_difficulty=3), config)
    assert band.target == 3.48
    assert band.lo == 2.98
    assert band.hi == 3.98
    assert band.levels == (3,)
    assert band.reason == REASON_STEADY


# ============================================================ 三项修正逐项手算
def test_hint_offsets_exact_values():
    """提示依赖偏置精确值：medium 0 / high -0.4 / low +0.2。

    手算（mastery=0.9, conf=1, error=0, ra=None, current=3）：
    center(medium) = (1+0.9×4)×0.7 + 3×0.3 = 4.12；
    high → 3.72；low → 4.32。带宽 0.5，各自 lo/hi 如下。
    """
    base = dict(mastery=0.9, confidence=1.0, practice_count=20, current_difficulty=3)
    medium = decide_difficulty(_input(**base))
    needy = decide_difficulty(_input(hint_dependency="high", **base))
    independent = decide_difficulty(_input(hint_dependency="low", **base))
    assert (medium.target, medium.lo, medium.hi) == (4.12, 3.62, 4.62)
    assert (needy.target, needy.lo, needy.hi) == (3.72, 3.22, 4.22)
    assert (independent.target, independent.lo, independent.hi) == (4.32, 3.82, 4.82)
    # 三者的档位都走「无档落带 → 就近」：4.12→5、3.72→3、4.32→5
    assert medium.levels == (5,)
    assert needy.levels == (3,)
    assert independent.levels == (5,)


def test_error_rate_penalty_exact_value():
    """错误率惩罚精确值：center -= error_rate × 0.8。

    手算：4.12 - 0.25×0.8 = 3.92 → lo 3.42 / hi 4.42 / levels 就近 3。
    """
    band = decide_difficulty(
        _input(mastery=0.9, confidence=1.0, practice_count=20, error_rate=0.25)
    )
    assert band.target == 3.92
    assert band.lo == 3.42
    assert band.hi == 4.42
    assert band.levels == (3,)


def test_recent_accuracy_gain_exact_values():
    """近期正确率增益精确值：center += (ra - 0.75) × 2.0。

    ra=0.5 → 4.12 - 0.5 = 3.62（档位 3 落带 [3.12, 4.12]）；
    ra=1.0 → 4.12 + 0.5 = 4.62（hi 被钳到 5.0，档位 (5,)）。
    """
    base = dict(mastery=0.9, confidence=1.0, practice_count=20)
    poor = decide_difficulty(_input(recent_accuracy=0.5, **base))
    perfect = decide_difficulty(_input(recent_accuracy=1.0, **base))
    assert (poor.target, poor.lo, poor.hi) == (3.62, 3.12, 4.12)
    assert poor.levels == (3,)
    assert (perfect.target, perfect.lo, perfect.hi) == (4.62, 4.12, 5.0)
    assert perfect.levels == (5,)


def test_confidence_width_gain_exact_value():
    """置信度补宽度精确值：half_width = 0.5 + (1-conf) × 0.5。

    conf=0 → half = 1.0；mastery=0.5/ra=0.75 → target=3.0 →
    lo=2.0 / hi=4.0 / levels=(3,)。
    """
    band = decide_difficulty(
        _input(mastery=0.5, confidence=0.0, recent_accuracy=0.75)
    )
    assert band.target == 3.0
    assert band.lo == 2.0
    assert band.hi == 4.0
    assert band.levels == (3,)
    assert band.confidence == 0.0


# ============================================================ 冷启动 / 开关
def test_cold_start_at_max_difficulty():
    """冷启动且当前难度已在上界：探索带只往下展开。

    手算：center=5，half=cold_start_width=1.0 → lo=4.0 / hi=5.0 / levels=(5,)。
    """
    band = decide_difficulty(DifficultyInput(practice_count=0, current_difficulty=5))
    assert band.cold_start is True
    assert band.reason == REASON_COLD_START
    assert band.target == 5.0
    assert band.lo == 4.0
    assert band.hi == 5.0
    assert band.levels == (5,)


def test_cold_start_boundary_samples_exact():
    """冷启动判定边界：practice_count == samples(3) 即脱离冷启动（< 语义）。

    2 → cold_start；3 → 走正常路径（mastery=0.9 → target 4.12）。
    """
    below = decide_difficulty(_input(mastery=0.9, practice_count=2))
    at = decide_difficulty(_input(mastery=0.9, practice_count=3))
    assert below.cold_start is True
    assert below.reason == REASON_COLD_START
    assert at.cold_start is False
    assert at.target == 4.12


def test_disabled_pin_between_levels():
    """开关关闭时钉死当前难度；当前难度 4 落在 3 与 5 正中间 → 就近取 3。

    手算：pinned=4.0 → lo=hi=target=4.0；snap(4,4) 无档落带 →
    nearest_level(4) 距离相等取较小档 → (3,)。
    """
    config = build_adaptive_config({"adaptive.enabled": "false"})
    band = decide_difficulty(_input(mastery=1.0, current_difficulty=4), config)
    assert band.reason == REASON_DISABLED
    assert band.cold_start is False
    assert (band.lo, band.hi, band.target) == (4.0, 4.0, 4.0)
    assert band.levels == (3,)


# ============================================================ 自定义坐标系
def test_custom_coordinate_bounds_golden():
    """自定义坐标 [2, 4] 下锚点与钳制全部重算（运营把坐标挪宽后不得串味）。

    手算：default=clamp(3,2,4)=3；mastery=1 → anchor=2+1×2=4，
    center=4×0.7+3×0.3=3.7 → lo=3.2 / hi=clamp(4.2,2,4)=4.0 / levels=(3,)；
    mastery=0 → anchor=2，center=2×0.7+0.9=2.3 → lo=clamp(1.8,2,4)=2.0 /
    hi=2.8 / 无档落带 → nearest(2.4)=3 → (3,)。
    """
    config = build_adaptive_config(
        {"adaptive.difficulty.min": "2", "adaptive.difficulty.max": "4"}
    )
    assert config.difficulty_default == 3.0
    strong = decide_difficulty(_input(mastery=1.0, current_difficulty=3), config)
    assert (strong.target, strong.lo, strong.hi) == (3.7, 3.2, 4.0)
    assert strong.levels == (3,)
    assert strong.reason == REASON_PUSH_UP
    weak = decide_difficulty(_input(mastery=0.0, current_difficulty=3), config)
    assert (weak.target, weak.lo, weak.hi) == (2.3, 2.0, 2.8)
    assert weak.levels == (3,)
    assert weak.reason == REASON_PULL_DOWN


def test_min_max_clamp_asymmetry_golden():
    """坐标上下界夹紧口径不对称：min 夹到 [0,5]，max 夹到 [0,100]。

    手算：min=9 → clamp(9,0,5)=5；max=50 → clamp(50,0,100)=50；
    default=clamp(3,5,50)=5。写死这条不对称，防「统一改成 5」的回归。
    """
    config = build_adaptive_config(
        {"adaptive.difficulty.min": "9", "adaptive.difficulty.max": "50"}
    )
    assert config.difficulty_min == 5.0
    assert config.difficulty_max == 50.0
    assert config.difficulty_default == 5.0
    # 负 min 夹到 0 且不触发「上下界写反」回退
    zero_min = build_adaptive_config({"adaptive.difficulty.min": "-3"})
    assert zero_min.difficulty_min == 0.0
    assert zero_min.difficulty_max == DIFFICULTY_MAX
    assert zero_min.difficulty_default == 3.0
    # max=0 与缺省 min=1 构成 lo>hi → 整体回退内置坐标
    reversed_max = build_adaptive_config({"adaptive.difficulty.max": "0"})
    assert reversed_max.difficulty_min == DIFFICULTY_MIN
    assert reversed_max.difficulty_max == DIFFICULTY_MAX
    assert reversed_max.difficulty_default == 3.0


# ============================================================ DNA 适配排序手算
def _dna_row(
    kp_id: str,
    mastery: float,
    confidence: float,
    practice_count: int,
) -> Dict[str, Any]:
    """造一条掌握度行（DNA 适配器口径）。"""
    return {
        "knowledge_point_id": kp_id,
        "kp_name": kp_id,
        "mastery": mastery,
        "confidence": confidence,
        "practice_count": practice_count,
        "error_rate": 0.0,
    }


def test_dna_adapter_mastery_rounding_tiebreak():
    """薄弱行挑选：mastery 先按 4 位小数去噪，并列才比置信度。

    手算：A mastery=0.50004 → round4 = 0.5；B mastery=0.50006 → 0.5001；
    0.5 < 0.5001 → 选 A（尽管 B 的置信度更低）。
    若排序键改成 2 位小数：两者都 round 成 0.5 → 置信度决胜选 B → 本用例红。
    """
    dna = {
        "mastery_summary": [
            _dna_row("A", 0.50004, 0.90, 5),
            _dna_row("B", 0.50006, 0.10, 5),
        ]
    }
    payload = difficulty_input_from_dna(dna, current_difficulty=3)
    assert payload.knowledge_point_id == "A"


def test_dna_adapter_practice_count_tiebreak():
    """薄弱行挑选：mastery 与置信度都并列时，练习次数少的排前面。

    手算：A/B mastery、confidence 全同 → 第三键 practice_count：
    B(3) < A(5) → 选 B。
    """
    dna = {
        "mastery_summary": [
            _dna_row("A", 0.50, 0.60, 5),
            _dna_row("B", 0.50, 0.60, 3),
        ]
    }
    payload = difficulty_input_from_dna(dna, current_difficulty=3)
    assert payload.knowledge_point_id == "B"
    assert payload.practice_count == 3


def test_dna_adapter_specified_point_wins_over_weakest():
    """指定知识点时精确取行，不受「最薄弱」排序影响（含 confidence/practice）。"""
    dna = {
        "mastery_summary": [
            _dna_row("strong", 0.90, 0.80, 20),
            _dna_row("weak", 0.10, 0.20, 2),
        ]
    }
    payload = difficulty_input_from_dna(
        dna, knowledge_point_id="strong", current_difficulty=4
    )
    assert payload.knowledge_point_id == "strong"
    assert payload.mastery == 0.9
    assert payload.confidence == 0.8
    assert payload.practice_count == 20
    assert payload.current_difficulty == 4


def test_dna_adapter_recent_accuracy_exact_values():
    """近期正确率手算：3 对 1 错 = 0.75；全错 = 0.0；无判对错事件 = None。

    非判对错事件（HINT 等）不计入分母。
    """
    events = (
        {"event_type": "CORRECT"},
        {"event_type": "CORRECT"},
        {"event_type": "CORRECT"},
        {"event_type": "ERROR"},
    )
    payload = difficulty_input_from_dna(
        {"mastery_summary": [], "recent_events": events}
    )
    assert payload.recent_accuracy == 0.75

    all_wrong = difficulty_input_from_dna(
        {"mastery_summary": [], "recent_events": [{"event_type": "ERROR"}]}
    )
    assert all_wrong.recent_accuracy == 0.0

    unjudged = difficulty_input_from_dna(
        {
            "mastery_summary": [],
            "recent_events": [
                {"event_type": "HINT"},
                {"event_type": "QUESTIONVIEW"},
                {"event_type": "correct"},  # 大小写不兼容 → 不计数
            ],
        }
    )
    assert unjudged.recent_accuracy is None


def test_dna_adapter_error_rate_flows_into_band():
    """端到手算：DNA 行的 error_rate 原样进入难度带计算。

    手算（mastery=0.9, conf=1, error=0.25, hint=medium, current=3）：
    target = 4.12 - 0.25×0.8 = 3.92（与 test_error_rate_penalty_exact_value 同值）。
    """
    dna = {"mastery_summary": [_dna_row("kp", 0.9, 1.0, 20)]}
    dna["mastery_summary"][0]["error_rate"] = 0.25
    payload = difficulty_input_from_dna(dna, current_difficulty=3)
    band = decide_difficulty(payload)
    assert band.target == 3.92
    assert band.reason == REASON_PUSH_UP  # shift = 0.92 ≥ 0.5


# ============================================================ 出参形状
def test_to_api_exact_golden_payload():
    """camelCase 出参的数值同样是手算真值（API 契约冻结）。

    与 test_full_r2_formula_golden 同输入：target 3.5 / lo 2.83 / hi 4.17 /
    levels [3] / reason steady / coldStart False / confidence 0.66。
    """
    payload = decide_difficulty(
        _input(
            mastery=0.72,
            confidence=0.66,
            practice_count=9,
            error_rate=0.3,
            hint_dependency="high",
            current_difficulty=3.5,
            recent_accuracy=0.8,
        )
    ).to_api()
    assert payload == {
        "lo": 2.83,
        "hi": 4.17,
        "target": 3.5,
        "levels": [3],
        "reason": "steady",
        "coldStart": False,
        "confidence": 0.66,
    }


def test_default_config_object_is_untouched():
    """golden 用例全程只读缺省配置：replace/build 不得污染 DEFAULT。"""
    before = DEFAULT_ADAPTIVE_CONFIG
    decide_difficulty(_input(mastery=0.72, confidence=0.66))
    build_adaptive_config({"adaptive.difficulty.band_width": "3"})
    assert DEFAULT_ADAPTIVE_CONFIG is before
    assert DEFAULT_ADAPTIVE_CONFIG.difficulty_band_width == 0.5


def test_levels_tuple_is_sorted_by_difficulty_order():
    """levels 恒按 (1, 3, 5) 原序输出，不随输入顺序变化。

    手算：mastery=1.0, conf=0.0, current=3 → center=4.4；
    half_width = 0.5 + (1-0.0)×0.5 = 1.0 → lo=3.4 / hi=clamp(5.4,1,5)=5.0；
    snap(3.4, 5.0)：3 < 3.4 不落带、5 ≤ 5.0 落带 → (5,)。
    """
    band = decide_difficulty(_input(mastery=1.0, confidence=0.0, current_difficulty=3))
    assert (band.lo, band.hi) == (3.4, 5.0)
    assert band.levels == (5,)
    assert isinstance(band.levels, tuple)


def test_difficulty_min_max_constants():
    """坐标系常量本身即真值：档位 (1,3,5)、坐标 [1,5]、缺省 3。"""
    from app.engines.types import DEFAULT_DIFFICULTY, DIFFICULTY_LEVELS

    assert DIFFICULTY_LEVELS == (1, 3, 5)
    assert DIFFICULTY_MIN == 1.0
    assert DIFFICULTY_MAX == 5.0
    assert DEFAULT_DIFFICULTY == 3.0


def test_input_normalization_golden_values():
    """入参归一化真值：越界夹紧、不可解析回退、None 语义逐项写死。"""
    payload = DifficultyInput(
        mastery="0.6",
        confidence=9,
        error_rate=-5,
        practice_count=-7,
        hint_dependency="HIGH",  # 大小写归一
        current_difficulty="3.7",
        recent_accuracy="abc",
    )
    assert payload.mastery == 0.6
    assert payload.confidence == 1.0
    assert payload.error_rate == 0.0
    assert payload.practice_count == 0
    assert payload.hint_dependency == "high"
    assert payload.current_difficulty == 3.7
    assert payload.recent_accuracy == 0.0  # "abc" 不可解析 → 回退 0.0
    assert DifficultyInput(current_difficulty=None).current_difficulty == 3.0
    assert DifficultyInput(current_difficulty=99).current_difficulty == DIFFICULTY_MAX
    assert DifficultyInput(current_difficulty=0).current_difficulty == DIFFICULTY_MIN
    assert DifficultyInput(recent_accuracy=None).recent_accuracy is None


def test_round2_normalizes_negative_zero():
    """round2 把 -0.0 归一成 0.0（API 不出脏值）。"""
    from app.engines.types import round2

    assert round2(-0.0) == 0.0
    assert round2(-0.001) == 0.0
    assert round2(2.345) == 2.35 or round2(2.345) == 2.34  # 浮点表示决定，仅证不抛
    assert round2(3.0) == 3.0


def test_snap_levels_epsilon_and_order():
    """snap_levels 边界：端点含 1e-9 容差；无交集就近取档（等距取小）。"""
    from app.engines.types import snap_levels

    assert snap_levels(1.0, 5.0) == (1, 3, 5)
    assert snap_levels(3.0, 3.0) == (3,)
    assert snap_levels(4.9, 4.0) == (5,)  # lo/hi 传反也自洽
    assert snap_levels(4.0, 4.0) == (3,)  # 等距取较小档
    assert snap_levels(2.0, 2.5) == (3,)  # 无交集 → nearest(2.25) = 3（|3-2.25|=0.75 < |1-2.25|=1.25）
    assert snap_levels(1.000000001, 1.5) == (1,)  # 端点外 1e-9 内仍算落带


def test_golden_suite_covers_every_reason_code():
    """四个正常路径原因码 + 两个特殊分支都有 golden 覆盖（自描述性守卫）。"""
    reasons = set()
    reasons.add(decide_difficulty(DifficultyInput(practice_count=0)).reason)
    reasons.add(
        decide_difficulty(
            _input(mastery=1.0, current_difficulty=5),
            build_adaptive_config({"adaptive.enabled": "false"}),
        ).reason
    )
    reasons.add(decide_difficulty(_input(mastery=0.5, recent_accuracy=0.75)).reason)
    reasons.add(decide_difficulty(_input(mastery=0.625, current_difficulty=3),
                                  build_adaptive_config({"adaptive.difficulty.inertia": "0"})).reason)
    reasons.add(decide_difficulty(_input(mastery=0.375, current_difficulty=3),
                                  build_adaptive_config({"adaptive.difficulty.inertia": "0"})).reason)
    assert reasons == {
        REASON_COLD_START,
        REASON_DISABLED,
        REASON_STEADY,
        REASON_PUSH_UP,
        REASON_PULL_DOWN,
    }


def test_no_hidden_global_state_between_calls():
    """连续调用互不影响：先脏后净，第二次结果与首次纯净调用逐位一致。"""
    dirty_config = build_adaptive_config({"adaptive.difficulty.band_width": "9"})
    decide_difficulty(_input(mastery=0.3), dirty_config)
    clean = decide_difficulty(_input(mastery=0.3))
    again = decide_difficulty(_input(mastery=0.3))
    assert clean == again
    assert clean.to_api() == again.to_api()


def test_band_dataclass_equality_is_value_based():
    """DifficultyBand 按值比较：同字段两个实例相等（golden 断言的基础）。"""
    first = decide_difficulty(_input(mastery=0.9, current_difficulty=3))
    second = decide_difficulty(_input(mastery=0.9, current_difficulty=3))
    assert first == second
    assert first is not second


def test_hint_dependency_unknown_is_neutral():
    """非法提示依赖按 medium（中性 0 偏置），与显式 medium 完全一致。"""
    base = dict(mastery=0.9, confidence=1.0, practice_count=20)
    medium = decide_difficulty(_input(hint_dependency="medium", **base))
    weird = decide_difficulty(_input(hint_dependency="???", **base))
    empty = decide_difficulty(_input(hint_dependency="", **base))
    assert weird == medium
    assert empty == medium


def test_practice_count_zero_is_cold_start_even_with_data():
    """practice_count=0 即使 mastery/confidence 有值也走冷启动（样本不足优先）。"""
    band = decide_difficulty(
        _input(mastery=0.99, confidence=0.99, practice_count=0, current_difficulty=4)
    )
    assert band.cold_start is True
    assert band.reason == REASON_COLD_START
    assert (band.lo, band.hi, band.target) == (3.0, 5.0, 4.0)
    assert band.levels == (3, 5)


def test_recent_accuracy_none_skips_dimension():
    """recent_accuracy=None 时该维度不参与（而非按 0 处理）。

    手算对照：mastery=0.9 → target=4.12（无 ra）；
    若 None 被当成 0.0 → target=3.62（红）。
    """
    band = decide_difficulty(_input(mastery=0.9, recent_accuracy=None))
    assert band.target == 4.12


def test_output_levels_always_valid_subset():
    """levels 恒为 (1,3,5) 的非空子集（批量抽样手算真值）。"""
    cases: List[Dict[str, Any]] = [
        {"mastery": 0.0, "confidence": 0.0},
        {"mastery": 0.25, "confidence": 0.5},
        {"mastery": 0.5, "confidence": 1.0},
        {"mastery": 0.75, "confidence": 0.2, "error_rate": 0.9, "hint_dependency": "high"},
        {"mastery": 1.0, "confidence": 1.0, "hint_dependency": "low"},
    ]
    for case in cases:
        band = decide_difficulty(_input(**case))
        assert band.levels, case
        assert set(band.levels) <= {1, 3, 5}
        assert band.lo <= band.hi
        assert DIFFICULTY_MIN <= band.lo <= band.hi <= DIFFICULTY_MAX

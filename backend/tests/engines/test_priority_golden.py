# -*- coding: utf-8 -*-
"""引擎层 · 优先级引擎 N1 golden 真值守卫（V6.5 Sprint 6 · T06 审计补齐）。

背景与目的
----------
既有 ``test_priority.py`` 的排序规则多用「A 在 B 前」「得分更高」这类**相对断言**，
N1 铁律要求关键路径配**手算 golden 真值**（输入 → 写死输出数值）。本文件补齐：

1. 四项权重 BASE 的完整手算（mastery/confidence/gap/degree 同时非零）；
2. 时间两因子（stale 加法 / recent 乘法）的**正交性**与窗口、陈旧阈值的精确边界；
3. 同分三元决胜（mastery → level → ID 字典序）的写死顺序；
4. 缺省常量与 ``CONFIG_SPECS`` 缺省值的结构一致性（防两套口径漂移）。

公式（定稿，见 ``docs/engines-design.md``）::

    BASE   = (1-mastery)·w_m + (1-confidence)·w_c + gap_ratio·w_g + degree_ratio·w_d
    raw    = (BASE + STALE) × RECENT × SATURATION + BOOST
    score  = round(clamp(raw, 0, 1), 4)

缺省权重 mastery 0.40 / confidence 0.25 / gap 0.20 / degree 0.15；
gap_divisor=3、out_degree_divisor=5、recent_window=7、stale_after=14。
所有期望值按分数 exact 运算推导（``Fraction``），再按 IEEE754 双精度核签；
不调用被测实现。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.engines.config import CONFIG_KEYS, DEFAULT_ADAPTIVE_CONFIG
from app.engines.priority import (
    PRIORITY_GAP_DIVISOR,
    PRIORITY_OUTDEG_DIVISOR,
    PRIORITY_RECENT_WINDOW,
    PRIORITY_STALE_AFTER,
    PRIORITY_TOP_N_DEFAULT,
    PRIORITY_WEIGHTS_DEFAULT,
    rank_priorities,
)
from app.engines.types import PriorityItem

#: 固定时间基准（引擎不读系统时钟）
_NOW: datetime = datetime(2026, 3, 1, 12, 0, 0)


def _item(
    kp_id: str,
    mastery: Any = 0.5,
    *,
    confidence: Any = 0.5,
    level: Any = 1,
    days_ago: Optional[int] = None,
    name: str = "",
) -> Dict[str, Any]:
    """构造掌握度条目（dict 形态）。"""
    row: Dict[str, Any] = {
        "knowledge_point_id": kp_id,
        "kp_name": name or kp_id,
        "mastery": mastery,
        "confidence": confidence,
        "level": level,
    }
    if days_ago is not None:
        row["last_practiced_at"] = (_NOW - timedelta(days=days_ago)).isoformat()
    return row


def _score_of(
    items: List[Dict[str, Any]],
    results: Any = None,
    **kwargs: Any,
) -> float:
    """取排序后首个知识点的得分（单点场景即该点得分）。"""
    return rank_priorities(items, results=results, now=_NOW, **kwargs)[0].score


def _only_mastery_config(**overrides: Any):
    """只保留 mastery 权重，隔离时间因子便于逐项手算。"""
    weights = {"mastery": 1.0, "confidence": 0.0, "gap": 0.0, "degree": 0.0}
    return DEFAULT_ADAPTIVE_CONFIG.replace(priority_weights=weights, **overrides)


# ============================================================ BASE 四项手算
def test_full_base_four_terms_golden():
    """四项权重同时非零时的完整 BASE 手算真值。

    手算（mastery=0.4, confidence=0.6, gap=2 条, degree=4, 无时间/results）::

        (1-0.4)×0.40 = 0.24
        (1-0.6)×0.25 = 0.10
        (2/3)×0.20    = 0.133333…
        (4/5)×0.15    = 0.12
        BASE          = 0.593333… → round4 = 0.5933
    """
    gaps = [{"pre_kp_id": "a"}, {"pre_kp_id": "a"}]
    result = rank_priorities(
        [_item("a", 0.4, confidence=0.6)],
        gaps,
        {"a": 4},
        adaptive_config=DEFAULT_ADAPTIVE_CONFIG,
    )
    assert result[0].score == 0.5933


def test_base_with_defaults_and_no_signals():
    """无任何附加信号（无缺口/无出度/无时间）时 BASE 只剩前两项。

    手算：mastery=0.5, confidence=0.5 →
    (1-0.5)×0.40 + (1-0.5)×0.25 = 0.2 + 0.125 = 0.325。
    """
    assert _score_of([_item("a", 0.5, confidence=0.5)]) == 0.325


def test_gap_and_degree_cap_at_divisors():
    """缺口/出度超过除数后归一封顶（gap_divisor=3, outdeg_divisor=5）。

    手算：gap=5 → min(5,3)/3 = 1.0；degree=9 → min(9,5)/5 = 1.0；
    BASE = (1-0.5)×0.40 + (1-0.5)×0.25 + 1.0×0.20 + 1.0×0.15 = 0.675。
    """
    gaps = [{"pre_kp_id": "a"}] * 5
    result = rank_priorities(
        [_item("a", 0.5, confidence=0.5)], gaps, {"a": 9}
    )
    assert result[0].score == 0.675


def test_missing_confidence_defaults_to_low_line_golden():
    """置信度缺失按低置信度线 0.5 处理（保守认为结论不可靠）。

    手算：mastery=0.5, confidence 缺失 → 0.5 →
    BASE = 0.2 + (1-0.5)×0.25 = 0.325。
    """
    result = rank_priorities([{"knowledge_point_id": "a", "mastery": 0.5}])
    assert result[0].score == 0.325


# ============================================================ 时间因子正交性
def test_recent_decay_window_boundaries_golden():
    """近期降权窗口边界手算：days=7 恰在窗口内但系数已回到 1.0。

    手算（mastery-only, base=0.5, window=7）::

        days=0 → recent = (0+1)/(7+1) = 1/8  → 0.0625
        days=6 → recent = 7/8                → 0.4375
        days=7 → recent = 8/8 = 1.0          → 0.5（窗口最后一天不再降权）
        days=8 → 窗口外，recent = 1.0        → 0.5
    """
    config = _only_mastery_config()
    scores = {
        days: _score_of([_item("a", 0.5, days_ago=days)], adaptive_config=config)
        for days in (0, 6, 7, 8)
    }
    assert scores == {0: 0.0625, 6: 0.4375, 7: 0.5, 8: 0.5}


def test_recent_window_last_day_still_counts_as_recent():
    """窗口最后一天（days=7）仍判「近期练过」（<= 语义）：分数与文案双真值。

    手算（mastery-only 配置，mastery=0.5）：BASE = (1-0.5)×1.0 = 0.5；
    recent = (7+1)/(7+1) = 1.0 → score 0.5（不再降权）；
    但 ``is_recent`` 仍为 True → 文案走「近期已经练过」分支
    （mastery 0.5 不满足 < 0.5 的低掌握度分支）。
    若边界被改成 ``<``：days=7 不再 recent，分数虽仍为 0.5，
    文案却退化成兜底句——只有这条文案 golden 抓得住（变异自证见报告）。
    """
    config = _only_mastery_config()
    result = rank_priorities(
        [_item("a", 0.5, days_ago=7, name="方程")], adaptive_config=config, now=_NOW
    )[0]
    assert result.score == 0.5
    assert result.reason == "「方程」近期已经练过，适当错峰后再复习。"


def test_stale_bonus_exact_values_golden():
    """陈旧加分精确值：days=14 不加分（> 语义），超出按 (days-14)/14 线性加。

    手算（mastery-only, base=0.5, stale_after=14）::

        days=14 → stale = 0        → 0.5
        days=15 → stale = 1/14     → 0.5714
        days=20 → stale = 6/14     → 0.9286
        days=28 → stale = 1.0      → raw 1.5 → clamp → 1.0

    days=14 还同时钉死**文案**：恰在阈值上不得误报「很久没练」
    （若 is_stale 被改成 >=，分数虽不变，文案会红——变异自证 P-M2）。
    """
    config = _only_mastery_config()
    at_threshold = rank_priorities(
        [_item("a", 0.5, days_ago=14, name="方程")], adaptive_config=config, now=_NOW
    )[0]
    assert at_threshold.score == 0.5
    assert at_threshold.reason == "「方程」掌握还不稳，建议安排一轮针对性练习。"

    scores = {
        days: _score_of([_item("a", 0.5, days_ago=days)], adaptive_config=config)
        for days in (14, 15, 20, 28)
    }
    assert scores == {14: 0.5, 15: 0.5714, 20: 0.9286, 28: 1.0}
    stale_reason = rank_priorities(
        [_item("a", 0.5, days_ago=15, name="方程")], adaptive_config=config, now=_NOW
    )[0]
    assert "很久没练" in stale_reason.reason


def test_stale_and_recent_are_orthogonal_golden():
    """stale（加法）与 recent（乘法）正交：同一 days 两种配置互不串味。

    手算（base=0.5, days=15）::

        配置A（window=20, stale_after=10）：stale = 5/10 = 0.5，recent = 1.0
            （is_stale → 不判近期）→ raw = (0.5+0.5)×1.0 = 1.0
        配置B（window=20, stale_after=30）：stale = 0，recent = 16/21
            → raw = 0.5×16/21 = 0.380952… → 0.381
    """
    both = _only_mastery_config(priority_recent_window=20, priority_stale_after=10)
    recent_only = _only_mastery_config(priority_recent_window=20, priority_stale_after=30)
    items = [_item("a", 0.5, days_ago=15)]
    assert _score_of(items, adaptive_config=both) == 1.0
    assert _score_of(items, adaptive_config=recent_only) == 0.381


def test_no_time_dimension_without_now():
    """不传 now 时时间维度整体跳过（stale=0, recent=1），不读系统时钟。

    手算：days_ago=30 的行在不传 now 时得分 = 裸 BASE = 0.5。
    """
    items = [_item("a", 0.5, days_ago=30)]
    without_now = rank_priorities(items, adaptive_config=_only_mastery_config())
    assert without_now[0].score == 0.5
    assert "很久没练" not in without_now[0].reason
    with_now = rank_priorities(
        items, adaptive_config=_only_mastery_config(), now=_NOW
    )
    assert with_now[0].score == 1.0  # (0.5 + 16/14) → clamp


# ============================================================ 同分三元决胜
def test_tie_breaker_three_levels_golden():
    """同分决胜链写死：mastery 升序 → level 升序 → ID 字典序。

    构造（零权重 → 全部 score=0）：b(0.7, level2)、a(0.3, level2)、
    c(0.3, level1) → 排序键 (-0, mastery, level, id)：
    c(0.3,1,c) < a(0.3,2,a) < b(0.7,2,b) → [c, a, b]。
    """
    zero_weights = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 0.0, "degree": 0.0}
    )
    items = [
        _item("b", 0.7, level=2),
        _item("a", 0.3, level=2),
        _item("c", 0.3, level=1),
    ]
    result = rank_priorities(items, adaptive_config=zero_weights)
    assert [item.knowledge_point_id for item in result] == ["c", "a", "b"]
    assert all(item.score == 0.0 for item in result)


def test_tie_breaker_mastery_beats_id_golden():
    """同分时掌握度决胜优先于 ID：weak(0.2) 必须排在 strong(0.8) 前。"""
    zero_weights = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 0.0, "degree": 0.0}
    )
    result = rank_priorities(
        [_item("aaa", 0.8), _item("zzz", 0.2)], adaptive_config=zero_weights
    )
    assert [item.knowledge_point_id for item in result] == ["zzz", "aaa"]


# ============================================================ 去重 / top_n / 脏值
def test_duplicate_keeps_first_occurrence_golden():
    """重复知识点保留**首次出现**的那一行（分数按首行计算）。

    手算：same 首行 mastery=0.2 → BASE=(1-0.2)×0.40+(1-0.5)×0.25=0.445；
    第二行 0.8 被忽略。other mastery=0.5 → 0.325。排序 [same, other]。
    """
    result = rank_priorities(
        [_item("same", 0.2), _item("same", 0.8), _item("other", 0.5)]
    )
    by_id = {item.knowledge_point_id: item for item in result}
    assert [item.knowledge_point_id for item in result] == ["same", "other"]
    assert by_id["same"].score == 0.445
    assert by_id["same"].mastery == 0.2
    assert by_id["other"].score == 0.325


def test_top_n_truncates_after_full_ranking():
    """top_n 在**完整排序之后**截断（不是先截断再排序）。

    手算：8 个点 mastery 0.9→0.2，得分 0.04+0.125=0.165 起递减；
    top_n=3 取前三 = mastery 最低的三个 [kp2, kp1, kp0]。
    """
    items = [_item(f"kp{index}", 0.9 - index * 0.1) for index in range(8)]
    result = rank_priorities(items, top_n=3)
    assert [item.knowledge_point_id for item in result] == ["kp7", "kp6", "kp5"]
    assert result[0].score == round((1 - 0.2) * 0.4 + (1 - 0.5) * 0.25, 4)


def test_dirty_mastery_falls_back_to_zero_golden():
    """mastery 不可解析按 0.0（最薄弱）处理，得分取最大值一端。

    手算：mastery="bad" → 0.0 → BASE = 1×0.40 + (1-0.5)×0.25 = 0.525。
    """
    result = rank_priorities([_item("a", "bad", confidence=0.5)])
    assert result[0].mastery == 0.0
    assert result[0].score == 0.525


def test_level_clamped_to_zero_three_golden():
    """level 越界夹到 [0,3]：-9 → 0，99 → 3。"""
    result = rank_priorities(
        [_item("low", 0.5, level=-9), _item("high", 0.5, level=99)]
    )
    by_id = {item.knowledge_point_id: item for item in result}
    assert by_id["low"].level == 0
    assert by_id["high"].level == 3


def test_graph_edge_sequence_degree_golden():
    """graph 传前置边序列：出度按 pre 出现次数计，得分手算一致。

    手算（degree-only 权重）：hub 出现 2 次 → 2/5 = 0.4 → score 0.4；
    leaf 出现 1 次 → 1/5 = 0.2 → score 0.2。
    """
    config = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 0.0, "degree": 1.0}
    )
    edges = [{"pre_kp_id": "hub"}, {"pre_kp_id": "hub"}, {"pre_kp_id": "leaf"}]
    result = rank_priorities(
        [_item("leaf", 0.5), _item("hub", 0.5)], graph=edges, adaptive_config=config
    )
    by_id = {item.knowledge_point_id: item for item in result}
    assert by_id["hub"].score == 0.4
    assert by_id["leaf"].score == 0.2
    assert [item.knowledge_point_id for item in result] == ["hub", "leaf"]


def test_graph_mapping_nested_form_golden():
    """graph 映射形态兼容嵌套 dict：``{kp: {"out_degree": n}}``。

    手算：a → 3/5 = 0.6；b 缺省 0 → 0.0。
    """
    config = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 0.0, "degree": 1.0}
    )
    result = rank_priorities(
        [_item("a", 0.5), _item("b", 0.5)],
        graph={"a": {"out_degree": 3}, "b": {}},
        adaptive_config=config,
    )
    by_id = {item.knowledge_point_id: item for item in result}
    assert by_id["a"].score == 0.6
    assert by_id["b"].score == 0.0


def test_object_items_and_alternate_keys_golden():
    """对象形态 + 兼容键名（kp_id/mastery_score/conf/name）走同一真值。

    手算：mastery=0.4, confidence=0.6 →
    BASE = (1-0.4)×0.40 + (1-0.6)×0.25 = 0.34。
    """

    class Row:
        kp_id = "obj"
        name = "对象行"
        mastery_score = 0.4
        conf = 0.6
        mastery_level = 2

    result = rank_priorities([Row()])
    assert result[0].knowledge_point_id == "obj"
    assert result[0].kp_name == "对象行"
    assert result[0].mastery == 0.4
    assert result[0].level == 2
    assert result[0].score == 0.34


def test_priority_item_frozen_and_value_equality():
    """PriorityItem 冻结且按值比较（golden 断言的基石）。"""
    item = rank_priorities([_item("a", 0.2)])[0]
    twin = PriorityItem(
        knowledge_point_id=item.knowledge_point_id,
        kp_name=item.kp_name,
        score=item.score,
        mastery=item.mastery,
        level=item.level,
        reason=item.reason,
    )
    assert item == twin
    try:
        item.score = 9.9  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - FrozenInstanceError
        return
    raise AssertionError("PriorityItem 应为冻结数据类")


def test_reason_texts_are_exact_golden():
    """五条理由文案逐字写死（前端直接展示，改字即回归）。"""
    cases = [
        (
            dict(mastery_items=[_item("a", 0.2, name="分数")], gaps=[{"pre_kp_id": "a"}]),
            "「分数」是未掌握的前置基础，建议先补牢再往后学。",
        ),
        (
            dict(mastery_items=[_item("a", 0.2, days_ago=30, name="分数")]),
            "「分数」很久没练了，先做几道把它捡回来。",
        ),
        (
            dict(mastery_items=[_item("a", 0.2, name="分数")]),
            "「分数」掌握度仅 20%，优先补齐薄弱基础。",
        ),
        (
            dict(mastery_items=[_item("a", 0.8, days_ago=1, name="分数")]),
            "「分数」近期已经练过，适当错峰后再复习。",
        ),
        (
            dict(mastery_items=[_item("a", 0.8, name="分数")]),
            "「分数」掌握还不稳，建议安排一轮针对性练习。",
        ),
    ]
    for kwargs, expected in cases:
        result = rank_priorities(now=_NOW, **kwargs)
        assert result[0].reason == expected, kwargs


def test_reason_mastery_percentage_rounds():
    """理由里的掌握度百分比按四舍五入取整：0.336 → 34%。"""
    result = rank_priorities([_item("a", 0.336, name="方程")])
    assert result[0].reason == "「方程」掌握度仅 34%，优先补齐薄弱基础。"


def test_config_defaults_match_engine_constants():
    """CONFIG_SPECS 缺省值与引擎常量同口径（31 键全量核对）。"""
    assert DEFAULT_ADAPTIVE_CONFIG.priority_weights == PRIORITY_WEIGHTS_DEFAULT
    assert DEFAULT_ADAPTIVE_CONFIG.priority_gap_divisor == PRIORITY_GAP_DIVISOR
    assert DEFAULT_ADAPTIVE_CONFIG.priority_out_degree_divisor == PRIORITY_OUTDEG_DIVISOR
    assert DEFAULT_ADAPTIVE_CONFIG.priority_recent_window == PRIORITY_RECENT_WINDOW
    assert DEFAULT_ADAPTIVE_CONFIG.priority_stale_after == PRIORITY_STALE_AFTER
    assert DEFAULT_ADAPTIVE_CONFIG.priority_top_n == PRIORITY_TOP_N_DEFAULT
    for key in (
        "adaptive.priority.weights",
        "adaptive.priority.gap_divisor",
        "adaptive.priority.out_degree_divisor",
        "adaptive.priority.recent_window",
        "adaptive.priority.stale_after",
        "adaptive.priority.top_n",
        "adaptive.priority.wrong_rate_floor",
        "adaptive.priority.wrong_boost",
        "adaptive.priority.wrong_saturation",
        "adaptive.priority.recent_attempts_cap",
        "adaptive.priority.saturation_penalty",
    ):
        assert key in CONFIG_KEYS, key


def test_divisor_protection_keeps_scores_sane():
    """除数保护两种口径都写死：gap_divisor 脏值回退缺省 3；
    out_degree_divisor 走 ``max(1, …)`` 地板（-5 → 1，不是回退 5）。

    手算：degree-only 下 hub 出度 5 → min(5,1)/1 = 1.0 → score 1.0；
    gap-only 下 a 有 1 条缺口 → min(1,3)/3 = 1/3 → 0.3333。
    """
    from app.engines.config import build_adaptive_config

    config = build_adaptive_config(
        {"adaptive.priority.gap_divisor": "0", "adaptive.priority.out_degree_divisor": "-5"}
    )
    degree_only = config.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 0.0, "degree": 1.0}
    )
    gap_only = config.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 1.0, "degree": 0.0}
    )
    hub = rank_priorities(
        [_item("hub", 0.5)], graph={"hub": 5}, adaptive_config=degree_only
    )
    assert hub[0].score == 1.0
    gapped = rank_priorities(
        [_item("a", 0.5)], [{"pre_kp_id": "a"}], adaptive_config=gap_only
    )
    assert gapped[0].score == round(1 / 3, 4)


def test_results_list_form_matches_dict_form():
    """results 传序列形态与映射形态等价（同一手算真值 0.4083）。

    手算见 test_priority_results_golden.py 的 C1 用例：
    base=0.325 + boost 0.25×(1/3) = 0.4083。
    """
    items = [_item("a", 0.5, days_ago=1)]
    as_dict = rank_priorities(
        items, results={"a": {"recent_attempts": 2, "recent_correct": 1}}, now=_NOW
    )
    as_list = rank_priorities(
        items,
        results=[
            {
                "knowledge_point_id": "a",
                "recent_attempts": 2,
                "recent_correct": 1,
            }
        ],
        now=_NOW,
    )
    assert as_dict[0].score == as_list[0].score == 0.4083

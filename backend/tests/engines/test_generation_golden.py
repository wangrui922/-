# -*- coding: utf-8 -*-
"""引擎层 · 出题编排引擎 N1 golden 真值守卫（V6.5 Sprint 6 · T06 审计补齐）。

背景与目的
----------
既有 ``test_generation.py`` 覆盖了配额/兜底/策略的**行为方向**，但 N1 铁律要求
把关键路径的数值写死。本文件补齐：

1. 最大余数法配额的**逐项手算**（含小数余数并列时沿队列顺序分配的决胜）；
2. ``_strategy`` 的 ``round(total × ratio)`` 银行家舍入边界（2.5→2、1.5→2、0.5→0）；
3. ``plan_units`` 覆盖后总和不符 → 回退引擎摊派的**复合路径**真值；
4. 难度引擎 → 出题引擎的链式 golden（档位原样传导）。

最大余数法（``_allocate``）复述::

    每项先保底 1 题；剩余题量按 score 比例取 raw_extras；
    floor 后剩余 ``leftovers`` 题按「小数部分降序 → 队列顺序」逐个 +1；
    总和恒等于 total。全零分时权重退化为等比。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.engines.config import DEFAULT_ADAPTIVE_CONFIG
from app.engines.difficulty import decide_difficulty
from app.engines.generation import (
    FALLBACK_KNOWLEDGE_POINT_ID,
    STRATEGY_DIAGNOSTIC_FALLBACK,
    plan_exercises,
)
from app.engines.types import DifficultyBand, DifficultyInput, ExercisePlan, PriorityItem


def _priority(kp_id: str, score: float = 1.0) -> PriorityItem:
    """构造优先级条目。"""
    return PriorityItem(
        knowledge_point_id=kp_id,
        kp_name=kp_id,
        score=score,
        mastery=0.3,
        level=1,
        reason="薄弱",
    )


def _band(*levels: int, target: float = 3.0) -> DifficultyBand:
    """构造难度带。"""
    return DifficultyBand(lo=1, hi=5, target=target, levels=tuple(levels))


def _counts(plans: List[ExercisePlan]) -> List[int]:
    """取各计划题量。"""
    return [plan.question_count for plan in plans]


# ============================================================ 最大余数法手算
def test_largest_remainder_allocation_golden():
    """最大余数法逐项手算（max_kp=4 放开候选数）：scores=[4,3,2,1], total=7 → [2,2,2,1]。

    手算::

        保底 [1,1,1,1]，remaining = 7-4 = 3，weight_sum = 10
        raw_extras = [3×4/10, 3×3/10, 3×2/10, 3×1/10] = [1.2, 0.9, 0.6, 0.3]
        floor      = [1, 0, 0, 0] → quotas [2,1,1,1]，leftovers = 3-1 = 2
        小数部分降序 → 队列顺序：0.9(b) > 0.6(c) > 0.3(d) > 0.2(a)
        前 2 名 +1 → [2, 2, 2, 1]，sum = 7 ✓

    对照组（缺省 max_kp=3）：4 个点只入选前 3 → [3, 2, 2]
    （remaining=4, weights [4,3,2] sum 9 → raw [1.777…, 1.333…, 0.888…]，
    floor [1,1,0] → [2,2,1]，leftovers=2 → 小数 c(0.888) > a(0.777) → [3,2,2]）。
    """
    config = DEFAULT_ADAPTIVE_CONFIG.replace(generation_max_knowledge_points=4)
    priorities = [_priority("a", 4), _priority("b", 3), _priority("c", 2), _priority("d", 1)]
    result = plan_exercises(priorities, [], total=7, config=config)
    assert _counts(result) == [2, 2, 2, 1]
    assert sum(_counts(result)) == 7
    assert [plan.knowledge_point_id for plan in result] == ["a", "b", "c", "d"]

    capped = plan_exercises(priorities, [], total=7)  # 缺省 max_kp=3
    assert [plan.knowledge_point_id for plan in capped] == ["a", "b", "c"]
    assert _counts(capped) == [3, 2, 2]


def test_remainder_tie_breaks_by_queue_order():
    """小数余数并列时沿**队列顺序**分配（不是 ID 字典序）。

    手算：scores=[1,1,1], total=5 → remaining=2，raw_extras 全为 2/3 ≈ 0.667，
    小数部分并列 → 按队列顺序取前 2 个 +1。
    队列 [a,b,c] → [2,2,1]；队列换成 [c,b,a] → 前两名是 c、b → [2,2,1]
    （若误按 ID 字典序，则 a、b 得加分 → 对 [c,b,a] 会给出 [1,2,2]，本用例红）。
    """
    forward = plan_exercises([_priority("a"), _priority("b"), _priority("c")], [], total=5)
    assert _counts(forward) == [2, 2, 1]
    reversed_queue = plan_exercises(
        [_priority("c"), _priority("b"), _priority("a")], [], total=5
    )
    assert [plan.knowledge_point_id for plan in reversed_queue] == ["c", "b", "a"]
    assert _counts(reversed_queue) == [2, 2, 1]


def test_allocation_with_extreme_score_gap():
    """分差极大时低分项仍保底 1 题、高分项吃掉剩余（零和守恒）。

    手算：scores=[100, 1], total=6 → remaining=4，weight_sum=101 →
    raw_extras = [4×100/101, 4×1/101] = [3.9604, 0.0396] →
    floor [3, 0] → quotas [4, 1]，leftovers = 1 → 小数部分 0.9604 > 0.0396 →
    a +1 → [5, 1]。
    """
    result = plan_exercises([_priority("a", 100), _priority("b", 1)], [], total=6)
    assert _counts(result) == [5, 1]


def test_candidate_cap_interaction_golden():
    """候选数 = min(队列长, max_kp, total)：三者同时收紧时只覆盖最前的点。

    手算：3 个点、max_kp=2、total=2 → candidate=2 → [a,b] 各 1 题；
    max_kp=3、total=2 → candidate 仍为 2（total 收紧）。
    """
    priorities = [_priority("a", 3), _priority("b", 2), _priority("c", 1)]
    capped = DEFAULT_ADAPTIVE_CONFIG.replace(generation_max_knowledge_points=2)
    result = plan_exercises(priorities, [], total=2, config=capped)
    assert [plan.knowledge_point_id for plan in result] == ["a", "b"]
    assert _counts(result) == [1, 1]
    # max_kp=3 但 total=2：仍是 2 个点各 1 题
    wide = plan_exercises(priorities, [], total=2)
    assert [plan.knowledge_point_id for plan in wide] == ["a", "b"]


# ============================================================ 策略舍入手算
def test_strategy_bankers_rounding_boundaries_golden():
    """策略判定的 round 舍入边界（银行家舍入，全部手算写死）。

    手算（ratio=0.5）：total=4 → new=2/review=2 → balanced；
    total=6 → 3/3 → balanced；total=5 → round(2.5)=2/review=3 → review_first；
    total=3 → round(1.5)=2/review=1 → new_first。
    ratio=0.25：total=2 → round(0.5)=0/review=2 → review_first。
    ratio=0.6：total=5 → round(3.0)=3/review=2 → new_first；
    total=1 → round(0.6)=1/review=0 → new_first。
    """
    half = DEFAULT_ADAPTIVE_CONFIG.replace(generation_new_ratio=0.5)
    assert plan_exercises([_priority("a")], [_band(3)], total=4, config=half)[0].strategy == "balanced"
    assert plan_exercises([_priority("a")], [_band(3)], total=6, config=half)[0].strategy == "balanced"
    assert plan_exercises([_priority("a")], [_band(3)], total=5, config=half)[0].strategy == "review_first"
    assert plan_exercises([_priority("a")], [_band(3)], total=3, config=half)[0].strategy == "new_first"

    quarter = DEFAULT_ADAPTIVE_CONFIG.replace(generation_new_ratio=0.25)
    assert plan_exercises([_priority("a")], [_band(3)], total=2, config=quarter)[0].strategy == "review_first"

    default_ratio = DEFAULT_ADAPTIVE_CONFIG
    assert plan_exercises([_priority("a")], [_band(3)], total=5, config=default_ratio)[0].strategy == "new_first"
    assert plan_exercises([_priority("a")], [_band(3)], total=1, config=default_ratio)[0].strategy == "new_first"


def test_strategy_ratio_clamped_before_rounding():
    """ratio 脏值先夹到 [0,1] 再舍入：9 → 1.0 → 全 new；-1 → 0.0 → 全 review。"""
    high = DEFAULT_ADAPTIVE_CONFIG.replace(generation_new_ratio=9.0)
    low = DEFAULT_ADAPTIVE_CONFIG.replace(generation_new_ratio=-1.0)
    assert high.generation_new_ratio == 1.0
    assert low.generation_new_ratio == 0.0
    assert plan_exercises([_priority("a")], [_band(3)], total=5, config=high)[0].strategy == "new_first"
    assert plan_exercises([_priority("a")], [_band(3)], total=5, config=low)[0].strategy == "review_first"


# ============================================================ plan_units 复合路径
def test_unit_counts_override_then_sum_mismatch_golden():
    """units 覆盖后总和 != safe_total → 回退引擎摊派（真值写死）。

    手算（scores=[9,1], units=[4,4], total=5）::

        摊派 _allocate(5,[a,b]) = [4,1]；units 覆盖 → [4,4]，sum=8 ≠ 5
        → 重新摊派 → [4,1]（最终真值）

    对照组（scores=[1,1], units=[5,1], total=5）::

        摊派 = [3,2]；覆盖 → [5,1]，sum=6 ≠ 5 → 重新摊派 → [3,2]
    """
    mismatched = plan_exercises(
        [_priority("a", 9), _priority("b", 1)],
        [_band(1), _band(5)],
        total=5,
        plan_units=[{"question_count": 4}, {"question_count": 4}],
    )
    assert _counts(mismatched) == [4, 1]

    flipped = plan_exercises(
        [_priority("a", 1), _priority("b", 1)],
        [],
        total=5,
        plan_units=[{"question_count": 5}, {"question_count": 1}],
    )
    assert _counts(flipped) == [3, 2]


def test_units_longer_than_selected_pairs_golden():
    """units 多于入选点位时多余单元被忽略（按索引对齐）。

    手算：3 个 units、total=2 → candidate=2 → 入选 a,b；
    units[0]=1、units[1]=1 覆盖摊派 [1,1] → sum=2 相符 → 保留；
    units[2]=9 被忽略（不对应任何计划）。
    ⚠️ 档位口径：``plan_units`` 非空时档位**一律以单元为准**
    （无 ``levels`` 的单元回退 ``(3,)``），难度带被整体旁路——
    两条 golden（(3,)/(3,) 而非带档 (1,)/(3,)）把这个语义钉死。
    """
    priorities = [_priority("a", 3), _priority("b", 2), _priority("c", 1)]
    result = plan_exercises(
        priorities,
        [_band(1), _band(3), _band(5)],
        total=2,
        plan_units=[{"question_count": 1}, {"question_count": 1}, {"question_count": 9}],
    )
    assert [plan.knowledge_point_id for plan in result] == ["a", "b"]
    assert _counts(result) == [1, 1]
    assert result[0].difficulty_levels == (3,)
    assert result[1].difficulty_levels == (3,)


def test_total_none_with_zero_units_uses_default():
    """total=None 且 units 题量全 0 → 回退配置默认题数 5。

    手算：fallback = sum([0]) = 0 → 取 generation_default_total = 5；
    单元题量 0 不覆盖摊派 → _allocate(5,[a]) = [5]。
    """
    config = DEFAULT_ADAPTIVE_CONFIG.replace(generation_default_total=5)
    result = plan_exercises(
        [_priority("a")], [_band(3)], total=None, plan_units=[{"question_count": 0}], config=config
    )
    assert _counts(result) == [5]


def test_fallback_plan_full_shape_golden():
    """空队列兜底计划的四字段全真值：count / levels / strategy / id。"""
    result = plan_exercises([], [], total=None)
    assert len(result) == 1
    plan = result[0]
    assert plan.knowledge_point_id == FALLBACK_KNOWLEDGE_POINT_ID
    assert plan.question_count == 5  # 默认题数
    assert plan.difficulty_levels == (1, 3, 5)  # DifficultyBand() 全档
    assert plan.strategy == STRATEGY_DIAGNOSTIC_FALLBACK
    assert plan.to_api() == {
        "knowledgePointId": "__mixed_review__",
        "questionCount": 5,
        "difficultyLevels": [1, 3, 5],
        "strategy": "diagnostic_fallback",
    }


def test_fallback_uses_first_valid_band():
    """空队列但带了有效难度带 → 兜底计划沿用该带档位（不是全档）。"""
    result = plan_exercises([], [_band(3, 5)], total=4)
    assert result[0].difficulty_levels == (3, 5)
    assert result[0].question_count == 4


# ============================================================ 难度档传导
def test_band_levels_flow_through_unchanged():
    """难度带档位原样传导进计划（引擎不改档、不补档）。

    手算（scores a=2, b=1, total=4）：remaining=2，raw_extras =
    [2×2/3, 2×1/3] = [1.333…, 0.666…] → floor [1,0] → [2,1]，
    leftovers=1 → 小数部分 b(0.666) > a(0.333) → b+1 → [2, 2]。
    """
    result = plan_exercises(
        [_priority("a", 2), _priority("b", 1)], [_band(1, 3), _band(3, 5)], total=4
    )
    assert result[0].difficulty_levels == (1, 3)
    assert result[1].difficulty_levels == (3, 5)
    assert _counts(result) == [2, 2]


def test_unit_levels_override_band_with_count():
    """units 同时给题量与档位 → 两者都覆盖难度带/摊派。"""
    result = plan_exercises(
        [_priority("a")],
        [_band(3)],
        total=3,
        plan_units=[{"question_count": 3, "levels": [1, 5]}],
    )
    assert result[0].question_count == 3
    assert result[0].difficulty_levels == (1, 5)
    assert result[0].strategy == "new_first"  # round(3×0.6)=2 > 1


def test_dirty_levels_fall_back_to_three_golden():
    """档位脏值（越界/缺失/非列表）一律回退 (3,)，逐形态写死。"""
    dirty_units: List[Optional[Dict[str, Any]]] = [
        None,
        {},
        {"levels": []},
        {"levels": [2, 4]},
        {"levels": "bad"},
        {"levels": [0, 2, 4, 6, "3"]},  # 只有 "3" 合法 → 过滤后 (3,)
    ]
    for unit in dirty_units[:5]:
        result = plan_exercises([_priority("a")], [], total=2, plan_units=[unit])
        assert result[0].difficulty_levels == (3,), unit
    filtered = plan_exercises([_priority("a")], [], total=2, plan_units=[dirty_units[5]])
    assert filtered[0].difficulty_levels == (3,)


def test_band_without_bands_arg_uses_default_band():
    """bands 缺省/短于队列 → 用 DifficultyBand() 全档兜底，不抛不空。"""
    result = plan_exercises([_priority("a"), _priority("b")], [], total=4)
    assert all(plan.difficulty_levels == (1, 3, 5) for plan in result)
    assert _counts(result) == [2, 2]


def test_chain_difficulty_to_generation_golden():
    """链式 golden：难度引擎产出 → 出题引擎消费，档位与策略逐位写死。

    手算：mastery=1.0, conf=1.0, current=3 → target=4.4, levels=(5,)
    （见 test_difficulty_golden 同参用例）；total=3 → round(3×0.6)=2 > 1 →
    new_first；单点配额 [3]。
    """
    band = decide_difficulty(
        DifficultyInput(mastery=1.0, confidence=1.0, practice_count=20, current_difficulty=3)
    )
    assert band.levels == (5,)
    plans = plan_exercises(
        [PriorityItem(knowledge_point_id="kp", kp_name="kp", score=0.8, mastery=0.9, level=3)],
        [band],
        total=3,
    )
    assert len(plans) == 1
    assert plans[0].knowledge_point_id == "kp"
    assert plans[0].question_count == 3
    assert plans[0].difficulty_levels == (5,)
    assert plans[0].strategy == "new_first"


def test_generation_config_defaults_golden():
    """generation_* 缺省值与引擎常量同口径（写死防漂移）。"""
    config = DEFAULT_ADAPTIVE_CONFIG
    assert config.generation_default_total == 5
    assert config.generation_max_total == 20
    assert config.generation_max_knowledge_points == 3
    assert config.generation_new_ratio == 0.6
    # 脏配置夹紧真值
    dirty = config.replace(generation_default_total=-1, generation_max_total=0, generation_max_knowledge_points=-7)
    assert dirty.generation_default_total == 1
    assert dirty.generation_max_total == 1
    assert dirty.generation_max_knowledge_points == 1


def test_max_total_clamps_request_golden():
    """超大题数被 max_total 夹紧：total=999, max=8 → 8（且每点至少 1 题）。

    手算（scores 6:3:1, total=8）：remaining=5 → raw_extras =
    [5×6/10, 5×3/10, 5×1/10] = [3.0, 1.5, 0.5] → floor [3,1,0] → [4,2,1]，
    leftovers = 5-4 = 1 → 小数部分 b(0.5) 与 c(0.5) 并列、a 为 0 →
    并列沿队列顺序取 b → [4, 3, 1]。
    """
    config = DEFAULT_ADAPTIVE_CONFIG.replace(generation_max_total=8)
    result = plan_exercises(
        [_priority("a", 6), _priority("b", 3), _priority("c", 1)], [], total=999, config=config
    )
    assert sum(_counts(result)) == 8
    assert all(count >= 1 for count in _counts(result))
    assert _counts(result) == [4, 3, 1]

# -*- coding: utf-8 -*-
"""引擎层 · 出题编排引擎单测（V6.5 Sprint 6 · T06）。

覆盖目标：
    1. 出参恒非空（队列为空走 ``__mixed_review__`` 综合摸底计划）；
    2. 题量配额：加权最大余数法、总和严格等于批次题数、每项至少一题；
    3. ``plan_units`` 题量优先、``total=None`` 取单元题量之和；
    4. 难度档：缺失/越界/脏值回退 ``(3,)``，档位恒非空且去重；
    5. 策略三段判定 ``new_first`` / ``review_first`` / ``balanced`` 与配置生效；
    6. 去重（同一知识点只保留最前一项）、脏入参兜底不抛异常；
    7. 出参 camelCase 键集合恒为 T05 冻结四键；
    8. 幂等性与纯函数铁律（AST 守卫）。
"""

from __future__ import annotations

import ast
import inspect
from typing import Any, Dict, List

from app.engines import generation as generation_module
from app.engines.config import DEFAULT_ADAPTIVE_CONFIG, build_adaptive_config
from app.engines.generation import (
    FALLBACK_KNOWLEDGE_POINT_ID,
    PLAN_NEW_RATIO_DEFAULT,
    plan_exercises,
)
from app.engines.types import DifficultyBand, ExercisePlan, PriorityItem


def _priority(
    kp_id: str,
    score: float = 1.0,
    *,
    mastery: float = 0.3,
    reason: str = "薄弱",
) -> PriorityItem:
    """构造优先级条目。"""
    return PriorityItem(
        knowledge_point_id=kp_id,
        kp_name=kp_id,
        score=score,
        mastery=mastery,
        level=1,
        reason=reason,
    )


def _band(*levels: int, target: float = 3.0) -> DifficultyBand:
    """构造难度带。"""
    return DifficultyBand(lo=1, hi=5, target=target, levels=tuple(levels))


def _counts(plans: List[ExercisePlan]) -> List[int]:
    """取各计划的题量。"""
    return [plan.question_count for plan in plans]


# ============================================================ 兜底：出参恒非空
def test_empty_queue_returns_non_empty_fallback():
    """空优先级队列仍返回综合摸底计划，不允许空出参。"""
    result = plan_exercises([], [], total=4)
    assert len(result) == 1
    assert result[0].knowledge_point_id == FALLBACK_KNOWLEDGE_POINT_ID
    assert result[0].question_count == 4
    assert result[0].strategy == "diagnostic_fallback"


def test_empty_queue_uses_supplied_band():
    """空队列但已有难度带时沿用该带的档位。"""
    result = plan_exercises([], [_band(3, 5)], total=2)
    assert result[0].difficulty_levels == (3, 5)


def test_empty_queue_without_band_uses_all_levels():
    """队列与难度带都为空时用 ``DifficultyBand()`` 的全档探索带。"""
    result = plan_exercises([], [], total=1)
    assert result[0].difficulty_levels == (1, 3, 5)


def test_none_sequences_do_not_raise():
    """运行时收到 None 也走非空兜底。"""
    result = plan_exercises(None, None, total=2)  # type: ignore[arg-type]
    assert result[0].question_count == 2


def test_invalid_priority_items_are_ignored():
    """None / 字符串 / 空 ID 不抛异常；全无效时走综合兜底。"""
    result = plan_exercises([None, "bad", PriorityItem()], [None], total=3)  # type: ignore[list-item]
    assert len(result) == 1
    assert result[0].knowledge_point_id == FALLBACK_KNOWLEDGE_POINT_ID


def test_plan_units_empty_returns_empty_list():
    """``plan_units`` 为空（且队列为空）时同样给出非空兜底，不返回空列表。"""
    result = plan_exercises([], [], total=3, plan_units=[])
    assert len(result) == 1
    assert result[0].question_count == 3


# ============================================================ 题量配额
def test_zero_total_uses_configured_default():
    """``total=0`` 由引擎自行摊派，使用配置默认题数。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(generation_default_total=7)
    result = plan_exercises([_priority("a")], [_band(3)], total=0, config=config)
    assert sum(_counts(result)) == 7


def test_none_total_with_units_uses_unit_counts_sum():
    """``total=None`` 时取 ``plan_units`` 各单元 ``question_count`` 之和。"""
    units = [{"question_count": 2}, {"question_count": 3}]
    result = plan_exercises(
        [_priority("a"), _priority("b")], [_band(1), _band(3)], total=None, plan_units=units
    )
    assert sum(_counts(result)) == 5


def test_none_total_without_units_uses_default():
    """``total=None`` 且无 ``plan_units`` 时用配置默认题数。"""
    result = plan_exercises([_priority("a")], [_band(3)], total=None)
    assert sum(_counts(result)) == DEFAULT_ADAPTIVE_CONFIG.generation_default_total


def test_negative_and_dirty_total_do_not_raise():
    """负数与不可解析题数均回退默认题数。"""
    negative = plan_exercises([_priority("a")], [], total=-8)
    dirty = plan_exercises([_priority("a")], [], total="bad")  # type: ignore[arg-type]
    assert sum(_counts(negative)) == 5
    assert sum(_counts(dirty)) == 5


def test_total_is_clamped_to_configured_maximum():
    """超大配额被 ``generation_max_total`` 夹紧。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(generation_max_total=8)
    result = plan_exercises([_priority("a")], [], total=999, config=config)
    assert sum(_counts(result)) == 8


def test_weighted_allocation_preserves_exact_total():
    """按优先级最大余数法分配，总和严格等于 total。"""
    priorities = [_priority("a", 6), _priority("b", 3), _priority("c", 1)]
    result = plan_exercises(priorities, [], total=10)
    assert _counts(result) == [5, 3, 2]
    assert sum(_counts(result)) == 10


def test_equal_scores_allocate_deterministically():
    """同分余数并列按队列顺序分配，保证可复现。"""
    result = plan_exercises([_priority("a"), _priority("b"), _priority("c")], [], total=5)
    assert _counts(result) == [2, 2, 1]


def test_all_zero_scores_fall_back_to_equal_weights():
    """全零分不会除零，退化成等比分配。"""
    result = plan_exercises([_priority("a", 0), _priority("b", 0)], [], total=5)
    assert _counts(result) == [3, 2]


def test_total_smaller_than_queue_keeps_top_points():
    """题量少于知识点数时只覆盖最靠前的点，每点至少一题。"""
    result = plan_exercises([_priority("a"), _priority("b"), _priority("c")], [], total=2)
    assert [plan.knowledge_point_id for plan in result] == ["a", "b"]
    assert _counts(result) == [1, 1]


def test_max_knowledge_points_config_applies():
    """每批最多覆盖的知识点数量可配置。"""
    priorities = [_priority("a"), _priority("b"), _priority("c")]
    config = DEFAULT_ADAPTIVE_CONFIG.replace(generation_max_knowledge_points=2)
    result = plan_exercises(priorities, [], total=9, config=config)
    assert len(result) == 2
    assert sum(_counts(result)) == 9


def test_plan_units_counts_override_allocation():
    """``plan_units`` 给出的题量优先于引擎摊派。"""
    units = [{"question_count": 4}, {"question_count": 1}]
    result = plan_exercises(
        [_priority("a", 9), _priority("b", 1)], [_band(1), _band(5)], total=5, plan_units=units
    )
    assert _counts(result) == [4, 1]


def test_plan_units_dirty_counts_fall_back_to_allocation():
    """单元题量缺失 / 为 0 / 脏值时回退引擎摊派，且不抛异常。"""
    units = [{"question_count": "bad"}, {"count": 3}]
    result = plan_exercises([_priority("a", 1), _priority("b", 1)], [], total=5, plan_units=units)
    assert sum(_counts(result)) == 5


# ============================================================ 难度档
def test_missing_band_uses_default_band():
    """``bands`` 短于 ``priorities`` 时缺失项使用全档默认带。"""
    result = plan_exercises([_priority("a"), _priority("b")], [_band(5)], total=4)
    assert result[0].difficulty_levels == (5,)
    assert result[1].difficulty_levels == (1, 3, 5)


def test_extra_bands_are_ignored():
    """多余难度带不产生幽灵计划。"""
    result = plan_exercises([_priority("a")], [_band(1), _band(3), _band(5)], total=3)
    assert len(result) == 1
    assert result[0].difficulty_levels == (1,)


def test_levels_fallback_to_three_when_missing_or_dirty():
    """``levels`` 取不到（缺失 / 越界 / 脏值）时回退 ``(3,)``。"""
    dirty_cases: List[Any] = [None, {}, {"levels": []}, {"levels": [2, 4]}, {"levels": "bad"}]
    for unit in dirty_cases:
        result = plan_exercises([_priority("a")], [], total=2, plan_units=[unit])
        assert result[0].difficulty_levels == (3,)


def test_plan_units_levels_override_band():
    """单元自带合法档位时覆盖难度带。"""
    units = [{"question_count": 2, "levels": [1, 5]}]
    result = plan_exercises([_priority("a")], [_band(3)], total=2, plan_units=units)
    assert result[0].difficulty_levels == (1, 5)


def test_difficulty_levels_are_deduplicated():
    """同一难度档重复出现只保留一次。"""
    result = plan_exercises([_priority("a")], [DifficultyBand(levels=(1, 1, 3, 3))], total=2)
    assert result[0].difficulty_levels == (1, 3)


def test_question_count_and_levels_are_always_non_empty():
    """每条计划至少一题、至少一个难度档。"""
    result = plan_exercises([_priority("a"), _priority("b")], [], total=2)
    assert all(plan.question_count >= 1 for plan in result)
    assert all(plan.difficulty_levels for plan in result)


# ============================================================ 策略三段判定
def test_strategy_new_first_when_new_ratio_high():
    """新题占比高 → ``new_first``。"""
    result = plan_exercises([_priority("a")], [_band(3)], total=5)
    assert result[0].strategy == "new_first"


def test_strategy_review_first_when_new_ratio_low():
    """新题占比低 → ``review_first``。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(generation_new_ratio=0.2)
    result = plan_exercises([_priority("a")], [_band(3)], total=5, config=config)
    assert result[0].strategy == "review_first"


def test_strategy_balanced_when_counts_equal():
    """新题与复习题相等 → ``balanced``。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(generation_new_ratio=0.5)
    result = plan_exercises([_priority("a")], [_band(3)], total=4, config=config)
    assert result[0].strategy == "balanced"


def test_new_ratio_default_and_clamped():
    """缺省新题占比为 0.6；脏值被夹到 ``[0, 1]``。"""
    assert PLAN_NEW_RATIO_DEFAULT == 0.6
    assert DEFAULT_ADAPTIVE_CONFIG.generation_new_ratio == 0.6
    high = build_adaptive_config({"adaptive.generation.new_ratio": "9"})
    low = build_adaptive_config({"adaptive.generation.new_ratio": "-1"})
    assert high.generation_new_ratio == 1.0
    assert low.generation_new_ratio == 0.0


def test_strategy_is_consistent_across_units():
    """同一批次内所有计划共用同一个批次策略。"""
    result = plan_exercises(
        [_priority("a", 2), _priority("b", 1)], [_band(1), _band(5)], total=6
    )
    assert {plan.strategy for plan in result} == {"new_first"}


# ============================================================ 去重 / 出参 / 幂等
def test_duplicate_knowledge_points_are_removed():
    """同一知识点只保留排名最前的一项，去重必须生效。"""
    priorities = [_priority("same", 9), _priority("same", 1), _priority("other", 1)]
    result = plan_exercises(priorities, [_band(5), _band(1), _band(3)], total=4)
    assert [plan.knowledge_point_id for plan in result] == ["same", "other"]
    assert len({plan.knowledge_point_id for plan in result}) == len(result)
    assert result[0].difficulty_levels == (5,)
    assert result[1].difficulty_levels == (3,)


def test_plan_api_keys_are_frozen_four_camel_case():
    """``to_api()`` 键集合恒为 T05 冻结四键，不追加任何键。"""
    payload = plan_exercises([_priority("a")], [_band(3)], total=2)[0].to_api()
    assert set(payload) == {
        "knowledgePointId",
        "questionCount",
        "difficultyLevels",
        "strategy",
    }
    assert isinstance(payload["difficultyLevels"], list)


def test_same_input_is_idempotent():
    """同输入重复调用得到完全相同的冻结数据类与 API 输出。"""
    priorities = [_priority("a", 2), _priority("b", 1)]
    bands = [_band(1, 3), _band(3, 5)]
    first = plan_exercises(priorities, bands, total=7)
    second = plan_exercises(priorities, bands, total=7)
    assert first == second
    assert [plan.to_api() for plan in first] == [plan.to_api() for plan in second]


def test_exercise_plan_is_frozen():
    """出题计划不可被调用方改脏。"""
    plan: ExercisePlan = plan_exercises([_priority("a")], [], total=1)[0]
    try:
        plan.question_count = 99  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - FrozenInstanceError
        return
    raise AssertionError("ExercisePlan 应为冻结数据类")


def test_build_config_reads_generation_keys():
    """generation 配置经过 CONFIG_SPECS 解码（完整键与短键均可）。"""
    config = build_adaptive_config(
        {
            "adaptive.generation.default_total": "6",
            "generation.max_total": "12",
            "adaptive.generation.max_knowledge_points": "4",
        }
    )
    assert config.generation_default_total == 6
    assert config.generation_max_total == 12
    assert config.generation_max_knowledge_points == 4


def test_dirty_generation_config_is_clamped():
    """负整数至少回到 1。"""
    config = build_adaptive_config(
        {
            "adaptive.generation.default_total": "-1",
            "adaptive.generation.max_total": "0",
            "adaptive.generation.max_knowledge_points": "-7",
        }
    )
    assert config.generation_default_total == 1
    assert config.generation_max_total == 1
    assert config.generation_max_knowledge_points == 1


def test_generation_module_has_no_forbidden_imports():
    """AST 守卫：出题引擎顶层禁止 ORM / models / os。"""
    tree = ast.parse(inspect.getsource(generation_module))
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    for name in names:
        assert not name.startswith("sqlalchemy")
        assert not name.startswith("app.models")
        assert name != "os"


def test_generation_module_never_touches_question_ids():
    """引擎不接收/返回题目 ID：签名里不得出现题目级选取的入参名。"""
    import inspect as _inspect

    signature = _inspect.signature(generation_module.plan_exercises)
    for forbidden in ("candidates", "exclude", "question_ids"):
        assert forbidden not in signature.parameters, forbidden
    tree = ast.parse(inspect.getsource(generation_module))
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    for name in names:
        assert not name.startswith("app.services"), name

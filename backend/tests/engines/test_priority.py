# -*- coding: utf-8 -*-
"""引擎层 · 优先级引擎单测（V6.5 Sprint 6 · T06）。

覆盖目标：
    1. 排序规则逐条生效（掌握度 / 置信度 / 前置缺口 / 图谱出度 / 陈旧 / 近期）；
    2. 缺省常量与配置口径一致，权重支持 dict 与 JSON 字符串两种载体；
    3. 阈值配置生效（除数、窗口、陈旧阈值、top_n）与除零保护；
    4. 脏入参（None / 越界 / 负数 / 脏字符串 / 非法 JSON）全不抛异常；
    5. 空输入返回 ``[]``、去重生效、幂等可复现；
    6. 出参 camelCase 键集合稳定；
    7. 纯函数铁律（AST 守卫：顶层不得 import ORM / models / os）。
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.engines import priority as priority_module
from app.engines.config import CONFIG_KEYS, DEFAULT_ADAPTIVE_CONFIG, build_adaptive_config
from app.engines.priority import (
    PRIORITY_GAP_DIVISOR,
    PRIORITY_LOW_CONFIDENCE,
    PRIORITY_LOW_MASTERY,
    PRIORITY_OUTDEG_DIVISOR,
    PRIORITY_RECENT_WINDOW,
    PRIORITY_STALE_AFTER,
    PRIORITY_TOP_N_DEFAULT,
    PRIORITY_WEIGHTS_DEFAULT,
    rank_priorities,
)
from app.engines.types import PriorityItem

#: 固定时间基准（引擎不读系统时间，全部由 ``now`` 注入）
_NOW: datetime = datetime(2026, 3, 1, 12, 0, 0)


def _item(
    kp_id: str,
    mastery: Any = 0.5,
    *,
    name: str = "",
    confidence: Any = 0.5,
    level: Any = 1,
    days_ago: Optional[int] = None,
) -> Dict[str, Any]:
    """构造一条掌握度条目（dict 形态）。"""
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


def _ids(items: List[PriorityItem]) -> List[str]:
    """取排好序的知识点 ID 列表。"""
    return [item.knowledge_point_id for item in items]


def _only_mastery_config(**overrides: Any):
    """只保留 mastery 权重，隔离其它维度便于逐条验证规则。"""
    weights = {"mastery": 1.0, "confidence": 0.0, "gap": 0.0, "degree": 0.0}
    return DEFAULT_ADAPTIVE_CONFIG.replace(priority_weights=weights, **overrides)


# ============================================================ 缺省常量与配置口径
def test_default_constants_match_spec():
    """缺省常量取值与定稿一致。"""
    assert PRIORITY_WEIGHTS_DEFAULT == {
        "mastery": 0.40,
        "confidence": 0.25,
        "gap": 0.20,
        "degree": 0.15,
    }
    assert PRIORITY_GAP_DIVISOR == 3.0
    assert PRIORITY_OUTDEG_DIVISOR == 5
    assert PRIORITY_RECENT_WINDOW == 7
    assert PRIORITY_STALE_AFTER == 14
    assert PRIORITY_LOW_MASTERY == 0.5
    assert PRIORITY_LOW_CONFIDENCE == 0.5
    assert PRIORITY_TOP_N_DEFAULT == 5


def test_config_default_matches_engine_constant():
    """``AdaptiveConfig.priority_weights`` 缺省值与引擎常量同口径（避免两套漂移）。"""
    assert DEFAULT_ADAPTIVE_CONFIG.priority_weights == PRIORITY_WEIGHTS_DEFAULT
    assert DEFAULT_ADAPTIVE_CONFIG.priority_gap_divisor == PRIORITY_GAP_DIVISOR
    assert DEFAULT_ADAPTIVE_CONFIG.priority_out_degree_divisor == PRIORITY_OUTDEG_DIVISOR
    assert DEFAULT_ADAPTIVE_CONFIG.priority_recent_window == PRIORITY_RECENT_WINDOW
    assert DEFAULT_ADAPTIVE_CONFIG.priority_stale_after == PRIORITY_STALE_AFTER
    assert DEFAULT_ADAPTIVE_CONFIG.priority_top_n == PRIORITY_TOP_N_DEFAULT


def test_weights_accept_dict_and_json_string():
    """权重既接受 dict，也接受 JSON 字符串；缺 key 回退缺省。"""
    as_dict = build_adaptive_config({"adaptive.priority.weights": {"mastery": 0.9}})
    as_json = build_adaptive_config(
        {"adaptive.priority.weights": '{"mastery": 0.9, "gap": 0.05}'}
    )
    assert as_dict.priority_weights["mastery"] == 0.9
    assert as_dict.priority_weights["confidence"] == PRIORITY_WEIGHTS_DEFAULT["confidence"]
    assert as_json.priority_weights["mastery"] == 0.9
    assert as_json.priority_weights["gap"] == 0.05


def test_weights_dirty_values_fall_back():
    """非法 JSON / 非映射 / 越界数值都回退到合法缺省。"""
    broken = build_adaptive_config({"adaptive.priority.weights": "{not json"})
    wrong_type = build_adaptive_config({"adaptive.priority.weights": 3})
    negative = build_adaptive_config({"adaptive.priority.weights": {"mastery": -2}})
    assert broken.priority_weights == PRIORITY_WEIGHTS_DEFAULT
    assert wrong_type.priority_weights == PRIORITY_WEIGHTS_DEFAULT
    assert negative.priority_weights["mastery"] == 0.0


def test_divisor_zero_is_protected():
    """除数被配成 0 / 负数时有除零保护，回退内置缺省且不抛异常。"""
    config = build_adaptive_config(
        {
            "adaptive.priority.gap_divisor": "0",
            "adaptive.priority.out_degree_divisor": "-5",
        }
    )
    assert config.priority_gap_divisor == PRIORITY_GAP_DIVISOR
    assert config.priority_out_degree_divisor == 1
    items = [_item("a", 0.5), _item("b", 0.5)]
    result = rank_priorities(items, [{"pre_kp_id": "a"}], {"a": 3}, adaptive_config=config)
    assert len(result) == 2


def test_priority_config_keys_are_registered():
    """新增阈值都登记进 CONFIG_SPECS（三步配置的第二步）。"""
    for key in (
        "adaptive.priority.weights",
        "adaptive.priority.gap_divisor",
        "adaptive.priority.out_degree_divisor",
        "adaptive.priority.recent_window",
        "adaptive.priority.stale_after",
        "adaptive.priority.top_n",
    ):
        assert key in CONFIG_KEYS, key


# ============================================================ 排序规则逐条生效
def test_lower_mastery_ranks_first():
    """R2：掌握度越低越优先（只保留 mastery 权重时仍严格成立）。"""
    result = rank_priorities(
        [_item("strong", 0.9), _item("weak", 0.1)],
        adaptive_config=_only_mastery_config(),
    )
    assert _ids(result) == ["weak", "strong"]


def test_lower_confidence_ranks_first():
    """R3：置信度越低越优先（结论不可靠，需先补样本）。"""
    weights = {"mastery": 0.0, "confidence": 1.0, "gap": 0.0, "degree": 0.0}
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_weights=weights)
    result = rank_priorities(
        [_item("sure", 0.5, confidence=0.95), _item("guess", 0.5, confidence=0.05)],
        adaptive_config=config,
    )
    assert _ids(result) == ["guess", "sure"]


def test_prerequisite_gap_promotes_prerequisite():
    """R1：被点名为前置缺口的知识点优先，且理由写明「前置基础」。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 1.0, "degree": 0.0}
    )
    result = rank_priorities(
        [_item("target", 0.2), _item("pre", 0.2)],
        [{"pre_kp_id": "pre", "kp_name": "分数除法"}],
        adaptive_config=config,
    )
    assert _ids(result)[0] == "pre"
    assert "前置" in result[0].reason


def test_gap_count_is_capped_by_divisor():
    """R1：缺口条数按 ``gap_divisor`` 归一，超过上限不再加分。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 1.0, "degree": 0.0},
        priority_gap_divisor=2.0,
    )
    gaps = [{"pre_kp_id": "a"}, {"pre_kp_id": "a"}, {"pre_kp_id": "b"}]
    result = rank_priorities([_item("a", 0.5), _item("b", 0.5), _item("c", 0.5)], gaps, adaptive_config=config)
    by_id = {item.knowledge_point_id: item for item in result}
    # a 被点名 2 次 == divisor 2 → 归一到 1.0；再多条也不会更高（上限生效）
    assert by_id["a"].score == 1.0
    assert by_id["b"].score == 0.5
    assert by_id["c"].score == 0.0


def test_higher_out_degree_ranks_first():
    """R4：图谱出度高（能解锁更多后续内容）的基础点优先。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 0.0, "degree": 1.0}
    )
    result = rank_priorities(
        [_item("leaf", 0.5), _item("hub", 0.5)],
        graph={"leaf": 1, "hub": 5},
        adaptive_config=config,
    )
    assert _ids(result)[0] == "hub"
    assert result[0].score == 1.0


def test_graph_accepts_edge_sequence():
    """graph 也接受前置边序列：按 pre 出现次数计出度。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 0.0, "degree": 1.0}
    )
    edges = [{"pre_kp_id": "hub"}, {"pre_kp_id": "hub"}, {"pre_kp_id": "leaf"}]
    result = rank_priorities([_item("leaf", 0.5), _item("hub", 0.5)], graph=edges, adaptive_config=config)
    by_id = {item.knowledge_point_id: item for item in result}
    assert by_id["hub"].score > by_id["leaf"].score


def test_stale_point_gets_boosted():
    """R5：超过陈旧阈值没练的知识点按超出天数加分。"""
    result = rank_priorities(
        [_item("fresh", 0.5, days_ago=1), _item("stale", 0.5, days_ago=30)],
        adaptive_config=_only_mastery_config(),
        now=_NOW,
    )
    assert _ids(result)[0] == "stale"
    assert "很久没练" in result[0].reason


def test_stale_threshold_is_configurable():
    """陈旧阈值可由 adaptive_config 调整。"""
    items = [_item("a", 0.5, days_ago=20), _item("b", 0.5, days_ago=20)]
    strict = _only_mastery_config(priority_stale_after=30)
    loose = _only_mastery_config(priority_stale_after=10)
    assert rank_priorities(items, adaptive_config=loose, now=_NOW)[0].score == 1.0
    assert rank_priorities(items, adaptive_config=strict, now=_NOW)[0].score == 0.5


def test_recent_practice_is_down_weighted():
    """R6：近期刚练过的降权，且「越近降得越多」。"""
    result = rank_priorities(
        [_item("today", 0.5, days_ago=0), _item("week", 0.5, days_ago=6)],
        adaptive_config=_only_mastery_config(),
        now=_NOW,
    )
    by_id = {item.knowledge_point_id: item for item in result}
    assert by_id["week"].score > by_id["today"].score
    assert "错峰" in by_id["today"].reason


def test_recent_window_is_configurable():
    """近期窗口天数可配置：窗口外的练习不再降权。"""
    items = [_item("a", 0.5, days_ago=5)]
    narrow = _only_mastery_config(priority_recent_window=2)
    wide = _only_mastery_config(priority_recent_window=10)
    assert rank_priorities(items, adaptive_config=narrow, now=_NOW)[0].score > rank_priorities(
        items, adaptive_config=wide, now=_NOW
    )[0].score


def test_now_is_injected_not_system_time():
    """时间基准完全由 ``now`` 注入；不传时跳过时间维度（不读系统时钟）。"""
    items = [_item("a", 0.5, days_ago=30)]
    without_now = rank_priorities(items, adaptive_config=_only_mastery_config())
    with_now = rank_priorities(items, adaptive_config=_only_mastery_config(), now=_NOW)
    assert "很久没练" not in without_now[0].reason
    assert "很久没练" in with_now[0].reason


def test_last_result_at_is_fallback_when_row_has_no_time():
    """掌握度行缺时间字段时，``results.last_result_at`` 兜底驱动陈旧/近期判定。"""
    items = [_item("a", 0.5)]
    baseline = rank_priorities(items, adaptive_config=_only_mastery_config(), now=_NOW)[0].score
    stale_result = {
        "a": {"recent_attempts": 0, "last_result_at": (_NOW - timedelta(days=30)).isoformat()}
    }
    with_result = rank_priorities(
        items, results=stale_result, adaptive_config=_only_mastery_config(), now=_NOW
    )
    assert with_result[0].score > baseline
    assert "很久没练" in with_result[0].reason


def test_results_none_is_ignored():
    """``results=None`` 时与其它参数组合完全一致（本次只留出入口）。"""
    items = [_item("a", 0.4), _item("b", 0.6)]
    assert rank_priorities(items, results=None) == rank_priorities(items)


# ============================================================ top_n / 去重 / 空输入
def test_top_n_default_and_explicit():
    """``top_n=None`` 取缺省 5；显式值按给定条数截断。"""
    items = [_item(f"kp{index}", 0.9 - index * 0.1) for index in range(8)]
    assert len(rank_priorities(items)) == PRIORITY_TOP_N_DEFAULT
    assert len(rank_priorities(items, top_n=3)) == 3


def test_top_n_dirty_values_fall_back_to_default():
    """``top_n`` 为 0 / 负数 / 脏字符串时回退缺省条数。"""
    items = [_item(f"kp{index}", 0.9 - index * 0.1) for index in range(8)]
    for dirty in (0, -3, "abc", None):
        assert len(rank_priorities(items, top_n=dirty)) == PRIORITY_TOP_N_DEFAULT


def test_duplicate_knowledge_points_are_deduplicated():
    """同一知识点重复出现只保留一条（保留首次出现位置）。"""
    result = rank_priorities([_item("same", 0.2), _item("same", 0.8), _item("other", 0.5)])
    assert _ids(result) == ["same", "other"]


def test_empty_and_none_inputs_return_empty_list():
    """``mastery_items`` 为空 / None / 脏容器时返回空列表。"""
    for empty in (None, [], (), {}, "bad", 42):
        assert rank_priorities(empty) == []


def test_items_without_id_are_skipped():
    """没有知识点 ID 的脏行不进入结果。"""
    result = rank_priorities([{"mastery": 0.1}, _item("ok", 0.3)])
    assert _ids(result) == ["ok"]


# ============================================================ 脏入参兜底
def test_dirty_numeric_values_are_clamped():
    """越界 / 负数 / 不可解析的数值都被夹到合法区间。"""
    result = rank_priorities(
        [
            _item("high", "bad", confidence=9, level=99),
            _item("low", -7, confidence=-7, level=-9),
        ],
        adaptive_config=_only_mastery_config(),
    )
    by_id = {item.knowledge_point_id: item for item in result}
    assert by_id["high"].mastery == 0.0
    assert by_id["high"].level == 3
    assert by_id["low"].mastery == 0.0
    assert by_id["low"].level == 0
    assert 0.0 <= by_id["high"].score <= 1.0


def test_missing_confidence_defaults_to_low_line():
    """置信度缺失时按低置信度线处理（保守地认为结论不可靠）。"""
    result = rank_priorities([{"knowledge_point_id": "a", "mastery": 0.5}])
    assert len(result) == 1
    assert result[0].score > 0.0


def test_object_items_are_supported():
    """入参支持对象形态（有同名属性即可），不只支持 dict。"""

    class Row:
        knowledge_point_id = "obj"
        kp_name = "对象形态"
        mastery = 0.2
        confidence = 0.4
        level = 1

    result = rank_priorities([Row()])
    assert _ids(result) == ["obj"]
    assert result[0].kp_name == "对象形态"


def test_dirty_gaps_and_graph_do_not_raise():
    """gaps / graph 传脏值（None / 字符串 / 缺字段）一律忽略，不抛异常。"""
    items = [_item("a", 0.3)]
    for dirty_gap in (None, "bad", [None, 3, {}], [{"pre_kp_id": ""}]):
        assert len(rank_priorities(items, dirty_gap)) == 1
    for dirty_graph in (None, "bad", [{"nope": 1}], {"a": "x"}):
        assert len(rank_priorities(items, graph=dirty_graph)) == 1


# ============================================================ 出参 / 幂等
def test_priority_item_api_keys_are_stable_camel_case():
    """出参 camelCase 键集合稳定（与 T05 冻结契约一致）。"""
    item = rank_priorities([_item("a", 0.2)])[0]
    assert set(item.to_api()) == {
        "knowledgePointId",
        "kpName",
        "score",
        "mastery",
        "level",
        "reason",
    }


def test_same_input_is_idempotent():
    """固定输入重复计算完全一致（含 dict 出口）。"""
    items = [_item("b", 0.4, days_ago=3), _item("a", 0.4, days_ago=3)]
    first = rank_priorities(items, [{"pre_kp_id": "a"}], {"a": 2}, now=_NOW)
    second = rank_priorities(items, [{"pre_kp_id": "a"}], {"a": 2}, now=_NOW)
    assert first == second
    assert [item.to_api() for item in first] == [item.to_api() for item in second]


def test_tie_breaker_is_deterministic():
    """同分时按掌握度、等级、ID 稳定排序，不依赖 dict 遍历顺序。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 0.0, "confidence": 0.0, "gap": 0.0, "degree": 0.0}
    )
    result = rank_priorities([_item("z", 0.5), _item("a", 0.5)], adaptive_config=config)
    assert _ids(result) == ["a", "z"]


def test_priority_module_has_no_forbidden_imports():
    """AST 守卫：引擎模块顶层禁止 ORM / models / os。"""
    tree = ast.parse(inspect.getsource(priority_module))
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

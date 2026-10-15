# -*- coding: utf-8 -*-
"""引擎层 · 优先级引擎 ``results`` 后处理修正单测（V6.5 Sprint 5 · T07 裁决①）。

覆盖目标（架构师派工口径第 3 条）：

    1. ``results=None`` 与改造前**逐位一致**（最关键的一条：老链路零改动）；
    2. C1「错题回炉」触发 / 不触发的四种边界（错 1 题取消降权、错 3 题打满、
       错误率不达线不触发、``recent_attempts=0`` 不参与）；
    3. C2「题量饱和」触发与关闭；
    4. 脏值兜底（负数 / 越界 / 脏字符串 / 非 Mapping）不抛异常；
    5. ``last_result_at`` 覆盖 ``last_practice_at`` 的兜底语义；
    6. 四个回滚开关各自独立生效；
    7. 理由文案新增一路「错题优先于遗忘」。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.engines.config import CONFIG_KEYS, DEFAULT_ADAPTIVE_CONFIG, build_adaptive_config
from app.engines.priority import (
    PRIORITY_RECENT_ATTEMPTS_CAP,
    PRIORITY_SATURATION_PENALTY,
    PRIORITY_WEIGHTS_DEFAULT,
    PRIORITY_WRONG_BOOST,
    PRIORITY_WRONG_RATE_FLOOR,
    PRIORITY_WRONG_SATURATION,
    _result_boost,
    _score,
    rank_priorities,
)
from app.engines.types import PriorityItem

#: 固定时间基准（引擎不读系统时间，全部由 ``now`` 注入）
_NOW: datetime = datetime(2026, 3, 1, 12, 0, 0)

#: 缺省阈值（与 config 的默认值同口径，改动前先确认两边一致）
_ASSERT_DEFAULTS = {
    "wrong_rate_floor": 0.5,
    "wrong_boost": 0.25,
    "wrong_saturation": 3.0,
    "recent_attempts_cap": 10,
    "saturation_penalty": 0.85,
}


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


def _cfg(**overrides: Any):
    """在缺省配置上覆盖若干阈值。"""
    return DEFAULT_ADAPTIVE_CONFIG.replace(**overrides)


def _score_of(
    item: Dict[str, Any],
    results: Any = None,
    *,
    gaps: Any = None,
    graph: Any = None,
    cfg: Any = None,
) -> float:
    """取单个知识点在给定 results 下的最终得分。"""
    ranked = rank_priorities(
        [item],
        gaps,
        graph,
        results,
        adaptive_config=cfg,
        now=_NOW,
        top_n=1,
    )
    return ranked[0].score


# ============================================================ 缺省值与配置登记
def test_default_constants_match_design_table():
    """五个新阈值的模块缺省值与施工图表格逐项一致。"""
    assert PRIORITY_WRONG_RATE_FLOOR == _ASSERT_DEFAULTS["wrong_rate_floor"]
    assert PRIORITY_WRONG_BOOST == _ASSERT_DEFAULTS["wrong_boost"]
    assert PRIORITY_WRONG_SATURATION == _ASSERT_DEFAULTS["wrong_saturation"]
    assert PRIORITY_RECENT_ATTEMPTS_CAP == _ASSERT_DEFAULTS["recent_attempts_cap"]
    assert PRIORITY_SATURATION_PENALTY == _ASSERT_DEFAULTS["saturation_penalty"]


def test_config_defaults_match_module_constants():
    """``AdaptiveConfig`` 的缺省值与引擎模块常量同口径（两处不许漂移）。"""
    config = DEFAULT_ADAPTIVE_CONFIG
    assert config.priority_wrong_rate_floor == PRIORITY_WRONG_RATE_FLOOR
    assert config.priority_wrong_boost == PRIORITY_WRONG_BOOST
    assert config.priority_wrong_saturation == PRIORITY_WRONG_SATURATION
    assert config.priority_recent_attempts_cap == PRIORITY_RECENT_ATTEMPTS_CAP
    assert config.priority_saturation_penalty == PRIORITY_SATURATION_PENALTY


def test_new_config_keys_are_registered():
    """五个新阈值都登记进 CONFIG_SPECS（三步配置的第二步）。"""
    for key in (
        "adaptive.priority.wrong_rate_floor",
        "adaptive.priority.wrong_boost",
        "adaptive.priority.wrong_saturation",
        "adaptive.priority.recent_attempts_cap",
        "adaptive.priority.saturation_penalty",
    ):
        assert key in CONFIG_KEYS, key


def test_build_adaptive_config_accepts_new_keys():
    """``build_adaptive_config`` 能从原始 key-value 解出新阈值，脏值回退缺省。"""
    config = build_adaptive_config(
        {
            "adaptive.priority.wrong_rate_floor": "0.4",
            "adaptive.priority.wrong_boost": "0.5",
            "adaptive.priority.wrong_saturation": "5",
            "adaptive.priority.recent_attempts_cap": "20",
            "adaptive.priority.saturation_penalty": "0.7",
        }
    )
    assert config.priority_wrong_rate_floor == 0.4
    assert config.priority_wrong_boost == 0.5
    assert config.priority_wrong_saturation == 5.0
    assert config.priority_recent_attempts_cap == 20
    assert config.priority_saturation_penalty == 0.7

    dirty = build_adaptive_config(
        {
            "adaptive.priority.wrong_rate_floor": "abc",
            "adaptive.priority.wrong_boost": "-9",
            "adaptive.priority.wrong_saturation": "0",
            "adaptive.priority.recent_attempts_cap": "x",
            "adaptive.priority.saturation_penalty": "3",
        }
    )
    assert dirty.priority_wrong_rate_floor == PRIORITY_WRONG_RATE_FLOOR
    assert dirty.priority_wrong_boost == PRIORITY_WRONG_BOOST
    assert dirty.priority_wrong_saturation == PRIORITY_WRONG_SATURATION
    assert dirty.priority_recent_attempts_cap == PRIORITY_RECENT_ATTEMPTS_CAP
    # 抑制系数被夹到 [0,1]，配成 3 视为 1.0（= 关闭抑制）
    assert dirty.priority_saturation_penalty == 1.0


# ============================================================ results=None 等价
def test_results_none_is_bitwise_identical_to_legacy():
    """``results=None`` 时输出必须与改造前**逐位相同**（现有 121 条单测的护栏）。

    取三组有代表性的输入（无时间 / 近期窗口内 / 已陈旧），逐条断言
    ``_result_boost`` 三因子均为中立项，且最终得分落在预期值上。
    """
    for item, expected in (
        (_item("plain", 0.4), 0.365),  # 无时间维度：BASE = 0.6*0.40 + 0.5*0.25
        (_item("fresh", 0.4, days_ago=0), 0.0456),  # 近期：BASE × (0+1)/(7+1)
        (_item("stale", 0.4, days_ago=30), 1.0),  # 陈旧：超阈值加分（1.365）被夹到 1.0
    ):
        legacy = _score_of(item, None)
        assert legacy == expected, (item["knowledge_point_id"], legacy, expected)
        # 传空 dict / 空 list 与 None 完全等价
        assert _score_of(item, {}) == legacy
        assert _score_of(item, []) == legacy


def test_result_boost_neutral_when_no_metrics():
    """``_result_boost`` 在 metrics 缺失时返回中立项 ``(recent原值, 1.0, 0.0)``。"""
    assert _result_boost(None, DEFAULT_ADAPTIVE_CONFIG) == (1.0, 1.0, 0.0)
    assert _result_boost({}, DEFAULT_ADAPTIVE_CONFIG) == (1.0, 1.0, 0.0)
    assert _result_boost("脏值", DEFAULT_ADAPTIVE_CONFIG) == (1.0, 1.0, 0.0)
    # recent 原值原样返回（不被莫名其妙改掉）
    assert _result_boost(None, DEFAULT_ADAPTIVE_CONFIG, recent=0.25) == (0.25, 1.0, 0.0)


def test_score_helper_keeps_legacy_branch_semantics():
    """``_score`` 拆分出的 STALE / RECENT 与原 if/elif 分支数学等价。"""
    weights = PRIORITY_WEIGHTS_DEFAULT
    kwargs = dict(
        mastery=0.4,
        confidence=0.5,
        gap_count=0,
        degree=0,
        weights=weights,
        gap_divisor=3.0,
        degree_divisor=5,
        recent_window=7,
        stale_after=14,
    )
    base = 0.6 * 0.40 + 0.5 * 0.25  # = 0.365
    plain, stale_flag, recent_flag = _score(days=None, **kwargs)
    assert (plain, stale_flag, recent_flag) == (round(base, 4), False, False)

    # 近期窗口内：RECENT = (days+1)/(window+1)
    fresh, stale_flag, recent_flag = _score(days=0.0, **kwargs)
    assert fresh == round(base * (1 / 8), 4)
    assert (stale_flag, recent_flag) == (False, True)

    # 陈旧：STALE = (days - 14) / 14 = 1.0，与 RECENT 互斥；最终被 clamp 到 1.0
    stale, stale_flag, recent_flag = _score(days=28.0, **kwargs)
    assert stale == 1.0
    assert (stale_flag, recent_flag) == (True, False)

    # 刚好在阈值上：days == stale_after 不算陈旧（严格大于才算）
    edge, stale_flag, recent_flag = _score(days=14.0, **kwargs)
    assert edge == base
    assert (stale_flag, recent_flag) == (False, False)


# ============================================================ C1 错题回炉
def test_c1_cancels_recent_penalty_with_one_wrong():
    """C1：错 1 题且错误率过线 → 取消「近期练过」降权（RECENT := 1.0）。"""
    item = _item("kp", 0.4, days_ago=0)
    penalized = _score_of(item, None)
    assert penalized < 0.365  # 确实被降权了

    results = {"kp": {"recent_attempts": 2, "recent_correct": 1}}  # 错 1 题，错误率 0.5
    boosted = _score_of(item, results)
    # 取消降权后回到 BASE，再叠加 BOOST = 0.25 × min(1, 1/3)
    assert boosted == round(0.365 + 0.25 * (1 / 3), 4)


def test_c1_boost_saturates_at_three_wrong():
    """C1：错 3 题即打满 ``wrong_boost``（再多错也不加）。"""
    item = _item("kp", 0.4, days_ago=0)
    three = {"kp": {"recent_attempts": 3, "recent_correct": 0}}
    eight = {"kp": {"recent_attempts": 8, "recent_correct": 0}}
    assert _score_of(item, three) == round(0.365 + 0.25, 4)
    assert _score_of(item, eight) == round(0.365 + 0.25, 4)


def test_c1_not_triggered_when_wrong_rate_below_floor():
    """C1：错误率 < ``wrong_rate_floor`` 不触发（近期降权保留、BOOST=0）。"""
    item = _item("kp", 0.4, days_ago=0)
    legacy = _score_of(item, None)
    # 错 1 题 / 共 5 题 → 错误率 0.2 < 0.5
    results = {"kp": {"recent_attempts": 5, "recent_correct": 4}}
    assert _score_of(item, results) == legacy


def test_c1_not_triggered_when_recent_attempts_is_zero():
    """C1：``recent_attempts=0`` 的知识点完全不参与修正。"""
    item = _item("kp", 0.4, days_ago=0)
    legacy = _score_of(item, None)
    for metrics in (
        {"recent_attempts": 0, "recent_correct": 0},
        {"recent_attempts": 0, "recent_correct": 5},
        {"recent_correct": 3},
    ):
        assert _score_of(item, {"kp": metrics}) == legacy


def test_c1_does_not_fire_when_no_recent_penalty():
    """C1 在「本就没有近期降权」时仍然加分（错题信号独立生效）。"""
    item = _item("kp", 0.4, days_ago=30)  # 已陈旧 → 加分被夹到 1.0
    assert _score_of(item, None) == 1.0
    assert _score_of(item, {"kp": {"recent_attempts": 3, "recent_correct": 0}}) == 1.0

    # 无时间维度：BASE + BOOST
    plain = _item("kp2", 0.4)
    assert _score_of(plain, None) == 0.365
    assert _score_of(plain, {"kp2": {"recent_attempts": 3, "recent_correct": 0}}) == round(
        0.365 + 0.25, 4
    )


def test_c1_wrong_rate_at_floor_boundary():
    """错误率**正好等于**门槛时触发（用 ``>=`` 而非 ``>``）。"""
    item = _item("kp", 0.4)
    at_floor = _score_of(item, {"kp": {"recent_attempts": 2, "recent_correct": 1}})
    assert at_floor == round(0.365 + 0.25 * (1 / 3), 4)
    below = _score_of(item, {"kp": {"recent_attempts": 3, "recent_correct": 2}})
    assert below == 0.365


# ============================================================ C2 题量饱和
def test_c2_saturation_penalty_applied_above_cap():
    """C2：``recent_attempts > 10`` 触发 0.85 抑制。"""
    item = _item("kp", 0.4)
    legacy = _score_of(item, None)
    saturated = _score_of(item, {"kp": {"recent_attempts": 11, "recent_correct": 11}})
    assert saturated == round(legacy * 0.85, 4)


def test_c2_not_applied_at_or_below_cap():
    """C2：``recent_attempts == 10`` 是边界，不触发抑制。"""
    item = _item("kp", 0.4)
    legacy = _score_of(item, None)
    assert _score_of(item, {"kp": {"recent_attempts": 10, "recent_correct": 10}}) == legacy


def test_c1_and_c2_stack_in_expected_order():
    """C1 与 C2 同时触发：``(BASE+STALE) × 1.0 × SATURATION + BOOST``。"""
    item = _item("kp", 0.4)
    # 近 12 题错 8 题：错误率 2/3 过线 → BOOST 打满；12 > 10 → 抑制 0.85
    results = {"kp": {"recent_attempts": 12, "recent_correct": 4}}
    assert _score_of(item, results) == round(0.365 * 0.85 + 0.25, 4)


# ============================================================ 回滚开关
def test_rollback_wrong_boost_zero_keeps_only_unpenalize():
    """开关：``wrong_boost=0`` → 只保留「取消近期降权」，去掉加分。"""
    item = _item("kp", 0.4, days_ago=0)
    cfg = _cfg(priority_wrong_boost=0)
    results = {"kp": {"recent_attempts": 3, "recent_correct": 0}}
    assert _score_of(item, results, cfg=cfg) == 0.365  # 恰好回到 BASE（降权被取消）


def test_rollback_saturation_penalty_one_disables_suppression():
    """开关：``saturation_penalty=1.0`` → 关闭单点刷量抑制。"""
    item = _item("kp", 0.4)
    cfg = _cfg(priority_saturation_penalty=1.0)
    results = {"kp": {"recent_attempts": 30, "recent_correct": 30}}
    assert _score_of(item, results, cfg=cfg) == _score_of(item, None)


def test_rollback_results_none_matches_legacy_ranking():
    """开关：``results=None`` → 完整排序（含 gap/graph）与不带 results 一致。"""
    items = [_item("a", 0.2, days_ago=1), _item("b", 0.8, days_ago=20)]
    gaps = [{"pre_kp_id": "b"}]
    graph = {"a": 4, "b": 1}
    without = rank_priorities(items, gaps, graph, None, now=_NOW)
    with_none = rank_priorities(items, gaps, graph, results=None, now=_NOW)
    with_empty = rank_priorities(items, gaps, graph, {}, now=_NOW)
    assert _ids(without) == _ids(with_none) == _ids(with_empty)
    assert [round(i.score, 6) for i in without] == [round(i.score, 6) for i in with_empty]


def test_rollback_adaptive_disabled_freezes_difficulty_not_priority():
    """``adaptive.enabled=false`` 锁定难度引擎，优先级链路仍可用（两者独立）。"""
    cfg = _cfg(enabled=False)
    results = {"kp": {"recent_attempts": 3, "recent_correct": 0}}
    boosted = _score_of(_item("kp", 0.4), results, cfg=cfg)
    assert boosted == round(0.365 + 0.25, 4)


# ============================================================ 脏值兜底
def test_dirty_results_never_raise():
    """脏 results（负数 / 越界 / 脏字符串 / 非 Mapping / 序列）一律不抛异常。"""
    item = _item("kp", 0.4, days_ago=2)
    legacy = _score_of(item, None)
    dirty_payloads: List[Any] = [
        {"kp": {"recent_attempts": -5, "recent_correct": -3}},
        {"kp": {"recent_attempts": "abc", "recent_correct": "xyz"}},
        {"kp": {"recent_attempts": None, "recent_correct": None}},
        {"kp": {"recent_attempts": 4, "recent_correct": 99}},  # 答对比总次数还多
        {"kp": "脏值"},
        {"kp": None},
        {123: {"recent_attempts": 3, "recent_correct": 0}},
        [
            {"knowledge_point_id": "kp", "recent_attempts": 3, "recent_correct": 0},
        ],
        [{"kp_id": "kp", "recent_attempts": 5, "recent_correct": 0}],
        3.14,
        "not-a-mapping",
    ]
    for payload in dirty_payloads:
        ranked = rank_priorities([item], None, None, payload, now=_NOW)
        assert len(ranked) == 1
        assert 0.0 <= ranked[0].score <= 1.0

    # 负数次数：夹到 0 → 不参与修正 → 与 legacy 一致
    assert _score_of(item, {"kp": {"recent_attempts": -5, "recent_correct": -3}}) == legacy
    # 答对次数超过总次数：夹到 total → 错误率 0 → 不触发 C1
    assert _score_of(item, {"kp": {"recent_attempts": 4, "recent_correct": 99}}) == legacy


def test_result_boost_clamps_dirty_numbers():
    """``_result_boost`` 对脏数值做夹紧，输出恒在合法区间。"""
    cfg = DEFAULT_ADAPTIVE_CONFIG
    for metrics in (
        {"recent_attempts": -1, "recent_correct": 5},
        {"recent_attempts": 3, "recent_correct": -2},
        {"recent_attempts": 1e9, "recent_correct": 0},
        {"recent_attempts": 3.7, "recent_correct": 1.2},
    ):
        recent, saturation, boost = _result_boost(metrics, cfg, recent=0.5)
        assert 0.0 <= recent <= 1.0
        assert 0.0 <= saturation <= 1.0
        assert 0.0 <= boost <= PRIORITY_WRONG_BOOST


def test_score_never_exceeds_one_with_boost():
    """BOOST 叠上去也恒被夹到 ``[0, 1]``，不会算出越界分值。"""
    item = _item("kp", 0.0, confidence=1.0)  # BASE 已经很高
    results = {"kp": {"recent_attempts": 9, "recent_correct": 0}}
    ranked = rank_priorities([item], None, None, results, now=_NOW)
    assert 0.0 <= ranked[0].score <= 1.0


# ============================================================ 时间兜底与理由
def test_last_result_at_overrides_last_practice_at():
    """``results.last_result_at`` 在掌握度行缺时间字段时兜底驱动 R5/R6。"""
    item = {"knowledge_point_id": "kp", "kp_name": "kp", "mastery": 0.4, "confidence": 0.5}
    # 掌握度行无时间 → 无 STALE / RECENT
    assert _score_of(item, None) == 0.365

    # 补上 3 天前的 last_result_at → 落进近期窗口，触发降权
    stamp = (_NOW - timedelta(days=3)).isoformat()
    results = {"kp": {"recent_attempts": 4, "recent_correct": 4, "last_result_at": stamp}}
    assert _score_of(item, results) == round(0.365 * (4 / 8), 4)


def test_reason_puts_wrong_answer_before_stale():
    """理由文案排序：gap > **错题回炉** > stale > 薄弱 > 近期 > 兜底。"""
    gap_item = _item("gap", 0.4, days_ago=30)
    ranked = rank_priorities(
        [gap_item],
        [{"pre_kp_id": "gap"}],
        None,
        {"gap": {"recent_attempts": 3, "recent_correct": 0}},
        now=_NOW,
    )
    assert "前置基础" in ranked[0].reason  # gap 仍排最前

    wrong_item = _item("分数加减法", 0.4, days_ago=30)
    ranked = rank_priorities(
        [wrong_item],
        None,
        None,
        {"分数加减法": {"recent_attempts": 4, "recent_correct": 1}},
        now=_NOW,
    )
    assert ranked[0].reason == "「分数加减法」刚做过 4 题错了 3 题，今天先回炉订正。"

    # 未触发 C1 时回落到「很久没练」
    ranked = rank_priorities(
        [wrong_item],
        None,
        None,
        {"分数加减法": {"recent_attempts": 10, "recent_correct": 9}},
        now=_NOW,
    )
    assert "很久没练" in ranked[0].reason


def test_reason_unchanged_without_results():
    """不带 results 时理由文案与改造前逐字一致（老文案零改动）。"""
    ranked = rank_priorities([_item("kp", 0.2, days_ago=30)], None, None, None, now=_NOW)
    assert ranked[0].reason == "「kp」很久没练了，先做几道把它捡回来。"
    ranked = rank_priorities([_item("kp2", 0.2, days_ago=2)], None, None, None, now=_NOW)
    assert "掌握度仅 20%" in ranked[0].reason


# ============================================================ 排序层面的净效应
def test_wrong_knowledge_point_outranks_clean_one():
    """产品效果：同样近两天练过，错的那个被顶到前面（错题当天回炉）。"""
    clean = _item("clean", 0.6, days_ago=1)
    wrong = _item("wrong", 0.6, days_ago=1)
    # 相同输入下按 ID 字典序，clean 在前
    assert _ids(rank_priorities([clean, wrong], None, None, None, now=_NOW)) == [
        "clean",
        "wrong",
    ]
    results = {
        "wrong": {"recent_attempts": 4, "recent_correct": 1},
        "clean": {"recent_attempts": 4, "recent_correct": 4},
    }
    assert _ids(rank_priorities([clean, wrong], None, None, results, now=_NOW)) == [
        "wrong",
        "clean",
    ]


def test_ranking_is_deterministic_with_results():
    """带 results 的排序可复现（同输入两次跑出同一结果）。"""
    items = [_item("a", 0.3, days_ago=1), _item("b", 0.5, days_ago=5), _item("c", 0.7)]
    results = {
        "a": {"recent_attempts": 4, "recent_correct": 1},
        "b": {"recent_attempts": 12, "recent_correct": 12},
        "c": {"recent_attempts": 2, "recent_correct": 2},
    }
    first = _ids(rank_priorities(items, None, None, results, now=_NOW))
    second = _ids(rank_priorities(items, None, None, results, now=_NOW))
    assert first == second

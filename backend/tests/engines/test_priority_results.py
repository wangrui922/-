# -*- coding: utf-8 -*-
"""引擎层 · 优先级引擎 `results` 修正因子单测（C1 错题回炉 + C2 题量饱和）。

硬门禁：``results=None`` 时必须与不含 results 的历史算法**逐位一致**
（``BOOST=0``、``SATURATION=1``、``RECENT`` 不变）——这是本版能安全上线的唯一理由，
因此文件里的等价守卫用例必须"改坏实现就会红"。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.engines.config import CONFIG_KEYS, DEFAULT_ADAPTIVE_CONFIG, build_adaptive_config
from app.engines.priority import rank_priorities
from app.engines.types import PriorityItem

#: 固定时间基准（引擎不读系统时钟）
_NOW: datetime = datetime(2026, 3, 1, 12, 0, 0)


def _item(
    kp_id: str,
    mastery: Any = 0.5,
    *,
    confidence: Any = 0.5,
    days_ago: Optional[int] = None,
    name: str = "",
) -> Dict[str, Any]:
    """构造掌握度条目。"""
    row: Dict[str, Any] = {
        "knowledge_point_id": kp_id,
        "kp_name": name or kp_id,
        "mastery": mastery,
        "confidence": confidence,
        "level": 1,
    }
    if days_ago is not None:
        row["last_practice_at"] = (_NOW - timedelta(days=days_ago)).isoformat()
    return row


def _score_of(items: List[Dict[str, Any]], results: Any = None, **kwargs: Any) -> float:
    """取单个知识点的得分。"""
    return rank_priorities(items, results=results, now=_NOW, **kwargs)[0].score


# ============================================================ 硬门禁：逐位等价
def test_results_none_is_bitwise_identical():
    """``results=None`` 与不传 results 得分逐位一致（多种信号组合都验证）。"""
    cases: List[List[Dict[str, Any]]] = [
        [_item("a", 0.2)],
        [_item("a", 0.9, confidence=0.95)],
        [_item("a", 0.5, days_ago=0)],
        [_item("a", 0.5, days_ago=3)],
        [_item("a", 0.5, days_ago=30)],
        [_item("a", 0.5, days_ago=30), _item("b", 0.4, days_ago=1)],
    ]
    for items in cases:
        without = rank_priorities(items, now=_NOW)
        with_none = rank_priorities(items, results=None, now=_NOW)
        with_empty = rank_priorities(items, results={}, now=_NOW)
        assert [item.score for item in without] == [item.score for item in with_none]
        assert [item.score for item in without] == [item.score for item in with_none]
        assert [item.score for item in without] == [item.score for item in with_empty]
        assert [item.reason for item in without] == [item.reason for item in with_empty]


def test_results_none_matches_golden_values():
    """``results=None`` 时必须等于**写死的历史算法真值**（对称比较防不住的回归靠这条）。

    ``expected`` 完全按 T06 定稿公式手算，不调用被测实现：
    ``BASE = (1-mastery)·0.40 + (1-confidence)·0.25``，
    ``raw = (BASE + STALE) × RECENT``，最后 ``clamp(0,1)`` 保留 4 位。
    任何对「无 results 路径」的改动（含 ``noop`` 三元组被改坏）都会让这条变红。
    """
    base = (1 - 0.5) * 0.40 + (1 - 0.5) * 0.25  # 0.325
    cases = [
        # (days_ago, expected)
        (None, round(base, 4)),  # 无时间维度
        (1, round(base * (1 + 1) / (7 + 1), 4)),  # 近期降权：× 2/8
        (30, 1.0),  # 陈旧：(0.325 + 16/14) → 夹到 1.0
        (14, round(base, 4)),  # 正好等于陈旧阈值：不加分也不降权
    ]
    for days_ago, expected in cases:
        item = _item("a", 0.5, days_ago=days_ago)
        assert _score_of([item]) == expected, (days_ago, expected)
        assert _score_of([item], None) == expected, days_ago


def test_results_for_other_point_does_not_change_score():
    """``results`` 里没有该知识点条目时，得分不受影响。"""
    items = [_item("a", 0.4, days_ago=2)]
    baseline = _score_of(items)
    assert _score_of(items, {"zzz": {"recent_attempts": 5, "recent_correct": 0}}) == baseline


def test_results_zero_attempts_is_noop():
    """``recent_attempts=0`` 时 C1/C2 都不参与（防除零）。"""
    items = [_item("a", 0.4, days_ago=2)]
    baseline = _score_of(items)
    assert _score_of(items, {"a": {"recent_attempts": 0, "recent_correct": 0}}) == baseline


# ============================================================ C1 错题回炉
def test_one_wrong_cancels_recent_decay():
    """错 1 题 → 取消「近期练过」的降权，得分回到未降权水平以上。"""
    items = [_item("a", 0.5, days_ago=1)]
    plain = _score_of(items)
    wrong = _score_of(items, {"a": {"recent_attempts": 1, "recent_correct": 0}})
    assert wrong > plain
    # 未降权的基准（同知识点用窗口外时间）应低于「取消降权 + BOOST」
    assert wrong > _score_of([_item("a", 0.5)])


def test_three_wrong_saturates_boost():
    """错 3 题时 BOOST 打满（0.25），错得更多不再加分。"""
    items = [_item("a", 0.5)]
    three = _score_of(items, {"a": {"recent_attempts": 6, "recent_correct": 3}})
    six = _score_of(items, {"a": {"recent_attempts": 12, "recent_correct": 6}})
    base = (1 - 0.5) * 0.40 + (1 - 0.5) * 0.25  # 0.325
    assert three == round(base + 0.25, 4)  # BOOST 打满
    assert six <= three  # 触发 C2 饱和抑制后只会更低


def test_wrong_rate_below_floor_does_not_trigger():
    """错误率低于门槛（1/3）不触发 C1。"""
    items = [_item("a", 0.5, days_ago=1)]
    baseline = _score_of(items)
    result = _score_of(items, {"a": {"recent_attempts": 3, "recent_correct": 2}})
    assert result == baseline


def test_wrong_rate_at_floor_triggers():
    """错误率正好等于门槛（2/4=0.5）时触发 C1（>= 语义）。"""
    items = [_item("a", 0.5, days_ago=1)]
    baseline = _score_of(items)
    result = _score_of(items, {"a": {"recent_attempts": 4, "recent_correct": 2}})
    assert result > baseline


def test_wrong_boost_zero_degrades_to_decay_cancel_only():
    """``wrong_boost=0`` 时只保留「取消近期降权」，不加分（A/B 观察用）。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_wrong_boost=0.0)
    items = [_item("a", 0.5, days_ago=1)]
    result = rank_priorities(
        items,
        results={"a": {"recent_attempts": 2, "recent_correct": 0}},
        adaptive_config=config,
        now=_NOW,
    )[0].score
    # 只剩取消降权：等于未降权的 BASE
    assert result == _score_of([_item("a", 0.5)])


def test_wrong_rate_floor_is_configurable():
    """错误率门槛可配置（调到 0.9 后 1/2 不再触发）。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_wrong_rate_floor=0.9)
    items = [_item("a", 0.5, days_ago=1)]
    baseline = _score_of(items)
    result = rank_priorities(
        items,
        results={"a": {"recent_attempts": 2, "recent_correct": 1}},
        adaptive_config=config,
        now=_NOW,
    )[0].score
    assert result == baseline


# ============================================================ C2 题量饱和
def test_eleven_attempts_triggers_saturation():
    """近窗口作答 11 题（> 缺省 cap 10）触发抑制系数 0.85。"""
    items = [_item("a", 0.5)]
    at_cap = _score_of(items, {"a": {"recent_attempts": 10, "recent_correct": 10}})
    over_cap = _score_of(items, {"a": {"recent_attempts": 11, "recent_correct": 11}})
    base = (1 - 0.5) * 0.40 + (1 - 0.5) * 0.25  # 0.325
    assert over_cap == round(base * 0.85, 4)
    assert at_cap == round(base, 4)


def test_saturation_penalty_one_disables_suppression():
    """``saturation_penalty=1.0`` 关闭抑制。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_saturation_penalty=1.0)
    items = [_item("a", 0.5)]
    result = rank_priorities(
        items,
        results={"a": {"recent_attempts": 20, "recent_correct": 20}},
        adaptive_config=config,
        now=_NOW,
    )[0].score
    base = (1 - 0.5) * 0.40 + (1 - 0.5) * 0.25  # 0.325
    assert result == round(base, 4)


def test_saturation_and_stale_apply_in_order():
    """``STALE`` 与 ``SATURATION`` 同时生效：先加后乘。"""
    items = [_item("a", 0.5, days_ago=28)]  # 超过 14 天 → STALE = 1.0
    result = _score_of(items, {"a": {"recent_attempts": 11, "recent_correct": 11}})
    # STALE 先加（0.325+1.0=1.325），SATURATION 再乘（×0.85=1.126），最后夹到 1.0
    assert result == 1.0


def test_recent_attempts_cap_is_configurable():
    """饱和阈值可配置。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_recent_attempts_cap=5)
    items = [_item("a", 0.5)]
    result = rank_priorities(
        items,
        results={"a": {"recent_attempts": 6, "recent_correct": 6}},
        adaptive_config=config,
        now=_NOW,
    )[0].score
    base = (1 - 0.5) * 0.40 + (1 - 0.5) * 0.25  # 0.325
    assert result == round(base * 0.85, 4)


# ============================================================ 脏值兜底
def test_dirty_correct_count_is_clamped():
    """``recent_correct > recent_attempts`` 这类脏值被夹紧，不产生负错题数。"""
    items = [_item("a", 0.5)]
    result = _score_of(items, {"a": {"recent_attempts": 2, "recent_correct": 99}})
    base = (1 - 0.5) * 0.40 + (1 - 0.5) * 0.25  # 0.325
    assert result == round(base, 4)  # wrong=0 → 不触发 C1


def test_dirty_metric_values_do_not_raise():
    """非数字 / 负数 / None 的指标一律兜底，不抛异常。"""
    items = [_item("a", 0.5)]
    dirty_cases: List[Any] = [
        {"a": {"recent_attempts": "bad", "recent_correct": "bad"}},
        {"a": {"recent_attempts": -3, "recent_correct": -1}},
        {"a": {"recent_attempts": None, "recent_correct": None}},
        {"a": "不是字典"},
        {"a": None},
        {"a": {}},
    ]
    base = (1 - 0.5) * 0.40 + (1 - 0.5) * 0.25  # 0.325
    for results in dirty_cases:
        assert _score_of(items, results) == round(base, 4)


def test_dirty_results_container_is_ignored():
    """``results`` 传非映射 / 字符串 / 数字时不参与计算。"""
    items = [_item("a", 0.5)]
    baseline = _score_of(items)
    for dirty in ("bad", 42, {"a": 5}, [("a", {"recent_attempts": 3, "recent_correct": 0})]):
        assert _score_of(items, dirty) >= baseline


def test_last_result_at_is_fallback_for_time():
    """掌握度行缺时间字段时，``last_result_at`` 兜底驱动时间维度。"""
    items = [{"knowledge_point_id": "a", "mastery": 0.5, "confidence": 0.5, "level": 1}]
    baseline = _score_of(items)
    stale_result = {
        "a": {
            "recent_attempts": 0,
            "last_result_at": (_NOW - timedelta(days=30)).isoformat(),
        }
    }
    assert _score_of(items, stale_result) > baseline


def test_row_time_wins_over_last_result_at():
    """掌握度行自带时间字段时优先用它（results 的时间只是兜底）。"""
    row_time = (_NOW - timedelta(days=30)).isoformat()
    items = [{"knowledge_point_id": "a", "mastery": 0.5, "confidence": 0.5,
              "level": 1, "last_practice_at": row_time}]
    recent_result = {
        "a": {"recent_attempts": 0, "last_result_at": (_NOW - timedelta(days=1)).isoformat()}
    }
    assert _score_of(items, recent_result) == _score_of(items)


# ============================================================ 权重 / 边界 / 排序
def test_weights_still_apply_with_results():
    """``priority_weights`` 覆盖仍然生效（C1/C2 不绕过权重）。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_weights={"mastery": 1.0, "confidence": 0.0, "gap": 0.0, "degree": 0.0},
        priority_wrong_boost=0.0,
    )
    items = [_item("a", 0.2), _item("b", 0.8)]
    results = {"a": {"recent_attempts": 2, "recent_correct": 0},
               "b": {"recent_attempts": 2, "recent_correct": 0}}
    ranked = rank_priorities(items, results=results, adaptive_config=config, now=_NOW)
    assert [item.knowledge_point_id for item in ranked] == ["a", "b"]
    assert ranked[0].score == 0.8


def test_score_stays_within_unit_interval():
    """无论 BOOST / STALE 多大，得分恒在 [0, 1]。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_wrong_boost=5.0)
    items = [_item("a", 0.9, confidence=1.0, days_ago=30)]
    results = {"a": {"recent_attempts": 2, "recent_correct": 0}}
    result = rank_priorities(items, results=results, adaptive_config=config, now=_NOW)[0]
    assert 0.0 <= result.score <= 1.0


def test_stable_sorting_is_preserved():
    """带 results 时排序依然是确定性的（同分按掌握度 / ID）。"""
    items = [_item("z", 0.5), _item("a", 0.5), _item("m", 0.5)]
    results = {
        "z": {"recent_attempts": 2, "recent_correct": 0},
        "a": {"recent_attempts": 2, "recent_correct": 0},
        "m": {"recent_attempts": 2, "recent_correct": 0},
    }
    first = rank_priorities(items, results=results, now=_NOW)
    second = rank_priorities(items, results=results, now=_NOW)
    assert [item.knowledge_point_id for item in first] == ["a", "m", "z"]
    assert first == second


def test_reason_reports_wrong_count():
    """理由文案输出「刚做过 X 题错了 Y 题，今天先回炉订正」。"""
    items = [_item("a", 0.5, days_ago=1, name="分数加减法")]
    result = rank_priorities(
        items,
        results={"a": {"recent_attempts": 4, "recent_correct": 1}},
        now=_NOW,
    )[0]
    assert result.reason == "「分数加减法」刚做过 4 题错了 3 题，今天先回炉订正。"


def test_reason_priority_gap_then_wrong_then_stale():
    """理由优先级：gap > 近窗口错题 > 陈旧（新增文案不得挤掉既有文案）。"""
    items = [_item("a", 0.5, days_ago=30, name="kp")]
    results = {"a": {"recent_attempts": 2, "recent_correct": 0}}

    with_gap = rank_priorities(items, [{"pre_kp_id": "a"}], results=results, now=_NOW)[0]
    assert with_gap.reason.startswith("「kp」是未掌握的前置基础")

    without_gap = rank_priorities([_item("a", 0.5, days_ago=30, name="kp")],
                                  results=results, now=_NOW)[0]
    assert "回炉订正" in without_gap.reason

    # results 不参与（attempts=0）时，陈旧文案仍然可达
    stale_only = rank_priorities(
        [_item("a", 0.5, days_ago=30, name="kp")],
        results={"a": {"recent_attempts": 0}},
        now=_NOW,
    )[0]
    assert stale_only.reason == "「kp」很久没练了，先做几道把它捡回来。"


def test_config_defaults_are_noop_and_registered():
    """5 个新配置项缺省值符合裁决，且都登记进 CONFIG_SPECS。"""
    assert DEFAULT_ADAPTIVE_CONFIG.priority_wrong_rate_floor == 0.5
    assert DEFAULT_ADAPTIVE_CONFIG.priority_wrong_boost == 0.25
    assert DEFAULT_ADAPTIVE_CONFIG.priority_wrong_saturation == 3.0
    assert DEFAULT_ADAPTIVE_CONFIG.priority_recent_attempts_cap == 10
    assert DEFAULT_ADAPTIVE_CONFIG.priority_saturation_penalty == 0.85
    for key in (
        "adaptive.priority.wrong_rate_floor",
        "adaptive.priority.wrong_boost",
        "adaptive.priority.wrong_saturation",
        "adaptive.priority.recent_attempts_cap",
        "adaptive.priority.saturation_penalty",
    ):
        assert key in CONFIG_KEYS, key


def test_config_values_round_trip():
    """配置项能被 adaptive_config 读出（含脏值回退）。"""
    config = build_adaptive_config(
        {
            "adaptive.priority.wrong_rate_floor": "9",
            "adaptive.priority.wrong_boost": "-1",
            "adaptive.priority.wrong_saturation": "0",
            "adaptive.priority.recent_attempts_cap": "20",
            "adaptive.priority.saturation_penalty": "0.5",
        }
    )
    assert config.priority_wrong_rate_floor == 1.0
    assert config.priority_wrong_boost == 0.25  # 负数回退缺省
    assert config.priority_wrong_saturation == 3.0  # 0 触发除零保护
    assert config.priority_recent_attempts_cap == 20
    assert config.priority_saturation_penalty == 0.5


def test_priority_item_contract_is_unchanged():
    """出参仍是 PriorityItem，camelCase 键集合不变。"""
    item: PriorityItem = rank_priorities(
        [_item("a", 0.5)], results={"a": {"recent_attempts": 2, "recent_correct": 0}}, now=_NOW
    )[0]
    assert isinstance(item, PriorityItem)
    assert set(item.to_api()) == {
        "knowledgePointId",
        "kpName",
        "score",
        "mastery",
        "level",
        "reason",
    }

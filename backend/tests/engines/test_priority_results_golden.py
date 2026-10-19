# -*- coding: utf-8 -*-
"""引擎层 · results 修正因子 N1 golden 真值守卫（C1 错题回炉 + C2 题量饱和）。

背景与目的
----------
``test_priority_results.py`` 已覆盖 C1/C2 的**行为方向**（触发/不触发、等价门禁），
但按 N1 铁律，「错题加分的上下界夹取」「C1 与 stale 加法的先后」「C1+C2 同时
命中的复合真值」这些关键路径只有相对断言。本文件把每条路径的**数值**写死：

* BOOST = ``wrong_boost × min(1, wrong / wrong_saturation)``，再夹到 ``[0,1]``；
* C1 命中时 RECENT 置 1.0（取消近期降权），BOOST 在乘法之后**追加**；
* C2 命中时整体乘 ``saturation_penalty``（对 BASE+STALE 生效，对 BOOST 不生效）；
* ``wrong = floor(attempts - correct + 1e-9)``（不用 round，防银行家舍入多算）。

所有期望值按定稿公式以分数精确运算推导，再按 IEEE754 双精度核签末位；
不调用被测实现。基准 BASE = (1-0.5)×0.40 + (1-0.5)×0.25 = 0.325。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.engines.config import DEFAULT_ADAPTIVE_CONFIG, build_adaptive_config
from app.engines.priority import rank_priorities
from app.engines.types import PriorityItem

#: 固定时间基准（引擎不读系统时钟）
_NOW: datetime = datetime(2026, 3, 1, 12, 0, 0)

#: 基准分（mastery=confidence=0.5，无其它信号）
BASE: float = (1 - 0.5) * 0.40 + (1 - 0.5) * 0.25  # 0.325


def _item(
    kp_id: str = "a",
    mastery: float = 0.5,
    *,
    confidence: float = 0.5,
    days_ago: Optional[int] = None,
    name: str = "",
) -> Dict[str, Any]:
    """构造掌握度条目（默认不带时间字段 → 无时间维度）。"""
    row: Dict[str, Any] = {
        "knowledge_point_id": kp_id,
        "kp_name": name or kp_id,
        "mastery": mastery,
        "confidence": confidence,
        "level": 1,
    }
    if days_ago is not None:
        row["last_practiced_at"] = (_NOW - timedelta(days=days_ago)).isoformat()
    return row


def _score_of(
    items: List[Dict[str, Any]],
    results: Any = None,
    **kwargs: Any,
) -> float:
    """取首个知识点的得分。"""
    return rank_priorities(items, results=results, now=_NOW, **kwargs)[0].score


def _score_with(results: Dict[str, Dict[str, Any]], **kwargs: Any) -> float:
    """单点 + results 的得分快捷方式。"""
    return _score_of([_item(**kwargs.pop("item", {}))], results, **kwargs)


# ============================================================ C1 加分曲线手算
def test_c1_boost_partial_ramp_golden():
    """C1 加分按错题数线性爬坡：wrong=1 → 1/3 满分，wrong=2 → 2/3。

    手算（days=1 有近期降权底、attempts=2 correct=1 → wrong=1 rate=0.5 ≥ 0.5）::

        BOOST = 0.25 × min(1, 1/3) = 0.083333…
        raw   = BASE×RECENT(取消=1.0) + BOOST = 0.325 + 0.083333… = 0.408333…
        → 0.4083

    对照（attempts=3 correct=1 → wrong=2 rate=2/3）::

        BOOST = 0.25 × 2/3 = 0.166666… → raw = 0.491666… → 0.4917
    """
    one_wrong = _score_of(
        [_item(days_ago=1)], {"a": {"recent_attempts": 2, "recent_correct": 1}}
    )
    two_wrong = _score_of(
        [_item(days_ago=1)], {"a": {"recent_attempts": 3, "recent_correct": 1}}
    )
    assert one_wrong == 0.4083
    assert two_wrong == 0.4917


def test_c1_boost_saturates_at_three_wrong_golden():
    """C1 加满后不再涨：wrong=3 与 wrong=6 的 BOOST 都是 0.25。

    手算（无时间维度，两组 attempts 都 ≤ cap 10 不触发 C2）::

        attempts=6 correct=3 → wrong=3 rate=0.5 → BOOST=0.25×min(1,3/3)=0.25
        attempts=6 correct=0 → wrong=6 rate=1.0 → BOOST=0.25×min(1,6/3)=0.25
        raw = 0.325 + 0.25 = 0.575（两组相同：错得更多不加分）
    """
    three = _score_of([_item()], {"a": {"recent_attempts": 6, "recent_correct": 3}})
    six = _score_of([_item()], {"a": {"recent_attempts": 6, "recent_correct": 0}})
    assert three == 0.575
    assert six == 0.575  # BOOST 早已打满，错更多不加分


def test_c1_rate_floor_boundary_golden():
    """错误率门槛 0.5 的边界：2/4=0.5 触发（>=），1/3≈0.333 不触发。

    手算：触发时（days=1）raw = 0.325 + 0.25×min(1, 2/3) = 0.491666… → 0.4917；
    不触发时保留近期降权 raw = 0.325 × (1+1)/(7+1) = 0.08125 → 0.0813。
    """
    at_floor = _score_of(
        [_item(days_ago=1)], {"a": {"recent_attempts": 4, "recent_correct": 2}}
    )
    below_floor = _score_of(
        [_item(days_ago=1)], {"a": {"recent_attempts": 3, "recent_correct": 2}}
    )
    assert at_floor == 0.4917
    assert below_floor == 0.0813


def test_c1_floor_is_configurable_golden():
    """门槛可配置：floor=0.34 时 1/3 不触发；floor=1/3 时恰好触发。

    手算：不触发 → 保留降权 0.0813；触发 → 0.325 + 0.25/3 = 0.4083。
    """
    strict = DEFAULT_ADAPTIVE_CONFIG.replace(priority_wrong_rate_floor=0.34)
    exact = DEFAULT_ADAPTIVE_CONFIG.replace(priority_wrong_rate_floor=1 / 3)
    blocked = _score_of(
        [_item(days_ago=1)],
        {"a": {"recent_attempts": 3, "recent_correct": 2}},
        adaptive_config=strict,
    )
    allowed = _score_of(
        [_item(days_ago=1)],
        {"a": {"recent_attempts": 3, "recent_correct": 2}},
        adaptive_config=exact,
    )
    assert blocked == 0.0813
    assert allowed == 0.4083


def test_c1_cancels_decay_but_keeps_stale_golden():
    """C1 取消的是**乘法**降权，stale 的**加法**加分照算。

    手算（days=20 → stale = 6/14；attempts=4 correct=2 → wrong=2 rate=0.5）::

        raw = (0.325 + 6/14) × 1.0 × 1.0 + 0.25×2/3
            = 0.753571… + 0.166666… = 0.920238… → 0.9202
    """
    result = _score_of(
        [_item(days_ago=20)], {"a": {"recent_attempts": 4, "recent_correct": 2}}
    )
    assert result == 0.9202


def test_wrong_boost_clamped_both_ends_golden():
    """BOOST 上下界夹取：配 5.0 → 钳 1.0；配 -1 → 钳 0.0（只取消降权）。

    手算：boost=1.0 时（attempts=2 correct=1 → wrong=1 rate=0.5 触发 C1）::

        BOOST = 1.0 × min(1, 1/3) = 0.333333… → raw = 0.658333… → 0.6583

    boost=0.0 时 days=1 → 仅取消降权 → raw = 0.325。
    """
    huge = DEFAULT_ADAPTIVE_CONFIG.replace(priority_wrong_boost=5.0)
    assert huge.priority_wrong_boost == 1.0
    big = _score_of(
        [_item()], {"a": {"recent_attempts": 2, "recent_correct": 1}}, adaptive_config=huge
    )
    assert big == 0.6583

    zero = DEFAULT_ADAPTIVE_CONFIG.replace(priority_wrong_boost=0.0)
    flat = _score_of(
        [_item(days_ago=1)],
        {"a": {"recent_attempts": 2, "recent_correct": 0}},
        adaptive_config=zero,
    )
    assert flat == 0.325


def test_wrong_saturation_divisor_golden():
    """满分所需错题数可配置：saturation=2 时 wrong=1 即半程、wrong=2 打满。

    手算：wrong=1 → 0.25×1/2 = 0.125 → raw = 0.45；
    wrong=2 → 0.25×1 = 0.25 → raw = 0.575。
    """
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_wrong_saturation=2.0)
    half = _score_of(
        [_item()], {"a": {"recent_attempts": 2, "recent_correct": 1}}, adaptive_config=config
    )
    full = _score_of(
        [_item()], {"a": {"recent_attempts": 2, "recent_correct": 0}}, adaptive_config=config
    )
    assert half == 0.45
    assert full == 0.575


def test_wrong_saturation_dirty_falls_back_to_three():
    """saturation 配 0/负数触发除零保护，回退缺省 3.0（真值同打满曲线）。"""
    config = build_adaptive_config({"adaptive.priority.wrong_saturation": "0"})
    assert config.priority_wrong_saturation == 3.0
    result = _score_of(
        [_item()], {"a": {"recent_attempts": 3, "recent_correct": 0}}, adaptive_config=config
    )
    assert result == 0.575  # 0.25×min(1, 3/3) 打满


# ============================================================ C2 饱和曲线手算
def test_c2_cap_boundary_golden():
    """C2 阈值边界：attempts=10 不抑制（> 语义），11 起乘 0.85。

    手算：base=0.325；10 → 0.325；11 → 0.325×0.85 = 0.27625。
    双精度下 ``0.325*0.85`` 的表示恰好落在 round4 中点或其下，
    ``round(0.27625, 4)`` 走银行家舍入得 **0.2762**（已用 IEEE754 核签）。
    """
    at_cap = _score_of([_item()], {"a": {"recent_attempts": 10, "recent_correct": 10}})
    over_cap = _score_of([_item()], {"a": {"recent_attempts": 11, "recent_correct": 11}})
    assert at_cap == 0.325
    assert over_cap == 0.2762


def test_c2_with_stale_adds_before_multiplies_golden():
    """C2 乘在 (BASE+STALE) 上、不加在 BOOST 上：先加后乘，BOOST 乘法后追加。

    手算（days=28 → stale=1.0；attempts=12 correct=6 → wrong=6 rate=0.5）::

        raw = (0.325 + 1.0) × 1.0 × 0.85 + 0.25×min(1, 6/3)
            = 1.325×0.85 + 0.25 = 1.12625 + 0.25 → clamp → 1.0

    换一组不触顶的（days=15 → stale=1/14；attempts=11 correct=4 →
    wrong=7 rate=7/11 ≥ 0.5 触发 C1）::

        raw = (0.325 + 1/14)×0.85 + 0.25×min(1, 7/3)
            = 0.336964… + 0.25 = 0.586964… → 0.587
    """
    saturated = _score_of(
        [_item(days_ago=28)], {"a": {"recent_attempts": 12, "recent_correct": 6}}
    )
    assert saturated == 1.0
    combined = _score_of(
        [_item(days_ago=15)], {"a": {"recent_attempts": 11, "recent_correct": 4}}
    )
    assert combined == 0.587


def test_c2_penalty_clamped_both_ends_golden():
    """抑制系数夹取：配 0 → 超量即 0 分；配 2 → 钳 1.0（关闭抑制）。

    手算：penalty=0 时 attempts=11 → raw = 0.325×0 = 0.0；
    penalty=2 → clamp 1.0 → 0.325。
    """
    killed = DEFAULT_ADAPTIVE_CONFIG.replace(priority_saturation_penalty=0.0)
    off = DEFAULT_ADAPTIVE_CONFIG.replace(priority_saturation_penalty=2.0)
    assert off.priority_saturation_penalty == 1.0
    dead = _score_of(
        [_item()], {"a": {"recent_attempts": 11, "recent_correct": 11}}, adaptive_config=killed
    )
    alive = _score_of(
        [_item()], {"a": {"recent_attempts": 11, "recent_correct": 11}}, adaptive_config=off
    )
    assert dead == 0.0
    assert alive == 0.325


def test_c2_cap_configurable_golden():
    """C2 阈值可配置：cap=5 时 attempts=6 即抑制。

    手算：0.325×0.85 = 0.27625 → round4 银行家舍入 = 0.2762（同边界用例）。
    """
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_recent_attempts_cap=5)
    result = _score_of(
        [_item()], {"a": {"recent_attempts": 6, "recent_correct": 6}}, adaptive_config=config
    )
    assert result == 0.2762


def test_c2_cap_zero_disables_suppression():
    """cap=0 时 C2 整体关闭（cap > 0 才生效），超量也不抑制。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_recent_attempts_cap=0)
    result = _score_of(
        [_item()], {"a": {"recent_attempts": 99, "recent_correct": 99}}, adaptive_config=config
    )
    assert result == 0.325


# ============================================================ 脏值与不变量
def test_wrong_uses_floor_not_round_golden():
    """错题数用 floor：attempts=3 correct=1.5 → wrong=1（1/3 < 0.5 不触发）。

    手算：不触发 → 裸 BASE 0.325（无时间维度）；
    若误用 round(1.5)=2 → 2/3 ≥ 0.5 会误触发 → 0.4917（红）。
    """
    floored = _score_of([_item()], {"a": {"recent_attempts": 3, "recent_correct": 1.5}})
    assert floored == 0.325
    # 对照组：attempts=4 correct=1.5 → floor(2.5)=2 → 触发，BOOST=0.25×2/3
    triggered = _score_of([_item()], {"a": {"recent_attempts": 4, "recent_correct": 1.5}})
    assert triggered == 0.4917


def test_correct_clamped_never_negative_wrong():
    """recent_correct 越界被夹紧：99 → 夹到 attempts（wrong=0，C1 不触发）；
    -5 → 夹到 0（wrong=attempts，C1 触发且 BOOST 按比例爬坡）。

    手算：attempts=2 correct=99 → wrong=0 → 裸 BASE 0.325；
    attempts=2 correct=-5 → correct 夹 0 → wrong=2 rate=1.0 →
    BOOST = 0.25×min(1, 2/3) = 0.166666… → raw = 0.491666… → 0.4917。
    """
    over = _score_of([_item()], {"a": {"recent_attempts": 2, "recent_correct": 99}})
    assert over == 0.325
    negative = _score_of([_item()], {"a": {"recent_attempts": 2, "recent_correct": -5}})
    assert negative == 0.4917


def test_zero_attempts_is_full_noop_golden():
    """attempts=0 / 缺失 / 脏值 → 与不传 results 逐位一致（硬门禁数值版）。

    基准手算（mastery=0.4, confidence=0.7, days=2）::

        BASE = 0.6×0.40 + 0.3×0.25 = 0.315
        raw  = 0.315 × (2+1)/(7+1) = 0.118125 → 0.1181
    """
    items = [_item(mastery=0.4, confidence=0.7, days_ago=2)]
    baseline = _score_of(items)
    assert baseline == 0.1181
    for dirty in (
        {"a": {"recent_attempts": 0, "recent_correct": 0}},
        {"a": {"recent_attempts": 0, "recent_correct": 9}},
        {"a": {}},
        {"a": None},
        {"a": "不是字典"},
        {},
        None,
        "bad",
        42,
    ):
        assert _score_of(items, dirty) == baseline, dirty


def test_other_point_results_do_not_leak():
    """results 里只有别的知识点 → 该点评分不受影响（含降权保留）。"""
    items = [_item(days_ago=1)]
    baseline = _score_of(items)
    leaked = _score_of(items, {"zzz": {"recent_attempts": 20, "recent_correct": 0}})
    assert leaked == baseline == 0.0813


def test_boost_over_one_cannot_flatten_ranking():
    """BOOST 封顶 1.0 后排序仍由分数差驱动：3 题全错 > 1 题错。

    手算（wrong_boost=5 → 钳 1.0）：attempts=2 correct=1 → wrong=1 rate=0.5 →
    BOOST = 1/3 → 0.325+0.333333… = 0.6583；
    attempts=6 correct=3 → wrong=3 → BOOST = 1.0 → 1.325 → clamp → 1.0。
    两者不并列（若 boost 不封顶，两者都会被最终 clamp 拉成 1.0）。
    """
    config = DEFAULT_ADAPTIVE_CONFIG.replace(priority_wrong_boost=5.0)
    one = _score_of(
        [_item("a")], {"a": {"recent_attempts": 2, "recent_correct": 1}}, adaptive_config=config
    )
    three = _score_of(
        [_item("b")], {"b": {"recent_attempts": 6, "recent_correct": 3}}, adaptive_config=config
    )
    assert one == 0.6583
    assert three == 1.0
    assert one < three


def test_score_always_in_unit_interval_golden():
    """无论信号多强，得分恒在 [0,1]（clamp 真值）。"""
    config = DEFAULT_ADAPTIVE_CONFIG.replace(
        priority_wrong_boost=5.0, priority_saturation_penalty=0.0
    )
    item = _item(mastery=0.0, confidence=0.0, days_ago=90)
    result = rank_priorities(
        [item],
        results={"a": {"recent_attempts": 3, "recent_correct": 0}},
        adaptive_config=config,
        now=_NOW,
    )[0]
    assert 0.0 <= result.score <= 1.0


def test_reason_priority_and_exact_text_with_results():
    """带 results 时理由优先级：gap > 回炉 > 陈旧，文案逐字写死。"""
    from datetime import timedelta

    stale_time = (_NOW - timedelta(days=30)).isoformat()
    items = [
        {
            "knowledge_point_id": "a",
            "kp_name": "分数加减法",
            "mastery": 0.5,
            "confidence": 0.5,
            "level": 1,
            "last_practiced_at": stale_time,
        }
    ]
    results = {"a": {"recent_attempts": 4, "recent_correct": 1}}
    plain = rank_priorities(items, results=results, now=_NOW)[0]
    assert plain.reason == "「分数加减法」刚做过 4 题错了 3 题，今天先回炉订正。"
    with_gap = rank_priorities(items, [{"pre_kp_id": "a"}], results=results, now=_NOW)[0]
    assert with_gap.reason == "「分数加减法」是未掌握的前置基础，建议先补牢再往后学。"


def test_contract_and_config_registration_golden():
    """出参契约 + 5 个 C1/C2 配置键全部登记且缺省值写死。"""
    item: PriorityItem = rank_priorities(
        [_item()], results={"a": {"recent_attempts": 2, "recent_correct": 0}}, now=_NOW
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
    defaults = DEFAULT_ADAPTIVE_CONFIG
    assert defaults.priority_wrong_rate_floor == 0.5
    assert defaults.priority_wrong_boost == 0.25
    assert defaults.priority_wrong_saturation == 3.0
    assert defaults.priority_recent_attempts_cap == 10
    assert defaults.priority_saturation_penalty == 0.85

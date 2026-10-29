"""六维掌握度评分引擎单测（C02 · Phase 1）。

纪律：纯函数、手算 golden 真值、覆盖降采样为 None 的守卫、覆盖错因口径切换。
不碰 DB、不设 DATABASE_URL（conftest 默认 :memory:）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.core.error_categories import ERROR_CALC, ERROR_KNOWLEDGE
from app.engines.config import (
    DEFAULT_MASTERY_DIM_CONFIG,
    MasteryDimConfig,
    build_mastery_dim_config,
)
from app.engines.mastery_sixdim import (
    RetentionEvent,
    SixDimEvent,
    six_dim_mastery,
    six_dim_mastery_for_student,
)

# 固定基准时刻，避免依赖系统时钟
NOW = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)


def _ev(kp_id, correct, *, days_ago=0, error_type=None, independent=False, transfer=False, weight=1.0):
    occurred = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    if days_ago:
        from datetime import timedelta
        occurred = occurred - timedelta(days=days_ago)
    return SixDimEvent(
        kp_id=kp_id,
        is_correct=correct,
        occurred_at=occurred,
        weight=weight,
        error_type=error_type,
        independent=independent,
        transfer=transfer,
    )


def _round4(x):
    return round(float(x), 4)


def test_d0_reuse_bayesian_smoothing():
    """D0 整体掌握度复用 compute_mastery：4 次近期全对 → 手算 (0.5*3+1.0*4)/(3+4)。"""
    # 4 recent correct, decay=1, eff=4, acc=1.0, prior=0.5, k=3
    # score = (1.5 + 4.0) / (3 + 4) = 5.5/7 ≈ 0.7857
    res = six_dim_mastery("k1", [_ev("k1", True) for _ in range(4)], now=NOW)
    assert res.mastery == _round4(Decimal("5.5") / Decimal("7")), res.mastery
    assert res.samples == 4
    # D1/D2 在全对下也应为 0.7857（无错因折扣）
    assert res.concept == res.mastery
    assert res.procedure == res.mastery


def test_d1_concept_deficit_zero_credit():
    """D1 概念硬伤(KNOWLEDGE)给 0 信用：1 对 + 2 个 KNOWLEDGE 错。

    事件全近期 decay=1，eff=3：
      D1 credit = 1.0 + 0.0 + 0.0 = 1.0, acc=1.0 → (1.5+1.0)/(3+3)=2.5/6≈0.4167
      D2 credit = 1.0 + slip(0.5) + slip(0.5) = 2.0 → (1.5+2.0)/6=3.5/6≈0.5833
      D0 = 1 对 / 3, acc=0.333 → (1.5+1.0)/6=2.5/6≈0.4167
    """
    events = [
        _ev("k1", True),
        _ev("k1", False, error_type=ERROR_KNOWLEDGE),
        _ev("k1", False, error_type=ERROR_KNOWLEDGE),
    ]
    res = six_dim_mastery("k1", events, now=NOW)
    assert res.mastery == _round4(Decimal("2.5") / Decimal("6")), res.mastery
    assert res.concept == _round4(Decimal("2.5") / Decimal("6")), res.concept
    assert res.procedure == _round4(Decimal("3.5") / Decimal("6")), res.procedure
    assert res.independent is None
    assert res.transfer is None
    assert res.retention is None


def test_d2_procedure_deficit_zero_credit():
    """D2 程序硬伤(CHECK/CALC)给 0 信用：1 对 + 2 个 CALC 错。

      D1 credit = 1.0 + slip(0.5) + slip(0.5) = 2.0 → (1.5+2.0)/6=3.5/6≈0.5833
      D2 credit = 1.0 + 0.0 + 0.0 = 1.0 → (1.5+1.0)/6=2.5/6≈0.4167
    """
    events = [
        _ev("k1", True),
        _ev("k1", False, error_type=ERROR_CALC),
        _ev("k1", False, error_type=ERROR_CALC),
    ]
    res = six_dim_mastery("k1", events, now=NOW)
    assert res.concept == _round4(Decimal("3.5") / Decimal("6")), res.concept
    assert res.procedure == _round4(Decimal("2.5") / Decimal("6")), res.procedure


def test_d3_independent_threshold_and_floor():
    """D3 独立完成：仅 independent 标记事件计入；未达 min_samples 判 None。"""
    # 3 次独立做对 → eff=3>=3 → d3 = (1.5+3.0)/(3+3)=4.5/6=0.75
    ok_events = [_ev("k1", True, independent=True) for _ in range(3)]
    res = six_dim_mastery("k1", ok_events, now=NOW)
    assert res.independent == _round4(Decimal("4.5") / Decimal("6")), res.independent

    # 2 次独立做对 → eff=2<3 → 样本不足降级为 None（守卫）
    few_events = [_ev("k1", True, independent=True) for _ in range(2)]
    res2 = six_dim_mastery("k1", few_events, now=NOW)
    assert res2.independent is None, res2.independent


def test_d4_transfer_threshold_and_floor():
    """D4 迁移应用：仅 transfer 标记事件；未达阈值判 None。"""
    ok_events = [_ev("k1", True, transfer=True) for _ in range(3)]
    res = six_dim_mastery("k1", ok_events, now=NOW)
    assert res.transfer == _round4(Decimal("4.5") / Decimal("6")), res.transfer

    none_events = [_ev("k1", True, transfer=True) for _ in range(2)]
    res2 = six_dim_mastery("k1", none_events, now=NOW)
    assert res2.transfer is None, res2.transfer


def test_d5_retention_recall_and_floor():
    """D5 保持留存：仅复习召回事件；无事件判 None，>=min_samples 才给值。"""
    # 无复习事件 → None
    res_none = six_dim_mastery("k1", [_ev("k1", True)], now=NOW)
    assert res_none.retention is None, res_none.retention

    # 3 次复习全 recall 正确 → eff=3>=3 → (1.5+3.0)/6=0.75
    ret = [RetentionEvent("k1", True, NOW) for _ in range(3)]
    res = six_dim_mastery("k1", [_ev("k1", True)], retention=ret, now=NOW)
    assert res.retention == _round4(Decimal("4.5") / Decimal("6")), res.retention

    # 2 次复习 → 样本不足 → None
    ret2 = [RetentionEvent("k1", True, NOW) for _ in range(2)]
    res2 = six_dim_mastery("k1", [_ev("k1", True)], retention=ret2, now=NOW)
    assert res2.retention is None, res2.retention


def test_d1_sample_floor_returns_none():
    """守卫：D1 有效样本 < min_samples(3) → 概念维度判 None（不编造 0）。"""
    # 1 对 + 1 个 KNOWLEDGE 错：eff=2 < 3
    events = [_ev("k1", True), _ev("k1", False, error_type=ERROR_KNOWLEDGE)]
    res = six_dim_mastery("k1", events, now=NOW)
    assert res.concept is None, res.concept
    # D0 仍有值（2 次练习足够算整体）
    assert res.mastery is not None


def test_decay_weights_older_events_lower():
    """时效衰减：1 次当前对 + 1 次 14 天前对 → 手算 (1.5 + 1.5)/(3 + 1.5)=0.6667。

    14 天前 decay=0.5，eff=1.0+0.5=1.5，acc=1.5 → score=(1.5+1.5)/(3+1.5)=3/4.5=0.6667
    """
    events = [_ev("k1", True), _ev("k1", True, days_ago=14)]
    res = six_dim_mastery("k1", events, now=NOW)
    assert res.mastery == _round4(Decimal("3") / Decimal("4.5")), res.mastery


def test_concept_deficit_config_switch_changes_credit():
    """错因口径可配：把 concept_deficit 改成 CALC 后，KNOWLEDGE 错不再给 D1 硬伤 0 分。"""
    events = [
        _ev("k1", True),
        _ev("k1", False, error_type=ERROR_KNOWLEDGE),
        _ev("k1", False, error_type=ERROR_KNOWLEDGE),
    ]
    # 默认 concept_deficit=KNOWLEDGE → D1≈0.4167（见 test_d1）
    default_res = six_dim_mastery("k1", events, now=NOW)
    # 切到 CALC → KNOWLEDGE 错变 slip(0.5)：credit=1.0+0.5+0.5=2.0 → (1.5+2.0)/6=0.5833
    cfg = MasteryDimConfig(concept_deficit=ERROR_CALC)
    switched_res = six_dim_mastery("k1", events, now=NOW, config=cfg)
    assert default_res.concept == _round4(Decimal("2.5") / Decimal("6"))
    assert switched_res.concept == _round4(Decimal("3.5") / Decimal("6")), switched_res.concept


def test_min_samples_boundary():
    """守卫边界：恰好等于 min_samples 通过，少 1 判 None。"""
    cfg = MasteryDimConfig(min_samples=3)
    # 3 个独立对 → 通过
    three = six_dim_mastery(
        "k1", [_ev("k1", True, independent=True) for _ in range(3)],
        now=NOW, config=cfg,
    )
    assert three.independent is not None
    # 2 个独立对 → None
    two = six_dim_mastery(
        "k1", [_ev("k1", True, independent=True) for _ in range(2)],
        now=NOW, config=cfg,
    )
    assert two.independent is None


def test_for_student_groups_by_kp():
    """six_dim_mastery_for_student 按 kp_id 分组，返回每点六维。"""
    events = [
        _ev("ka", True),
        _ev("ka", False, error_type=ERROR_KNOWLEDGE),
        _ev("kb", True),
        _ev("kb", True),
    ]
    result = six_dim_mastery_for_student(events, now=NOW)
    assert set(result.keys()) == {"ka", "kb"}
    assert result["ka"].samples == 2
    assert result["kb"].samples == 2
    # kb 全对（2 次近期全对）→ (0.5*3+1.0*2)/(3+2)=3.5/5=0.7
    assert result["kb"].mastery == _round4(Decimal("3.5") / Decimal("5"))


def test_build_config_from_rows():
    """配置经 build_mastery_dim_config 注入：短写 key 也能识别，脏值回退缺省。"""
    rows = {
        "mastery.dim.min_samples": "5",
        "mastery.dim.slip_credit": "0.25",
    }
    cfg = build_mastery_dim_config(rows)
    assert cfg.min_samples == 5
    assert cfg.slip_credit == 0.25
    # 未提供的字段保持缺省
    assert cfg.prior == DEFAULT_MASTERY_DIM_CONFIG.prior
    assert cfg.half_life == DEFAULT_MASTERY_DIM_CONFIG.half_life


def test_default_config_clamps_dirty_values():
    """配置夹紧：脏值（负数 / 越界）回退缺省，不把评分算飞。"""
    cfg = MasteryDimConfig(min_samples=-1, slip_credit=2.0, prior=-0.5)
    assert cfg.min_samples == 0
    assert cfg.slip_credit == 1.0
    assert cfg.prior == 0.0


def test_subminute_event_treated_fresh_not_degraded():
    """回归守卫：事件比 now 晚数秒（子分钟级）不得因浮点衰减被误降级。

    背景：recompute 在每日 cron 跑，事件通常发生在几毫秒~几十秒前。
    若事件时间略晚于 now（或 now 略晚于事件），衰减应取 1.0；否则 N 个事件
    的有效样本浮点略小于 N，恰好踩中 min_samples 阈值会把维度判成 None。

    构造：3 个独立正确事件（= 默认 min_samples），occurred_at = now - 30 秒。
    修复前 → decay≈0.99999998 → 有效样本≈2.99999994 < 3 → independent=None；
    修复后 → 子分钟归零 → decay=1 → 有效样本=3 → independent 应被算出。
    """
    from datetime import timedelta

    occurred = NOW - timedelta(seconds=30)
    events = [
        SixDimEvent(
            kp_id="k1",
            is_correct=True,
            occurred_at=occurred,
            weight=1.0,
            independent=True,
        )
        for _ in range(3)
    ]
    res = six_dim_mastery("k1", events, now=NOW, config=DEFAULT_MASTERY_DIM_CONFIG)
    # 3 个独立正确、无错因 → independent 应为 (0.5*3 + 3) / (3 + 3) = 4.5/6 = 0.75
    assert res.independent is not None, "子分钟事件不应被误降级为 None"
    assert res.independent == _round4(Decimal("4.5") / Decimal("6")), res.independent
    # 同时 D1/D2 也应算出（4 个事件维度的总样本 >= min_samples）
    assert res.concept is not None
    assert res.procedure is not None

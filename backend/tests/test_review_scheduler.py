"""#65 复习排程（间隔重复）纯算法单测。

不依赖方舟、不依赖 DB，纯本地规则。覆盖：
    * SM-2 三档间隔（第1/2/n 次）；
    * 没记住（q<3）回退 + EF 下调 + 下限钳制；
    * is_due / select_due 到期挑选；
    * quality_from_correct 从「对错 + verdict」收敛。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.review_scheduler import (
    FIRST_INTERVAL_DAYS,
    SECOND_INTERVAL_DAYS,
    MIN_EASE_FACTOR,
    ReviewState,
    initial_state,
    is_due,
    quality_from_correct,
    review,
    select_due,
    update_ease_factor,
)

NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def test_first_review_quality5_sets_interval_1_and_raises_ef():
    st = initial_state("CZ_POWER", now=NOW)
    plan = review(st, 5, now=NOW)
    assert plan.repetitions == 1
    assert plan.interval_days == FIRST_INTERVAL_DAYS
    assert plan.ease_factor == 2.6  # 2.5 + 0.1
    assert plan.due_at == NOW.replace(day=14)  # +1 天
    assert plan.lapsed is False


def test_second_review_quality5_sets_interval_6():
    st = ReviewState(key="CZ_POWER", ease_factor=2.6, interval_days=1, repetitions=1,
                     last_reviewed_at=NOW, due_at=NOW)
    plan = review(st, 5, now=NOW)
    assert plan.repetitions == 2
    assert plan.interval_days == SECOND_INTERVAL_DAYS
    assert plan.due_at == NOW.replace(day=19)  # +6 天
    assert plan.lapsed is False


def test_third_review_grows_interval_by_ef():
    # 第2次后 ef=2.6, interval=6；第3次 quality5 → interval=round(6*2.6)=16
    st = ReviewState(key="CZ_POWER", ease_factor=2.6, interval_days=6, repetitions=2,
                     last_reviewed_at=NOW, due_at=NOW)
    plan = review(st, 5, now=NOW)
    assert plan.repetitions == 3
    assert plan.interval_days == 16
    assert plan.ease_factor == 2.7


def test_lapse_quality2_resets_repetitions_and_clamps_interval():
    st = ReviewState(key="CZ_POWER", ease_factor=2.6, interval_days=16, repetitions=5,
                     last_reviewed_at=NOW, due_at=NOW)
    plan = review(st, 2, now=NOW)
    assert plan.lapsed is True
    assert plan.repetitions == 0
    assert plan.interval_days == 1
    # EF 下调：2.6 + (0.1 - 3*(0.08+3*0.02)) = 2.6 + (0.1 - 0.42) = 2.28
    assert plan.ease_factor == 2.28


def test_ease_factor_clamped_to_min():
    # 连续低质量后 EF 不应低于 1.3
    ef = 1.31
    for _ in range(5):
        ef = update_ease_factor(ef, 0)
    assert ef == MIN_EASE_FACTOR


def test_is_due_rules():
    # 从未复习 → 立即到期
    assert is_due(initial_state("X", now=NOW), now=NOW) is True
    # 未来到期 → 不到期
    future = ReviewState(key="X", due_at=NOW.replace(day=20))
    assert is_due(future, now=NOW) is False
    # 已过期 → 到期
    past = ReviewState(key="X", due_at=NOW.replace(day=10))
    assert is_due(past, now=NOW) is True


def test_select_due_orders_by_due_at():
    # 注意：NOW=2026-08-13，故 future 项（>13日）会被 is_due 正确排除。
    # 这里全部用过去/None 的 due_at，专门验证「按到期时间升序」的排序。
    a = ReviewState(key="A", due_at=NOW.replace(day=8))    # 5天前到期
    b = ReviewState(key="B", due_at=NOW.replace(day=3))    # 10天前到期（最早）
    c = ReviewState(key="C", due_at=NOW.replace(day=12))   # 1天前到期
    never = initial_state("D", now=NOW)  # due_at=None → 立即到期，排最前
    due = select_due([a, b, c, never], now=NOW)
    keys = [s.key for s in due]
    # 排序：None(min) < day3(B) < day8(A) < day12(C)
    assert keys == ["D", "B", "A", "C"]


def test_quality_from_correct_mapping():
    assert quality_from_correct(False, "wrong") == 1
    assert quality_from_correct(True, "correct") == 5
    assert quality_from_correct(True, "pending") == 3
    assert quality_from_correct(True, "unknown") == 3

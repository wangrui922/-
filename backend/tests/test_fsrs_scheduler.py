"""方向三 FSRS 调度器契约测试（纯算法层，不依赖 DB）。

覆盖（docs/regression-followup-20261001.md 方向三验收口径）：
    * 调度算法确定性断言：rating → quality 映射、间隔增长、EF（difficulty）演化；
    * 状态迁移：New → Learning → Review / Relearning；
    * 边界：首次复习、多次复习、遗忘重置（lapse）、elapsed_days、retrievability；
    * 不修改入参（不可变语义）与 ReviewLog 字段完整性。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.fsrs_scheduler import (
    DEFAULT_QUALITY,
    RATING_TO_QUALITY,
    STATE_LEARNING,
    STATE_NEW,
    STATE_RELEARNING,
    STATE_REVIEW,
    Card,
    FSRSScheduler,
    ReviewLog,
)

NOW = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)


def _card(**kw) -> Card:
    base = dict(
        id="c1",
        due=None,
        stability=0.0,
        difficulty=0.0,
        elapsed_days=0,
        scheduled_days=0,
        reps=0,
        lapses=0,
        state=STATE_NEW,
        last_review=None,
    )
    base.update(kw)
    return Card(**base)


# ================================================================ 确定性断言
class TestRatingMapping:
    def test_rating_to_quality_table(self):
        """1=Again→1, 2=Hard→3, 3=Good→4, 4=Easy→5（仅 1 为遗忘，其余成功）。"""
        assert RATING_TO_QUALITY == {1: 1, 2: 3, 3: 4, 4: 5}

    @pytest.mark.parametrize(
        ("rating", "expected_quality"),
        [(1, 1), (2, 3), (3, 4), (4, 5)],
    )
    def test_rating_to_quality(self, rating, expected_quality):
        from app.services.fsrs_scheduler import _rating_to_quality

        assert _rating_to_quality(rating) == expected_quality

    def test_invalid_rating_defaults_to_good(self):
        from app.services.fsrs_scheduler import _rating_to_quality

        assert _rating_to_quality(99) == DEFAULT_QUALITY == 4


class TestFirstReview:
    def test_first_good_enters_learning_interval1(self):
        """首次 Good：state→Learning(1)，reps=1，间隔 1 天，stability=1。"""
        s = FSRSScheduler()
        card, log = s.review_card(_card(), 3, NOW)
        assert card.state == STATE_LEARNING
        assert card.reps == 1
        assert card.scheduled_days == 1
        assert card.stability == 1.0
        assert card.lapses == 0
        assert card.due == NOW + timedelta(days=1)
        # difficulty = EF（SM-2 Good → EF 不变 2.5）
        assert card.difficulty == 2.5

    def test_first_easy_raises_ef(self):
        """首次 Easy：EF 2.5→2.6，difficulty 编码 EF。"""
        s = FSRSScheduler()
        card, _ = s.review_card(_card(), 4, NOW)
        assert card.difficulty == 2.6
        assert card.scheduled_days == 1  # 首次间隔仍 1 天（SM-2）

    def test_first_again_is_lapse(self):
        """首次 Again：quality=1 <3 → 遗忘重置，state→Relearning(3)，lapses=1。"""
        s = FSRSScheduler()
        card, log = s.review_card(_card(), 1, NOW)
        assert card.state == STATE_RELEARNING
        assert card.reps == 0
        assert card.lapses == 1
        assert card.scheduled_days == 1  # 回退 LAPSE_INTERVAL_DAYS
        assert card.difficulty < 2.5  # EF 下调


class TestMultipleReviews:
    def _good_good(self):
        s = FSRSScheduler()
        c1, _ = s.review_card(_card(), 3, NOW)
        c2, _ = s.review_card(c1, 3, NOW + timedelta(days=1))
        return s, c1, c2

    def test_second_good_review_state_interval6(self):
        """第二次成功：state→Review(2)，间隔 6 天，elapsed=1。"""
        _, _, c2 = self._good_good()
        assert c2.state == STATE_REVIEW
        assert c2.reps == 2
        assert c2.scheduled_days == 6
        assert c2.stability == 6.0
        assert c2.elapsed_days == 1

    def test_third_good_interval_scales_by_ef(self):
        """第三次成功：间隔=round(6*EF)；Good（EF 不变 2.5）→ 15。"""
        s, _, c2 = self._good_good()
        c3, _ = s.review_card(c2, 3, NOW + timedelta(days=7))
        assert c3.reps == 3
        assert c3.scheduled_days == 15  # round(6 * 2.5)
        assert c3.stability == 15.0

    def test_elapsed_days_tracks_gap(self):
        """elapsed_days = 距上次复习的自然日。"""
        s = FSRSScheduler()
        c1, _ = s.review_card(_card(), 3, NOW)
        c2, _ = s.review_card(c1, 3, NOW + timedelta(days=5))
        assert c2.elapsed_days == 5


class TestForgettingReset:
    def test_lapse_resets_and_enters_relearning(self):
        """遗忘重置：reps=0、lapses+1、间隔回退 1、state→Relearning。"""
        s = FSRSScheduler()
        c1, _ = s.review_card(_card(), 3, NOW)
        c2, _ = s.review_card(c1, 3, NOW + timedelta(days=1))
        c3, _ = s.review_card(c2, 1, NOW + timedelta(days=10))  # Again
        assert c3.reps == 0
        assert c3.lapses == 1
        assert c3.state == STATE_RELEARNING
        assert c3.scheduled_days == 1
        assert c3.difficulty < 2.5  # EF 下调

    def test_relearn_success_starts_over(self):
        """遗忘后再次成功：重新从 Learning 起步（reps=1、间隔 1）。"""
        s = FSRSScheduler()
        c1, _ = s.review_card(_card(), 3, NOW)
        c2, _ = s.review_card(c1, 3, NOW + timedelta(days=1))
        c3, _ = s.review_card(c2, 1, NOW + timedelta(days=10))
        c4, _ = s.review_card(c3, 3, NOW + timedelta(days=11))
        assert c4.state == STATE_LEARNING  # 重学后首次成功 → Learning
        assert c4.reps == 1
        assert c4.scheduled_days == 1
        assert c4.lapses == 1  # lapses 累计不回退


# ================================================================ 遗忘曲线
class TestRetrievability:
    def test_at_due_time_returns_0_9(self):
        """t = stability 时 R ≈ 0.9（到期边界）。"""
        s = FSRSScheduler()
        r = s.get_retrievability(
            Card(stability=6.0, last_review=NOW), NOW + timedelta(days=6)
        )
        assert r == pytest.approx(0.9, abs=1e-4)

    def test_immediately_after_review_returns_1(self):
        s = FSRSScheduler()
        r = s.get_retrievability(Card(stability=6.0, last_review=NOW), NOW)
        assert r == pytest.approx(1.0)

    def test_zero_stability_returns_0(self):
        s = FSRSScheduler()
        assert s.get_retrievability(Card(stability=0.0, last_review=NOW), NOW) == 0.0

    def test_no_last_review_returns_0(self):
        s = FSRSScheduler()
        assert s.get_retrievability(Card(stability=6.0, last_review=None), NOW) == 0.0

    def test_monotonic_decreasing(self):
        """随时间推移 R 单调下降。"""
        s = FSRSScheduler()
        base = Card(stability=10.0, last_review=NOW)
        r1 = s.get_retrievability(base, NOW + timedelta(days=1))
        r2 = s.get_retrievability(base, NOW + timedelta(days=5))
        assert r1 > r2


# ================================================================ 契约健壮性
class TestContract:
    def test_review_card_does_not_mutate_input(self):
        """review_card 返回新对象，不修改入参。"""
        s = FSRSScheduler()
        original = _card(state=STATE_NEW, reps=0)
        before = (original.state, original.reps, original.scheduled_days)
        s.review_card(original, 3, NOW)
        assert (original.state, original.reps, original.scheduled_days) == before

    def test_review_log_fields_complete(self):
        """ReviewLog 覆盖 review_logs 表契约字段。"""
        s = FSRSScheduler()
        card, log = s.review_card(_card(), 3, NOW)
        assert isinstance(log, ReviewLog)
        assert log.rating == 3
        assert log.state == card.state
        assert log.elapsed_days == card.elapsed_days
        assert log.scheduled_days == card.scheduled_days
        assert log.stability_before == 0.0
        assert log.stability_after == card.stability
        assert log.difficulty_before == 0.0
        assert log.difficulty_after == card.difficulty

    def test_card_defaults_match_review_cards_schema(self):
        """Card 无参构造默认值与 013 迁移列默认对齐。"""
        c = Card()
        assert c.id is None
        assert c.due is None
        assert c.stability == 0.0
        assert c.difficulty == 0.0
        assert c.elapsed_days == 0
        assert c.scheduled_days == 0
        assert c.reps == 0
        assert c.lapses == 0
        assert c.state == STATE_NEW
        assert c.last_review is None

    def test_naive_datetime_accepted(self):
        """naive datetime 被视为 UTC，不与 aware 混算报错。"""
        s = FSRSScheduler()
        naive = datetime(2026, 10, 1, 8, 0, 0)
        c, _ = s.review_card(_card(), 3, naive)
        assert c.due.tzinfo is not None

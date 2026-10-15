"""py-fsrs 与既有 SM-2 双跑一致性（parity）测试。

背景（双版本裁决，详见 ``docs/scheduler-py-fsrs-migration.md``）：
    复习调度统一入口收敛到 ``app.services.fsrs_scheduler``，内核可在
    ``fsrs_py``（py-fsrs）与 ``sm2``（既有 SM-2）之间切换。切换前必须回答一个问题：
    **新内核排出来的复习间隔，和旧内核差多少？** 本文件用固定随机种子让两套内核
    双跑 1000 次，断言排程偏差 < 5%。

设计要点：
    * **固定种子**（``SEED = 20261001``）：结果可复现，失败可定位；
    * **同一 rating 流喂给两套内核**：消除输入差异，比的是算法本身；
    * **py-fsrs 未安装 → skip 而非 fail**：本机无外网时不应误报红灯
      （安装后自动生效：``pip install "py-fsrs>=5.0,<6"``）；
    * **注入桩的分支测试不依赖 py-fsrs**：即使没装包，也能验证「开关裁决 /
      灰度放量 / 未安装自动降级」三条链路，保证这部分永远有覆盖。

偏差口径（唯一权威定义）：
    对每个 trial 用同一条 rating 流跑完 N 次复习，取**最终 scheduled_days**；
    1000 个 trial 求均值，偏差 = ``|mean_pyfsrs - mean_sm2| / mean_sm2``。
    阈值 5%（``PYFSRS_PARITY_TOLERANCE``）是**校准哨兵**：若实测超标，
    处理方式是重新校准 py-fsrs 的学习步/目标保留率（见迁移文档「偏差超标怎么办」），
    **不是**放宽阈值。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from types import ModuleType
from typing import Callable, List, Optional, Tuple

import pytest

from app.services.fsrs_scheduler import (
    MODE_FSRS_PY,
    MODE_SM2,
    Card,
    FSRSScheduler,
    _pyfsrs_review_card,
    _sm2_review_card,
    pyfsrs_available,
    reset_pyfsrs_cache,
    resolve_scheduler_mode,
)

# ------------------------------------------------------------------- 固定参数
#: 固定随机种子（结果可复现）
SEED: int = 20261001
#: 双跑次数（需求：1000 次）
TRIALS: int = 1000
#: 偏差阈值（5%）
PYFSRS_PARITY_TOLERANCE: float = 0.05
#: 起始时间（UTC，固定）
START = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)
#: rating 抽样权重：Good 为主，少量 Hard/Easy/Again，贴近真实学生操作分布
RATING_POOL: Tuple[int, ...] = (3, 3, 3, 3, 3, 3, 4, 2, 3, 1)
#: 每个 trial 的复习链长度（短链：K-12 场景单张卡片几次复习即可进入稳定期）
CHAIN_MIN: int = 2
CHAIN_MAX: int = 4

#: py-fsrs 是否真的可用（未安装 → 双跑用例 skip）
PYFSRS_AVAILABLE: bool = pyfsrs_available()


# ==================================================================== 工具
def _walk(
    engine: Callable[[Card, int, datetime], Tuple[Card, object]],
    ratings: List[int],
) -> Tuple[int, List[int]]:
    """用给定内核跑完一条 rating 流，返回 ``(最终间隔, 每次间隔列表)``。"""
    card = Card(id="parity")
    now = START
    intervals: List[int] = []
    for rating in ratings:
        card, _log = engine(card, rating, now)
        intervals.append(int(card.scheduled_days))
        # 按排程推进到下次到期（+1 天避免 0 间隔导致原地踏步）
        now = now + timedelta(days=max(1, int(card.scheduled_days)))
    return int(card.scheduled_days), intervals


def _make_trials() -> List[List[int]]:
    """固定种子生成 1000 条 rating 流（与 random 全局状态隔离）。"""
    import random

    rng = random.Random(SEED)
    trials: List[List[int]] = []
    for _ in range(TRIALS):
        length = rng.randint(CHAIN_MIN, CHAIN_MAX)
        trials.append([rng.choice(RATING_POOL) for _ in range(length)])
    return trials


# ==================================================================== 双跑一致性
@pytest.mark.skipif(
    not PYFSRS_AVAILABLE,
    reason="py-fsrs 未安装（pip install \"py-fsrs>=5.0,<6\" 后本用例自动生效）",
)
def test_dual_run_1000_reviews_deviation_below_5pct():
    """新旧内核双跑 1000 次：平均排程偏差 < 5%。"""
    trials = _make_trials()
    sm2_finals: List[int] = []
    pyfsrs_finals: List[int] = []

    for ratings in trials:
        sm2_final, sm2_intervals = _walk(_sm2_review_card, ratings)
        py_final, py_intervals = _walk(_pyfsrs_review_card, ratings)

        # 逐 trial 的健壮性断言（任何内核都不该排出非正间隔）
        assert all(i > 0 for i in sm2_intervals), f"SM-2 排出非正间隔: {sm2_intervals}"
        assert all(i > 0 for i in py_intervals), f"py-fsrs 排出非正间隔: {py_intervals}"

        sm2_finals.append(sm2_final)
        pyfsrs_finals.append(py_final)

    mean_sm2 = sum(sm2_finals) / len(sm2_finals)
    mean_py = sum(pyfsrs_finals) / len(pyfsrs_finals)
    deviation = abs(mean_py - mean_sm2) / mean_sm2

    assert deviation < PYFSRS_PARITY_TOLERANCE, (
        f"双跑偏差 {deviation:.4%} 超过阈值 {PYFSRS_PARITY_TOLERANCE:.0%} "
        f"（SM-2 均值 {mean_sm2:.3f} 天 / py-fsrs 均值 {mean_py:.3f} 天，"
        f"trials={TRIALS}，seed={SEED}）。"
        f"请按 docs/scheduler-py-fsrs-migration.md 重新校准 py-fsrs 学习步/保留率，"
        f"不要放宽阈值。"
    )


@pytest.mark.skipif(
    not PYFSRS_AVAILABLE,
    reason="py-fsrs 未安装（pip install \"py-fsrs>=5.0,<6\" 后本用例自动生效）",
)
def test_pyfsrs_engine_is_deterministic():
    """同一输入两次调用结果完全一致（py-fsrs 已关闭 fuzzing）。"""
    ratings = [3, 3, 4, 3]
    first, intervals_1 = _walk(_pyfsrs_review_card, ratings)
    second, intervals_2 = _walk(_pyfsrs_review_card, ratings)
    assert intervals_1 == intervals_2
    assert first == second


# ============================================================ 注入桩：分支链路
class _FakeState(IntEnum):
    """桩：py-fsrs 的 State（取值与 fsrs.State 对齐）。"""

    New = 0
    Learning = 1
    Review = 2
    Relearning = 3


class _FakeRating(IntEnum):
    """桩：py-fsrs 的 Rating（1=Again 2=Hard 3=Good 4=Easy）。"""

    Again = 1
    Hard = 2
    Good = 3
    Easy = 4


@dataclass
class _FakeCard:
    """桩：py-fsrs 的 Card（字段与 fsrs.Card 对齐）。"""

    due: Optional[datetime] = None
    stability: float = 0.0
    difficulty: float = 5.0
    elapsed_days: int = 0
    scheduled_days: int = 0
    reps: int = 0
    lapses: int = 0
    state: _FakeState = _FakeState.New
    last_review: Optional[datetime] = None


class _FakeScheduler:
    """桩：py-fsrs 的 Scheduler。

    刻意实现一套**与 SM-2 不同**的简化增长曲线（Good ×3、Easy ×4、Hard ×1.5、
    Again 回退 1 天），这样「是否真的走了 py-fsrs 分支」可以通过数值辨别出来。
    """

    #: 构造参数记录（用于断言我们传的口径被采纳）
    last_kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        _FakeScheduler.last_kwargs = dict(kwargs)

    def review_card(
        self, card: _FakeCard, rating: _FakeRating, review_datetime: Optional[datetime] = None
    ) -> Tuple[_FakeCard, object]:
        now = review_datetime or datetime.now(timezone.utc)
        r = int(rating)
        if r == 1:  # Again → 遗忘，间隔回退 1 天
            return (
                _FakeCard(
                    due=now + timedelta(days=1),
                    stability=max(0.5, card.stability * 0.5),
                    difficulty=min(10.0, card.difficulty + 1.0),
                    scheduled_days=1,
                    reps=0,
                    lapses=card.lapses + 1,
                    state=_FakeState.Relearning,
                    last_review=now,
                ),
                object(),
            )
        growth = {2: 1.5, 3: 3.0, 4: 4.0}[r]
        base = card.scheduled_days if card.scheduled_days > 0 else 1
        scheduled = max(1, int(round(base * growth)))
        return (
            _FakeCard(
                due=now + timedelta(days=scheduled),
                stability=float(scheduled),
                difficulty=max(1.0, card.difficulty - 0.5),
                scheduled_days=scheduled,
                reps=card.reps + 1,
                lapses=card.lapses,
                state=_FakeState.Review if card.reps + 1 > 1 else _FakeState.Learning,
                last_review=now,
            ),
            object(),
        )


def _build_fake_fsrs_module() -> ModuleType:
    """造一个可被 ``from fsrs import ...`` 命中的桩模块。"""
    mod = ModuleType("fsrs")
    mod.Card = _FakeCard  # type: ignore[attr-defined]
    mod.Rating = _FakeRating  # type: ignore[attr-defined]
    mod.State = _FakeState  # type: ignore[attr-defined]
    mod.Scheduler = _FakeScheduler  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def fake_fsrs(monkeypatch):
    """把桩模块注入 ``sys.modules['fsrs']`` 并清空探测缓存。

    用例结束后由 monkeypatch 还原 ``sys.modules``，fixture 再清一次缓存，
    避免桩泄漏到其它用例。
    """
    reset_pyfsrs_cache()
    mod = _build_fake_fsrs_module()
    monkeypatch.setitem(sys.modules, "fsrs", mod)
    yield mod
    reset_pyfsrs_cache()


@pytest.fixture
def set_scheduler_mode(monkeypatch):
    """运行时改写调度开关（``Settings`` 单例属性），用例结束自动还原。"""

    def _apply(mode: str, ratio: float = 1.0) -> None:
        from app.core.config import get_settings

        settings_obj = get_settings()
        monkeypatch.setattr(settings_obj, "scheduler", mode)
        monkeypatch.setattr(settings_obj, "scheduler_fsrs_rollout_ratio", ratio)

    return _apply


class TestSchedulerModeResolution:
    """开关裁决与灰度放量（不依赖 py-fsrs 真身，用桩跑通分支）。"""

    def test_default_mode_is_sm2(self):
        """默认灰度基线必须是 SM-2（不允许默认就把学生切到新算法）。"""
        from app.core.config import Settings

        assert Settings().scheduler == "sm2"
        assert Settings().scheduler_mode == MODE_SM2

    def test_sm2_mode_never_uses_pyfsrs(self, fake_fsrs, set_scheduler_mode):
        """scheduler=sm2 → 裁决恒为 sm2，即使 py-fsrs 已装。"""
        set_scheduler_mode("sm2", ratio=1.0)
        assert resolve_scheduler_mode("student-1") == MODE_SM2

    def test_full_rollout_uses_pyfsrs(self, fake_fsrs, set_scheduler_mode):
        """scheduler=fsrs_py + ratio=1 → 全量 py-fsrs。"""
        set_scheduler_mode("fsrs_py", ratio=1.0)
        assert resolve_scheduler_mode("any-student") == MODE_FSRS_PY

    def test_zero_ratio_keeps_sm2(self, fake_fsrs, set_scheduler_mode):
        """scheduler=fsrs_py + ratio=0 → 仍全量 SM-2（开而不放）。"""
        set_scheduler_mode("fsrs_py", ratio=0.0)
        assert resolve_scheduler_mode("any-student") == MODE_SM2

    def test_partial_rollout_is_stable_and_balanced(
        self, fake_fsrs, set_scheduler_mode
    ):
        """ratio=0.5：命中 py-fsrs 的比例接近一半，且同一学生判定恒定。"""
        set_scheduler_mode("fsrs_py", ratio=0.5)
        students = [f"student-{i}" for i in range(200)]
        hit = [s for s in students if resolve_scheduler_mode(s) == MODE_FSRS_PY]
        ratio = len(hit) / len(students)
        assert 0.35 <= ratio <= 0.65, f"灰度比例失衡: {ratio:.2%}"
        # 稳定哈希：重复裁决结果一致
        assert [resolve_scheduler_mode(s) for s in students] == [
            resolve_scheduler_mode(s) for s in students
        ]
        assert all(resolve_scheduler_mode(s) == MODE_FSRS_PY for s in hit)

    def test_pyfsrs_missing_degrades_to_sm2(self, monkeypatch, set_scheduler_mode):
        """py-fsrs 未安装：即使开关打开也降级 SM-2，绝不阻断。"""
        monkeypatch.delitem(sys.modules, "fsrs", raising=False)
        reset_pyfsrs_cache()
        try:
            if pyfsrs_available():  # 真身装了就跳过（本用例只验降级路径）
                pytest.skip("py-fsrs 已安装，无法构造缺失场景")
            set_scheduler_mode("fsrs_py", ratio=1.0)
            assert resolve_scheduler_mode("student-1") == MODE_SM2
        finally:
            reset_pyfsrs_cache()

    def test_unknown_mode_falls_back_to_sm2(self, fake_fsrs, set_scheduler_mode):
        """开关写错值（如 FSRS-5）→ 归一化回落 sm2。"""
        set_scheduler_mode("fsrs-5", ratio=1.0)
        assert resolve_scheduler_mode("student-1") == MODE_SM2


class TestPyfsrsBranchBehaviour:
    """注入桩后，验证统一入口确实会走 py-fsrs 分支且异常可降级。"""

    def test_review_card_uses_pyfsrs_when_enabled(
        self, fake_fsrs, set_scheduler_mode
    ):
        """fsrs_py 分支：结果与 SM-2 不同，说明确实换了内核。"""
        set_scheduler_mode("fsrs_py", ratio=1.0)
        scheduler = FSRSScheduler(student_id="student-1")
        assert scheduler.mode == MODE_FSRS_PY

        card, _log = scheduler.review_card(Card(id="c1"), 3, START)
        sm2_card, _sm2_log = _sm2_review_card(Card(id="c1"), 3, START)
        # 桩的 Good 增长 3 倍 → 与 SM-2（首次 1 天）不同即可证明命中新内核
        assert card.scheduled_days != sm2_card.scheduled_days
        assert card.state in (0, 1, 2, 3)
        assert card.due is not None and card.due > START

    def test_pyfsrs_kwargs_are_applied(self, fake_fsrs, set_scheduler_mode):
        """Scheduler 按「天粒度 + 关 fuzzing」口径构造。"""
        set_scheduler_mode("fsrs_py", ratio=1.0)
        FSRSScheduler(student_id="s").review_card(Card(id="c1"), 3, START)
        kwargs = _FakeScheduler.last_kwargs
        assert kwargs.get("enable_fuzzing") is False
        assert kwargs.get("desired_retention") == 0.9
        assert kwargs.get("maximum_interval") == 365

    def test_pyfsrs_runtime_error_degrades_to_sm2(
        self, fake_fsrs, set_scheduler_mode, monkeypatch
    ):
        """py-fsrs 运行期抛异常 → 本次降级 SM-2，不阻塞复习链路。"""
        set_scheduler_mode("fsrs_py", ratio=1.0)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("py-fsrs 内部炸了")

        monkeypatch.setattr(_FakeScheduler, "review_card", _boom)
        scheduler = FSRSScheduler(student_id="student-1")
        card, log = scheduler.review_card(Card(id="c1"), 3, START)
        sm2_card, _ = _sm2_review_card(Card(id="c1"), 3, START)
        assert card.scheduled_days == sm2_card.scheduled_days
        assert card.difficulty == sm2_card.difficulty
        assert log.state == card.state

    def test_ef_roundtrip_is_lossless(self, fake_fsrs, set_scheduler_mode):
        """EF → py-fsrs difficulty → EF 往返可逆（回滚不丢状态）。"""
        from app.services.fsrs_scheduler import (
            _ef_to_pyfsrs_difficulty,
            _pyfsrs_difficulty_to_ef,
        )

        for ef in (1.3, 1.7, 2.5, 2.6, 3.0):
            back = _pyfsrs_difficulty_to_ef(_ef_to_pyfsrs_difficulty(ef))
            assert abs(back - ef) < 1e-3, f"EF 往返失真: {ef} -> {back}"

    def test_difficulty_column_keeps_ef_semantics(
        self, fake_fsrs, set_scheduler_mode
    ):
        """无论哪个内核，difficulty 列存的都是 SM-2 EF（1.3~3.0），不是 1~10。"""
        set_scheduler_mode("fsrs_py", ratio=1.0)
        card, _ = FSRSScheduler(student_id="s").review_card(Card(id="c1"), 4, START)
        assert 1.3 <= card.difficulty <= 3.0


class TestContractStability:
    """对外契约稳定性（与内核切换无关，任何配置下都必须成立）。"""

    def test_review_card_signature_and_immutability(self):
        """review_card 返回新对象、不修改入参（默认 sm2 分支）。"""
        scheduler = FSRSScheduler()
        original = Card(id="c1", state=0, reps=0)
        before = (original.state, original.reps, original.scheduled_days)
        new_card, log = scheduler.review_card(original, 3, START)
        assert (original.state, original.reps, original.scheduled_days) == before
        assert new_card is not original
        assert log.rating == 3

    def test_no_student_id_falls_back_to_global_mode(self):
        """拿不到 student_id 时按全局开关裁决，不报错。"""
        assert FSRSScheduler().mode == resolve_scheduler_mode(None)
        assert FSRSScheduler().student_id is None

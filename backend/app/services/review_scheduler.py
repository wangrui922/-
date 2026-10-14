"""复习排程（间隔重复 / Spaced Repetition）—— #65「留得住」的算法层。

硬约束（与 ``mastery_engine`` 一致）：
    * 本模块**不 import 任何 ORM / Session / FastAPI**，只吃 dataclass 吐 dataclass；
      可被纯单测覆盖（``tests/test_review_scheduler.py``），天然避开跨方言问题。
    * 不调用方舟（纯本地规则）。

为什么需要它：
    ``mastery_engine.compute_mastery`` 算出了「孩子这个知识点现在掌握得怎么样」（掌握度曲线），
    但光知道掌握度不够——**学习要「留得住」**必须按遗忘规律安排复习。本模块用经典 SM-2
    算法把一次练习的结果（quality 0-5）转成「下次什么时候复习」「间隔天数」「难度系数 EF」，
    让系统能挑出「今天该复习哪些题」。

时区约定：入参时间统一按 UTC 处理（与 ``mastery_engine`` 一致）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence

# ----------------------------------------------------------------- SM-2 常量
#: 初始难度系数（SM-2 标准值）
INITIAL_EASE_FACTOR: float = 2.5
#: 难度系数下限（低于此值题目基本等于不会，需要重新学）
MIN_EASE_FACTOR: float = 1.3
#: 难度系数上限（SM-2 惯例封顶；防止 ease 无界膨胀导致间隔爆炸，
#: 也为 mastery 联动等外部调整提供可收敛的取值区间）
MAX_EASE_FACTOR: float = 3.0
#: 首次复习间隔（天）
FIRST_INTERVAL_DAYS: int = 1
#: 第二次复习间隔（天）
SECOND_INTERVAL_DAYS: int = 6
#: 一次「完全想不起来 / 答错」后，间隔回退到的天数（重新来过）
LAPSE_INTERVAL_DAYS: int = 1
#: quality 低于此值视为「没记住」，需要重置连续正确次数并把间隔打回
LAPSE_QUALITY_THRESHOLD: int = 3


# ==================================================================== 数据结构
@dataclass
class ReviewState:
    """一个知识点（或一题）的复习排程状态。

    Attributes:
        key: 聚合键——通常是 ``kp_code``，也可一题一 key（取决于调用方）。
        ease_factor: 难度系数 EF（SM-2，初始 2.5，下限 1.3）。
        interval_days: 当前间隔天数（上一次复习到下一次复习之间）。
        repetitions: 连续「记住」的次数（答错归零）。
        last_reviewed_at: 上次复习时间（UTC）；从未复习为 ``None``。
        due_at: 下次应复习时间（UTC）；从未复习为 ``None``（视为「立即到期」）。
    """

    key: str
    ease_factor: float = INITIAL_EASE_FACTOR
    interval_days: int = 0
    repetitions: int = 0
    last_reviewed_at: Optional[datetime] = None
    due_at: Optional[datetime] = None


@dataclass
class ReviewPlan:
    """一次复习后的排程结果（机器读，可落库）。"""

    key: str
    ease_factor: float
    interval_days: int
    repetitions: int
    last_reviewed_at: datetime
    due_at: datetime
    lapsed: bool = False


# ==================================================================== 核心算法
def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """naive datetime 兜底补 UTC，aware 原样返回。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def update_ease_factor(current_ef: float, quality: int) -> float:
    """SM-2 难度系数更新。

    ``EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))``，下限 ``MIN_EASE_FACTOR``。
    quality 取 0-5；q≥3 时 EF 上升或持平，q<3 时下降。
    """
    q = max(0, min(5, int(quality)))
    delta = 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    new_ef = current_ef + delta
    return max(MIN_EASE_FACTOR, round(new_ef, 4))


def review(
    state: ReviewState, quality: int, now: Optional[datetime] = None
) -> ReviewPlan:
    """根据一次练习的 quality（0-5）推进复习排程，返回新的 ``ReviewPlan``。

    Args:
        state: 当前复习状态（不修改入参，返回新对象）。
        quality: 本次回忆质量 0-5（5=秒回且熟练，0=完全不会）。
        now: 当前时间（UTC）；缺省取 ``datetime.now(timezone.utc)``。

    Returns:
        ``ReviewPlan``：含新的 EF / 间隔 / 连续次数 / 下次到期时间。

    排程规则（SM-2）：
        * q < 3（没记住）：``repetitions`` 归零，间隔回退到 ``LAPSE_INTERVAL_DAYS``，EF 下调；
        * q >= 3（记住了）：
            - 第 1 次：间隔 = ``FIRST_INTERVAL_DAYS``；
            - 第 2 次：间隔 = ``SECOND_INTERVAL_DAYS``；
            - 第 n 次（n>=3）：间隔 = round(上次间隔 * EF)。
    """
    now = _as_utc(now) or datetime.now(timezone.utc)
    q = max(0, min(5, int(quality)))
    lapsed = q < LAPSE_QUALITY_THRESHOLD

    new_ef = update_ease_factor(state.ease_factor, q)
    new_repetitions = 0 if lapsed else state.repetitions + 1

    if lapsed:
        new_interval = LAPSE_INTERVAL_DAYS
    elif new_repetitions == 1:
        new_interval = FIRST_INTERVAL_DAYS
    elif new_repetitions == 2:
        new_interval = SECOND_INTERVAL_DAYS
    else:
        new_interval = max(SECOND_INTERVAL_DAYS, round(state.interval_days * new_ef))

    due = now + timedelta(days=new_interval)
    return ReviewPlan(
        key=state.key,
        ease_factor=new_ef,
        interval_days=new_interval,
        repetitions=new_repetitions,
        last_reviewed_at=now,
        due_at=due,
        lapsed=lapsed,
    )


def is_due(state: ReviewState, now: Optional[datetime] = None) -> bool:
    """该知识点现在是否该复习了。

    ``due_at`` 为 ``None``（从未复习）视为「立即到期」→ 返回 ``True``。
    """
    now = _as_utc(now) or datetime.now(timezone.utc)
    if state.due_at is None:
        return True
    return _as_utc(state.due_at) <= now


def select_due(
    states: Iterable[ReviewState], now: Optional[datetime] = None
) -> List[ReviewState]:
    """从一批复习状态里挑出「今天该复习的」，按到期时间升序返回。"""
    now = _as_utc(now) or datetime.now(timezone.utc)
    due = [s for s in states if is_due(s, now)]
    return sorted(
        due,
        key=lambda s: _as_utc(s.due_at) or datetime.min.replace(tzinfo=timezone.utc),
    )


def quality_from_correct(correct: bool, verdict: str = "pending") -> int:
    """把「对/错 + 辅导 verdict」收敛成 SM-2 的 quality（0-5）。

    这是调用方（practice_service / ai_exercise）的便捷映射，避免各处自己拍脑袋：
        * 错（wrong）：q=1（基本没记住）；
        * 对但似懂（pending / 蒙对）：q=3（刚好过线，间隔不长）；
        * 对且讲清（correct）：q=5（熟练，拉长间隔）。
    """
    if not correct:
        return 1
    if verdict == "correct":
        return 5
    # pending / 其他：算「记住了但不算特别熟」
    return 3


def initial_state(key: str, now: Optional[datetime] = None) -> ReviewState:
    """生成一个「从未复习」的初始状态（due_at=None → 立即到期，等待首次练习）。"""
    return ReviewState(key=key, due_at=None, last_reviewed_at=_as_utc(now))


__all__ = [
    "INITIAL_EASE_FACTOR",
    "MIN_EASE_FACTOR",
    "MAX_EASE_FACTOR",
    "FIRST_INTERVAL_DAYS",
    "SECOND_INTERVAL_DAYS",
    "ReviewState",
    "ReviewPlan",
    "update_ease_factor",
    "review",
    "is_due",
    "select_due",
    "quality_from_correct",
    "initial_state",
]

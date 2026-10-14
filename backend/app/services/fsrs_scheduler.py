"""FSRS 调度器（方向三）—— review_cards / review_logs 表对应的调度核心。

选型说明（方向三落地决策，与 docs/regression-followup-20261001.md 验收口径一致：
「真实 FSRS/SM-2 逻辑，任一可接受」）：

    * FSRS-4.5 完整公式需要 19 个权重参数，本项目无权重文件、无校准数据，
      强行引入会成为不可验证的黑盒；而 ``app.services.review_scheduler`` 已是
      成熟、可解释、有 8 项单测覆盖的 SM-2 算法层。
    * 因此本模块**复用 SM-2 核心**（``review_scheduler.review`` 及其常量），
      在外层做 FSRS 语义适配，映射到 review_cards / review_logs 的字段：
        - rating 1-4（Again/Hard/Good/Easy）→ SM-2 quality {1, 3, 4, 5}；
        - stability = 本次调度间隔天数（记忆强度代理）；
        - difficulty 列直接编码 SM-2 难度系数 EF（初始 2.5，下限 1.3，无上限），
          保证跨复习的 EF 连续可逆（0~10 归一化会因 EF>2.5 饱和而丢信息）；
        - state = 0 New / 1 Learning / 2 Review / 3 Relearning；
        - retrievability = 指数遗忘曲线 R(t) = 0.9^(t / S)。

硬约束（与 review_scheduler 一致）：本模块**不 import 任何 ORM / Session /
FastAPI**，只吃 dataclass 吐 dataclass，可被纯单测覆盖（tests/test_fsrs_scheduler.py）。

时区约定：入参时间统一按 UTC 处理（与 review_scheduler / mastery_engine 一致）；
naive datetime 视为 UTC。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from app.services.review_scheduler import (
    FIRST_INTERVAL_DAYS,
    INITIAL_EASE_FACTOR,
    LAPSE_QUALITY_THRESHOLD,
    MIN_EASE_FACTOR,
    ReviewState,
    review as sm2_review,
)

# ------------------------------------------------------------------- 常量
#: rating（1=Again, 2=Hard, 3=Good, 4=Easy）→ SM-2 quality（0-5）
RATING_TO_QUALITY: dict[int, int] = {1: 1, 2: 3, 3: 4, 4: 5}
#: 默认 quality（rating 非法时的兜底，等价于 Good）
DEFAULT_QUALITY: int = RATING_TO_QUALITY[3]

#: 状态机取值（review_cards.state / review_logs.state）
STATE_NEW = 0  # 新建未调度
STATE_LEARNING = 1  # 学习中（首次成功 / 重学后首次成功）
STATE_REVIEW = 2  # 稳定复习中
STATE_RELEARNING = 3  # 遗忘 / 答错，重新学习中


# ==================================================================== 数据结构
@dataclass
class Card:
    """FSRS 复习卡片（与 review_cards 表 15 列对齐，不含时间戳列）。

    支持无参构造（``Card()`` → 全新未调度卡片）与具名构造
    （review_service.submit_review 从库里重建时使用）。
    """

    id: Optional[str] = None
    due: Optional[datetime] = None
    stability: float = 0.0
    difficulty: float = 0.0
    elapsed_days: int = 0
    scheduled_days: int = 0
    reps: int = 0
    lapses: int = 0
    state: int = STATE_NEW
    last_review: Optional[datetime] = None


@dataclass
class ReviewLog:
    """一次复习的调度日志（与 review_logs 表 13 列对齐，不含 id/card_id/
    student_id/review_at——这些由调用方填写）。

    字段顺序与表列一致，方便将来直接落库。
    """

    rating: int = 0
    state: int = STATE_NEW
    elapsed_days: int = 0
    scheduled_days: int = 0
    stability_before: float = 0.0
    stability_after: float = 0.0
    difficulty_before: float = 0.0
    difficulty_after: float = 0.0


# ==================================================================== 工具函数
def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """naive datetime 兜底补 UTC，aware 原样返回。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _rating_to_quality(rating: int) -> int:
    """rating（1-4）→ SM-2 quality（0-5）。非法值兜底到 Good。"""
    q = RATING_TO_QUALITY.get(int(rating))
    return DEFAULT_QUALITY if q is None else q


def _ef_to_difficulty(ef: float) -> float:
    """SM-2 EF → difficulty 列编码（直接存 EF 本身）。

    0~10 归一化会在 EF>2.5（连续 Easy 上浮）时饱和而丢失信息，破坏
    跨复习可逆性，因此不做归一化：difficulty 列 = SM-2 难度系数 EF。
    语义上「EF 高 = 更容易记」与「difficulty 低 = 难度小」方向一致。
    """
    return round(float(ef), 4)


def _next_state(lapsed: bool, reps: int) -> int:
    """依据本review是否遗忘与新的连续成功次数，决定新的 state。

    * 遗忘（q < 3）：进入 Relearning，reps 归零；
    * 成功：第 1 次成功 → Learning（含重学后首次成功）；
      连续第 2 次及以上 → Review。
    """
    if lapsed:
        return STATE_RELEARNING
    return STATE_LEARNING if reps <= 1 else STATE_REVIEW


# ==================================================================== 调度器
class FSRSScheduler:
    """FSRS/SM-2 调度器：把一次复习 rating 推进为新的卡片排程。

    实现层委托 ``review_scheduler.review``（SM-2），本类负责 rating→quality、
    state 迁移与 FSRS 字段（stability/difficulty）的语义适配。
    """

    def review_card(
        self,
        card: Card,
        rating: int,
        now: Optional[datetime] = None,
    ) -> Tuple[Card, ReviewLog]:
        """根据一次复习的 rating（1=Again 2=Hard 3=Good 4=Easy）推进排程。

        Args:
            card: 当前卡片（不修改入参，返回新对象）。
            rating: 1=Again, 2=Hard, 3=Good, 4=Easy。
            now: 当前时间（UTC）；缺省取 ``datetime.now(timezone.utc)``。

        Returns:
            ``(new_card, log)``：新的卡片状态与本次调度日志。
        """
        now = _as_utc(now) or datetime.now(timezone.utc)
        q = _rating_to_quality(rating)

        # elapsed_days：距上次复习的自然日（首复习为 0）
        last = _as_utc(card.last_review)
        elapsed_days = max(0, (now - last).days) if last else (card.elapsed_days or 0)

        # 委托 SM-2 核心计算新的 EF / 间隔 / 连续次数 / 到期时间
        plan = sm2_review(
            ReviewState(
                key=str(card.id or "card"),
                ease_factor=_sm2_restore_ef(card),
                interval_days=card.scheduled_days,
                repetitions=card.reps,
                last_reviewed_at=card.last_review,
                due_at=card.due,
            ),
            q,
            now,
        )

        lapsed = q < LAPSE_QUALITY_THRESHOLD
        reps = plan.repetitions
        lapses = card.lapses + (1 if lapsed else 0)

        new_card = Card(
            id=card.id,
            due=plan.due_at,
            stability=float(plan.interval_days),
            difficulty=_ef_to_difficulty(plan.ease_factor),
            elapsed_days=elapsed_days,
            scheduled_days=plan.interval_days,
            reps=reps,
            lapses=lapses,
            state=_next_state(lapsed, reps),
            last_review=plan.last_reviewed_at,
        )

        log = ReviewLog(
            rating=int(rating),
            state=new_card.state,
            elapsed_days=elapsed_days,
            scheduled_days=plan.interval_days,
            stability_before=card.stability,
            stability_after=new_card.stability,
            difficulty_before=card.difficulty,
            difficulty_after=new_card.difficulty,
        )
        return new_card, log

    def get_retrievability(
        self,
        card: Card,
        now: Optional[datetime] = None,
    ) -> float:
        """计算卡片当前记忆保留概率 R(t)（0~1）。

        指数遗忘曲线：``R = 0.9 ** (t / stability)``，t 为距上次复习的天数。
        语义：距上次复习 t = stability 天时保留率约 0.9（到期边界）；
        无稳定性或从未复习 → 返回 0.0。
        """
        if card.stability <= 0 or card.last_review is None:
            return 0.0
        now_utc = _as_utc(now) or datetime.now(timezone.utc)
        last = _as_utc(card.last_review)
        if last is None:  # 防御：上面的分支已排除，此处仅为类型收窄
            return 0.0
        t_days = max(0.0, (now_utc - last).total_seconds() / 86400.0)
        return round(0.9 ** (t_days / card.stability), 6)


# ==================================================================== 内部辅助
def _sm2_restore_ef(card: Card) -> float:
    """把库里存的 difficulty（SM-2 EF 编码）还原为 EF。

    difficulty 列直接编码 SM-2 难度系数 EF（初始 2.5、下限 1.3、无上限），
    保证跨复习 EF 连续可逆；新卡（difficulty=0 且未复习过）→ 初始 EF。
    """
    if card.difficulty <= 0 and card.reps == 0:
        return INITIAL_EASE_FACTOR
    return max(MIN_EASE_FACTOR, card.difficulty or INITIAL_EASE_FACTOR)


__all__ = [
    "Card",
    "ReviewLog",
    "FSRSScheduler",
    "RATING_TO_QUALITY",
    "DEFAULT_QUALITY",
    "STATE_NEW",
    "STATE_LEARNING",
    "STATE_REVIEW",
    "STATE_RELEARNING",
]

"""复习排程服务层 —— #65「留得住」的持久化 + 查询入口。

把纯算法 ``review_scheduler``（SM-2）与 ORM 表 ``ReviewSchedule`` 接起来：

    * 一次练习结果（correct/verdict 或 quality）→ ``record_review`` upsert 一行；
    * 给一个学生，挑出「今天该复习的」知识点 → ``list_due_reviews``；
    * 状态在 ``ReviewState``（算法）与 ``ReviewSchedule``（表）之间双向转换。

硬约束（与 ``review_scheduler`` 一致）：
    * 时间统一按 UTC 处理；
    * 不调用方舟；
    * 入参 ``quality`` 走 0-5，``correct+verdict`` 走 ``quality_from_correct`` 收敛。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.learning import ReviewSchedule
from app.services.review_scheduler import (
    FIRST_INTERVAL_DAYS,
    ReviewState,
    initial_state,
    is_due,
    quality_from_correct,
    review,
    select_due,
)

StudentId = uuid.UUID
KpId = uuid.UUID


# ============================================================ 状态转换
def _row_to_state(row: ReviewSchedule) -> ReviewState:
    """``ReviewSchedule`` 行 → 算法用的 ``ReviewState``。"""
    return ReviewState(
        key=str(row.knowledge_point_id),
        ease_factor=row.ease_factor,
        interval_days=row.interval_days,
        repetitions=row.repetitions,
        last_reviewed_at=row.last_reviewed_at,
        due_at=row.due_at,
    )


def _state_to_row(
    row: Optional[ReviewSchedule],
    student_id: StudentId,
    kp_id: KpId,
    state: ReviewState,
    now: datetime,
) -> ReviewSchedule:
    """把 ``ReviewState`` 写回 ``ReviewSchedule`` 行（新建或就地更新）。"""
    if row is None:
        row = ReviewSchedule(
            student_id=student_id,
            knowledge_point_id=kp_id,
            created_at=now,
        )
    row.ease_factor = state.ease_factor
    row.interval_days = state.interval_days
    row.repetitions = state.repetitions
    row.last_reviewed_at = state.last_reviewed_at
    row.due_at = state.due_at
    row.updated_at = now
    return row


# ============================================================ 查询 / 记录
def get_state(session: Session, student_id: StudentId, kp_id: KpId) -> ReviewState:
    """取某学生某知识点的复习状态；无记录返回 ``initial_state``（立即到期）。"""
    row = _find_row(session, student_id, kp_id)
    if row is None:
        return initial_state(key=str(kp_id), now=datetime.now(timezone.utc))
    return _row_to_state(row)


def _find_row(
    session: Session, student_id: StudentId, kp_id: KpId
) -> Optional[ReviewSchedule]:
    return session.execute(
        select(ReviewSchedule).where(
            ReviewSchedule.student_id == student_id,
            ReviewSchedule.knowledge_point_id == kp_id,
        )
    ).scalar_one_or_none()


def record_review(
    session: Session,
    student_id: StudentId,
    kp_id: KpId,
    quality: int,
    now: Optional[datetime] = None,
) -> ReviewState:
    """按一次练习的 quality（0-5）记录复习并落库，返回新的 ``ReviewState``。

    调用方（practice_service / ai_exercise）应在同一个事务里先记 attempt，
    再调本函数更新排程——本函数只负责 ``ReviewSchedule`` 一行。
    """
    now = now or datetime.now(timezone.utc)
    row = _find_row(session, student_id, kp_id)
    prior = _row_to_state(row) if row is not None else initial_state(key=str(kp_id), now=now)
    plan = review(prior, quality, now=now)
    new_state = ReviewState(
        key=str(kp_id),
        ease_factor=plan.ease_factor,
        interval_days=plan.interval_days,
        repetitions=plan.repetitions,
        last_reviewed_at=plan.last_reviewed_at,
        due_at=plan.due_at,
    )
    if row is None:
        # 新建：构造一行并加入会话
        row = ReviewSchedule(
            student_id=student_id,
            knowledge_point_id=kp_id,
            created_at=now,
        )
        session.add(row)
    # 无论新建还是已存在，都把新状态写回该行
    row.ease_factor = new_state.ease_factor
    row.interval_days = new_state.interval_days
    row.repetitions = new_state.repetitions
    row.last_reviewed_at = new_state.last_reviewed_at
    row.due_at = new_state.due_at
    row.updated_at = now
    session.flush()
    return new_state


def record_review_from_result(
    session: Session,
    student_id: StudentId,
    kp_id: KpId,
    correct: bool,
    verdict: str = "pending",
    now: Optional[datetime] = None,
) -> ReviewState:
    """便捷入口：用「对/错 + verdict」收敛成 quality 后记录。

    verdict 取值同 ``review_scheduler.quality_from_correct``：
        * correct=False → q=1（没记住）；
        * correct=True & verdict='correct' → q=5（熟练）；
        * 其余（pending / 蒙对）→ q=3（刚过线）。
    """
    now = now or datetime.now(timezone.utc)
    quality = quality_from_correct(correct, verdict)
    return record_review(session, student_id, kp_id, quality, now=now)


def list_due_reviews(
    session: Session,
    student_id: StudentId,
    now: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Tuple[KpId, ReviewState]]:
    """挑出该学生「现在该复习」的知识点，按到期时间升序返回。

    Returns:
        ``[(knowledge_point_id, ReviewState), ...]``，可直接喂给组卷/任务生成。
    """
    now = now or datetime.now(timezone.utc)
    rows = (
        session.execute(
            select(ReviewSchedule).where(ReviewSchedule.student_id == student_id)
        )
        .scalars()
        .all()
    )
    states = [_row_to_state(r) for r in rows]
    due = select_due(states, now=now)
    out: List[Tuple[KpId, ReviewState]] = []
    for st in due:
        kp_id = uuid.UUID(st.key) if _looks_like_uuid(st.key) else uuid.UUID(int=0)
        # 用 key 反查 row 的 kp_id（更稳妥）：直接映射回原 row
        out.append((_kp_id_of(rows, st), st))
    if limit is not None:
        out = out[:limit]
    return out


def _looks_like_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


def _kp_id_of(rows: Sequence[ReviewSchedule], st: ReviewState) -> KpId:
    """从原 rows 里按 key 找回对应的 knowledge_point_id（避免字符串↔UUID 风险）。"""
    target = st.key
    for r in rows:
        if str(r.knowledge_point_id) == target:
            return r.knowledge_point_id
    # 兜底：直接解析
    return uuid.UUID(target)


def seed_initial(
    session: Session,
    student_id: StudentId,
    kp_ids: Sequence[KpId],
    now: Optional[datetime] = None,
) -> int:
    """为新学生批量初始化一批知识点的复习排程（立即到期，等首次练习）。

    已存在的行不重复建（幂等）。返回新建行数。
    """
    now = now or datetime.now(timezone.utc)
    created = 0
    for kp_id in kp_ids:
        if _find_row(session, student_id, kp_id) is not None:
            continue
        st = initial_state(key=str(kp_id), now=now)
        session.add(_state_to_row(None, student_id, kp_id, st, now))
        created += 1
    session.flush()
    return created


__all__ = [
    "FIRST_INTERVAL_DAYS",
    "get_state",
    "record_review",
    "record_review_from_result",
    "list_due_reviews",
    "seed_initial",
]

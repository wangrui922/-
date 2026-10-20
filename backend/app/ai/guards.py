"""AI 调用限流守卫（T03 · 3.3）。

职责：在任何一次在线 Agent 调用**之前**做日配额检查，超额直接抛 ``ApiError(429)``。

设计要点：
    * 无 Redis，配额走 DB 计数（docs/v4-db-api.md §C.4 的 Redis 方案在 V4.1 用 DB 替代）；
    * 自然日按 **Asia/Shanghai**（UTC+8，中国无夏令时，用固定偏移即可）切分，
      落库时间仍是 UTC，比较时把"北京时间今日 0 点"换算成 UTC 再传参；
    * 只用 SQLAlchemy Core 表达式 ``select(func.count()).where(...)``，
      不出现任何 PG 专属 SQL，SQLite / PostgreSQL 行为一致。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import ApiError, CODE_TOO_MANY_REQUESTS
from app.models.ai import AiSession
from app.models.learning import Attempt

#: 北京时区（固定 UTC+8，中国大陆不实行夏令时）
CN_TZ = timezone(timedelta(hours=8))

#: 超配额时给用户看的提示语
QUOTA_EXCEEDED_MSG: str = "今日 AI 辅导次数已用完，明天再来"


def cn_day_start_utc(now: datetime | None = None) -> datetime:
    """返回"北京时间今日 0 点"对应的 UTC 时间（timezone-aware）。

    Args:
        now: 参考时刻（aware / naive 均可，naive 视为 UTC）；缺省取当前时间。

    Returns:
        可直接与 ``created_at``（UTC 存储）比较的 aware datetime。
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_start = current.astimezone(CN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc)


def _to_uuid(raw: Any) -> uuid.UUID:
    """把字符串 / UUID 统一成 ``uuid.UUID``，非法值按 400 处理。"""
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "student_id 非法") from exc


def count_today_usage(student_id: uuid.UUID, db: Session) -> Tuple[int, int]:
    """统计该学生今日（北京时间）的 AI 用量。

    Args:
        student_id: 学生 ID。
        db: 调用方传入的 SQLAlchemy 会话。

    Returns:
        ``(会话数, 作答数)`` —— 分别来自 ``ai_sessions`` 与 ``attempts``。
    """
    day_start = cn_day_start_utc()

    session_count = db.execute(
        select(func.count())
        .select_from(AiSession)
        .where(AiSession.student_id == student_id, AiSession.created_at >= day_start)
    ).scalar_one()

    attempt_count = db.execute(
        select(func.count())
        .select_from(Attempt)
        .where(Attempt.student_id == student_id, Attempt.created_at >= day_start)
    ).scalar_one()

    return int(session_count or 0), int(attempt_count or 0)


def check_ai_quota(student_id: uuid.UUID, db: Session) -> None:
    """日配额守卫：今日会话数或作答数达到上限即拒绝。

    上限取 ``settings.free_daily_ai``（内测期默认 0 = 不限流）。两个计数只要有一个
    达到上限就拦截（"或"的语义），避免刷会话或刷作答两条路径互相绕过。

    Args:
        student_id: 学生 ID（``uuid.UUID`` 或可转换的字符串）。
        db: 调用方传入的 SQLAlchemy 会话。

    Raises:
        ApiError: code=429，今日额度已用完。
    """
    limit = int(settings.free_daily_ai or 0)
    if limit <= 0:  # 0 或负数视为不限流（便于内部/压测环境关闭配额）
        return

    sid = _to_uuid(student_id)
    session_count, attempt_count = count_today_usage(sid, db)
    used = max(session_count, attempt_count)

    if used >= limit:
        raise ApiError(
            CODE_TOO_MANY_REQUESTS,
            QUOTA_EXCEEDED_MSG,
            {"used": used, "limit": limit},
        )


__all__ = [
    "check_ai_quota",
    "count_today_usage",
    "cn_day_start_utc",
    "CN_TZ",
    "QUOTA_EXCEEDED_MSG",
]

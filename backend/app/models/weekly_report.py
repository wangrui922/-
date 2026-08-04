"""家长周报快照表 ``weekly_reports``（Epic6 · T1）。

⚠️ 为什么需要这张表（见 `docs/epic6-design.md` §1.2 K2）：
    ``student_mastery`` 是 upsert **覆盖式**写入（``profile_service.upsert_mastery``），
    全库**没有任何历史留存**，因此无法回答"这周比上周进步了多少"。
    本表在每次生成周报时把当周的「掌握度 + 错因 + 计数」整体存档（``snapshot_json``），
    下一周即可与最近一条快照做 diff。

关键业务约束：
    * ``(student_id, period_start)`` 唯一 —— 同一学生同一周只有一份，重复生成 = **原地更新**；
    * 重新生成时 **不换 ``token``**（家长手上的链接必须持续可用），
      仅当 ``expires_at`` 已过期才续期到 ``now + 30d``；
    * ``token`` 全局唯一，公开分享页仅凭它免登录访问。

跨方言：UUID 走 ``base.GUID``，JSON 走 ``base.JSONType``，无任何 PG 专属语法
（同一份模型必须能在 PostgreSQL 与 SQLite 下 ``create_all()`` 成功）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, JSONType, TimestampMixin, utcnow, uuid_pk


class WeeklyReport(Base, TimestampMixin):
    """一份家长周报的持久化快照。

    Attributes:
        student_id: 所属学生（级联删除）。
        period_start: 统计周期起点（北京时间周一 00:00:00 → 以 UTC 存储）。
        period_end: 统计周期终点（北京时间周日 23:59:59 → 以 UTC 存储）。
        token: 分享令牌，``secrets.token_urlsafe(24)``（32 字符 URL-safe），全局唯一。
        expires_at: 令牌过期时刻（生成时刻 + 30 天，UTC）。
        view_count: 家长打开分享页的累计次数（U4 已采纳：MVP 核心假设验证指标）。
        snapshot_json: 当周快照，结构见 `docs/epic6-design.md` §4.2。
        report_text: Report Agent 输出，结构见 §4.3（含 ``source`` = ai/fallback）。
        created_at: 首次生成时间（TimestampMixin）。
        updated_at: 最近一次重新生成时间（TimestampMixin，前端显示"更新于"）。
    """

    __tablename__ = "weekly_reports"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属学生",
    )

    # ---- 统计周期（库内一律 UTC） -----------------------------------------
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="周期起点(UTC)，对应北京时间周一00:00:00",
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="周期终点(UTC)，对应北京时间周日23:59:59",
    )

    # ---- 分享令牌 ----------------------------------------------------------
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="分享令牌，secrets.token_urlsafe(24)",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        comment="令牌过期时刻(UTC)",
    )
    view_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="家长打开分享页累计次数(U4)",
    )

    # ---- 载荷 --------------------------------------------------------------
    snapshot_json: Mapped[Dict[str, Any]] = mapped_column(
        JSONType(),
        nullable=False,
        default=dict,
        comment="当周快照：掌握度+错因+计数(见设计文档 §4.2)",
    )
    report_text: Mapped[Dict[str, Any]] = mapped_column(
        JSONType(),
        nullable=False,
        default=dict,
        comment="Report Agent 输出：headline/三段文案/source(见 §4.3)",
    )

    __table_args__ = (
        UniqueConstraint("student_id", "period_start", name="uq_weekly_reports_student_period"),
        UniqueConstraint("token", name="uq_weekly_reports_token"),
        Index("idx_weekly_reports_student_period", "student_id", "period_start"),
        Index("idx_weekly_reports_token_expires", "token", "expires_at"),
        {"comment": "家长周报快照表(Epic6)"},
    )

    # ------------------------------------------------------------------ 方法
    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """判断分享令牌是否已过期。

        Args:
            now: 参照时刻（tz-aware UTC）；缺省取当前 UTC 时间。

        Returns:
            ``True`` 表示已过期（``expires_at <= now``）。
        """
        moment = now or utcnow()
        expires = self.expires_at
        if expires is None:
            return True
        # SQLite 取回的 datetime 可能是 naive，统一补 UTC 时区再比较，避免 TypeError
        if expires.tzinfo is None:
            from datetime import timezone as _tz

            expires = expires.replace(tzinfo=_tz.utc)
        if moment.tzinfo is None:
            from datetime import timezone as _tz

            moment = moment.replace(tzinfo=_tz.utc)
        return expires <= moment


__all__ = ["WeeklyReport"]

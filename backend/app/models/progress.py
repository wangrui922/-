"""练习进度云同步模型。

支持多端进度同步：学生可在 A 设备刷题一半，到 B 设备继续。
每张进度记录对应一个学生的「最近一次练习会话的快照」。

字段与前端 ``practiceState`` 对齐，JSON 字段存不可变数据，
关系字段存可枚举的枚举值，便于查询和合并。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, GUID, JSONType, TimestampMixin, fk_uuid, utcnow


class PracticeProgress(Base, TimestampMixin):
    """练习进度快照。

    每个学生最多一条活跃记录（按 ``updated_at`` 判断最新），
    新进度覆盖旧进度（UPSERT 语义）。
    """

    __tablename__ = "practice_progress"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    student_id: Mapped[uuid.UUID] = fk_uuid("students.id", nullable=False, index=True)

    # ---- 状态机三态 ----------------------------------------------------------
    phase: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="filter",
        comment="当前阶段：filter / doing / result",
    )

    # ---- 题目池（JSON 存完整题目列表）----------------------------------------
    questions_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONType(),
        nullable=True,
        comment="题目列表（questions 数组的完整快照）",
    )
    pool_info_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONType(),
        nullable=True,
        comment="抽题池元信息（pool_size / unmapped_count / relaxation_steps 等）",
    )

    # ---- 答题状态（JSON 存 per-question 状态）---------------------------------
    answers_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONType(),
        nullable=True,
        comment="每道题的作答记录（selected / feedback / reveal 等）",
    )

    # ---- 当前位置 ------------------------------------------------------------
    current_index: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="当前做到第几题（0-based）",
    )
    total_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="本轮总题数",
    )

    # ---- 会话关联 ------------------------------------------------------------
    session_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="后端练习会话 ID（如有）",
    )

    # ---- 深链上下文（从画像页/错题本直达时才有）--------------------------------
    deep_link_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONType(),
        nullable=True,
        comment="深链参数（kp_id / mistake_ids / grade / difficulty 等）",
    )

    # ---- 时间戳（用于多端冲突解决：取最新 saved_at）--------------------------
    client_saved_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="前端保存时的时间戳（ms 精度，用于冲突解决）",
    )

    # ---- 软删（用户手动清空进度时标记）----------------------------------------
    cleared_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        default=None,
        comment="用户手动清空进度的时间",
    )

    __table_args__ = (
        Index("ix_practice_progress_student_active", "student_id", "cleared_at"),
    )

    def is_active(self) -> bool:
        """进度是否有效（未清空且非空）。"""
        return self.cleared_at is None and self.phase in ("doing", "result")

    def to_snapshot(self) -> Dict[str, Any]:
        """转成前端可直接恢复的 snapshot 字典。"""
        return {
            "phase": self.phase,
            "index": self.current_index,
            "questions": self.questions_json or [],
            "pool": self.pool_info_json or {},
            "answers": self.answers_json or {},
            "session_id": self.session_id,
            "deep_link": self.deep_link_json,
            "saved_at": self.client_saved_at.isoformat() if self.client_saved_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

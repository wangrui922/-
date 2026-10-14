"""自适应难度模型：``student_adaptive_level``（学生 × 难度档位状态，#4 draw_adaptive）。

方案约束（本轮决议）：
* **不新增 ``LearningTask.task_type`` 枚举**（库级 CHECK 只允许 daily/review/exam，
  主理人裁决①），自适应只影响抽题难度分布，不引入新任务类型；
* **不 ``ALTER TABLE`` 改 ``student_profile`` 加列**（避免迁移负担），改用
  **独立新表** + ``Base.metadata.create_all()`` 自动建表（项目无 Alembic）；
* 难度口径与 ``questions.difficulty`` 一致：只允许 1/3/5。

字段语义（配合 ``app.services.adaptive_service.adjust_adaptive_state`` 纯函数）：

* ``current_difficulty``：当前推荐主难度（1/3/5，初始 3）；
* ``up_streak`` / ``down_streak``：连续「达标窗口」计数（防抖：连续 2 个窗口
  判定通过才真正调档，避免单次发挥波动反复跳档）；
* ``window_correct`` / ``window_total``：当前窗口内累计答对 / 答题总数，
  满 ``WINDOW_SIZE``（默认 10）结算一次窗口。

⚠️ 新增模型必须登记进 ``app/models/__init__.py``，否则 ``create_all()`` 不会建表。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, utcnow, uuid_pk

if TYPE_CHECKING:  # 仅类型检查期引用，运行期不产生循环导入
    from app.models.user import Student


class StudentAdaptiveLevel(Base):
    """学生自适应难度状态（1:1，``create_all`` 自动建表）。"""

    __tablename__ = "student_adaptive_level"

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="所属学生(1:1)",
    )
    current_difficulty: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, comment="当前主难度 1/3/5（初始 3）"
    )
    up_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="连续高分达标窗口数（防抖）"
    )
    down_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="连续低分达标窗口数（防抖）"
    )
    window_correct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="当前窗口累计答对题数"
    )
    window_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="当前窗口累计答题总数"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        comment="更新时间(UTC)",
    )

    __table_args__ = ({"comment": "学生自适应难度表(#4 draw_adaptive)"},)


__all__ = ["StudentAdaptiveLevel"]
"""复习卡片接口的请求模型（FSRS 科学复习 · 方向三 T3）。

⚠️ 命名风格（硬约束 C10）：本文件直接继承 ``pydantic.BaseModel`` 保持
snake_case 契约（对齐 ``schemas/practice.py`` 的做法），**禁止**继承
``app/schemas/common.py::BaseSchema``（alias_generator 会把字段改成驼峰）。

前端契约见 ``student-web/src/api/review.ts``：
    GET  /api/review/daily   → { cards, count, date }
    POST /api/review/submit  → { card_id, rating }
    GET  /api/review/stats   → 复习统计
    POST /api/review/sync    → { kp_only }
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ReviewBase(BaseModel):
    """复习域基类：保持 snake_case，忽略多余字段。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", from_attributes=True)


class ReviewSubmitRequest(ReviewBase):
    """``POST /api/review/submit`` 请求体（提交一次复习结果）。"""

    card_id: str = Field(..., description="复习卡片 ID")
    rating: int = Field(..., ge=1, le=4, description="FSRS 评分 1=Again 2=Hard 3=Good 4=Easy")


class ReviewSyncRequest(ReviewBase):
    """``POST /api/review/sync`` 请求体（从掌握度同步复习计划）。"""

    kp_only: Optional[bool] = Field(
        True, description="是否只为知识点建档（True=知识点卡片，当前实现即此语义）"
    )
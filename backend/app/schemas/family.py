"""家长-孩子绑定域出入参 Schema（camelCase alias，继承 common.BaseSchema）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from app.schemas.common import BaseSchema
from app.models.family import RELATION_VALUES


class BindChildIn(BaseSchema):
    """``POST /api/family/bind`` 入参：家长绑定一个孩子。"""

    phone: str = Field(..., min_length=1, max_length=20, description="要绑定的孩子手机号")
    relation: str = Field(default="guardian", description="father/mother/guardian/other")


class ChildBrief(BaseSchema):
    """家长视角下的孩子摘要（列表项）。"""

    student_id: str = Field(..., description="学生 ID")
    nickname: Optional[str] = Field(default=None, description="昵称")
    real_name: Optional[str] = Field(default=None, description="真实姓名")
    grade: Optional[int] = Field(default=None, description="年级 1-12")
    stage: Optional[str] = Field(default=None, description="primary/junior/senior")
    relation: Optional[str] = Field(default=None, description="绑定关系")


class ChildSummary(BaseSchema):
    """家长视角下的孩子完整概览（复用数学画像 + 周报快照 + 雷达）。"""

    student_id: str = Field(..., description="学生 ID")
    nickname: Optional[str] = Field(default=None)
    real_name: Optional[str] = Field(default=None)
    grade: Optional[int] = Field(default=None)
    stage: Optional[str] = Field(default=None)
    profile: Dict[str, Any] = Field(default_factory=dict, description="学生档案（同 /api/student/profile）")
    weekly: Dict[str, Any] = Field(default_factory=dict, description="当周学习快照")
    radar: Dict[str, Any] = Field(default_factory=dict, description="学情雷达（同 /api/profile/radar）")


__all__ = ["BindChildIn", "ChildBrief", "ChildSummary", "RELATION_VALUES"]

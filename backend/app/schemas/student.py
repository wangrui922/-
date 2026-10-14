"""学生档案出入参 Schema（camelCase alias，见 schemas/common.BaseSchema）。

年级越界（0 / 13）的判定放在 service 层显式抛 ``ApiError(400)``，
这里不加 ``ge/le`` 约束，保证错误提示语可控且 ``code`` 恒为 400。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from app.schemas.common import BaseSchema


class ProfileSummary(BaseSchema):
    """画像摘要（来自 ``student_profile`` 表）。"""

    mastery: Dict[str, Any] = Field(default_factory=dict, description="知识点掌握度")
    weak_points: List[Any] = Field(default_factory=list, description="薄弱点列表")
    learning_style: Dict[str, Any] = Field(default_factory=dict, description="学习风格")
    mastered_count: int = Field(default=0, description="掌握度达标(>=80)的知识点数")
    tracked_count: int = Field(default=0, description="已有记录的知识点数")
    updated_at: Optional[str] = Field(default=None, description="画像更新时间(ISO8601 UTC)")


class StudentProfileOut(BaseSchema):
    """``GET /api/student/profile`` 返回体。"""

    student_id: str = Field(..., description="学生 ID")
    user_id: str = Field(..., description="账号 ID")
    phone: str = Field(..., description="手机号")
    nickname: Optional[str] = Field(default=None, description="昵称")
    avatar_url: Optional[str] = Field(default=None, description="头像")
    real_name: Optional[str] = Field(default=None, description="真实姓名")
    grade: int = Field(..., description="年级 1-12")
    stage: str = Field(..., description="primary/junior/senior")
    goal: Optional[str] = Field(default=None, description="学习目标")
    daily_target: int = Field(default=5, description="每日目标题量")
    streak_days: int = Field(default=0, description="连续打卡天数")
    total_points: int = Field(default=0, description="成就积分")
    profile: ProfileSummary = Field(default_factory=ProfileSummary, description="画像摘要")


class StudentProfileIn(BaseSchema):
    """``PUT /api/student/profile`` 入参（全部可选，只更新传了的字段）。"""

    grade: Optional[int] = Field(default=None, description="年级 1-12，越界返 400")
    goal: Optional[str] = Field(default=None, max_length=500, description="学习目标")
    real_name: Optional[str] = Field(default=None, max_length=50, description="真实姓名")
    nickname: Optional[str] = Field(default=None, max_length=50, description="昵称")
    daily_target: Optional[int] = Field(default=None, description="每日目标题量 1-50")

    def has_any(self) -> bool:
        """是否至少传了一个可更新字段。"""
        return any(
            value is not None
            for value in (self.grade, self.goal, self.real_name, self.nickname, self.daily_target)
        )


__all__ = ["ProfileSummary", "StudentProfileOut", "StudentProfileIn"]

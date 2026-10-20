"""管理后台请求/响应 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ActivationCodeCreate(BaseModel):
    """批量生成激活码请求。"""

    count: int = Field(10, ge=1, le=500, description="生成数量")
    label: Optional[str] = Field(None, max_length=50, description="发放备注")
    expires_at: Optional[datetime] = Field(None, description="过期时间(UTC)")


class ActivationCodeItem(BaseModel):
    """激活码列表项。"""

    id: str
    label: Optional[str] = None
    used: bool
    user_id: Optional[str] = None
    activated_at: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: Optional[str] = None


class DashboardStats(BaseModel):
    """看板统计响应。"""

    total_users: int
    active_today: int
    total_mistakes: int
    total_ai_sessions: int
    total_ai_messages: int
    total_attempts: int
    weekly_reports: int


class UserListItem(BaseModel):
    """用户列表项。"""

    id: str
    phone: str
    nickname: Optional[str] = None
    role: str
    status: str
    last_login_at: Optional[str] = None
    created_at: Optional[str] = None
    student: Optional[dict] = None


class UserListQuery(BaseModel):
    """用户列表查询参数。"""

    page: int = 1
    size: int = 20
    role: Optional[str] = None
    keyword: Optional[str] = None


class ConfigUpdate(BaseModel):
    """更新系统配置项请求。

    value 一律按字符串入库（存储契约），读取侧由
    ``system_config`` 按各键 cast 函数转型；写入门禁在
    ``admin.update_config`` 校验可解析性。
    """

    value: str = Field(..., min_length=1, max_length=500, description="配置值（字符串）")

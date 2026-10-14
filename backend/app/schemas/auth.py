"""鉴权域出入参 Schema（camelCase alias，见 schemas/common.BaseSchema）。"""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from app.schemas.common import BaseSchema


class LoginIn(BaseSchema):
    """``POST /api/auth/login`` 入参。

    Attributes:
        phone: 手机号（中国大陆 11 位）。
        code: 验证码（通过 ``/api/auth/code`` 获取，6 位数字）。
    """

    phone: str = Field(..., min_length=1, max_length=20, description="手机号")
    code: str = Field(..., min_length=1, max_length=10, description="验证码")


class ActivationEnterIn(BaseSchema):
    """激活码首次开通或后续登录。"""

    code: str = Field(..., min_length=8, max_length=40, description="激活码")
    nickname: Optional[str] = Field(default=None, max_length=50, description="学生称呼，首次激活必填")
    pin: str = Field(..., min_length=6, max_length=6, description="6位个人密码")


class SendCodeIn(BaseSchema):
    """``POST /api/auth/code`` 入参。"""

    phone: str = Field(..., min_length=1, max_length=20, description="手机号")


class RegisterIn(BaseSchema):
    """``POST /api/auth/register`` 入参（指定角色注册）。

    Attributes:
        phone: 手机号（中国大陆 11 位）。
        code: 验证码（通过 ``/api/auth/code`` 获取，6 位数字）。
        role: 注册角色，仅允许 ``student`` / ``parent``（admin 不开放自助注册）。
    """

    phone: str = Field(..., min_length=1, max_length=20, description="手机号")
    code: str = Field(..., min_length=1, max_length=10, description="验证码")
    role: str = Field(default="student", description="student/parent")


class AdminLoginIn(BaseSchema):
    """``POST /api/auth/admin/login`` 入参（管理员专用）。

    Attributes:
        phone: 管理员手机号。
        pin: 管理员个人密码（6 位以上，存取在 ``access_pin_hash``）。
    """

    phone: str = Field(..., min_length=1, max_length=20, description="管理员手机号")
    pin: str = Field(..., min_length=6, max_length=64, description="管理员个人密码")


class RefreshIn(BaseSchema):
    """``POST /api/auth/refresh`` 入参。

    兼容两种字段名：``refresh``（T02 契约）与 ``refreshToken``（T01 令牌对约定）。
    """

    refresh: Optional[str] = Field(default=None, max_length=2048, description="刷新令牌")
    refresh_token: Optional[str] = Field(default=None, max_length=2048, description="刷新令牌(别名)")

    def resolve(self) -> str:
        """取出实际的刷新令牌（两个字段任一即可）。"""
        return (self.refresh or self.refresh_token or "").strip()


class UserBrief(BaseSchema):
    """登录返回的用户摘要。"""

    user_id: str = Field(..., description="用户 ID")
    student_id: Optional[str] = Field(default=None, description="学生 ID")
    phone: Optional[str] = Field(default=None, description="手机号")
    nickname: Optional[str] = Field(default=None, description="昵称")
    avatar_url: Optional[str] = Field(default=None, description="头像")
    role: str = Field(..., description="student/parent/admin")
    grade: Optional[int] = Field(default=None, description="年级 1-12")
    stage: Optional[str] = Field(default=None, description="primary/junior/senior")


class TokenOut(BaseSchema):
    """令牌载荷。

    ``access``/``refresh`` 与 ``accessToken``/``refreshToken`` 是同一对令牌的两种键名，
    前端任选其一即可。
    """

    access: str = Field(..., description="访问令牌，2h")
    refresh: str = Field(..., description="刷新令牌，30d")
    access_token: str = Field(..., description="访问令牌(别名)")
    refresh_token: str = Field(..., description="刷新令牌(别名)")
    token_type: str = Field(default="Bearer", description="令牌类型")
    expires_in: int = Field(..., description="access 有效期(秒)")
    is_new_user: bool = Field(default=False, description="是否本次自动注册")
    user: Optional[UserBrief] = Field(default=None, description="用户摘要")


class SendCodeOut(BaseSchema):
    """验证码下发结果。"""

    sent: bool = Field(..., description="是否已下发")
    dev_code: Optional[str] = Field(default=None, description="dev 环境回带验证码，生产为 null")


__all__ = [
    "LoginIn",
    "ActivationEnterIn",
    "SendCodeIn",
    "RefreshIn",
    "UserBrief",
    "TokenOut",
    "SendCodeOut",
]

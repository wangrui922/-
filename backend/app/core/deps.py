"""FastAPI 鉴权依赖（★跨模块硬约定：JWT HS256，payload {sub, role, sid}）。

职责：
    * ``get_current_user``：从 ``Authorization: Bearer <token>`` 取令牌，
      用 ``app.core.security.verify_token`` 解码，返回 ``CurrentUser``；
      缺令牌 / 令牌非法 / 过期一律抛 ``ApiError(401)``。
    * ``require_role(*roles)``：依赖工厂，越权返 ``ApiError(403)``。

注意：本文件不强制依赖任何 ORM 模型，方便后续里程碑按需挂载。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from app.core.response import ApiError, CODE_FORBIDDEN, CODE_UNAUTHORIZED
from app.core.security import ROLE_STUDENT, TOKEN_TYPE_ACCESS, verify_token

# auto_error=False：缺失令牌时返回 None，由我们统一转成 401 业务异常，
# 避免 OAuth2 默认抛出的 403 与全局约定不符。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    """已鉴权的当前用户（来自 JWT payload 的核心字段）。

    Attributes:
        sub: 用户 ID（字符串，``users.id``）。
        role: 角色，student/parent/admin。
        sid: 关联学生 ID（字符串或 None）。
    """

    sub: str
    role: str
    sid: Optional[str]

    def is_admin(self) -> bool:
        """是否为管理员。"""
        return self.role == "admin"

    def is_student(self) -> bool:
        """是否为学生。"""
        return self.role == "student"


async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> CurrentUser:
    """解析 Bearer 令牌，返回当前登录用户。

    Raises:
        ApiError: code=401，缺失令牌或令牌无效/过期。
    """
    if not token or not isinstance(token, str):
        raise ApiError(CODE_UNAUTHORIZED, "缺少访问令牌")

    # 兼容个别客户端把 "Bearer " 前缀也一并塞进来的情况
    raw = token
    if raw.lower().startswith("bearer "):
        raw = raw[len("bearer "):].strip()

    payload = verify_token(raw, expected_type=TOKEN_TYPE_ACCESS)
    return CurrentUser(
        sub=str(payload.get("sub")),
        role=str(payload.get("role", ROLE_STUDENT)),
        sid=(str(payload["sid"]) if payload.get("sid") is not None else None),
    )


def require_role(*allowed_roles: str):
    """依赖工厂：要求当前用户具备指定角色之一，否则返 403。

    Args:
        *allowed_roles: 允许的角色集合；为空表示不限制（仅校验登录）。

    Returns:
        一个 FastAPI 依赖函数，返回通过校验的 ``CurrentUser``。
    """

    async def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if allowed_roles and user.role not in allowed_roles:
            raise ApiError(CODE_FORBIDDEN, "无权限访问该资源")
        return user

    return _checker


def require_admin() -> CurrentUser:
    """占位便捷依赖（实际由 require_role 工厂驱动）。"""
    return require_role("admin")  # pragma: no cover - 仅作可读性提示


# 便于类型标注引用
RoleList = List[str]

__all__ = [
    "CurrentUser",
    "get_current_user",
    "require_role",
    "require_admin",
    "oauth2_scheme",
]

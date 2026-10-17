"""FastAPI 鉴权依赖（★跨模块硬约定：JWT HS256，payload {sub, role, sid}）。

职责：
    * ``get_current_user``：从 ``Authorization: Bearer <token>`` 取令牌，
      用 ``app.core.security.verify_token`` 解码，返回 ``CurrentUser``；
      缺令牌 / 令牌非法 / 过期一律抛 ``ApiError(401)``。
    * ``require_role(*roles)``：依赖工厂，越权返 ``ApiError(403)``。
    * ``require_guardian_consent``：监护人同意门控（V6.5 合规闸门）。
      读 ``system_config`` 的 ``CONSENT_GATE_ENABLED``（默认关）/ ``CONSENT_VERSION``
      （默认 ``v1``）；开启后按 ``current.sub`` 查 ``guardian_consents``
      当前版本同意记录，没有则抛 ``ApiError(403)``，msg 固定为
      「使用练习功能需要监护人同意」（前端按此话术识别，勿漂移）。

注意：本文件顶部不 import 任何 ORM 模型（``require_guardian_consent`` 内部
lazy import），保持鉴权依赖层的轻量与无环。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import List, Optional

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.response import ApiError, CODE_FORBIDDEN, CODE_UNAUTHORIZED
from app.core.security import ROLE_STUDENT, TOKEN_TYPE_ACCESS, verify_token
from app.core.system_config import get_config
from app.db import get_db

# auto_error=False：缺失令牌时返回 None，由我们统一转成 401 业务异常，
# 避免 OAuth2 默认抛出的 403 与全局约定不符。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

#: 监护人同意门控的固定拒绝话术（★前端按此话术识别并弹监护人同意引导，勿漂移）
CONSENT_GATE_DENIED_MSG: str = "使用练习功能需要监护人同意"


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


async def require_guardian_consent(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """监护人同意门控（V6.5 合规闸门 · T13 留痕后的服务端强制锁）。

    行为：
        * ``CONSENT_GATE_ENABLED`` 为假（默认）→ 直接放行，零行为变化；
        * 为真 → 按 ``current.sub`` 查 ``guardian_consents`` 中
          ``(user_id, consent_version=CONSENT_VERSION)`` 是否有记录；
          没有则抛 ``ApiError(403)``，msg 固定为
          ``CONSENT_GATE_DENIED_MSG``（「使用练习功能需要监护人同意」）。

    版本耦合：``CONSENT_VERSION`` 变更（规则修订）后，旧版本的同意记录
    一律失效，需重新取得同意——与 ``compliance_service`` 按版本查询的口径一致。

    挂载方式：逐路由 ``dependencies=[Depends(require_guardian_consent)]``
    （**禁止** router 级挂载，那会波及 GET 读操作）。依赖缓存保证
    ``current`` / ``db`` 与路由入参是同一实例，不重复解析 JWT、不开新会话。

    Args:
        current: 当前登录用户（JWT payload ``{sub, role, sid}``）。
        db: 请求级数据库会话（与路由共享，FastAPI 依赖缓存保证）。

    Raises:
        ApiError: 403 —— 门控开启且当前账号没有当前版本的监护人同意记录。
    """
    if not get_config("CONSENT_GATE_ENABLED"):
        return

    version = str(get_config("CONSENT_VERSION") or "").strip() or "v1"

    # sub 不是合法 UUID 时不可能存在同意记录，按未同意处理（避免 500）
    try:
        uid = uuid.UUID(str(current.sub))
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_FORBIDDEN, CONSENT_GATE_DENIED_MSG) from exc

    # ORM 依赖放在函数内 lazy import：本文件顶部不依赖任何 ORM 模型
    from sqlalchemy import select

    from app.models.compliance import GuardianConsent

    agreed = db.execute(
        select(GuardianConsent.id).where(
            GuardianConsent.user_id == uid,
            GuardianConsent.consent_version == version,
        )
    ).scalar_one_or_none()
    if agreed is None:
        raise ApiError(CODE_FORBIDDEN, CONSENT_GATE_DENIED_MSG)


# 便于类型标注引用
RoleList = List[str]

__all__ = [
    "CurrentUser",
    "get_current_user",
    "require_role",
    "require_admin",
    "require_guardian_consent",
    "CONSENT_GATE_DENIED_MSG",
    "oauth2_scheme",
]

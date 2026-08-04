"""鉴权与密码工具。

JWT 硬约定：
    * 算法 HS256；
    * payload ``{"sub": user_id, "role": role, "sid": student_id, "exp": ...}``；
    * access 有效期 2h、refresh 有效期 30d（由配置项控制）；
    * 额外带 ``typ``（access/refresh）、``iat``、``iss``、``jti`` 便于治理。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Final, Optional

import jwt  # PyJWT

from app.core.config import settings
from app.core.response import ApiError, CODE_UNAUTHORIZED

TOKEN_TYPE_ACCESS: Final[str] = "access"
TOKEN_TYPE_REFRESH: Final[str] = "refresh"

ROLE_STUDENT: Final[str] = "student"
ROLE_PARENT: Final[str] = "parent"
ROLE_ADMIN: Final[str] = "admin"
VALID_ROLES: Final[frozenset] = frozenset({ROLE_STUDENT, ROLE_PARENT, ROLE_ADMIN})


def _utcnow() -> datetime:
    """当前 UTC 时间（带时区）。"""
    return datetime.now(timezone.utc)


def _default_expires(token_type: str) -> timedelta:
    """按 token 类型返回默认有效期。"""
    if token_type == TOKEN_TYPE_REFRESH:
        return timedelta(days=settings.refresh_token_expire_days)
    return timedelta(minutes=settings.access_token_expire_minutes)


def create_token(
    sub: Any,
    role: str = ROLE_STUDENT,
    sid: Optional[Any] = None,
    token_type: str = TOKEN_TYPE_ACCESS,
    expires_delta: Optional[timedelta] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """签发 JWT。

    Args:
        sub: 用户 ID（``users.id``），会被转成字符串。
        role: 角色，student/parent/admin。
        sid: 学生 ID（``students.id``），学生端必带，家长/管理员可为 None。
        token_type: ``access`` 或 ``refresh``。
        expires_delta: 自定义有效期；缺省按类型取 2h / 30d。
        extra: 需要额外塞进 payload 的字段（不会覆盖标准字段）。

    Returns:
        编码后的 JWT 字符串。
    """
    if token_type not in (TOKEN_TYPE_ACCESS, TOKEN_TYPE_REFRESH):
        raise ValueError(f"unsupported token_type: {token_type}")

    now = _utcnow()
    expire = now + (expires_delta if expires_delta is not None else _default_expires(token_type))

    payload: Dict[str, Any] = {
        "sub": str(sub),
        "role": role,
        "sid": str(sid) if sid is not None else None,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": settings.jwt_issuer,
        "jti": uuid.uuid4().hex,
    }
    if extra:
        for key, value in extra.items():
            payload.setdefault(key, value)

    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    # PyJWT 2.x 返回 str；1.x 返回 bytes，这里做一次兜底
    return token.decode("utf-8") if isinstance(token, bytes) else token


def create_access_token(sub: Any, role: str = ROLE_STUDENT, sid: Optional[Any] = None, **kwargs: Any) -> str:
    """签发 access token（默认 2h）。"""
    return create_token(sub=sub, role=role, sid=sid, token_type=TOKEN_TYPE_ACCESS, **kwargs)


def create_refresh_token(sub: Any, role: str = ROLE_STUDENT, sid: Optional[Any] = None, **kwargs: Any) -> str:
    """签发 refresh token（默认 30d）。"""
    return create_token(sub=sub, role=role, sid=sid, token_type=TOKEN_TYPE_REFRESH, **kwargs)


def create_token_pair(sub: Any, role: str = ROLE_STUDENT, sid: Optional[Any] = None) -> Dict[str, Any]:
    """一次性签发 access + refresh，供登录/刷新接口直接返回。"""
    return {
        "accessToken": create_access_token(sub, role, sid),
        "refreshToken": create_refresh_token(sub, role, sid),
        "tokenType": "Bearer",
        "expiresIn": settings.access_token_expire_minutes * 60,
    }


def verify_token(token: str, expected_type: Optional[str] = TOKEN_TYPE_ACCESS) -> Dict[str, Any]:
    """校验并解析 JWT。

    Args:
        token: 原始 token 字符串（不含 ``Bearer `` 前缀）。
        expected_type: 期望的 token 类型；传 None 表示不校验类型。

    Returns:
        解码后的 payload。

    Raises:
        ApiError: code=401，token 缺失/过期/签名错误/类型不符。
    """
    if not token or not isinstance(token, str):
        raise ApiError(CODE_UNAUTHORIZED, "缺少访问令牌")

    try:
        payload: Dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(CODE_UNAUTHORIZED, "登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError(CODE_UNAUTHORIZED, "访问令牌无效") from exc

    if expected_type is not None and payload.get("typ") != expected_type:
        raise ApiError(CODE_UNAUTHORIZED, f"令牌类型不匹配，需要 {expected_type}")

    return payload


def decode_token_unsafe(token: str) -> Dict[str, Any]:
    """不校验签名地解析 payload（仅用于日志/排障，禁止用于鉴权）。"""
    try:
        return jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except jwt.InvalidTokenError:
        return {}


# ---------------------------------------------------------------- 密码哈希
# V4.1 登录走「手机号 + 验证码」，密码仅作管理后台备用能力。
# 默认方案 pbkdf2_sha256（纯 Python，Windows 零编译），同时兼容校验历史 bcrypt 哈希。
_pwd_context = None


def _get_pwd_context():
    """惰性初始化 passlib CryptContext，避免 import 期做重活。"""
    global _pwd_context
    if _pwd_context is None:
        from passlib.context import CryptContext

        _pwd_context = CryptContext(
            schemes=["pbkdf2_sha256", "bcrypt"],
            default="pbkdf2_sha256",
            deprecated="auto",
        )
    return _pwd_context


def hash_password(plain_password: str) -> str:
    """生成密码哈希。"""
    if not plain_password:
        raise ValueError("password must not be empty")
    return _get_pwd_context().hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码；任何解析异常一律判为不通过。"""
    if not plain_password or not hashed_password:
        return False
    try:
        return bool(_get_pwd_context().verify(plain_password, hashed_password))
    except Exception:
        return False


__all__ = [
    "TOKEN_TYPE_ACCESS",
    "TOKEN_TYPE_REFRESH",
    "ROLE_STUDENT",
    "ROLE_PARENT",
    "ROLE_ADMIN",
    "VALID_ROLES",
    "create_token",
    "create_access_token",
    "create_refresh_token",
    "create_token_pair",
    "verify_token",
    "decode_token_unsafe",
    "hash_password",
    "verify_password",
]

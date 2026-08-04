"""鉴权接口（T02 · 2.1）。

    POST /api/auth/code     下发验证码（V4.1 mock 通道，只打日志）
    POST /api/auth/login    手机号 + 验证码登录，首次登录自动建号
    POST /api/auth/refresh  用 refresh 换新令牌
    GET  /api/auth/me       当前登录态自检（受保护，无 token 返 401）

硬约定：
    * 响应恒为 ``{"code":0,"msg":"ok","data":{}}``，失败由 ``ApiError`` 经全局 handler 转换；
    * access 2h / refresh 30d，HS256，payload ``{sub: user_id, role, sid: student_id|null}``；
    * **dev 环境万能码 ``000000``**（``settings.dev_sms_code``），无短信通道也能联调。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_BAD_REQUEST, ok
from app.db import get_db
from app.schemas.auth import LoginIn, RefreshIn, SendCodeIn
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/code")
async def send_code(payload: SendCodeIn, db: Session = Depends(get_db)) -> dict:
    """下发登录验证码。

    V4.1 未接真实短信：mock 通道只打日志，dev 环境直接回带万能码方便前端自测。
    """
    return ok(auth_service.send_login_code(payload.phone))


@router.post("/login")
async def login(payload: LoginIn, db: Session = Depends(get_db)) -> dict:
    """手机号 + 验证码登录（首次登录自动注册 student）。

    Returns:
        ``{"code":0,"msg":"ok","data":{access, refresh, accessToken, refreshToken,
        tokenType, expiresIn, isNewUser, user}}``。
    """
    data = auth_service.login_by_code(db, phone=payload.phone, code=payload.code)
    return ok(data)


@router.post("/refresh")
async def refresh(payload: RefreshIn, db: Session = Depends(get_db)) -> dict:
    """用 refresh token 换一对新令牌。"""
    token = payload.resolve()
    if not token:
        raise ApiError(CODE_BAD_REQUEST, "缺少刷新令牌")
    return ok(auth_service.refresh_tokens(db, token))


@router.get("/me")
async def me(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """返回当前令牌解析出的登录态（受保护接口，无 token 返 401）。"""
    return ok({"userId": current.sub, "role": current.role, "studentId": current.sid})


__all__ = ["router"]

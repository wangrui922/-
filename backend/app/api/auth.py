"""鉴权接口。

    POST /api/auth/code     下发验证码（随机 6 位，5min 过期；dev 环境回带方便自测）
    POST /api/auth/login    手机号 + 验证码登录，首次登录自动建号
    POST /api/auth/activation/activate  激活码首次开通 / 后续登录
    POST /api/auth/refresh  用 refresh 换新令牌
    GET  /api/auth/me       当前登录态自检（受保护，无 token 返 401）

硬约定：
    * 响应恒为 ``{"code":0,"msg":"ok","data":{}}``，失败由 ``ApiError`` 经全局 handler 转换；
    * access 2h / refresh 30d，HS256，payload ``{sub: user_id, role, sid: student_id|null}``；
    * 验证码为随机 6 位数字，5 分钟过期，一次性使用；
    * dev 环境 ``/api/auth/code`` 返回体带 ``devCode`` 字段回传验证码方便前端自测；
    * 限流：发码 60s 冷却 + 1h 5 次；登录 15min 10 次。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_BAD_REQUEST, ok
from app.db import get_db
from app.schemas.auth import ActivationEnterIn, LoginIn, RefreshIn, RegisterIn, SendCodeIn
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/code")
async def send_code(payload: SendCodeIn, db: Session = Depends(get_db)) -> dict:
    """下发验证码。

    随机 6 位验证码，5 分钟过期。dev 环境回带 ``devCode`` 方便前端自测。
    限流：同一手机号 60s 冷却 + 1h 最多 5 次。
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


@router.post("/activation/activate")
async def activation_login(payload: ActivationEnterIn, db: Session = Depends(get_db)) -> dict:
    """用激活码进入学习。

    首次输入有效码时创建学生账号并设置个人密码；之后同一串码配合个人密码可在
    新手机或电脑重新登录。明文激活码不会写入数据库。
    """
    return ok(auth_service.enter_by_activation_code(db, payload.code, payload.pin, payload.nickname))


@router.post("/register")
async def register(payload: RegisterIn, db: Session = Depends(get_db)) -> dict:
    """手机号 + 验证码注册（指定角色 student / parent）。

    家长注册走此接口并带 ``role=parent``；学生也可显式注册。
    已存在该手机号的账号不会重复创建，直接返回其令牌。
    """
    data = auth_service.register_user_by_code(
        db, phone=payload.phone, code=payload.code, role=payload.role
    )
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

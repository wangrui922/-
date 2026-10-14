"""FastAPI 应用入口。

装配顺序：
    1. 创建 ``FastAPI`` 实例（标题/版本，开放 ``/docs``）。
    2. 挂载 CORS（允许 localhost 前端开发服务器）。
    3. 注册全局异常处理器（业务异常/HTTP 异常/校验异常/未捕获异常
       -> 统一 ``{code,msg,data}``，HTTP 状态码与 code 同步）。
    4. 挂载 API 路由（``/api`` 前缀）。

T04 说明：
    学习画像三个接口（``/api/profile/*``）已在 ``app.api.__init__`` 的
    ``api_router`` 里收口，本文件挂 ``api_router`` 即自动生效，无需单独 include。
    这里额外 ``import app.models``，保证 ``student_mastery`` 等新表在
    ``Base.metadata.create_all`` 时一定已注册（不依赖 router 的 import 顺序）。
"""

from __future__ import annotations

import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models as _models  # noqa: F401
from app.api import api_router
from app.core.config import settings
from app.core.response import register_exception_handlers
from app.tasks import cron_jobs


def _lan_cors_origins() -> list[str]:
    ports = [5173, 5174, 5175, 4173, 8000]
    hosts: set[str] = set()
    try:
        hostname = socket.gethostname()
        hosts.add(socket.gethostbyname(hostname))
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if not ip.startswith("127.") and ":" not in ip:
                hosts.add(ip)
    except Exception:
        pass
    return [f"http://{h}:{p}" for h in sorted(hosts) for p in ports]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """应用生命周期：启动定时任务，关闭时清理。"""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    # 每天凌晨 2 点：掌握度刷新 + 周日周报生成
    scheduler.add_job(cron_jobs.daily_sync, "cron", hour=2, minute=0)
    # 每天凌晨 3 点：清理 30 天前的 AI 会话
    scheduler.add_job(cron_jobs.cleanup_old_sessions, "cron", hour=3, minute=0)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


def create_app() -> FastAPI:
    """工厂函数：构建并装配 FastAPI 应用。"""
    # ---- 公网临时试用：穿透后前端域名随机，CORS_ALLOW_ALL 全放开 ----------
    # 仅 dev 生效，且配合 allow_credentials=False（鉴权走 Bearer token，
    # 不依赖 cookie，故关闭 credentials 不影响登录）。生产环境切勿开启。
    if settings.is_dev and settings.cors_allow_all:
        allow_origins: list[str] = ["*"]
        allow_credentials = False
    else:
        allow_origins = settings.cors_origin_list
        if settings.is_dev and "*" not in allow_origins:
            allow_origins = list(dict.fromkeys([*allow_origins, *_lan_cors_origins()]))
        if settings.cors_extra_origin_list:
            allow_origins = list(dict.fromkeys([*allow_origins, *settings.cors_extra_origin_list]))
        allow_credentials = True

    # ---- 生产环境安全检查 ----------------------------------------------
    if not settings.jwt_secret_safe:
        raise RuntimeError(
            "生产环境 (APP_ENV=prod) 必须设置安全的 JWT_SECRET "
            "（至少 16 字符，不能使用默认值）。请在环境变量或 .env 中配置。"
        )

    app = FastAPI(
        title="AI 数学教练",
        version="4.1",
        description="AI 数学教练后端服务（V4.1）",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=_lifespan,
    )

    # ---- CORS：白名单取自环境变量 CORS_ORIGINS（逗号分隔），默认含常用本地地址 ----
    # dev 模式额外自动放行本机局域网 IP 的各前端端口，手机连同一 WiFi 即可跨域试用。
    # 生产部署把前端域名（如 https://xxx.edgeone.cool）加入 CORS_ORIGINS 即可。
    # 公网临时试用见 create_app 顶部 CORS_ALLOW_ALL 分支。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # ---- 全局异常 -> 统一响应 --------------------------------------------
    register_exception_handlers(app)

    # ---- 路由 ------------------------------------------------------------
    app.include_router(api_router)

    return app


#: 模块级单例，供 uvicorn / 测试直接引用
app = create_app()


__all__ = ["app", "create_app"]

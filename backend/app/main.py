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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models as _models  # noqa: F401  确保全部 ORM 模型注册进 Base.metadata
from app.api import api_router
from app.core.config import settings
from app.core.response import register_exception_handlers

#: 本地前端常用开发地址（Vite 默认 5173，CRA/Next 常用 3000）
_LOCAL_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]


def create_app() -> FastAPI:
    """工厂函数：构建并装配 FastAPI 应用。"""
    app = FastAPI(
        title="AI 数学教练",
        version="4.1",
        description="AI 数学教练后端服务（V4.1）",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # ---- CORS：允许 localhost 前端跨域调用 -------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_LOCAL_ORIGINS,
        allow_credentials=True,
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

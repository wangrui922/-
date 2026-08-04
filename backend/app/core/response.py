"""统一响应封装与全局异常处理。

硬约定（全团队必守）：
    * 响应体恒为 ``{"code": 0, "msg": "ok", "data": {}}``；
    * HTTP 状态码与 ``code`` 同步：成功 200，失败 400/401/403/429/500；
    * ``data`` 永不为 ``null``，无内容时给 ``{}``。
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

try:  # orjson 更快，装不上时优雅退回标准 JSONResponse
    from fastapi.responses import ORJSONResponse as _JSONResponse
except Exception:  # pragma: no cover - 仅在缺少 orjson 时触发
    from fastapi.responses import JSONResponse as _JSONResponse

JSONResponseClass = _JSONResponse

logger = logging.getLogger("app.response")

# ---------------------------------------------------------------- 错误码字典
CODE_OK: int = 0
CODE_BAD_REQUEST: int = 400
CODE_UNAUTHORIZED: int = 401
CODE_FORBIDDEN: int = 403
CODE_NOT_FOUND: int = 404
CODE_TOO_MANY_REQUESTS: int = 429
CODE_SERVER_ERROR: int = 500

CODE_MESSAGES: Dict[int, str] = {
    CODE_OK: "ok",
    CODE_BAD_REQUEST: "参数非法",
    CODE_UNAUTHORIZED: "未登录或登录已过期",
    CODE_FORBIDDEN: "无权限",
    CODE_NOT_FOUND: "资源不存在",
    CODE_TOO_MANY_REQUESTS: "已超出今日使用配额",
    CODE_SERVER_ERROR: "服务开小差了，请稍后再试",
}

#: 允许直接作为 HTTP 状态码返回的业务码
_HTTP_SYNCED_CODES = {
    CODE_BAD_REQUEST,
    CODE_UNAUTHORIZED,
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_TOO_MANY_REQUESTS,
    CODE_SERVER_ERROR,
}


def http_status_for(code: int) -> int:
    """把业务 code 映射成 HTTP 状态码（保持两者同步）。"""
    if code == CODE_OK:
        return 200
    if code in _HTTP_SYNCED_CODES:
        return code
    return CODE_SERVER_ERROR


class ApiError(Exception):
    """业务异常。抛出后由全局 handler 统一转成 ``{code,msg,data}``。

    Args:
        code: 业务错误码，取值见 ``CODE_*`` 常量。
        msg: 面向用户的提示语；缺省用 ``CODE_MESSAGES`` 中的默认文案。
        data: 附加数据，默认 ``{}``。
        http_status: 强制指定 HTTP 状态码；缺省与 ``code`` 同步。
    """

    def __init__(
        self,
        code: int = CODE_SERVER_ERROR,
        msg: Optional[str] = None,
        data: Optional[Any] = None,
        http_status: Optional[int] = None,
    ) -> None:
        self.code: int = int(code)
        self.msg: str = msg or CODE_MESSAGES.get(self.code, "请求失败")
        self.data: Any = {} if data is None else data
        self.http_status: int = http_status or http_status_for(self.code)
        super().__init__(self.msg)

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return f"<ApiError code={self.code} msg={self.msg!r}>"


# ---------------------------------------------------------------- 便捷构造器
def envelope(code: int = CODE_OK, msg: str = "ok", data: Any = None) -> Dict[str, Any]:
    """构造统一响应字典（不带 HTTP 语义，供直接 return 使用）。"""
    return {"code": int(code), "msg": msg, "data": {} if data is None else data}


def ok(data: Any = None, msg: str = "ok") -> Dict[str, Any]:
    """成功响应：``{"code":0,"msg":"ok","data":{...}}``。"""
    return envelope(CODE_OK, msg, data)


def fail(code: int = CODE_SERVER_ERROR, msg: Optional[str] = None, data: Any = None) -> Dict[str, Any]:
    """失败响应字典（HTTP 状态码需调用方自行同步，通常直接抛 ``ApiError``）。"""
    return envelope(code, msg or CODE_MESSAGES.get(code, "请求失败"), data)


def json_response(
    code: int = CODE_OK,
    msg: str = "ok",
    data: Any = None,
    http_status: Optional[int] = None,
) -> JSONResponseClass:
    """构造带正确 HTTP 状态码的 JSON 响应。"""
    return JSONResponseClass(
        status_code=http_status or http_status_for(code),
        content=envelope(code, msg, data),
    )


# ---------------------------------------------------------------- 全局处理器
def register_exception_handlers(app: FastAPI) -> None:
    """把所有异常出口收敛到统一响应格式上。

    覆盖四类：业务 ``ApiError``、FastAPI ``HTTPException``、
    请求参数校验失败、以及任何未捕获异常。
    """

    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponseClass:
        logger.info("ApiError %s %s -> code=%s msg=%s", request.method, request.url.path, exc.code, exc.msg)
        return json_response(exc.code, exc.msg, exc.data, http_status=exc.http_status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponseClass:
        status = int(exc.status_code)
        code = status if status in _HTTP_SYNCED_CODES else (CODE_OK if status < 400 else CODE_SERVER_ERROR)
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail else CODE_MESSAGES.get(code, "请求失败")
        return json_response(code, detail, {}, http_status=status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponseClass:
        errors = []
        for err in exc.errors():
            loc = ".".join(str(item) for item in err.get("loc", ()) if item != "body")
            errors.append({"field": loc or "body", "message": err.get("msg", "invalid")})
        first = errors[0]["message"] if errors else "参数非法"
        field = errors[0]["field"] if errors else "body"
        return json_response(
            CODE_BAD_REQUEST,
            f"参数非法: {field} {first}",
            {"errors": errors},
            http_status=CODE_BAD_REQUEST,
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponseClass:
        logger.error(
            "Unhandled error on %s %s: %s\n%s",
            request.method,
            request.url.path,
            exc,
            traceback.format_exc(),
        )
        return json_response(
            CODE_SERVER_ERROR,
            CODE_MESSAGES[CODE_SERVER_ERROR],
            {},
            http_status=CODE_SERVER_ERROR,
        )


__all__ = [
    "ApiError",
    "CODE_OK",
    "CODE_BAD_REQUEST",
    "CODE_UNAUTHORIZED",
    "CODE_FORBIDDEN",
    "CODE_NOT_FOUND",
    "CODE_TOO_MANY_REQUESTS",
    "CODE_SERVER_ERROR",
    "CODE_MESSAGES",
    "envelope",
    "ok",
    "fail",
    "json_response",
    "http_status_for",
    "register_exception_handlers",
    "JSONResponseClass",
]

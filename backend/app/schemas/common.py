"""统一出入参 Pydantic 模型（★API 层 camelCase 约定）。

约定：
    * 所有对外响应体形态均为 ``ApiResp``：``{"code":0,"msg":"ok","data":{}}``。
    * 字段命名走 camelCase（通过 ``alias_generator`` 在 base 配置中统一转换），
      同时 ``populate_by_name=True`` 允许用原名赋值，方便内部构造。
    * DB 层仍是 snake_case，转换发生在序列化/反序列化边界（由 pydantic 完成）。
"""

from __future__ import annotations

import re
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


def _to_camel(name: str) -> str:
    """snake_case -> camelCase。

    已经是单个单词或 camelCase 的不受影响。
    """
    parts = re.split(r"_+", name)
    if len(parts) == 1:
        return name
    head = parts[0].lower()
    tail = "".join(word[:1].upper() + word[1:] for word in parts[1:] if word)
    return head + tail


class BaseSchema(BaseModel):
    """所有对外 Schema 的基类：统一 camelCase alias。"""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="ignore",
        from_attributes=True,
    )


class ApiResp(BaseSchema, Generic[T]):
    """统一响应外壳（泛型）。

    Examples:
        >>> ApiResp(code=0, msg="ok", data={"db": "sqlite"}).model_dump(by_alias=True)
        {'code': 0, 'msg': 'ok', 'data': {'db': 'sqlite'}}
    """

    code: int = 0
    msg: str = "ok"
    data: Optional[T] = None


class HealthData(BaseSchema):
    """``/api/health`` 的 data 载荷。"""

    db: str


class ErrorDetail(BaseSchema):
    """字段级错误描述（参数校验失败时使用）。"""

    field: str
    message: str


class ErrorResp(BaseSchema):
    """业务失败时的 data 结构（含字段级错误列表）。"""

    errors: list[ErrorDetail] = []


def api_resp(data: Any = None, code: int = 0, msg: str = "ok") -> dict:
    """构造一个可直接 return 的响应字典（camelCase 已是字段原名）。"""
    return {"code": code, "msg": msg, "data": data}


__all__ = [
    "BaseSchema",
    "ApiResp",
    "HealthData",
    "ErrorDetail",
    "ErrorResp",
    "api_resp",
]

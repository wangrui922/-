"""系统探活接口。

仅做连通性探测，**不读写任何业务表**（业务查询属后续里程碑）。
行为：
    GET /api/health
    -> 200 {"code":0,"msg":"ok","data":{"db": <"postgres"|"sqlite">}}
    db 取值由 ``app.db.db_kind()`` 依据 ``DATABASE_URL`` 判断。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.response import ok
from app.db import db_kind, ping
from app.schemas.common import HealthData

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    """探活：验证数据库引擎可连通，并回传当前 DB 类型。"""
    # 通过引擎执行 SELECT 1 验证连通（失败仅记日志，不阻断探活）
    ping()
    return ok(HealthData(db=db_kind()).model_dump())

"""Cron 触发端点（供 SCF 云函数 / 外部调度器调用）。

仅接受 ``X-Cron-Token`` 请求头，与 ``.env.production`` 的 ``CRON_TOKEN`` 一致。
执行与 APScheduler ``daily_sync`` 完全相同的逻辑，作为云端定时触发的兜底入口。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from app.core.response import CODE_FORBIDDEN
from app.tasks import cron_jobs

logger = logging.getLogger("app.api.cron")

router = APIRouter(prefix="/cron", tags=["cron"])

# 读取注入的 CRON_TOKEN；未配置时端点返回 503（防误开放）
_CRON_TOKEN = os.environ.get("CRON_TOKEN", "")


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"code": CODE_FORBIDDEN, "msg": "invalid cron token", "data": None},
    )


@router.post("/daily-sync")
def daily_sync(x_cron_token: str = Header(default="")) -> JSONResponse:
    """触发每日同步：掌握度刷新 + 周报 + 会话清理。"""
    if not _CRON_TOKEN or x_cron_token != _CRON_TOKEN:
        return _unauthorized()

    try:
        sync_result: Dict[str, Any] = cron_jobs.daily_sync()
        cleanup_count: int = cron_jobs.cleanup_old_sessions()
        result = {**sync_result, "sessions_cleaned": cleanup_count}
        logger.info("cron daily-sync finished: %s", result)
        return JSONResponse(
            status_code=200,
            content={"code": 0, "msg": "ok", "data": result},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cron daily-sync failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "msg": str(exc), "data": None},
        )
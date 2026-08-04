"""家长周报接口（Epic6 · T4/T5）。

    POST /api/student/report/generate   生成周报（鉴权：学生本人）
    GET  /api/report/share/{token}      ★公开只读，家长免登录
    GET  /api/student/report/history    历史周报列表（P1，鉴权：学生本人）

硬约定：
    * 公开路由 ``/report/share/{token}`` 的函数签名里**绝对不能出现**
      ``Depends(get_current_user)``，也不得调用 ``_require_student``——
      家长是未登录访问，挂上鉴权依赖会直接吃 401（K1 / S14）。
    * token 查不到 / 已过期 → **统一同一句 404 文案**，不区分两种情况，
      ``data`` 给 ``{}``，不回显 token 本身，日志里只打前 6 位（S15）。
    * 生成失败**不抛 500**：``db.rollback()`` + ``fail(5002, ..., 空壳)``，
      HTTP 仍 200（沿用 ``api/profile.py`` 的降级约定，S7）。
    * 用同步 ``def`` 路由（FastAPI 自动丢线程池），与 profile.py / mistake.py 一致（S9）。
    * 出参一律 snake_case。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.orm import Session

from app.core.constants import CODE_REPORT_GENERATE_FAILED
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_FORBIDDEN, CODE_NOT_FOUND, fail, ok
from app.db import get_db
from app.schemas.report import GenerateReportRequest
from app.services import profile_service, report_service

logger = logging.getLogger("app.api.report")

router = APIRouter(tags=["report"])

#: token 失效的统一对外文案（不区分「不存在」与「已过期」，防探测）
TOKEN_INVALID_MESSAGE = "这个链接已失效，请让孩子重新生成一份"


# ==================================================================== 内部工具
def _require_student(current: CurrentUser) -> Any:
    """校验「必须是绑定了学生的学生账号」，并取出学生 ID。

    与 ``api/profile.py::_require_student`` 同口径（非学生 / 未绑定 → 403）。

    Args:
        current: 当前登录用户。

    Returns:
        ``uuid.UUID`` 形态的学生 ID。

    Raises:
        ApiError: code=403，角色不是 student 或未绑定学生或 sid 非法。
    """
    if not current.is_student():
        raise ApiError(CODE_FORBIDDEN, "仅学生本人可生成家长周报")
    if not current.sid:
        raise ApiError(CODE_FORBIDDEN, "当前账号未绑定学生")
    try:
        return profile_service.to_uuid(current.sid)
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_FORBIDDEN, "当前账号未绑定学生") from exc


def _mask_token(token: Optional[str]) -> str:
    """日志脱敏：只打 token 前 6 位（S15）。"""
    value = str(token or "")
    return f"{value[:6]}***" if value else "<empty>"


def _empty_generate_payload() -> Dict[str, Any]:
    """生成失败时的「空壳」载荷（字段齐全、值为空，前端不至于取不到键）。"""
    return {
        "report_id": "",
        "share_token": "",
        "share_url": "",
        "period_start": None,
        "period_end": None,
        "is_partial": False,
        "expires_at": None,
        "cached": False,
        "ai_source": "fallback",
    }


# ==================================================================== 生成周报
@router.post("/student/report/generate")
def generate_report(
    body: GenerateReportRequest = Body(default_factory=GenerateReportRequest),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """生成（或复用）本周家长周报，返回可直接转发给家长的分享链接。

    幂等：同一学生同一周已有记录且 ``force=false`` → 直接返回缓存，不烧模型 token；
    重新生成**不会更换** ``share_token``（家长手上的链接必须一直可用）。

    Returns:
        ``{"code":0,"msg":"ok","data":{
            "report_id","share_token","share_url","period_start","period_end",
            "is_partial","expires_at","cached","ai_source"}}``

    Raises:
        ApiError: 401 未登录；403 非学生本人。
    """
    student_id = _require_student(current)

    try:
        record, cached = report_service.generate_weekly_report(
            db,
            student_id,
            force=bool(body.force),
        )
    except Exception:  # noqa: BLE001 - 画像/AI 侧异常一律降级，不给前端 500
        db.rollback()
        logger.exception("周报生成失败 student=%s", student_id)
        return fail(
            CODE_REPORT_GENERATE_FAILED,
            "周报生成暂时不可用，请稍后再试",
            _empty_generate_payload(),
        )

    payload = report_service.build_report_payload(db, record)
    narrative = payload.get("narrative") or {}

    return ok(
        {
            "report_id": str(record.id),
            "share_token": record.token,
            "share_url": report_service.build_share_url(record.token),
            "period_start": (payload.get("period") or {}).get("start"),
            "period_end": (payload.get("period") or {}).get("end"),
            "is_partial": bool((payload.get("period") or {}).get("is_partial")),
            "expires_at": payload.get("expires_at"),
            "cached": bool(cached),
            "ai_source": str(narrative.get("source") or "fallback"),
        }
    )


# ============================================================ 公开分享（★无登录）
@router.get("/report/share/{token}")
def get_shared_report(
    token: str = Path(..., description="分享令牌"),
    db: Session = Depends(get_db),
) -> dict:
    """家长免登录查看周报。

    ⚠️ 本函数签名里**没有也不能有** ``Depends(get_current_user)``。

    Returns:
        ``{"code":0,"msg":"ok","data":{...}}``，结构见 ``schemas/report.SharedReportData``。

    Raises:
        ApiError: 404，token 不存在 / 已过期 / 被篡改（统一同一句文案，data 为空）。
    """
    record = report_service.get_report_by_token(db, token)
    if record is None:
        logger.info("分享链接无效 token=%s", _mask_token(token))
        raise ApiError(CODE_NOT_FOUND, TOKEN_INVALID_MESSAGE, {})

    payload = report_service.build_report_payload(db, record)
    # U4：家长每打开一次 +1。打点失败不影响正文（内部已 try/except）。
    report_service.touch_view_count(db, record)

    return ok(payload)


# ==================================================================== 历史列表
@router.get("/student/report/history")
def list_history(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """该学生的历史周报摘要，按周期倒序（P1 · R-08）。

    Returns:
        ``{"code":0,"msg":"ok","data":{"items":[...],"total":n}}``

    Raises:
        ApiError: 401 未登录；403 非学生本人。
    """
    student_id = _require_student(current)

    try:
        items = report_service.list_reports(db, student_id)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("周报历史查询失败 student=%s", student_id)
        return fail(CODE_REPORT_GENERATE_FAILED, "周报列表暂时不可用，请稍后再试", {"items": [], "total": 0})

    return ok({"items": items, "total": len(items)})


__all__ = ["router"]

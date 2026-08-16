"""学习画像看板接口（T04 · B8）。

    GET /api/profile/mastery          知识点掌握度
    GET /api/profile/error-patterns   错因分布（恒 6 类）
    GET /api/profile/recommendations  练习推荐 Top N

硬约定：
    * 需要登录，且必须是**学生本人**（``role == "student"`` 且 ``sid`` 非空）；
      画像是学生私有资源，非学生角色 / 未绑定学生一律 **403**。
      （注：T03 的 ``require_student_id`` 返 400，这里**不改**它，
        避免破坏既有 T03 测试；本模块用自己的 ``_require_student``。）
    * 出参一律 **snake_case**（与 /api/chat、/api/exercise 对齐），
      不复用 ``schemas/common.BaseSchema`` 的 camelCase alias。
    * **无数据不报错**：返回空数组 + 空 summary，前端画空态。
    * 计算异常时返回 ``code=5001``、**HTTP 仍是 200**，
      让看板只把这一块降级成局部空态，而不是整页 500。
    * 用同步 ``def`` 路由（FastAPI 自动丢线程池），与 mistake.py 保持一致。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.constants import (
    CODE_PROFILE_COMPUTE_FAILED,
    ERROR_PATTERN_DEFAULT_DAYS,
    MODE_FRESH,
    RECOMMEND_TOP_N,
)
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_FORBIDDEN, fail, ok
from app.db import get_db
from app.services import profile_service
from app.services.mastery_engine import ErrorPattern, Recommendation

logger = logging.getLogger("app.api.profile")

router = APIRouter(tags=["profile"])


# ==================================================================== 内部工具
def _require_student(current: CurrentUser) -> Any:
    """校验「必须是绑定了学生的学生账号」，并取出学生 ID。

    Args:
        current: 当前登录用户。

    Returns:
        ``uuid.UUID`` 形态的学生 ID。

    Raises:
        ApiError: code=403，角色不是 student 或未绑定学生或 sid 非法。
    """
    if not current.is_student():
        raise ApiError(CODE_FORBIDDEN, "仅学生本人可查看学习画像")
    if not current.sid:
        raise ApiError(CODE_FORBIDDEN, "当前账号未绑定学生")
    try:
        return profile_service.to_uuid(current.sid)
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_FORBIDDEN, "当前账号未绑定学生") from exc


def _iso(value: Any) -> Optional[str]:
    """把 datetime 安全转成 ISO 8601 字符串。"""
    return value.isoformat() if value is not None else None


def _pattern_to_dict(pattern: ErrorPattern) -> Dict[str, Any]:
    """``ErrorPattern`` 数据类 → snake_case 出参。"""
    return {
        "category": pattern.category,
        "label": pattern.label,
        "count": int(pattern.count),
        "ratio": round(float(pattern.ratio), 4),
        "sample_hits": list(pattern.sample_hits),
    }


def _recommendation_to_dict(item: Recommendation) -> Dict[str, Any]:
    """``Recommendation`` 数据类 → snake_case 出参。"""
    return {
        "kp_id": item.kp_id,
        "kp_name": item.kp_name,
        "reason": item.reason,
        "priority": round(float(item.priority), 4),
        "score": round(float(item.score), 4),
        "error_rate": round(float(item.error_rate), 4),
        "top_error_category": item.top_error_category,
        "suggested_question_ids": list(item.suggested_question_ids),
    }


def _degraded(student_id: Any, payload: Dict[str, Any], where: str) -> Dict[str, Any]:
    """计算失败时的统一降级响应（HTTP 200 + code 5001）。

    Args:
        student_id: 学生 ID。
        payload: 该接口的「空壳」数据结构（字段齐全、值为空）。
        where: 出错位置，仅用于日志。

    Returns:
        ``{"code":5001,"msg":"...","data":{...}}``。
    """
    logger.exception("画像计算失败 at=%s student=%s", where, student_id)
    body = {"student_id": str(student_id), "computed_at": None, "mode": MODE_FRESH}
    body.update(payload)
    return fail(CODE_PROFILE_COMPUTE_FAILED, "画像计算暂时不可用，请稍后再试", body)


# ==================================================================== 掌握度
@router.get("/profile/mastery")
def get_mastery(
    force: int = Query(0, ge=0, le=1, description="1=跳过缓存强制重算"),
    kp_id: Optional[List[str]] = Query(None, description="只看这些知识点，可重复传"),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """知识点掌握度列表（按薄弱程度排序：等级升序 → 错误率降序）。

    Returns:
        ``{"code":0,"msg":"ok","data":{
            "student_id": "...",
            "mastery": [{"kp_id","kp_name","score","level","attempts",
                         "correct_count","error_rate","effective_attempts",
                         "last_practiced_at"}],
            "summary": {"total_kp": n, "level_count": {"0":..,"1":..,"2":..,"3":..}},
            "computed_at": "...", "mode": "fresh|cache"}}``

    Raises:
        ApiError: 401 未登录；403 非学生本人。
    """
    student_id = _require_student(current)
    empty = {"mastery": [], "summary": {"total_kp": 0, "level_count": {"0": 0, "1": 0, "2": 0, "3": 0}}}

    try:
        view = profile_service.get_mastery(
            db,
            student_id=student_id,
            force=bool(force),
            kp_ids=kp_id,
        )
    except Exception:  # noqa: BLE001 - 看板需要局部降级而非整页 500
        db.rollback()
        return _degraded(student_id, empty, "mastery")

    return ok(
        {
            "student_id": view.student_id,
            "mastery": view.items,
            "summary": view.summary,
            "computed_at": _iso(view.computed_at),
            "mode": view.mode,
        }
    )


# ==================================================================== 学情雷达图
@router.get("/profile/radar")
def get_radar(
    force: int = Query(0, ge=0, le=1, description="1=跳过缓存强制重算"),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """学情能力雷达图（axis=全部 L1 知识点，value=掌握度百分比 0~100）。

    面向家长/学生的「一眼看清孩子强弱项」视图，纯算法无外网依赖。

    Returns:
        ``{"code":0,"msg":"ok","data":{
            "student_id": "...",
            "axes": [{"kp_id","kp_name","score","level","practiced"}],
            "summary": {"total_kp","practiced_kp","overall_score","level_count"},
            "computed_at": "...", "mode": "fresh|cache"}}``

    Raises:
        ApiError: 401 未登录；403 非学生本人。
    """
    student_id = _require_student(current)
    empty = {
        "axes": [],
        "summary": {"total_kp": 0, "practiced_kp": 0, "overall_score": 0.0,
                    "level_count": {"0": 0, "1": 0, "2": 0, "3": 0}},
    }

    try:
        view = profile_service.get_radar(db, student_id=student_id, force=bool(force))
    except Exception:  # noqa: BLE001 - 看板需要局部降级而非整页 500
        db.rollback()
        return _degraded(student_id, empty, "radar")

    return ok(
        {
            "student_id": view.student_id,
            "axes": [
                {
                    "kp_id": axis.kp_id,
                    "kp_name": axis.kp_name,
                    "score": axis.score,
                    "level": axis.level,
                    "practiced": axis.practiced,
                }
                for axis in view.axes
            ],
            "summary": view.summary,
            "computed_at": _iso(view.computed_at),
            "mode": view.mode,
        }
    )


# ==================================================================== 错因分布
@router.get("/profile/error-patterns")
def get_error_patterns(
    range_days: int = Query(
        ERROR_PATTERN_DEFAULT_DAYS, ge=1, le=3650, description="统计窗口天数，默认 90"
    ),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """错因五类分布（``count`` 为 0 的类别也会返回，保证环形图完整）。

    Returns:
        ``{"code":0,"msg":"ok","data":{
            "student_id": "...",
            "error_patterns": [{"category","label","count","ratio","sample_hits"}],
            "total_mistakes": n, "unmapped_mistakes": m,
            "range_days": 90, "computed_at": "...", "mode": "fresh"}}``

    Raises:
        ApiError: 401 未登录；403 非学生本人。
    """
    student_id = _require_student(current)
    empty = {
        "error_patterns": [],
        "total_mistakes": 0,
        "unmapped_mistakes": 0,
        "range_days": int(range_days),
    }

    try:
        view = profile_service.get_error_patterns(db, student_id=student_id, range_days=range_days)
    except Exception:  # noqa: BLE001
        db.rollback()
        return _degraded(student_id, empty, "error-patterns")

    return ok(
        {
            "student_id": view.student_id,
            "error_patterns": [_pattern_to_dict(item) for item in view.patterns],
            "total_mistakes": view.total_mistakes,
            "unmapped_mistakes": view.unmapped_mistakes,
            "range_days": view.range_days,
            "computed_at": _iso(view.computed_at),
            "mode": view.mode,
        }
    )


# ==================================================================== 推荐
@router.get("/profile/recommendations")
def get_recommendations(
    limit: int = Query(RECOMMEND_TOP_N, ge=1, le=20, description="返回条数上限，默认 5"),
    force: int = Query(0, ge=0, le=1, description="1=跳过缓存强制重算"),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """练习推荐 Top N（含后端生成的中文理由）。

    Returns:
        ``{"code":0,"msg":"ok","data":{
            "student_id": "...",
            "recommendations": [{"kp_id","kp_name","reason","priority","score",
                                 "error_rate","top_error_category",
                                 "suggested_question_ids"}],
            "computed_at": "...", "mode": "fresh|cache"}}``

    Raises:
        ApiError: 401 未登录；403 非学生本人。
    """
    student_id = _require_student(current)
    empty: Dict[str, Any] = {"recommendations": []}

    try:
        view = profile_service.get_recommendations(
            db,
            student_id=student_id,
            limit=limit,
            force=bool(force),
        )
    except Exception:  # noqa: BLE001
        db.rollback()
        return _degraded(student_id, empty, "recommendations")

    return ok(
        {
            "student_id": view.student_id,
            "recommendations": [_recommendation_to_dict(item) for item in view.items],
            "computed_at": _iso(view.computed_at),
            "mode": view.mode,
        }
    )


__all__ = ["router"]

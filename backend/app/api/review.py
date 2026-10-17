"""FSRS 科学复习接口（方向三 · T3，V6.5 巩固链路）。

    GET  /api/review/daily   → { cards, count, date }  今日待复习卡片
    POST /api/review/submit  → { card_id, rating }     提交复习结果（FSRS 1-4）
    GET  /api/review/stats   → 复习统计（总卡 / 今日到期 / 近期 / 平均分 / 状态分布）
    POST /api/review/sync    → { kp_only }             从掌握度同步复习计划

实现上复用 ``app.services.review_service.ReviewService``（FSRS 卡片版）：
    * ``get_due_cards``      —— 到期卡片（联查题目/知识点，含 content 结构化载荷）；
    * ``submit_review``      —— FSRS 调度推进（rating 1-4 → SM-2 quality 映射）；
    * ``get_review_stats``   —— 复习统计聚合；
    * ``sync_cards_from_mastery`` —— 从 ``student_mastery`` 同步建档（fsrs_scheduler 联动）。

与 ``/api/practice/review/*``（知识点级别 SM-2 排程，review_schedule 表）并存：
    * practice/review/today·record —— 练习闭环内的「今日复习入口 + 记录」（KP 粒度）；
    * 本模块 review_cards 卡片流 —— 独立复习页（Review.vue）的卡片粒度 FSRS 排程。

硬约定：
    * 需要登录且 ``user.sid`` 非空（``require_student_id`` 返 400，沿用 T03 口径）；
    * POST 路由（``/submit`` / ``/sync``）挂 ``require_guardian_consent``
      监护人同意门控（``CONSENT_GATE_ENABLED`` 默认关，开启后无当前版本同意返 403）；
      GET 一律不挂（今日卡片 / 统计保持可浏览）；
    * 出参一律 **snake_case**，由 ``app.core.response.ok()`` 包裹（C10）；
    * 全部为纯本地计算（FSRS 调度），用 sync ``def`` 路由不进事件循环；
    * 他人卡片一律按 404 处理（不泄露资源存在性）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_guardian_consent
from app.core.response import ApiError, CODE_NOT_FOUND, ok
from app.db import get_db
from app.schemas.review import ReviewSubmitRequest, ReviewSyncRequest
from app.services.learning_service import require_student_id
from app.services.review_service import ReviewService

logger = logging.getLogger("app.api.review")

router = APIRouter(prefix="/review", tags=["review"])


def _get_service(db: Session) -> ReviewService:
    """复用请求级会话构造复习服务（避免 SessionLocal 与请求事务分叉）。"""
    return ReviewService(db=db)


def _card_dict(card: dict) -> dict:
    """把 service 返回的卡片载荷展平成前端契约（ReviewCard）。

    service 侧 ``content`` 里已带 ``type / question_content / question_type /
    kp_name / kp_code``；前端 Review.vue 顶层字段（question_content /
    kp_name / kp_code / question_type）也需要，直接平铺到顶层。
    """
    out = dict(card)
    content = out.get("content") or {}
    for key in ("question_content", "question_type", "kp_name", "kp_code"):
        if content.get(key) is not None:
            out[key] = content[key]
    return out


@router.get("/daily")
def review_daily(
    limit: int = Query(50, ge=1, le=200, description="今日待复习卡片数上限"),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """今日待复习卡片（FSRS 到期卡片流）。

    Returns:
        ``{"code":0,"msg":"ok","data":{cards:[...], count, date}}``，
        ``cards`` 按到期时间升序、难度降序；``count`` 为当日到期卡片总数。
    """
    student_id = require_student_id(current.sid)
    svc = _get_service(db)
    cards = svc.get_due_cards(str(student_id), limit=limit)
    stats = svc.get_review_stats(str(student_id), days=1)
    return ok({
        "cards": [_card_dict(c) for c in cards],
        "count": stats["today_due"],
        "date": datetime.now().strftime("%Y-%m-%d"),
    })


@router.post("/submit", dependencies=[Depends(require_guardian_consent)])
def review_submit(
    body: ReviewSubmitRequest,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交一次复习结果（FSRS 调度推进）。

    Args:
        body.card_id: 复习卡片 ID（他人卡片返 404）；
        body.rating: 1=Again 2=Hard 3=Good 4=Easy。

    Returns:
        ``data`` 含 ``card_id / due / stability / difficulty / state /
        scheduled_days / reps / lapses``。
    """
    student_id = require_student_id(current.sid)
    svc = _get_service(db)

    # 归属校验：只能操作自己的卡片
    owner = db.execute(
        text("SELECT student_id FROM review_cards WHERE id = :p1"),
        {"p1": str(body.card_id)},
    ).fetchone()
    if owner is None or str(owner[0]) != str(student_id):
        raise ApiError(CODE_NOT_FOUND, "复习卡片不存在")

    result = svc.submit_review(str(body.card_id), body.rating)
    return ok(result)


@router.get("/stats")
def review_stats(
    days: int = Query(30, ge=1, le=365, description="统计窗口天数"),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """复习统计。

    Returns:
        ``data`` 含 ``total_cards / today_due / recent_reviews /
        average_rating / state_distribution / period_days``。
    """
    student_id = require_student_id(current.sid)
    svc = _get_service(db)
    return ok(svc.get_review_stats(str(student_id), days=days))


@router.post("/sync", dependencies=[Depends(require_guardian_consent)])
def review_sync(
    body: ReviewSyncRequest,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """从掌握度同步复习计划（为有掌握度记录的知识点建档/更新卡片）。

    Returns:
        ``data`` 含 ``created / updated / skipped / total``。
    """
    student_id = require_student_id(current.sid)
    svc = _get_service(db)
    result = svc.sync_cards_from_mastery(str(student_id))
    return ok(result)


__all__ = ["router"]
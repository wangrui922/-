"""练习进度云同步 API。

    POST /api/progress              保存当前进度（UPSERT，每次练习状态变化时调用）
    GET  /api/progress              读取最新进度（登录后进入练习页时调用）
    GET  /api/progress/adaptive-plan 今日学习计划（T07 自适应编排，只读）
    DELETE /api/progress            清空进度（用户手动放弃/完成时调用）

冲突解决策略：
    前端每次保存时带 ``client_saved_at``（ms 时间戳）。
    后端收到保存请求时：
      - 若该学生无记录 → 新建；
      - 若已有记录且 ``client_saved_at >= 现有.client_saved_at`` → 覆盖；
      - 若已有记录且 ``client_saved_at < 现有.client_saved_at`` → 返回 409 Conflict，
        前端收到 409 后应弹窗提示「另一设备有更新的进度，是否覆盖？」。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.db import get_db
from app.models.progress import PracticeProgress
from app.services.adaptive_plan_service import build_adaptive_plan
from app.services.learning_service import require_student_id

logger = logging.getLogger("app.api.progress")

router = APIRouter(prefix="/progress", tags=["progress"])

# ---- T07：今日学习计划的题量上下界（与 practice_service 保持同一口径）--------
ADAPTIVE_PLAN_MIN_COUNT: int = 1
ADAPTIVE_PLAN_MAX_COUNT: int = 30


class ProgressSaveIn(BaseModel):
    """保存进度请求体。"""
    phase: str = "filter"
    index: int = 0
    questions: Optional[list] = None
    pool: Optional[dict] = None
    answers: Optional[dict] = None
    session_id: Optional[str] = None
    deep_link: Optional[dict] = None
    saved_at: Optional[int] = None  # 客户端时间戳 ms
    total_count: int = 0


class ProgressOut(BaseModel):
    """进度响应体。"""
    phase: str
    index: int
    questions: list
    pool: dict
    answers: dict
    session_id: Optional[str]
    deep_link: Optional[dict]
    saved_at: Optional[str]
    updated_at: Optional[str]
    total_count: int


@router.post("")
def save_progress(
    body: ProgressSaveIn = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """保存练习进度（UPSERT，多端冲突时返回 409）。"""
    sid = require_student_id(current)

    # 查询现有记录
    existing = (
        db.query(PracticeProgress)
        .filter(
            PracticeProgress.student_id == sid,
            PracticeProgress.cleared_at.is_(None),
        )
        .order_by(PracticeProgress.updated_at.desc())
        .first()
    )

    client_ts = body.saved_at
    if existing and client_ts is not None:
        existing_ts = int(existing.client_saved_at.timestamp() * 1000) if existing.client_saved_at else 0
        if client_ts < existing_ts:
            logger.info(
                "progress conflict: student=%s client_ts=%s < server_ts=%s",
                sid, client_ts, existing_ts,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "另一设备有更新的进度",
                    "server_progress": existing.to_snapshot(),
                },
            )

    # 无记录或客户端较新 → 覆盖
    if existing:
        progress = existing
    else:
        progress = PracticeProgress(student_id=uuid.UUID(str(sid)))
        db.add(progress)

    progress.phase = body.phase
    progress.current_index = body.index
    progress.total_count = body.total_count
    progress.questions_json = body.questions or []
    progress.pool_info_json = body.pool or {}
    progress.answers_json = body.answers or {}
    progress.session_id = body.session_id
    progress.deep_link_json = body.deep_link
    progress.client_saved_at = datetime.fromtimestamp(client_ts / 1000, tz=datetime.timezone.utc) if client_ts else None
    progress.cleared_at = None

    db.commit()
    db.refresh(progress)
    logger.info("progress saved: student=%s phase=%s", sid, body.phase)
    return ok({"saved": True, "updated_at": progress.updated_at.isoformat()})


def _adaptive_plan_to_api(payload: Dict[str, Any]) -> Dict[str, Any]:
    """把编排层的 snake_case 信封转成 camelCase（DB/内部 snake，出入参 camel）。"""
    items = payload.get("items")
    return {
        "generatedAt": payload.get("generated_at"),
        "engineVersion": payload.get("engine_version"),
        "total": payload.get("total", 0),
        "actualCount": payload.get("actual_count", 0),
        "missCount": payload.get("miss_count", 0),
        "questionIds": payload.get("question_ids") or [],
        "items": [
            {
                "knowledgePointId": item.get("knowledge_point_id", ""),
                "kpName": item.get("kp_name", ""),
                "questionCount": item.get("question_count", 0),
                "difficultyLevels": item.get("difficulty_levels") or [],
                "strategy": item.get("strategy", ""),
                "reason": item.get("reason", ""),
            }
            for item in (items if isinstance(items, list) else [])
        ],
        "relaxationSteps": payload.get("relaxation_steps") or [],
    }


@router.get("/adaptive-plan")
def get_adaptive_plan(
    count: Optional[int] = Query(
        default=None, ge=ADAPTIVE_PLAN_MIN_COUNT, le=ADAPTIVE_PLAN_MAX_COUNT
    ),
    grade: Optional[int] = Query(default=None, ge=1, le=12),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """今日学习计划（T07 自适应编排，**只读**，不建会话、不写库）。

    依次经过 优先级排序 → 难度带决策 → 题量配额 → 逐计划抽题，产出今日该练的
    知识点清单与题目 ID。``adaptive.enabled=false`` 时自动降级为普通抽题，
    出参 ``engineVersion`` 会变成 ``legacy-draw``。

    Query:
        count: 本批题数（1~30），缺省 10。
        grade: 年级筛选（1~12），缺省不限。

    Returns:
        ``{code:0,msg:"ok",data:{generatedAt, engineVersion, total, actualCount,
        missCount, questionIds, items[], relaxationSteps}}``。

    Raises:
        ApiError: 400 未绑定学生 / 参数越界；401 未登录。
    """
    sid = require_student_id(current)
    payload = build_adaptive_plan(db, student_id=sid, count=count, grade=grade)
    logger.info(
        "adaptive-plan student=%s count=%s grade=%s → engine=%s total=%s actual=%s items=%d",
        sid,
        count,
        grade,
        payload.get("engine_version"),
        payload.get("total"),
        payload.get("actual_count"),
        len(payload.get("items") or []),
    )
    return ok(_adaptive_plan_to_api(payload))


@router.get("")
def get_progress(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """读取最新练习进度。"""
    sid = require_student_id(current)

    progress = (
        db.query(PracticeProgress)
        .filter(
            PracticeProgress.student_id == sid,
            PracticeProgress.cleared_at.is_(None),
        )
        .order_by(PracticeProgress.updated_at.desc())
        .first()
    )

    if not progress or not progress.is_active():
        return ok({"has_progress": False})

    return ok({
        "has_progress": True,
        "progress": progress.to_snapshot(),
    })


@router.delete("")
def clear_progress(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """清空练习进度（软删）。"""
    sid = require_student_id(current)

    progress = (
        db.query(PracticeProgress)
        .filter(
            PracticeProgress.student_id == sid,
            PracticeProgress.cleared_at.is_(None),
        )
        .first()
    )

    if progress:
        progress.cleared_at = datetime.now(datetime.timezone.utc)
        db.commit()
        logger.info("progress cleared: student=%s", sid)

    return ok({"cleared": True})

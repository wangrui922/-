"""练习题生成接口（T03 · 3.4）。

    POST /api/exercise/gen  基于会话的分析结论，生成修复题 + 迁移题

硬约定：
    * 需要登录且 ``user.sid`` 非空；
    * ``orchestrator.generate_exercise`` 是同步函数且内含方舟调用，
      用 ``run_in_threadpool`` 包裹；
    * 生成结果**不落题库**，由前端决定是否再走 ``POST /api/mistake`` 建档。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.ai import orchestrator
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.db import get_db
from app.schemas.common import BaseSchema
from app.services.learning_service import require_student_id, to_uuid

router = APIRouter(tags=["exercise"])


class ExerciseGen(BaseSchema):
    """``POST /api/exercise/gen`` 请求体。

    Attributes:
        session_id: 辅导会话 ID（别名 ``sessionId``）。
        client_event_id: 客户端幂等键（别名 ``clientEventId``）。
    """

    session_id: str
    client_event_id: Optional[str] = None


@router.post("/exercise/gen")
async def gen_exercise(
    body: ExerciseGen = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """基于会话分析结论生成两道练习题。

    Returns:
        ``{"code":0,"msg":"ok","data":{"exercises":[{q,a,type,knowledge,difficulty,role,tag}]}}``。

    Raises:
        ApiError: 400 未绑定学生 / 会话不存在 / sessionId 非法。
    """
    require_student_id(current.sid)

    result = await run_in_threadpool(
        orchestrator.generate_exercise,
        session_id=to_uuid(body.session_id, "sessionId"),
        db=db,
    )
    return ok(result)


__all__ = ["router", "ExerciseGen"]

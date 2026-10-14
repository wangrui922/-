"""学生 DNA 聚合接口（V6.5 · Sprint 2 DNA-005）。

    GET /api/students/{student_id}/learning-dna  学生 DNA：画像 + 掌握汇总 + 最近事件

硬约定：
    * 需要登录；**学生本人**（``role=student`` 且 ``sid`` 匹配）或**绑定家长**
      （``role=parent`` 且已在 ``family`` 表绑定该孩子）可读，两者以外返 403；
    * 出参全部 **snake_case**（``student_id`` / ``learning_goal`` /
      ``mastery_summary`` / ``recent_events``），由 ``app.core.response.ok()`` 包裹；
    * 无画像 / 无掌握 / 无事件的学生返回空默认值，**不报错**（规划验收口径）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_FORBIDDEN, CODE_NOT_FOUND, ok
from app.db import get_db
from app.models.user import Student
from app.services import family_service, student_dna_service
from app.services.learning_service import require_student_id, to_uuid

router = APIRouter(prefix="/students", tags=["student-dna"])


def _assert_can_view(db: Session, current: CurrentUser, student_id: str) -> uuid.UUID:
    """鉴权：仅学生本人或绑定家长可看目标学生的 DNA，否则 403。

    路径 ``student_id`` 不存在时按 404（不泄露资源存在性）。
    """
    sid = to_uuid(student_id, "student_id")
    if db.get(Student, sid) is None:
        raise ApiError(CODE_NOT_FOUND, "学生不存在")

    if current.is_student():
        own_sid = require_student_id(current.sid)
        if own_sid != sid:
            raise ApiError(CODE_FORBIDDEN, "无权限查看该学生的学习档案")
        return sid

    if current.role == "parent":
        family_service.assert_parent_of(db, uuid.UUID(current.sub), sid)
        return sid

    raise ApiError(CODE_FORBIDDEN, "无权限查看该学生的学习档案")


@router.get("/{student_id}/learning-dna")
def get_learning_dna(
    student_id: str,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """学生 DNA 聚合：画像五要素 + 知识点掌握汇总 + 最近事件摘要。

    Returns:
        ``{"code":0,"msg":"ok","data":{student_id,profile,mastery_summary,recent_events}}``。

    Raises:
        ApiError: 401 未登录；403 非本人或未绑定家长；404 学生不存在。
    """
    sid = _assert_can_view(db, current, student_id)
    return ok(student_dna_service.get_learning_dna(db, student_id=sid))


__all__ = ["router"]

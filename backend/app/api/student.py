"""学生档案接口（T02 · 2.2）。

    GET /api/student/profile   读取当前学生年级 / 目标 / 画像摘要
    PUT /api/student/profile   更新 grade / goal / realName / nickname / dailyTarget

硬约定：
    * 两个接口均 ``Depends(get_current_user)``，无 token 返 401；
    * ``grade`` 越界（0 / 7）返 ``code=400``（HTTP 400 同步）；
    * 出入参 camelCase，见 ``app/schemas/student.py``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.db import get_db
from app.schemas.student import StudentProfileIn
from app.services import student_service

router = APIRouter(prefix="/student", tags=["student"])


@router.get("/profile")
async def get_profile(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """读取当前学生档案与画像摘要。"""
    return ok(student_service.get_profile(db, current))


@router.put("/profile")
async def update_profile(
    payload: StudentProfileIn,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """更新当前学生档案（只改传了的字段；grade 越界返 400）。"""
    return ok(student_service.update_profile(db, current, payload))


__all__ = ["router"]

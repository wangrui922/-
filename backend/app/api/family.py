"""家长-孩子绑定接口（V4.2 商业核心）。

    GET  /api/family/children                  列出已绑定的孩子
    POST /api/family/bind                      绑定一个孩子（手机号 + 关系）
    DELETE /api/family/bind/{student_id}       解绑
    GET  /api/family/children/{student_id}/summary  家长视角孩子概览（需已绑定）

全部受保护，且限定 ``role=parent``。
"""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_role
from app.core.response import ok
from app.db import get_db
from app.schemas.family import BindChildIn
from app.services import family_service

router = APIRouter(prefix="/family", tags=["family"])


@router.post("/bind")
async def bind_child(
    payload: BindChildIn,
    current: CurrentUser = Depends(require_role("parent")),
    db: Session = Depends(get_db),
) -> dict:
    """家长绑定一个孩子（按手机号）。"""
    pc = family_service.bind_child(db, uuid.UUID(current.sub), payload.phone, payload.relation)
    return ok({"studentId": str(pc.student_id), "relation": pc.relation})


@router.get("/children")
async def list_children(
    current: CurrentUser = Depends(require_role("parent")),
    db: Session = Depends(get_db),
) -> dict:
    """列出当前家长绑定的所有孩子。"""
    children = family_service.list_children(db, uuid.UUID(current.sub))
    return ok(children)


@router.delete("/bind/{student_id}")
async def unbind_child(
    student_id: str,
    current: CurrentUser = Depends(require_role("parent")),
    db: Session = Depends(get_db),
) -> dict:
    """解绑一个孩子。"""
    family_service.unbind_child(db, uuid.UUID(current.sub), student_id)
    return ok()


@router.get("/children/{student_id}/summary")
async def child_summary(
    student_id: str,
    current: CurrentUser = Depends(require_role("parent")),
    db: Session = Depends(get_db),
) -> dict:
    """家长视角：查看已绑定孩子的完整概览（画像 + 周报 + 雷达）。"""
    family_service.assert_parent_of(db, uuid.UUID(current.sub), student_id)
    return ok(family_service.get_child_summary(db, student_id))


__all__ = ["router"]

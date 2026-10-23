"""家长-孩子绑定接口（V4.2 商业核心）。

    GET  /api/family/children                  列出已绑定的孩子
    POST /api/family/bind                      绑定一个孩子（手机号 + 关系）
    DELETE /api/family/bind/{student_id}       解绑
    GET  /api/family/children/{student_id}/summary  家长视角孩子概览（需已绑定）

全部受保护，且限定 ``role=parent``。

出口统一 camelCase：family_service 返回的是 raw dict（snake_case），
而前端契约（``src/api/family.ts`` 的类型定义与 JSDoc）全按 camelCase
读。2026-10-23 修复：此前 ``list_children`` 返回 ``student_id`` 而前端读
``child.studentId``，家长端孩子列表点击跳转拿到 ``undefined``，
整条家长链路静默坏掉。现在所有出口过 ``_camelize``，契约以 api.ts 为准。
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_role
from app.core.response import ok
from app.db import get_db
from app.schemas.family import BindChildIn
from app.services import family_service

router = APIRouter(prefix="/family", tags=["family"])

_SNAKE_RE = re.compile(r"_([a-z])")


def _camelize(value: Any) -> Any:
    """递归把 dict 的 key 转 camelCase（list/dict 递归，标量原样）。

    只动 key 不动 value——``is_correct`` 这类布尔值若被误转会破坏语义。
    """
    if isinstance(value, dict):
        return {_SNAKE_RE.sub(lambda m: m.group(1).upper(), str(k)): _camelize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


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
    return ok(_camelize(children))


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
    return ok(_camelize(family_service.get_child_summary(db, student_id)))


@router.get("/children/{student_id}/mistakes")
async def child_mistakes(
    student_id: str,
    page: int = 1,
    size: int = 20,
    current: CurrentUser = Depends(require_role("parent")),
    db: Session = Depends(get_db),
) -> dict:
    """家长视角：孩子错题列表（含题干）。"""
    family_service.assert_parent_of(db, uuid.UUID(current.sub), student_id)
    return ok(_camelize(family_service.list_child_mistakes(db, student_id, page=page, size=size)))


@router.get("/children/{student_id}/attempts")
async def child_attempts(
    student_id: str,
    page: int = 1,
    size: int = 20,
    current: CurrentUser = Depends(require_role("parent")),
    db: Session = Depends(get_db),
) -> dict:
    """家长视角：孩子练习记录。"""
    family_service.assert_parent_of(db, uuid.UUID(current.sub), student_id)
    return ok(_camelize(family_service.list_child_attempts(db, student_id, page=page, size=size)))


@router.get("/children/{student_id}/mastery")
async def child_mastery(
    student_id: str,
    page: int = 1,
    size: int = 50,
    current: CurrentUser = Depends(require_role("parent")),
    db: Session = Depends(get_db),
) -> dict:
    """家长视角：孩子知识点掌握度。"""
    family_service.assert_parent_of(db, uuid.UUID(current.sub), student_id)
    return ok(_camelize(family_service.list_child_mastery(db, student_id, page=page, size=size)))


@router.get("/children/{student_id}/consent")
async def child_consent(
    student_id: str,
    current: CurrentUser = Depends(require_role("parent")),
    db: Session = Depends(get_db),
) -> dict:
    """家长视角：孩子的监护人同意三态（已同意 / 未同意 / 版本失效）。

    读操作，**刻意不挂** ``require_guardian_consent`` 门控：同意状态
    查询不该被同意门控本身拦住（门控开启 + 孩子未同意时，家长更要
    能查到状态）。门控只拦练习类 POST（见 ``deps`` 模块文档）。

    Returns:
        ``{"granted": bool, "agreedAt": iso8601|None,
        "consentVersion": str|None, "currentVersion": str}``；
        前端据 ``granted`` / ``consentVersion`` 区分三态。
    """
    return ok(
        _camelize(
            family_service.get_child_consent_status(
                db, uuid.UUID(current.sub), student_id
            )
        )
    )


@router.get("/children/{student_id}/entitlement")
async def child_entitlement(
    student_id: str,
    current: CurrentUser = Depends(require_role("parent")),
    db: Session = Depends(get_db),
) -> dict:
    """家长视角：孩子订阅/权益状态。"""
    family_service.assert_parent_of(db, uuid.UUID(current.sub), student_id)
    return ok(_camelize(family_service.get_child_entitlement(db, student_id)))


__all__ = ["router"]

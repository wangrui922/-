"""知识点读接口（Sprint 1 · KG-003 详情 / KG-004 前置 / KG-005 关系）。

    GET  /api/knowledge-points/{kp_id}                详情（KG-003）
    GET  /api/knowledge-points/{kp_id}/prerequisites  前置知识（KG-004）
    GET  /api/knowledge-points/{kp_id}/relations      相关知识（KG-005）

硬约定：
    * 知识点是**公开只读教学内容**（供学生/家长/教师共享浏览）——函数
      签名**不能出现** ``Depends(get_current_user)``，也不调用
      ``_require_student``；
    * 详情接口：id 非法 / 不存在 → **统一 404**（不区分两种情形，防资源探测）；
    * 前置接口（KG-004）：id 非法 / 不存在 / 无前置 → **空 items 恒 200**，
      不给 404（前置查询是引导性读接口，空结果本身是合法语义）；
    * 关系接口（KG-005）：id 非法 / 不存在 / 无关系 → **三类分组均空数组
      恒 200**，不给 404（空关系返回空数组而非报错）；
    * 出参 snake_case（T04 起新接口约定）；
    * 用同步 ``def`` 路由（FastAPI 自动丢线程池），与 profile/report 一致。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.response import ApiError, CODE_NOT_FOUND, ok
from app.db import get_db
from app.models.content import KnowledgePoint
from app.services import knowledge_service

router = APIRouter(prefix="/knowledge-points", tags=["knowledge-points"])

#: 404 统一文案（非法 id 与不存在同句，防探测）
KP_NOT_FOUND_MESSAGE = "知识点不存在"


def _to_item(kp: KnowledgePoint) -> Dict[str, Any]:
    """把 ``KnowledgePoint`` 序列化成 snake_case 出参（KG-003 完整字段）。"""
    return {
        "id": str(kp.id),
        "name": kp.name,
        "code": kp.code,
        "parent_id": str(kp.parent_id) if kp.parent_id else None,
        "description": kp.description,
        "subject": kp.subject,
        "grade": kp.grade,
        "semester": kp.semester,
        "chapter": kp.chapter,
        "difficulty": kp.difficulty,
        "importance": kp.importance,
        "status": kp.status,
        "sort": kp.sort,
        "created_at": kp.created_at.isoformat() if kp.created_at else None,
        "updated_at": kp.updated_at.isoformat() if kp.updated_at else None,
    }


@router.get("/{kp_id}")
def get_knowledge_point_detail(
    kp_id: str = Path(..., description="知识点 ID（UUID 字符串）"),
    db: Session = Depends(get_db),
) -> dict:
    """KG-003 · 知识点详情（含描述、难度、重要程度）。

    Returns:
        ``{"code":0,"msg":"ok","data":{...}}``，字段见 :func:`_to_item`。

    Raises:
        ApiError: 404 —— id 非法或不存在（统一文案）。
    """
    kp = knowledge_service.get_knowledge_point(db, kp_id)
    if kp is None:
        raise ApiError(CODE_NOT_FOUND, KP_NOT_FOUND_MESSAGE, {})
    return ok(_to_item(kp))


@router.get("/{kp_id}/prerequisites")
def get_knowledge_point_prerequisites(
    kp_id: str = Path(..., description="知识点 ID（UUID 字符串）"),
    db: Session = Depends(get_db),
) -> dict:
    """KG-004 · 前置知识查询（prerequisite 边，可递归一层，防环）。

    Returns:
        ``{"code":0,"msg":"ok","data":{"items":[{...}]}}``；
        id 非法 / 不存在 / 无前置 → ``items=[]``（**恒 200**）。
    """
    items = knowledge_service.get_prerequisites(db, kp_id, recursive=True)
    return ok({"items": [_to_item(kp) for kp in items]})


@router.get("/{kp_id}/relations")
def get_knowledge_point_relations(
    kp_id: str = Path(..., description="知识点 ID（UUID 字符串）"),
    db: Session = Depends(get_db),
) -> dict:
    """KG-005 · 相关知识查询（prerequisite/related/next 三类分组）。

    Returns:
        ``{"code":0,"msg":"ok","data":{"prerequisite":[{...}],
        "related":[{...}],"next":[{...}]}}``；
        id 非法 / 不存在 / 无关系 → 三类分组均为 ``[]``（**恒 200**）。
    """
    groups = knowledge_service.get_relations(db, kp_id)
    return ok(
        {rel_type: [_to_item(kp) for kp in kps] for rel_type, kps in groups.items()}
    )


__all__ = ["router", "KP_NOT_FOUND_MESSAGE"]

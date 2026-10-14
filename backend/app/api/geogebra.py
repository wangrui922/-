"""GeoGebra 几何交互 API（方向四·3）。

    POST /api/geogebra         提交交互任务（题干/AI 讲解 -> 同步生成指令）
    GET  /api/geogebra/{id}     查询任务详情（含 instructions + applet_config）
    GET  /api/geogebra          最近任务列表（可选 limit）

交互约定：
    * 提交后返回 ``{task_id, status, instructions, applet_config}``；
      前端直接用 ``instructions`` 在 GeoGebra Applet 中 ``evalCommand`` 执行；
    * 缓存：同一学生同内容已完成的任务直接复用（秒回 done）；
    * 无需轮询：生成是同步的，提交即完成（status 恒为 done）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_BAD_REQUEST, ok
from app.db import get_db
from app.services.learning_service import require_student_id
from app.services import geogebra_service

router = APIRouter(prefix="/geogebra", tags=["geogebra"])


class GeoGebraCreate(BaseModel):
    """``POST /api/geogebra`` 请求体。

    Attributes:
        text: 题干或 AI 讲解文本（不能为空）。
        topic: 知识点提示（可选，帮助 AI 选模板）。
        force: True 时忽略缓存强制重新生成（可选，默认 False）。
    """

    text: str
    topic: Optional[str] = None
    force: bool = False


@router.post("")
def create_geogebra(
    body: GeoGebraCreate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> dict:
    """提交 GeoGebra 交互任务（同步生成指令）。"""
    student_id = require_student_id(current.sid)
    if not (body.text or "").strip():
        raise ApiError(CODE_BAD_REQUEST, "缺少题目或讲解文本")
    task = geogebra_service.create_task(
        db,
        student_id=student_id,
        source_text=body.text,
        topic=body.topic,
        force=body.force,
    )
    return ok(task.to_dict())


@router.get("/{task_id}")
def get_geogebra(
    task_id: str,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> dict:
    """按 ID 查询 GeoGebra 任务详情。"""
    student_id = require_student_id(current.sid)
    task = geogebra_service.get_task(db, task_id)
    # 权限校验：只能查自己的任务
    if str(task.student_id) != str(student_id):
        raise ApiError(CODE_BAD_REQUEST, "无权访问该任务")
    return ok(task.to_dict())


@router.get("")
def list_geogebra_tasks(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> dict:
    """最近 GeoGebra 任务列表（历史记录）。"""
    student_id = require_student_id(current.sid)
    tasks = geogebra_service.list_tasks(db, student_id=student_id, limit=limit)
    return ok({"items": [t.to_dict() for t in tasks]})

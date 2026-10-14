"""Manim 讲解动画 API（方向四·2）。

    POST /api/manim         提交渲染任务（题干/AI 讲解 -> 异步渲染）
    GET  /api/manim/{id}     查询任务状态（前端轮询，含 video_url）
    GET  /api/manim/{id}/video  拉取渲染完成的 mp4（FileResponse）
    GET  /api/manim           最近任务列表（可选 limit）

交互约定：
    * 提交后返回 ``{task_id, status}``；前端每 2s 轮询 ``GET /api/manim/{id}``，
      status=done 时用 ``video_url`` 直接 <video> 播放；
    * 缓存：同一学生同内容已完成的任务直接复用（秒回 done）；
    * 渲染失败 status=failed + error 摘要，前端给「重新生成/换一种」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_NOT_FOUND, ok
from app.db import get_db
from app.models.manim_task import STATUS_DONE
from app.services.learning_service import require_student_id
from app.services import manim_service

router = APIRouter(prefix="/manim", tags=["manim"])


class ManimCreate(BaseModel):
    """``POST /api/manim`` 请求体。

    Attributes:
        text: 题干或 AI 讲解文本（不能为空）。
        topic: 知识点提示（可选，帮助 AI 选模板）。
        force: True 时忽略缓存强制重渲染（可选，默认 False）。
    """

    text: str
    topic: Optional[str] = None
    force: bool = False


@router.post("")
def create_manim(
    body: ManimCreate,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> dict:
    """提交 Manim 渲染任务（异步）。"""
    student_id = require_student_id(current.sid)
    if not (body.text or "").strip():
        raise ApiError(CODE_BAD_REQUEST, "缺少题目或讲解文本")
    task = manim_service.create_task(
        db,
        student_id=student_id,
        source_text=body.text,
        topic=body.topic,
        force=body.force,
    )
    return ok(task.to_dict())


@router.get("")
def list_manim_tasks(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> dict:
    """最近渲染任务列表（历史记录）。"""
    student_id = require_student_id(current.sid)
    tasks = manim_service.list_tasks(db, student_id=student_id, limit=limit)
    return ok({"items": [t.to_dict() for t in tasks]})


@router.get("/{task_id}")
def get_manim_task(
    task_id: str,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> dict:
    """查询单个任务状态（轮询用）。"""
    require_student_id(current.sid)
    task = manim_service.get_task(db, task_id)
    return ok(task.to_dict())


@router.get("/{task_id}/video")
def get_manim_video(
    task_id: str,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
) -> FileResponse:
    """拉取渲染完成的 mp4 视频。

    鉴权说明：与 ``/api/file/{key}`` 一致走「鉴权 + 不可枚举 id」，
    避免公开外链被刷流量（2 核 4G 机器带宽有限）。
    """
    require_student_id(current.sid)
    task = manim_service.get_task(db, task_id)
    if task.status != STATUS_DONE or not task.video_path:
        raise ApiError(CODE_NOT_FOUND, "动画尚未渲染完成")
    video = settings.storage_path / task.video_path
    if not video.is_file():
        raise ApiError(CODE_NOT_FOUND, "视频文件不存在，请重新生成")
    return FileResponse(video, media_type="video/mp4", filename=f"manim-{task_id}.mp4")
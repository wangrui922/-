"""GeoGebra 几何交互任务服务（方向四·3）。

职责：
    1. ``create_task``：AI 解析场景 -> 生成 GeoGebra 指令列表 -> 落库 -> 同步返回
    2. ``get_task`` / ``list_tasks``：查询任务（前端缓存命中时秒回）

与 Manim 的差异：
    * **无异步队列**：GeoGebra 是前端嵌入执行，后端只生成指令（纯文本），
      同步完成，无需 Redis 队列或渲染容器；
    * 缓存命中直接复用已有 done 任务；
    * 失败时直接抛异常（不存 failed 状态）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_NOT_FOUND
from app.models.geogebra_task import (
    STATUS_DONE,
    GeoGebraTask,
)
from app.services.geogebra_scene import plan_scene
from app.services.geogebra_templates import build_scene

logger = logging.getLogger("app.services.geogebra_service")


def _content_md5(source_text: str, topic: Optional[str]) -> str:
    """内容指纹：source+topic 的 sha1 前 32 位。"""
    return hashlib.sha1(f"{source_text}|{(topic or '').strip()}".encode("utf-8")).hexdigest()[:32]


# ------------------------------------------------------------------ 查询

def get_task(db: Session, task_id: str) -> GeoGebraTask:
    """按 id 查询任务；不存在抛 404。"""
    try:
        uuid.UUID(str(task_id))
    except (ValueError, TypeError):
        raise ApiError(CODE_NOT_FOUND, "几何任务不存在")
    task = db.get(GeoGebraTask, task_id)
    if task is None:
        raise ApiError(CODE_NOT_FOUND, "几何任务不存在")
    return task


def list_tasks(db: Session, student_id, limit: int = 20) -> List[GeoGebraTask]:
    """最近 N 个任务（前端展示历史列表用）。"""
    student_id = str(student_id)
    return (
        db.query(GeoGebraTask)
        .filter(GeoGebraTask.student_id == student_id)
        .order_by(GeoGebraTask.created_at.desc())
        .limit(min(max(limit, 1), 50))
        .all()
    )


# ------------------------------------------------------------------ 创建

def create_task(
    db: Session,
    *,
    student_id,
    source_text: str,
    topic: Optional[str] = None,
    force: bool = False,
) -> GeoGebraTask:
    """创建（或命中缓存复用）GeoGebra 交互任务。

    同步生成指令列表，无需队列。
    """
    start = time.monotonic()
    text = (source_text or "").strip()
    if not text:
        raise ApiError(CODE_BAD_REQUEST, "缺少题目或讲解文本")

    student_id = str(student_id)
    md5 = _content_md5(text, topic)

    # 缓存命中：同一学生 + 同内容 + 已成功 -> 直接复用
    if not force:
        hit: Optional[GeoGebraTask] = (
            db.query(GeoGebraTask)
            .filter(
                GeoGebraTask.student_id == student_id,
                GeoGebraTask.content_md5 == md5,
                GeoGebraTask.status == STATUS_DONE,
            )
            .order_by(GeoGebraTask.created_at.desc())
            .first()
        )
        if hit is not None:
            dur = int((time.monotonic() - start) * 1000)
            logger.info("metric=geogebra_scene action=create result=cache_hit task=%s duration_ms=%d", hit.id, dur)
            return hit

    # AI 解析场景（失败自动兜底）
    scene_type, params = plan_scene(text, topic=topic)
    instructions, applet_config = build_scene(scene_type, params)

    task = GeoGebraTask(
        id=uuid.uuid4(),
        student_id=student_id,
        source_text=text[:2000],
        topic=(topic or "")[:64] or None,
        scene_type=scene_type,
        instructions=json.dumps(instructions, ensure_ascii=False),
        applet_config=json.dumps(applet_config, ensure_ascii=False),
        status=STATUS_DONE,
        content_md5=md5,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    dur = int((time.monotonic() - start) * 1000)
    logger.info("metric=geogebra_scene action=create result=success task=%s scene=%s duration_ms=%d", task.id, scene_type, dur)
    return task


__all__ = [
    "create_task",
    "get_task",
    "list_tasks",
]

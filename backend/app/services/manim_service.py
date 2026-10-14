"""Manim 渲染任务分发服务（方向四·2）。

职责：
    1. ``create_task``：AI 解析场景 -> 生成 Manim 源码 -> 落库 -> 推 Redis 队列
    2. ``get_task`` / ``list_tasks``：查询任务状态（前端轮询）

协作关系：
    * 本服务在 **backend 容器** 内运行，Redis 队列 ``manim:queue`` 由
      ``deploy-manim`` 渲染容器消费（独立镜像，含 Manim+ffmpeg，主镜像零负担）；
    * 场景源码写进 ``storage/manim/{task_id}/scene.py``，渲染容器输出
      ``output.mp4`` 到同目录，前端经 ``GET /api/manim/{task_id}/video`` 拉取；
    * 缓存：同一学生 + 同一 content_md5 的 done 任务直接复用。

队列可靠性：
    * 任务先落库（queued）再推队列，消费者以「任务行存在且状态=queued」为
      幂等起点，重入不产生重复渲染（渲染容器会先查库再决定是否渲染）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_NOT_FOUND
from app.models.manim_task import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RENDERING,
    ManimTask,
)
from app.services.manim_scene import FALLBACK_SCENE, plan_scene
from app.services.manim_templates import build_scene_file

logger = logging.getLogger("app.services.manim_service")

#: Redis 队列名（渲染容器 blpop 该队列）
MANIM_QUEUE = "manim:queue"

#: 渲染超时保护（秒）：2 核 4G 机器 480p 场景最多约 3 分钟
RENDER_TIMEOUT_SEC = 240


def _redis_client():
    """惰性创建 redis 客户端（backend 容器内可用 redis://redis:6379/0）。"""
    import redis  # local import：避免缺失 redis 依赖时拖垮主应用

    broker = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
    return redis.Redis.from_url(
        broker,
        decode_responses=True,
        socket_timeout=3,
    )


def _manim_dir(task_id: str) -> Path:
    """任务专属目录（场景 + 成品视频）。"""
    base = settings.storage_path / "manim" / task_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def _content_md5(source_text: str, topic: Optional[str]) -> str:
    """内容指纹：source+topic 的 sha1 前 32 位（避免重复渲染）。"""
    return hashlib.sha1(f"{source_text}|{(topic or '').strip()}".encode("utf-8")).hexdigest()[:32]


# ------------------------------------------------------------------ 查询

def get_task(db: Session, task_id: str) -> ManimTask:
    """按 id 查询任务；不存在抛 404（非 UUID 格式同样按 404 处理，避免 GUID 绑定异常）。"""
    try:
        uuid.UUID(str(task_id))
    except (ValueError, TypeError):
        raise ApiError(CODE_NOT_FOUND, "动画任务不存在")
    task = db.get(ManimTask, task_id)
    if task is None:
        raise ApiError(CODE_NOT_FOUND, "动画任务不存在")
    return task


def list_tasks(db: Session, student_id, limit: int = 20) -> List[ManimTask]:
    """最近 N 个任务（前端展示历史列表用）。"""
    return (
        db.query(ManimTask)
        .filter(ManimTask.student_id == student_id)
        .order_by(ManimTask.created_at.desc())
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
) -> ManimTask:
    """创建（或命中缓存复用）Manim 渲染任务并推入渲染队列.

    Args:
        db: 数据库会话。
        student_id: 任务归属学生。
        source_text: 题干/AI 讲解文本（AI 解析与缓存指纹的输入）。
        topic: 知识点提示（可选）。
        force: True 时忽略已有 done 缓存，强制重新渲染。

    Returns:
        ManimTask：新建的 queued 任务，或复用的 done 任务。

    Raises:
        ApiError: source_text 为空 -> 400。
    """
    start = time.monotonic()
    text = (source_text or "").strip()
    if not text:
        raise ApiError(CODE_BAD_REQUEST, "缺少题目或讲解文本")

    md5 = _content_md5(text, topic)

    # 缓存命中：同一学生 + 同内容 + 已成功 -> 直接复用（省一次渲染）
    if not force:
        hit: Optional[ManimTask] = (
            db.query(ManimTask)
            .filter(
                ManimTask.student_id == student_id,
                ManimTask.content_md5 == md5,
                ManimTask.status == STATUS_DONE,
            )
            .order_by(ManimTask.created_at.desc())
            .first()
        )
        if hit is not None:
            dur = int((time.monotonic() - start) * 1000)
            logger.info("metric=manim_render action=create result=cache_hit task=%s duration_ms=%d", hit.id, dur)
            return hit

    # AI 解析场景（失败自动兜底，不阻塞主流程）
    scene_type, params = plan_scene(text, topic=topic)
    scene_code = build_scene_file(scene_type, params).decode("utf-8")

    task = ManimTask(
        id=uuid.uuid4(),
        student_id=student_id,
        source_text=text[:2000],
        topic=(topic or "")[:64] or None,
        scene_type=scene_type,
        scene_params=json.dumps(params, ensure_ascii=False),
        scene_code=scene_code,
        status=STATUS_QUEUED,
        content_md5=md5,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 写场景文件（渲染容器读取）
    work_dir = _manim_dir(str(task.id))
    (work_dir / "scene.py").write_bytes(scene_code.encode("utf-8"))

    # 推队列（尽力而为；miss 时渲染容器会扫描 queued 行兜底）
    try:
        _redis_client().rpush(MANIM_QUEUE, str(task.id))
        dur = int((time.monotonic() - start) * 1000)
        logger.info("metric=manim_render action=create result=success task=%s scene=%s duration_ms=%d", task.id, scene_type, dur)
    except Exception as exc:  # noqa: BLE001 - 队列 miss 不影响落库/查询
        dur = int((time.monotonic() - start) * 1000)
        logger.warning("metric=manim_render action=create result=queue_warn task=%s duration_ms=%d error=%s", task.id, dur, exc)

    return task


# ------------------------------------------------------------------ 渲染结果回写
# 以下函数由「渲染容器」经 Redis 回传结果，或本地管理脚本调用。

def _mark_status(db: Session, task_id: str, status: str, *, error: Optional[str] = None, video_path: Optional[str] = None, duration_sec: Optional[int] = None, cdn_url: Optional[str] = None) -> bool:
    """回写任务状态（benchmark/CRON 修复时用）。返回是否更新成功。"""
    task = db.get(ManimTask, task_id)
    if task is None:
        logger.warning("metric=manim_render action=mark_status result=not_found task=%s", task_id)
        return False
    from app.models.base import utcnow

    task.status = status
    task.error = error or (task.error if status == STATUS_FAILED else None)
    if video_path:
        task.video_path = video_path
    if cdn_url:
        task.cdn_url = cdn_url
    if duration_sec is not None:
        task.duration_sec = duration_sec
    if status in (STATUS_DONE, STATUS_FAILED):
        task.finished_at = utcnow()
    db.commit()
    result_label = "done" if status == STATUS_DONE else ("failed" if status == STATUS_FAILED else status)
    logger.info("metric=manim_render action=mark_status result=%s task=%s duration_sec=%s cdn=%s", result_label, task_id, duration_sec, bool(cdn_url))
    return True


__all__ = [
    "MANIM_QUEUE",
    "RENDER_TIMEOUT_SEC",
    "create_task",
    "get_task",
    "list_tasks",
    "plan_scene",
    "FALLBACK_SCENE",
]
"""Manim 讲解动画任务表（方向四·2）。

一个任务 = 一次「AI 解析题干 -> 生成 Manim 场景 -> 渲染视频」的完整请求。

设计要点：
    * **主表只存结构化状态**，渲染现场（场景 .py / 视频 .mp4）在共享卷
      ``{storage}/manim/{task_id}/`` 目录，与字段一一对应，避免 DB 存大字段；
    * 状态机：queued -> rendering -> done | failed（失败带 error 摘要）；
    * 同一学生 + 同一 content_md5 永不重复排队（前端先查 ``GET /api/manim/{id}``
      命中 done 直接返回缓存，避免 2 核 4G 机器重复渲染浪费资源）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, GUID, utcnow, uuid_pk

#: 任务生命周期：排队中 / 渲染中 / 成功 / 失败
STATUS_QUEUED = "queued"
STATUS_RENDERING = "rendering"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


class ManimTask(Base, CreatedAtMixin):
    """Manim 渲染任务。"""

    __tablename__ = "manim_tasks"

    id = uuid_pk()
    #: 任务归属学生（必须绑定学生才允许生成动画）
    student_id = mapped_column(GUID(), nullable=False, index=True)
    #: 触发源：题干文本 或 AI 讲解文本（缓存键成分）
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: 数学知识点提示（可选，帮助 AI 选模板：如「分数」「几何面积」）
    topic: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: AI 选定的 Manim 场景模板名（如 ``function_plot`` / ``area_grid``）
    scene_type: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    #: AI 抽出的结构化参数（JSON 字符串，场景模板消费）
    scene_params: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: AI 生成的 Manim 场景代码（渲染前写盘，便于审计/重放）
    scene_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: 状态机字段
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_QUEUED, index=True)
    #: 渲染失败时的错误摘要
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: 视频文件相对名（如 ``manim/{id}/output.mp4``），Nginx 静态可直达
    video_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: COS CDN URL（上传成功时填充，前端优先使用该 URL 播放）
    cdn_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    #: 内容指纹：source_text + topic + 模型版本 的 md5，用于缓存命中
    content_md5: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: 渲染耗时（秒，仅 done 时记录）
    duration_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self, exclude=()):  # noqa: D102
        data = super().to_dict(exclude=("scene_code", "scene_params"))
        # CDN URL 优先，未上传成功时回退到本地 API 拉取
        if self.status == STATUS_DONE:
            data["video_url"] = self.cdn_url or f"/api/manim/{self.id}/video"
        else:
            data["video_url"] = None
        return data
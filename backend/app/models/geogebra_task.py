"""GeoGebra 几何交互任务表（方向四·3）。

设计要点：
    * GeoGebra 是前端嵌入执行，**不需要异步渲染队列**（无 CPU 密集型任务），
      后端同步生成指令列表后直接返回；
    * 保留 DB 表用于**缓存复用**（同一学生 + 同 content_md5 秒回）和审计；
    * instructions 字段存 GeoGebra evalCommand 指令列表（JSON 数组），
      applet_config 存 Applet 参数覆盖（如 coordSystem、gridVisible 等）；
    * 状态机极简：created -> done（同步生成，失败直接抛异常不存 failed）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, GUID, uuid_pk

#: 任务状态：已创建 / 完成
STATUS_CREATED = "created"
STATUS_DONE = "done"


class GeoGebraTask(Base, CreatedAtMixin):
    """GeoGebra 几何交互任务。"""

    __tablename__ = "geogebra_tasks"

    id = uuid_pk()
    #: 任务归属学生（与 students.id 同类型：普通字符串 UUID/手机号）
    student_id = mapped_column(String(64), nullable=False, index=True)
    #: 触发源：题干文本或 AI 讲解文本（缓存键成分）
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: 数学知识点提示（可选）
    topic: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: AI 选定的场景模板名
    scene_type: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    #: GeoGebra 指令列表（evalCommand 参数数组，JSON 字符串）
    instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Applet 配置覆盖（如 coordSystem、gridVisible 等，JSON 字符串）
    applet_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: 状态
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_DONE, index=True
    )
    #: 内容指纹：source_text + topic 的 sha1 前 32 位
    content_md5: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    def to_dict(self, exclude=()):  # noqa: D102
        data = super().to_dict(exclude=("instructions", "applet_config", *exclude))
        import json

        data["instructions"] = json.loads(self.instructions) if self.instructions else []
        data["applet_config"] = json.loads(self.applet_config) if self.applet_config else {}
        data["scene_type"] = self.scene_type or ""
        return data

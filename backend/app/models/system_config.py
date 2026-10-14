# -*- coding: utf-8 -*-
"""系统配置表（key-value 存储，用于 AI 引导阈值等运营配置）。"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Column, String, Text

from app.models.base import Base


class SystemConfig(Base):
    """运营配置项。

    key 唯一，value 均为字符串存储（消费侧自行转型）。
    """

    __tablename__ = "system_config"

    id: str = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    key: str = Column(String(64), nullable=False, unique=True, index=True)
    value: str = Column(Text, nullable=False)
    description: str = Column(Text, nullable=True)

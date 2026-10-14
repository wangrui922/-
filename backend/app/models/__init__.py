"""ORM 模型统一出口。

汇总全部模型类，并对外暴露声明式基类 ``Base`` 与其 ``metadata``，
供 ``create_all`` / Alembic 使用。业务代码统一从 ``app.models`` 导入。

⚠️ 新增模型必须在此登记，否则 ``Base.metadata.create_all()`` 不会建表
（测试用的内存库完全依赖这条链路）。
"""

from __future__ import annotations

from app.models.base import Base
from app.models.user import Student, User
from app.models.family import ParentChild
from app.models.content import Answer, KnowledgePoint, Question
from app.models.learning import Attempt, LearningTask, Mistake, ReviewSchedule, StudentProfile
from app.models.ai import AiMessage, AiSession
from app.models.student_mastery import StudentMastery
from app.models.weekly_report import WeeklyReport
from app.models.activation import ActivationCode
from app.models.subscription import Order, Subscription

#: 声明式元数据（供建表 / 迁移使用）
metadata = Base.metadata

__all__ = [
    "Base",
    "metadata",
    # 账号域
    "User",
    "Student",
    "ActivationCode",
    "ParentChild",
    # 内容域
    "KnowledgePoint",
    "Question",
    "Answer",
    # 学习域
    "Mistake",
    "Attempt",
    "StudentProfile",
    "LearningTask",
    "ReviewSchedule",
    # 画像域（T04）
    "StudentMastery",
    # 家长周报域（Epic6）
    "WeeklyReport",
    # AI 域
    "AiSession",
    "AiMessage",
    # 订阅与收费域（M2）
    "Subscription",
    "Order",
]

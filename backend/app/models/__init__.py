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
from app.models.content import Answer, KnowledgePoint, KnowledgeRelation, Question
from app.models.adaptive import StudentAdaptiveLevel
from app.models.learning import (
    Attempt,
    LearningTask,
    Mistake,
    ReviewSchedule,
    StudentProfile,
)
from app.models.dna import (
    StudentKnowledgeMastery,
    StudentLearningEvent,
    StudentLearningProfile,
)
from app.models.ai import AiMessage, AiSession
from app.models.student_mastery import StudentMastery
from app.models.weekly_report import WeeklyReport
from app.models.activation import ActivationCode
from app.models.subscription import Plan, Subscription, PaymentTransaction
from app.models.gamification import (
    Badge,
    GameSession,
    Leaderboard,
    StudentBadge,
    StudentPoint,
)
from app.models.knowledge_prerequisite import KnowledgePrerequisite
from app.models.system_config import SystemConfig
from app.models.progress import PracticeProgress
from app.models.manim_task import ManimTask
from app.models.geogebra_task import GeoGebraTask
from app.models.compliance import GuardianConsent

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
    "KnowledgeRelation",
    "Question",
    "Answer",
    # 学习域
    "Mistake",
    "Attempt",
    "StudentProfile",
    "LearningTask",
    "ReviewSchedule",
    # 自适应难度域（#4 draw_adaptive）
    "StudentAdaptiveLevel",
    # 画像域（T04）
    "StudentMastery",
    # 学生 DNA 域（V6.5 Sprint 2：DNA-001/003/004）
    "StudentLearningProfile",
    "StudentKnowledgeMastery",
    "StudentLearningEvent",
    # 家长周报域（Epic6）
    "WeeklyReport",
    # 订阅域
    "Plan",
    "Subscription",
    "PaymentTransaction",
    # AI 域
    "AiSession",
    "AiMessage",
    # 游戏化域（方向六）
    "GameSession",
    "StudentPoint",
    "Badge",
    "StudentBadge",
    "Leaderboard",
    "GeoGebraTask",
    # 合规域（V6.5 T13：监护人同意留痕）
    "GuardianConsent",
]

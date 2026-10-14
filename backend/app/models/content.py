"""内容域模型：``knowledge_points``、``questions``、``answers``、``knowledge_relations``。

字段以 docs/v4-db-api.md §A.2 为准。
所有 UUID 主键/外键统一用 ``base.GUID``，JSON 用 ``base.JSONType``，跨方言一致。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, JSONType, TimestampMixin, utcnow, uuid_pk

if TYPE_CHECKING:  # 仅类型检查期引用，运行期不产生循环导入
    from app.models.ai import AiSession
    from app.models.learning import Attempt, Mistake

#: 题型枚举。
#:
#: T05（docx 题库导入）扩到 6 类：原 4 类 + 「图形」「判断」。
#: ⚠️ 数据库层**没有** CHECK 约束（``type`` 列只是 VARCHAR(16)），本元组是应用层
#: 白名单，扩容不需要迁移 DDL。
#: ⚠️ 与 ``app/ai/agents/question.py::QUESTION_TYPES`` **不是同一套枚举**——那是 AI
#: 出题 Agent 的独立枚举，本期零改动，切勿"顺手对齐"。
QUESTION_TYPE_VALUES = ("选择", "填空", "应用", "计算", "图形", "判断")
#: 难度档位 1/3/5
DIFFICULTY_VALUES = (1, 3, 5)
#: 学科，默认数学
SUBJECT_DEFAULT = "数学"
#: 题目上下架状态：active 可用 / archived 归档（V4.1 初中题入库即归档，不进推荐池）
QUESTION_STATUS_VALUES = ("active", "archived")
QUESTION_STATUS_ACTIVE = "active"
QUESTION_STATUS_ARCHIVED = "archived"

#: 教材版本枚举（T05 · docx 题库导入）
EDITION_VALUES = ("青岛版", "苏教版")
#: 来源类型枚举（T05）
SOURCE_TYPE_VALUES = ("衔接", "期末")
#: 册内分区枚举（T05）；同一册的温故/预习靠它区分，**不拆年级**
SECTION_VALUES = ("温故", "预习", "提优", "摸底", "专项", "自测")
#: 溯源标记枚举（examprep 借鉴 · 讲解质量护栏 T06）。
#: green=来自教材/资料且已核验(可信) / yellow=AI 生成补充(正确但建议抽检) /
#: red=疑似编造或未经核验(进待抽检队列, 不对外抽题)。
SOURCE_TAG_VALUES = ("green", "yellow", "red")

#: 学段（商业化学段选择器用）。
#:
#: ⚠️ 与 ``grade``（升入年级）的映射约定：**一律由 grade 推导，不单独建列、不做 grade-1 换算**。
#:   小学 1-6 / 初中 7-9 / 高中 10-12，沿用 U1「升入年级」语义向初高中自然延展。
GRADE_BANDS = ("小学", "初中", "高中")
#: grade → 学段 推导边界（含端点）。
BAND_GRADE_RANGE = {"小学": (1, 6), "初中": (7, 9), "高中": (10, 12)}


def grade_band(grade: Optional[int]) -> Optional[str]:
    """由升入年级推导学段。

    Args:
        grade: 升入年级（1-12），None 表示未标注。

    Returns:
        学段字符串（小学/初中/高中）或 None。
    """
    if grade is None:
        return None
    if grade <= 6:
        return "小学"
    if grade <= 9:
        return "初中"
    return "高中"


def grades_of_band(band: str) -> range:
    """返回某学段覆盖的升入年级 range（用于按学段抽题）。"""
    lo, hi = BAND_GRADE_RANGE.get(band, (1, 12))
    return range(lo, hi + 1)


class KnowledgePoint(TimestampMixin, Base):
    """知识点（自引用树，支持多级归类）。

    V6.5（KG-001）新增学习元数据列：``subject`` / ``grade`` / ``semester`` /
    ``chapter`` / ``difficulty`` / ``importance`` / ``status``，用于
    Student-DNA、Mastery、Priority 与难度引擎按学段/知识点定位学习状态；
    存量行由 migration 014 回填默认值。
    """

    __tablename__ = "knowledge_points"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="知识点名称")
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="父知识点(自引用)",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="描述"
    )
    code: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, unique=True, comment="编码(唯一)"
    )
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="排序权重"
    )
    subject: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="math",
        server_default="math",
        comment="学科",
    )
    grade: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="适用年级(小学3=3 … 初三=9)"
    )
    semester: Mapped[Optional[str]] = mapped_column(
        String(8), nullable=True, comment="学期：上/下"
    )
    chapter: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="所属章节"
    )
    difficulty: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=3,
        server_default="3",
        comment="难度档位 1/3/5",
    )
    importance: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=3,
        server_default="3",
        comment="重要程度 1-5",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default="active",
        comment="状态 active/archived",
    )

    parent: Mapped[Optional["KnowledgePoint"]] = relationship(
        back_populates="children",
        remote_side=[id],
    )
    children: Mapped[List["KnowledgePoint"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    questions: Mapped[List["Question"]] = relationship(
        back_populates="kp",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_knowledge_points_parent", "parent_id"),
        {"comment": "知识点表"},
    )


#: 知识点关系边类型（KG-002 · 规划 Sprint 1）：
#:
#: * ``prerequisite`` —— 前置关系：source 是 target 的前置知识（先学 source 再学 target）；
#: * ``related``     —— 相关知识：两知识点相关（跨章节关联、对比、互证）；
#: * ``next``        —— 学习顺序：source 学完后自然衔接 target（教材推进路径）。
#:
#: ⚠️ 数据库层带 CHECK 约束（见 ``KnowledgeRelation.__table_args__``），
#: 新增类型必须同步迁移 DDL（PG/SQLite 两份 015）。
KNOWLEDGE_RELATION_VALUES = ("prerequisite", "related", "next")


class KnowledgeRelation(TimestampMixin, Base):
    """知识点关系边表（KG-002 · 知识树的关系维度）。

    与 ``knowledge_points`` 的自引用 ``parent_id``（归类树）正交：
    归类树描述「知识点归属」，关系边描述「学习依赖/关联/顺序」，
    两者各自独立，共同构成 V6.5 知识树（见 docs/sprint1-kg.md 设计约定）。

    约束（与 migration 015 双方言 DDL 一致）：
        * ``source_id`` / ``target_id`` 外键指向 ``knowledge_points.id``；
        * ``relation_type`` 只能取 :data:`KNOWLEDGE_RELATION_VALUES` 之一；
        * ``source_id <> target_id``（禁止自环）；
        * ``(source_id, target_id, relation_type)`` 唯一（同一类型不重复建边）。

    主键派生（幂等 merge 用，见 ``scripts/seed_kp_relations_v65.py``）：
        ``rel_uuid(source_code, relation_type, target_code)
         = uuid5(NS_QUESTION_BANK, "rel:" + source_code + ":" + relation_type + ":" + target_code)``
        与 ``kp_uuid`` 共用 ``NS_QUESTION_BANK`` 命名空间，前缀不同避免碰撞。
    """

    __tablename__ = "knowledge_relations"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="源知识点（需先掌握/关联起点/顺序起点）",
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="目标知识点",
    )
    relation_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="关系类型 prerequisite/related/next",
    )

    #: 指向两端知识点的只读关系（级联删除由数据库 FK 负责）
    source: Mapped["KnowledgePoint"] = relationship(
        foreign_keys=[source_id],
    )
    target: Mapped["KnowledgePoint"] = relationship(
        foreign_keys=[target_id],
    )

    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('prerequisite','related','next')",
            name="ck_knowledge_relations_type",
        ),
        CheckConstraint(
            "source_id <> target_id",
            name="ck_knowledge_relations_no_self_loop",
        ),
        UniqueConstraint(
            "source_id",
            "target_id",
            "relation_type",
            name="uq_knowledge_relations_edge",
        ),
        Index("idx_knowledge_relations_target_type", "target_id", "relation_type"),
        {"comment": "知识点关系边表"},
    )


class Question(Base):
    """题目（关联知识点，可有标准答案）。"""

    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = uuid_pk()
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="题干")
    image_url: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True, comment="配图地址"
    )
    subject: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SUBJECT_DEFAULT, comment="学科"
    )
    # ⚠️ U1 年级语义（老板拍板，禁止改动）：``grade`` 存的是**升入年级**。
    #    青岛版衔接卷：一升二=2、二升三=3、三升四=4、四升五=5、五升六=6；
    #    苏教版下册卷：4下=4、5下=5、6下=6。
    #    册内的「温故（复习上学期）」与「预习（新课衔接）」用 ``section`` 区分，
    #    **不拆年级**。任何地方都严禁做 ``grade - 1`` 之类的换算。
    grade: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="适用年级(升入年级) 1-12"
    )
    difficulty: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="难度 1/3/5"
    )
    kp_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        ForeignKey("knowledge_points.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联知识点",
    )
    type_: Mapped[Optional[str]] = mapped_column(
        "type", String(16), nullable=True, comment="选择/填空/应用/计算/图形/判断"
    )
    options: Mapped[Any] = mapped_column(
        JSONType(), nullable=True, comment="选择题选项(JSON)"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=QUESTION_STATUS_ACTIVE,
        server_default=QUESTION_STATUS_ACTIVE,
        comment="active 可用 / archived 归档",
    )
    # ------------------------------------------------------------ T05 题库来源 5 列
    edition: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, comment="教材版本：青岛版/苏教版"
    )
    source_type: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, comment="来源类型：衔接/期末"
    )
    section: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, comment="册内分区：温故/预习/提优/摸底/专项"
    )
    source_doc: Mapped[Optional[str]] = mapped_column(
        String(256), nullable=True, comment="来源 docx 文件名（不含路径）"
    )
    #: 知识点是否人工复核通过。
    #:
    #: ⚠️ V7 线上事故坑：默认 **True**，而不是 False。
    #: 存量 909 道 seed 题不带 ``source_doc``，若默认 False 会被整体判成"未复核"，
    #: ``profile_service`` 的知识点归因当场置空 → 掌握度/画像/家长周报断流。
    #: docx 导入器写新题时**显式**传 ``kp_verified=False``。
    kp_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="知识点是否人工复核通过"
    )
    #: 溯源标记（T06 · examprep 借鉴）。
    #:
    #: green=来自教材/资料且已核验(讲解可直接信任) /
    #: yellow=AI 生成补充(正确但需要抽检) / red=疑似编造或未经核验(进待抽检队列, 不抽题)。
    #: 存量 docx 题回填 ``green``；AI 生成归档题(``source_doc='ai_gen'``)回填 ``red``；
    #: 本库批量生成题(``scripts/generate_math_bank.py``)标 ``yellow``。
    source_tag: Mapped[Optional[str]] = mapped_column(
        String(8),
        nullable=True,
        default="green",
        server_default="green",
        comment="溯源标记 green/yellow/red",
    )
    #: 是否可随产品对外发布（T07 · 版权护栏）。
    #:
    #: 默认 **True**（可发布）。真实考卷若版权未清/仅内部评测，导入时显式置 **False**，
    #: 使其不进入任何对外发布/导出池（仅内部校验、讲解质量评测可用）。
    #: 例：云南中考真题(用户声明可公开)=True；下载站模拟卷(内部金标准)=False。
    ship_ready: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        comment="是否可对外发布；真实卷内部金标准置0",
    )
    # ------------------------------------------------------------ 方向六 · 生活场景题库
    scenario_type: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, comment="场景类型：shopping/cake/time/distance"
    )
    scenario_template: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="题目模板（含占位符）"
    )
    scenario_params: Mapped[Any] = mapped_column(
        JSONType(), nullable=True, comment="JSON：模板参数范围"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, comment="创建时间(UTC)"
    )

    kp: Mapped[Optional["KnowledgePoint"]] = relationship(back_populates="questions")
    answer: Mapped[Optional["Answer"]] = relationship(
        back_populates="question",
        uselist=False,
        cascade="all, delete-orphan",
    )
    mistakes: Mapped[List["Mistake"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )
    attempts: Mapped[List["Attempt"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )
    ai_sessions: Mapped[List["AiSession"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("difficulty IN (1,3,5)", name="ck_questions_difficulty"),
        Index("idx_questions_kp", "kp_id"),
        Index("idx_questions_status_grade", "status", "grade"),
        Index("idx_questions_source_doc", "source_doc"),
        Index("idx_questions_edition_grade", "edition", "grade"),
        Index("idx_questions_scenario", "scenario_type"),
        {"comment": "题目表"},
    )


class Answer(Base):
    """题目标准答案与解析（1:1 对应 Question）。"""

    __tablename__ = "answers"

    id: Mapped[uuid.UUID] = uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="所属题目(1:1)",
    )
    correct_answer: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="正确答案"
    )
    analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="解析")
    distractor_analysis: Mapped[Any] = mapped_column(
        JSONType(), nullable=False, default=dict, comment="干扰项分析(JSON)"
    )

    question: Mapped["Question"] = relationship(back_populates="answer")

    __table_args__ = ({"comment": "题目答案表"},)


__all__ = [
    "KnowledgePoint",
    "KnowledgeRelation",
    "Question",
    "Answer",
    "KNOWLEDGE_RELATION_VALUES",
    "QUESTION_TYPE_VALUES",
    "DIFFICULTY_VALUES",
    "SUBJECT_DEFAULT",
    "QUESTION_STATUS_VALUES",
    "QUESTION_STATUS_ACTIVE",
    "QUESTION_STATUS_ARCHIVED",
    "EDITION_VALUES",
    "SOURCE_TYPE_VALUES",
    "SECTION_VALUES",
    "SOURCE_TAG_VALUES",
    "GRADE_BANDS",
    "BAND_GRADE_RANGE",
    "grade_band",
    "grades_of_band",
]

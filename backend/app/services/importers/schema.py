"""docx 题库导入的数据契约（T05 · T2 解析内核）。

只放**纯数据结构**与异常，不含任何解析逻辑、不依赖 SQLAlchemy，
便于解析器与落库器解耦、也便于单测直接构造。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: 稳定业务键的前缀。与 ``backend/scripts/seed_db.py`` 的 ``qbank:`` 天然不撞。
STABLE_KEY_PREFIX = "docx"


class ImporterError(Exception):
    """导入器基类异常。"""


class PairingError(ImporterError):
    """苏教版 A 组「正文 / 答案」配对闸门未通过。

    ⚠️ 抛出即代表**整卷跳过**。绝不允许降级成顺序对齐 / 文件名模糊匹配 /
    overlap 不足时部分入库等 best-effort 行为——错配答案的危害远大于没有答案。
    """


class SchemaGuardError(ImporterError):
    """V7 前置守卫未通过（存量题被误判为未复核）。"""


@dataclass
class ParsedQuestion:
    """一道解析出来的题目。

    Attributes:
        stable_key: 稳定业务键，**只由「文件名 + 卷号 + 大题号 + 小题号」构成，
            不含题干内容**。解析规则迭代后主键不变，重跑是覆盖而不是新增。
        content: 题干（已清洗）。
        q_type: 题型，取值来自 ``QUESTION_TYPE_VALUES``；判不出来时为 ``None``。
        paper_no: 卷号 / 单元号（如 ``巩固提升1``、``摸底诊断卷(一)``、``专项复习卷3``）。
        major_no: 大题号（中文数字，如 ``三``）。
        minor_no: 小题号（阿拉伯数字字符串；大题下无小题时为 ``"0"``）。
        section: 册内分区（温故 / 预习 / 提优 / 摸底 / 专项 / 自测）。
        correct_answer: 标准答案；抓不到时为 ``None``（宁缺毋错）。
        analysis: 解析；原书没有解析文本时为 ``None``。
        options: 选择题选项列表；非选择题为 ``None``。
    """

    stable_key: str
    content: str
    q_type: Optional[str] = None
    paper_no: str = ""
    major_no: str = ""
    minor_no: str = "0"
    section: Optional[str] = None
    correct_answer: Optional[str] = None
    analysis: Optional[str] = None
    options: Optional[List[str]] = None

    def answer_key(self) -> str:
        """题目在「卷内」的定位键，用于与答案侧做集合比对。"""
        return f"{self.paper_no}|{self.major_no}|{self.minor_no}"


@dataclass
class SkippedPaper:
    """被整卷跳过的记录（配对闸门不过 / 解析为空）。"""

    paper_no: str
    reason: str
    detail: str = ""


@dataclass
class ParseOutcome:
    """一个「导入单元」的解析结果。

    导入单元 = 青岛版一册 / 苏教版 A 组一卷（正文+答案）/ 苏教版 B 组一个文件。
    """

    source_key: str
    label: str
    edition: str
    source_type: str
    grade: int
    source_doc: str
    questions: List[ParsedQuestion] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skipped: List[SkippedPaper] = field(default_factory=list)

    @property
    def question_count(self) -> int:
        """解析出的题目数。"""
        return len(self.questions)

    @property
    def answered_count(self) -> int:
        """带标准答案的题目数。"""
        return sum(1 for q in self.questions if q.correct_answer)

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的摘要（导入报告用）。"""
        return {
            "sourceKey": self.source_key,
            "label": self.label,
            "edition": self.edition,
            "sourceType": self.source_type,
            "grade": self.grade,
            "sourceDoc": self.source_doc,
            "questions": self.question_count,
            "answered": self.answered_count,
            "missingAnswer": self.question_count - self.answered_count,
            "warnings": list(self.warnings),
            "skipped": [
                {"paperNo": s.paper_no, "reason": s.reason, "detail": s.detail}
                for s in self.skipped
            ],
        }


@dataclass
class LoadStats:
    """落库统计。

    ``gated_clean`` / ``gated_review`` 是 P1 质量闸门落地的直接体现：
    新题（``source_doc`` 非空）按 ``quality_gate.classify_question`` 逐题定性，
    分别计入这两格。种子题（``source_doc`` 为空）不受闸门影响，不计入。
    """

    inserted: int = 0
    updated: int = 0
    answers: int = 0
    promoted_preserved: int = 0  # 历史遗留字段，闸门模型下恒为 0（保留以兼容报告字段）
    gated_clean: int = 0
    gated_review: int = 0

    def merge(self, other: "LoadStats") -> "LoadStats":
        """把另一份统计累加进来并返回自身。"""
        self.inserted += other.inserted
        self.updated += other.updated
        self.answers += other.answers
        self.promoted_preserved += other.promoted_preserved
        self.gated_clean += other.gated_clean
        self.gated_review += other.gated_review
        return self

    def to_dict(self) -> Dict[str, int]:
        """转字典（导入报告用）。"""
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "answers": self.answers,
            "promotedPreserved": self.promoted_preserved,
            "gatedClean": self.gated_clean,
            "gatedReview": self.gated_review,
        }


def build_stable_key(source_doc: str, paper_no: str, major_no: str, minor_no: str) -> str:
    """拼稳定业务键。

    Args:
        source_doc: 来源 docx 文件名（不含路径）。
        paper_no: 卷号 / 单元号。
        major_no: 大题号。
        minor_no: 小题号。

    Returns:
        形如 ``docx:实验班....docx|巩固提升1|一|2`` 的字符串。

    Note:
        **严禁**把题干内容拼进来——那样解析规则一改主键就漂移，重跑会灌出重复题。
    """
    return f"{STABLE_KEY_PREFIX}:{source_doc}|{paper_no}|{major_no}|{minor_no}"


__all__ = [
    "STABLE_KEY_PREFIX",
    "ImporterError",
    "PairingError",
    "SchemaGuardError",
    "ParsedQuestion",
    "SkippedPaper",
    "ParseOutcome",
    "LoadStats",
    "build_stable_key",
]

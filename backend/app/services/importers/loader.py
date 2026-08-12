"""解析结果落库（T05 · T4）。

完整沿用 ``backend/scripts/seed_db.py`` 的**唯一权威导入范式**：
    * ``uuid5(NS_QUESTION_BANK, 稳定键)`` 派生确定性主键；
    * 稳定键前缀用 ``docx:`` / ``docx-answer:``，与 seed 的 ``qbank:`` 天然不撞；
    * upsert 走「先 SELECT 再 UPDATE/INSERT」，不用 PG 专属 ``ON CONFLICT``。

━━━━━━━━━━━━━━━━━━━━━━━━ U2 新题状态 ━━━━━━━━━━━━━━━━━━━━━━━━
新导入的题由 **质量闸门** （``quality_gate.classify_question``）逐题定性：

    * ``clean``  → ``status='active'``（进推荐池，可被学生刷到）
    * ``review`` → ``status='archived'``（**不上架**，留待人工复核）

落库时把命中原因回写到该题 ``Answer.analysis`` 末尾（``【质量待复核】…``）。
``promote_bank.py`` 的 ``--promote --all`` 还会**二次过滤**：只对闸门判定为
``clean`` 的 archived 题执行上架，构成双保险。

⚠️ **重跑会重新定性（P1 关键变更）**：
   闸门才是新题上架的唯一权威。幂等重灌时，新题（``source_doc`` 非空）的
   ``status`` 一律按**最新**闸门判定覆盖——包括把上一轮被 ``--promote --all``
   全量上架的题，重新打回 ``archived``。种子题（``source_doc IS NULL``）不受
   闸门影响，``status`` 保持历史/人工值，永不被重跑改动。
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content import (
    QUESTION_STATUS_ACTIVE,
    QUESTION_STATUS_ARCHIVED,
    SUBJECT_DEFAULT,
    Answer,
    Question,
)
from app.services.importers.quality_gate import (
    VERDICT_CLEAN,
    classify_question,
    validate_question_integrity,
)
from app.services.importers.schema import LoadStats, ParsedQuestion, ParseOutcome

logger = logging.getLogger("app.services.importers.loader")

#: 质量闸门原因回写标记（前置，追加到答案解析末尾）。
_REVIEW_TAG_PREFIX = "【质量待复核】"


def _strip_review_tag(text: str | None) -> str:
    """剥掉历史遗留的「【质量待复核】…」整段，只留原始解析内容。

    幂等重灌时会先剥掉旧块再重新追加，避免原因越叠越长。

    Args:
        text: 原始答案解析。

    Returns:
        标记之前的内容（即导入器产出的原始解析），已 strip。
    """
    if not text:
        return ""
    # 第一个标记之前是原始解析；之后都是历次叠加的待复核块
    return text.split(_REVIEW_TAG_PREFIX, 1)[0].strip()


def _render_analysis(base_analysis: str | None, reasons: List[str]) -> str | None:
    """把质量闸门原因（若有）幂等地附加到答案解析末尾。

    Args:
        base_analysis: 导入器产出的原始解析（可能为空）。
        reasons: 闸门命中原因列表；空代表 ``clean``。

    Returns:
        组合后的解析；若既无原始解析也无原因返回 ``None``。
    """
    base = _strip_review_tag(base_analysis)
    if not reasons:
        return base or None
    reason_text = "；".join(reasons)
    rendered = (base + "\n" + _REVIEW_TAG_PREFIX + reason_text).strip()
    return rendered or None


#: uuid5 命名空间。
#:
#: ⚠️ 必须与 ``backend/scripts/seed_db.py::NS_QUESTION_BANK`` **完全一致**
#: （单测 ``test_t05_docx_import.py::test_namespace_matches_seed_db`` 会守住这一点）。
#: 这里重新声明而不是 import，是为了避免 ``app/`` 反向依赖 ``scripts/``。
NS_QUESTION_BANK: uuid.UUID = uuid.UUID("6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21")


def question_uuid(stable_key: str) -> uuid.UUID:
    """由稳定键派生题目主键。

    Args:
        stable_key: :func:`app.services.importers.schema.build_stable_key` 的产物，
            已自带 ``docx:`` 前缀。

    Returns:
        确定性 UUID。
    """
    return uuid.uuid5(NS_QUESTION_BANK, stable_key)


def answer_uuid(stable_key: str) -> uuid.UUID:
    """由稳定键派生答案主键。

    Args:
        stable_key: 同 :func:`question_uuid`。

    Returns:
        确定性 UUID。
    """
    return uuid.uuid5(NS_QUESTION_BANK, stable_key.replace("docx:", "docx-answer:", 1))


def _upsert_question(
    session: Session,
    parsed: ParsedQuestion,
    outcome: ParseOutcome,
    stats: LoadStats,
    is_new_bank: bool,
) -> Tuple[uuid.UUID, str, List[str]]:
    """写一道题（存在则更新内容），并按质量闸门裁决 ``status``。

    Args:
        session: 数据库会话（调用方负责 commit）。
        parsed: 解析出来的题目。
        outcome: 所属解析结果（提供 edition / grade / source_doc）。
        stats: 累加统计。
        is_new_bank: 是否为新题（``source_doc`` 非空）。新题走质量闸门；
            种子题（``source_doc`` 为空）不走闸门，恒 ``clean``。

    Returns:
        ``(题目主键, 闸门判定, 命中原因列表)``。
    """
    # 新题逐题过闸门；种子题不受闸门影响
    if is_new_bank:
        verdict, reasons = classify_question(
            parsed.content,
            parsed.correct_answer,
            q_type=parsed.q_type,
            options=parsed.options,
        )
        # 防新增死题卡口：缺题型/缺答案/选择·判断答案无法解析的题强制 review。
        # 与批处理归档脚本共用 judge_engine 口径（单点真相），不各自判断。
        integrity_reasons = validate_question_integrity(
            parsed.q_type, parsed.options, parsed.correct_answer
        )
        all_reasons = reasons + integrity_reasons
    else:
        verdict, reasons = VERDICT_CLEAN, []
        all_reasons = []
    is_clean = verdict == VERDICT_CLEAN and not integrity_reasons
    target_status = QUESTION_STATUS_ACTIVE if is_clean else QUESTION_STATUS_ARCHIVED

    qid = question_uuid(parsed.stable_key)
    existing: Optional[Question] = session.get(Question, qid)

    if existing is None:
        question = Question(
            id=qid,
            content=parsed.content,
            image_url=None,
            subject=SUBJECT_DEFAULT,
            # U1：grade 存「升入年级」，直接取 manifest 登记值，禁止任何 grade-1 换算
            grade=outcome.grade,
            difficulty=None,  # 原书无难度标注，留空（CHECK 允许 NULL）
            kp_id=None,       # 自动打标留待后续，先空着
            type_=parsed.q_type,
            options=parsed.options or None,
            # P1：status 由质量闸门裁决（clean→active / review→archived）
            status=target_status,
            edition=outcome.edition,
            source_type=outcome.source_type,
            section=parsed.section,
            source_doc=outcome.source_doc,
            kp_verified=False,  # 未复核；profile_service 会把它的知识点归因置空
        )
        session.add(question)
        stats.inserted += 1
    else:
        # 幂等重灌：内容类字段覆盖
        existing.content = parsed.content
        existing.type_ = parsed.q_type
        existing.options = parsed.options or None
        existing.grade = outcome.grade
        existing.edition = outcome.edition
        existing.source_type = outcome.source_type
        existing.section = parsed.section
        existing.source_doc = outcome.source_doc
        # P1：新题 status 由闸门重新裁决（重跑可把上一轮误上架的题打回 archived）。
        # 种子题 status 不在此改动，保持历史/人工值。
        if is_new_bank:
            existing.status = target_status
        stats.updated += 1

    # 闸门计数（仅新题）
    if is_new_bank:
        if is_clean:
            stats.gated_clean += 1
        else:
            stats.gated_review += 1

    return qid, verdict, all_reasons


def _upsert_answer(
    session: Session,
    parsed: ParsedQuestion,
    question_id: uuid.UUID,
    stats: LoadStats,
    reasons: List[str],
) -> None:
    """写答案（1:1 挂在题目上）。

    答案为空且解析为空、也无闸门原因时，不建行。若命中闸门原因，则即使
    原始解析为空，也把原因落进 ``analysis``（幂等：旧块先剥后加）。

    Args:
        session: 数据库会话。
        parsed: 解析出来的题目。
        question_id: 题目主键。
        stats: 累加统计。
        reasons: 闸门命中原因（空代表 ``clean``）。
    """
    has_content = bool(parsed.correct_answer) or bool(parsed.analysis)
    if not has_content and not reasons:
        return

    analysis = _render_analysis(parsed.analysis, reasons)
    aid = answer_uuid(parsed.stable_key)
    existing: Optional[Answer] = session.get(Answer, aid)
    if existing is None:
        session.add(
            Answer(
                id=aid,
                question_id=question_id,
                correct_answer=parsed.correct_answer,
                analysis=analysis,
                distractor_analysis={},
            )
        )
        stats.answers += 1
    else:
        existing.correct_answer = parsed.correct_answer
        existing.analysis = analysis
        stats.answers += 1


def load_outcome(session: Session, outcome: ParseOutcome, *, flush_every: int = 200) -> LoadStats:
    """把一个解析结果落库。

    Args:
        session: 数据库会话（**不 commit**，由调用方决定事务边界）。
        outcome: 解析结果。
        flush_every: 每写多少条 flush 一次。

    Returns:
        :class:`LoadStats`。
    """
    stats = LoadStats()
    is_new_bank = bool(outcome.source_doc)  # 新题（docx 来源）才过闸门
    for index, parsed in enumerate(outcome.questions, start=1):
        qid, _verdict, reasons = _upsert_question(session, parsed, outcome, stats, is_new_bank)
        _upsert_answer(session, parsed, qid, stats, reasons)
        if index % flush_every == 0:
            session.flush()
    session.flush()
    logger.info(
        "[loader] %s 落库完成：新增 %d / 更新 %d / 答案 %d",
        outcome.source_doc, stats.inserted, stats.updated, stats.answers,
    )
    return stats


def load_all(session: Session, outcomes: Iterable[ParseOutcome]) -> LoadStats:
    """批量落库。

    Args:
        session: 数据库会话（不 commit）。
        outcomes: 解析结果序列。

    Returns:
        汇总后的 :class:`LoadStats`。
    """
    total = LoadStats()
    for outcome in outcomes:
        total.merge(load_outcome(session, outcome))
    return total


def count_by_source_doc(session: Session, source_docs: Optional[List[str]] = None) -> Dict[str, int]:
    """统计各来源文件已入库的题数。

    Args:
        session: 数据库会话。
        source_docs: 限定的文件名列表；``None`` 表示全部 docx 来源题。

    Returns:
        ``{source_doc: 题数}``。
    """
    stmt = select(Question.source_doc, Question.id).where(Question.source_doc.is_not(None))
    if source_docs:
        stmt = stmt.where(Question.source_doc.in_(source_docs))
    counts: Dict[str, int] = {}
    for doc, _qid in session.execute(stmt).all():
        counts[str(doc)] = counts.get(str(doc), 0) + 1
    return counts


__all__ = [
    "NS_QUESTION_BANK",
    "question_uuid",
    "answer_uuid",
    "load_outcome",
    "load_all",
    "count_by_source_doc",
]

"""学习画像服务层：事件采集 + 缓存管理 + 三个看板视图（T04 · B6）。

分层：
    ``app.services.mastery_engine``  纯算法（无 DB）
    ``app.services.profile_service`` 本层：读 DB → 规约成事件 → 调算法 → 物化缓存
    ``app.api.profile``              路由：鉴权 + 序列化

━━━━━━━━━━━━━━━━━━━━━━ 事件源核查结论（R2 / R3 / R5）━━━━━━━━━━━━━━━━━━━━━━
R2 正样本从哪来？
    ✅ ``attempts`` 表**已存在**且带 ``is_correct`` 布尔字段（app/models/learning.py），
       正负样本齐全，无需退化到「只有负样本」的保守模式。
       故一手事件源 = ``attempts JOIN questions`` 取 ``questions.kp_id``。
    ``mistakes`` 作为二手补充：**仅当该题在 attempts 里没有任何记录时**才计入，
    避免「一次做错既进 attempts 又进 mistakes」被重复计两次负样本。
    已攻克的错题（solved=True）按 OQ 默认值近似成一次正样本（权重 0.6）。

R3 ai_messages 能否关联题目？
    ✅ 能。``ai_messages.role`` 字段存在，``ai_sessions.question_id`` 可回连题目。
       但 T04 的错因聚类**不依赖**它：``mistakes.error_type/error_reason`` 是
       结构化程度更高、噪音更小的一手错因源，够用且稳定。
       ai_messages 仅作为「错题没写错因时」的兜底证据（取该题会话里学生的最后一句），
       因此**不需要**降级为会话级全局统计。

R5 未关联知识点的错题？
    ``questions.kp_id`` 可空。kp_id 为空的错题：
      * **不进**掌握度计算（没有归属知识点，算不了）；
      * **计入** ``/error-patterns`` 的 ``total_mistakes``，
        并在 summary 里单独报 ``unmapped_mistakes``，避免数据「凭空消失」。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 跨方言约定 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    * upsert 一律「先 SELECT 再 UPDATE/INSERT」，不用 PG 的 ``ON CONFLICT``；
    * 不出现 ``jsonb_*`` / ``array_agg`` / ``ILIKE``；
    * 分组聚合全部在 Python 内存里做（V4.1 单学生数据量极小）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import (
    ERROR_PATTERN_DEFAULT_DAYS,
    ERROR_PATTERN_MAX_DAYS,
    EVENT_WEIGHT_ATTEMPT,
    EVENT_WEIGHT_MISTAKE,
    EVENT_WEIGHT_MISTAKE_SOLVED,
    MASTERY_CACHE_TTL_SECONDS,
    MODE_CACHE,
    MODE_FRESH,
    RECOMMEND_QUESTION_LIMIT,
    RECOMMEND_TOP_N,
)
from app.models.ai import AiMessage, AiSession
from app.models.base import utcnow
from app.models.content import KnowledgePoint, Question
from app.models.learning import Attempt, Mistake
from app.models.student_mastery import StudentMastery
from app.services import mastery_engine as engine
from app.services.mastery_engine import (
    AttemptEvent,
    ErrorEvidence,
    ErrorPattern,
    MasteryEntry,
    MasteryResult,
    Recommendation,
    as_utc,
)

logger = logging.getLogger("app.services.profile")


# ==================================================================== 返回结构
@dataclass
class MasteryView:
    """``GET /api/profile/mastery`` 的服务层结果。"""

    student_id: str
    items: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    computed_at: Optional[datetime] = None
    mode: str = MODE_FRESH


@dataclass
class ErrorPatternView:
    """``GET /api/profile/error-patterns`` 的服务层结果。"""

    student_id: str
    patterns: List[ErrorPattern] = field(default_factory=list)
    total_mistakes: int = 0
    unmapped_mistakes: int = 0
    range_days: int = ERROR_PATTERN_DEFAULT_DAYS
    computed_at: Optional[datetime] = None
    mode: str = MODE_FRESH


@dataclass
class RecommendationView:
    """``GET /api/profile/recommendations`` 的服务层结果。"""

    student_id: str
    items: List[Recommendation] = field(default_factory=list)
    computed_at: Optional[datetime] = None
    mode: str = MODE_FRESH


# ==================================================================== 通用工具
def to_uuid(raw: Any) -> uuid.UUID:
    """宽松地把入参转成 UUID（失败抛 ValueError，由调用方决定如何处理）。"""
    if isinstance(raw, uuid.UUID):
        return raw
    return uuid.UUID(str(raw))


def _kp_key(value: Any) -> str:
    """知识点 ID 的字符串键（算法层统一用字符串，规避 UUID/str 混用）。"""
    return str(value)


def _clamp_range_days(range_days: Optional[int]) -> int:
    """把 ``range_days`` 收敛到 ``[1, ERROR_PATTERN_MAX_DAYS]``。"""
    try:
        days = int(range_days) if range_days is not None else ERROR_PATTERN_DEFAULT_DAYS
    except (TypeError, ValueError):
        return ERROR_PATTERN_DEFAULT_DAYS
    if days <= 0:
        return ERROR_PATTERN_DEFAULT_DAYS
    return min(days, ERROR_PATTERN_MAX_DAYS)


def _mistake_error_text(mistake: Mistake) -> str:
    """把错题的错误类型 + 原因拼成一段用于分类的文本。"""
    parts = [
        (mistake.error_type or "").strip(),
        (mistake.error_reason or "").strip(),
    ]
    return " ".join(part for part in parts if part).strip()


# ============================================================ 练习事件采集器
class PracticeEventCollector:
    """把 ``attempts`` / ``mistakes`` 规约成按知识点展开的 ``AttemptEvent``。

    用法::

        collector = PracticeEventCollector(db, student_id)
        collector.load()
        events = collector.events_by_kp        # {kp_id: [AttemptEvent, ...]}
        errors = collector.error_evidence_by_kp

    设计要点：
        * 一题多知识点时逐 kp 展开（当前 ``questions.kp_id`` 是单值，
          这里仍按 list 处理，将来加多对多映射表时只要改 ``_kp_ids_of`` 一处）；
        * 去重优先级：attempts > mistakes，见模块 docstring 的 R2 说明。
    """

    def __init__(self, db: Session, student_id: uuid.UUID) -> None:
        """初始化采集器。

        Args:
            db: 数据库会话。
            student_id: 学生 ID。
        """
        self.db: Session = db
        self.student_id: uuid.UUID = student_id

        #: {kp_id(str): [AttemptEvent]}
        self.events_by_kp: Dict[str, List[AttemptEvent]] = {}
        #: {kp_id(str): [ErrorEvidence]}
        self.error_evidence_by_kp: Dict[str, List[ErrorEvidence]] = {}
        #: {kp_id(str): kp_name}
        self.kp_names: Dict[str, str] = {}
        #: 没有关联知识点的错题条数（R5）
        self.unmapped_mistakes: int = 0
        #: 全部错题（含无 kp 的），供 /error-patterns 使用
        self.all_error_evidence: List[ErrorEvidence] = []
        #: 已在 attempts 里出现过的题目，用于「错题不重复计负样本」的去重
        self._attempted_question_ids: Set[uuid.UUID] = set()

    # ------------------------------------------------------------ 内部查询
    def _question_kp_map(self, question_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, Optional[uuid.UUID]]:
        """批量取 ``question_id -> kp_id`` 映射（一次查询，避免 N+1）。"""
        ids = [qid for qid in question_ids if qid is not None]
        if not ids:
            return {}
        rows = self.db.execute(
            select(Question.id, Question.kp_id).where(Question.id.in_(ids))
        ).all()
        return {row[0]: row[1] for row in rows}

    def _kp_ids_of(self, kp_id: Optional[uuid.UUID]) -> List[uuid.UUID]:
        """一道题归属的知识点列表。

        当前数据模型是「一题一知识点」（``questions.kp_id``），
        返回 list 是为了将来接多对多映射时不用改调用方。
        """
        return [kp_id] if kp_id is not None else []

    def _load_kp_names(self, kp_ids: Set[uuid.UUID]) -> None:
        """批量取知识点名称。"""
        if not kp_ids:
            return
        rows = self.db.execute(
            select(KnowledgePoint.id, KnowledgePoint.name).where(KnowledgePoint.id.in_(list(kp_ids)))
        ).all()
        for row in rows:
            self.kp_names[_kp_key(row[0])] = str(row[1] or "未命名知识点")

    def _fallback_evidence(self, question_id: uuid.UUID) -> str:
        """错题没写错因时，取该题 AI 会话里学生的最后一句话当证据（R3 兜底）。

        Args:
            question_id: 题目 ID。

        Returns:
            一段文本；查不到时返回空串。
        """
        row = self.db.execute(
            select(AiMessage.content)
            .join(AiSession, AiMessage.session_id == AiSession.id)
            .where(
                AiSession.student_id == self.student_id,
                AiSession.question_id == question_id,
                AiMessage.role == "user",
            )
            .order_by(AiMessage.created_at.desc())
            .limit(1)
        ).first()
        return str(row[0]).strip() if row and row[0] else ""

    # ------------------------------------------------------------ 主流程
    def load(self, *, range_days: Optional[int] = None) -> "PracticeEventCollector":
        """加载并规约全部事件。

        Args:
            range_days: 仅统计最近 N 天的错题证据；``None`` 表示不限。
                掌握度计算恒用全量（时效衰减自会削弱旧数据的影响）。

        Returns:
            self，便于链式调用。
        """
        self._load_attempts()
        self._load_mistakes(range_days=range_days)

        kp_ids: Set[uuid.UUID] = set()
        for key in list(self.events_by_kp.keys()) + list(self.error_evidence_by_kp.keys()):
            try:
                kp_ids.add(to_uuid(key))
            except (TypeError, ValueError):  # pragma: no cover - 键恒由 UUID 生成
                continue
        self._load_kp_names(kp_ids)
        return self

    def load_error_evidence(self, *, range_days: Optional[int] = None) -> "PracticeEventCollector":
        """只加载错因证据（``/error-patterns`` 用，跳过掌握度事件的重活）。

        Args:
            range_days: 统计窗口天数；``None`` 表示不限。

        Returns:
            self，便于链式调用。
        """
        self._load_mistakes(range_days=range_days)
        return self

    def _load_attempts(self) -> None:
        """一手事件源：作答流水（含正负样本）。"""
        rows = self.db.execute(
            select(Attempt.question_id, Attempt.is_correct, Attempt.created_at, Question.kp_id)
            .join(Question, Attempt.question_id == Question.id)
            .where(Attempt.student_id == self.student_id)
        ).all()

        for question_id, is_correct, created_at, kp_id in rows:
            self._attempted_question_ids.add(question_id)
            for target_kp in self._kp_ids_of(kp_id):
                self.events_by_kp.setdefault(_kp_key(target_kp), []).append(
                    AttemptEvent(
                        kp_id=_kp_key(target_kp),
                        is_correct=bool(is_correct),
                        occurred_at=as_utc(created_at) or utcnow(),
                        weight=EVENT_WEIGHT_ATTEMPT,
                        source="attempt",
                    )
                )

    def _load_mistakes(self, *, range_days: Optional[int] = None) -> None:
        """二手事件源：错题本（补充负样本 + 提供错因证据）。"""
        stmt = select(Mistake).where(Mistake.student_id == self.student_id)
        if range_days is not None:
            since = utcnow() - timedelta(days=int(range_days))
            stmt = stmt.where(Mistake.created_at >= since)

        mistakes: List[Mistake] = list(self.db.execute(stmt).scalars())
        if not mistakes:
            return

        kp_map = self._question_kp_map({m.question_id for m in mistakes})

        for mistake in mistakes:
            kp_id = kp_map.get(mistake.question_id)
            targets = self._kp_ids_of(kp_id)

            # ---- 错因证据（无论有没有 kp 都要计入全局统计，R5） ----------
            text = _mistake_error_text(mistake)
            if not text:
                text = self._fallback_evidence(mistake.question_id)
            evidence = ErrorEvidence(
                text=text,
                occurred_at=as_utc(mistake.created_at),
                kp_id=_kp_key(targets[0]) if targets else None,
            )
            self.all_error_evidence.append(evidence)

            if not targets:
                self.unmapped_mistakes += 1
                continue

            for target_kp in targets:
                key = _kp_key(target_kp)
                self.error_evidence_by_kp.setdefault(key, []).append(evidence)

                # ---- 已攻克 → 近似一次正样本（OQ 默认值） --------------------
                # 必须放在下面的去重 continue **之前**：去重只用于避免「同一道题
                # 做错」被 attempts 与 mistakes 重复计负样本，而「攻克」是一个独立
                # 的正向信号。主场景是「先作答(写 attempts) → 建错题 → 攻克」，
                # 此时错题几乎都有 attempts 前置，若放在 continue 之后，正样本会
                # 被连带跳过，导致标记已掌握后掌握度纹丝不动（P1-5 失效）。
                if mistake.solved:
                    solved_at = as_utc(mistake.solved_at) or as_utc(mistake.created_at) or utcnow()
                    self.events_by_kp.setdefault(key, []).append(
                        AttemptEvent(
                            kp_id=key,
                            is_correct=True,
                            occurred_at=solved_at,
                            weight=EVENT_WEIGHT_MISTAKE_SOLVED,
                            source="mistake_solved",
                        )
                    )

                # ---- 去重：该题已有 attempts 记录时不再补负样本，避免重复计数 --
                if mistake.question_id in self._attempted_question_ids:
                    continue

                self.events_by_kp.setdefault(key, []).append(
                    AttemptEvent(
                        kp_id=key,
                        is_correct=False,
                        occurred_at=as_utc(mistake.created_at) or utcnow(),
                        weight=EVENT_WEIGHT_MISTAKE,
                        source="mistake",
                    )
                )

# ==================================================================== 缓存判定
def _is_cache_valid(rows: Sequence[StudentMastery], *, now: Optional[datetime] = None) -> bool:
    """判断物化缓存是否仍然新鲜。

    两个条件同时满足才算有效：
        1. ``computed_at + TTL > now``（没过期）；
        2. ``last_practiced_at <= computed_at``（算完之后没有新的练习/攻克）。

    Args:
        rows: 该学生的全部 ``student_mastery`` 行。
        now: 基准时刻，缺省取当前 UTC。

    Returns:
        True 表示可以直接用缓存。
    """
    if not rows:
        return False

    base_now = as_utc(now) or utcnow()
    ttl = timedelta(seconds=MASTERY_CACHE_TTL_SECONDS)

    for row in rows:
        computed_at = as_utc(row.computed_at)
        if computed_at is None or computed_at + ttl <= base_now:
            return False
        last_practiced = as_utc(row.last_practiced_at)
        if last_practiced is not None and last_practiced > computed_at:
            return False
    return True


def upsert_mastery(
    db: Session,
    *,
    student_id: uuid.UUID,
    knowledge_point_id: uuid.UUID,
    result: MasteryResult,
    error_summary: Optional[Dict[str, int]] = None,
    computed_at: Optional[datetime] = None,
) -> StudentMastery:
    """跨方言 upsert：先 SELECT，再 UPDATE 或 INSERT（**不用 PG 的 ON CONFLICT**）。

    Args:
        db: 数据库会话（本函数只 flush，不 commit，事务边界交给调用方）。
        student_id: 学生 ID。
        knowledge_point_id: 知识点 ID。
        result: 掌握度计算结果。
        error_summary: 该知识点的错因分布，如 ``{"CALC": 3}``。
        computed_at: 计算时间，缺省取当前 UTC。

    Returns:
        写入后的 ``StudentMastery`` 实例。
    """
    stamp = as_utc(computed_at) or utcnow()
    row = db.execute(
        select(StudentMastery).where(
            StudentMastery.student_id == student_id,
            StudentMastery.knowledge_point_id == knowledge_point_id,
        )
    ).scalar_one_or_none()

    if row is None:
        row = StudentMastery(
            student_id=student_id,
            knowledge_point_id=knowledge_point_id,
        )
        db.add(row)

    row.mastery_score = float(result.score)
    row.mastery_level = int(result.level)
    row.attempts = int(result.attempts)
    row.correct_count = int(result.correct_count)
    row.error_rate = float(result.error_rate)
    row.effective_attempts = float(result.effective_attempts)
    row.error_summary = dict(error_summary or {})
    row.last_practiced_at = as_utc(result.last_practiced_at)
    row.computed_at = stamp

    db.flush()
    return row


def touch_mastery_practiced(
    db: Session,
    *,
    student_id: uuid.UUID,
    knowledge_point_ids: Sequence[uuid.UUID],
    practiced_at: Optional[datetime] = None,
) -> int:
    """把指定知识点的 ``last_practiced_at`` 推到最新，**使缓存失效**（B11 调用）。

    Args:
        db: 数据库会话（不 commit）。
        student_id: 学生 ID。
        knowledge_point_ids: 需要失效的知识点 ID 列表。
        practiced_at: 练习时间，缺省当前 UTC。

    Returns:
        实际更新的行数。
    """
    targets = [kp for kp in knowledge_point_ids if kp is not None]
    if not targets:
        return 0

    stamp = as_utc(practiced_at) or utcnow()
    rows = list(
        db.execute(
            select(StudentMastery).where(
                StudentMastery.student_id == student_id,
                StudentMastery.knowledge_point_id.in_(targets),
            )
        ).scalars()
    )
    for row in rows:
        row.last_practiced_at = stamp
    if rows:
        db.flush()
    return len(rows)


# ==================================================================== 计算入口
def _compute_and_store(
    db: Session,
    student_id: uuid.UUID,
) -> Tuple[List[MasteryEntry], datetime]:
    """全量重算掌握度并物化到 ``student_mastery``。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。

    Returns:
        ``(条目列表, 计算时间)``。
    """
    collector = PracticeEventCollector(db, student_id).load()
    stamp = utcnow()
    entries: List[MasteryEntry] = []

    kp_keys = set(collector.events_by_kp.keys()) | set(collector.error_evidence_by_kp.keys())
    for key in sorted(kp_keys):
        events = collector.events_by_kp.get(key, [])
        result = engine.compute_mastery(events, now=stamp)

        evidences = collector.error_evidence_by_kp.get(key, [])
        error_counts: Dict[str, int] = {}
        for category in engine.classify_batch([item.text for item in evidences]):
            error_counts[category] = error_counts.get(category, 0) + 1

        entries.append(
            MasteryEntry(
                kp_id=key,
                kp_name=collector.kp_names.get(key, "未命名知识点"),
                result=result,
                error_counts=error_counts,
            )
        )

        upsert_mastery(
            db,
            student_id=student_id,
            knowledge_point_id=to_uuid(key),
            result=result,
            error_summary=error_counts,
            computed_at=stamp,
        )

    db.commit()
    return entries, stamp


def _entries_from_cache(
    db: Session,
    rows: Sequence[StudentMastery],
) -> Tuple[List[MasteryEntry], Optional[datetime]]:
    """把缓存行还原成 ``MasteryEntry`` 列表。

    Args:
        db: 数据库会话（用于补知识点名称）。
        rows: 缓存行。

    Returns:
        ``(条目列表, 最早的 computed_at)``。
    """
    if not rows:
        return [], None

    kp_ids = [row.knowledge_point_id for row in rows]
    name_rows = db.execute(
        select(KnowledgePoint.id, KnowledgePoint.name).where(KnowledgePoint.id.in_(kp_ids))
    ).all()
    names = {_kp_key(item[0]): str(item[1] or "未命名知识点") for item in name_rows}

    entries: List[MasteryEntry] = []
    stamps: List[datetime] = []
    for row in rows:
        key = _kp_key(row.knowledge_point_id)
        summary = row.error_summary if isinstance(row.error_summary, dict) else {}
        entries.append(
            MasteryEntry(
                kp_id=key,
                kp_name=names.get(key, "未命名知识点"),
                result=MasteryResult(
                    score=float(row.mastery_score or 0.0),
                    level=int(row.mastery_level or 0),
                    attempts=int(row.attempts or 0),
                    correct_count=int(row.correct_count or 0),
                    error_rate=float(row.error_rate or 0.0),
                    effective_attempts=float(row.effective_attempts or 0.0),
                    last_practiced_at=as_utc(row.last_practiced_at),
                ),
                error_counts={str(k): int(v) for k, v in summary.items()},
            )
        )
        stamp = as_utc(row.computed_at)
        if stamp is not None:
            stamps.append(stamp)

    entries.sort(key=lambda item: item.kp_name)
    return entries, (min(stamps) if stamps else None)


def load_entries(
    db: Session,
    student_id: uuid.UUID,
    *,
    force: bool = False,
) -> Tuple[List[MasteryEntry], datetime, str]:
    """取掌握度条目：命中缓存直接返回，否则重算并物化。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        force: True 时跳过缓存强制重算（对应 ``?force=1``）。

    Returns:
        ``(条目列表, 计算时间, mode)``，``mode`` 为 ``fresh`` / ``cache``。
    """
    if not force:
        rows = list(
            db.execute(
                select(StudentMastery).where(StudentMastery.student_id == student_id)
            ).scalars()
        )
        if _is_cache_valid(rows):
            entries, stamp = _entries_from_cache(db, rows)
            return entries, stamp or utcnow(), MODE_CACHE

    entries, stamp = _compute_and_store(db, student_id)
    return entries, stamp, MODE_FRESH


# ==================================================================== 对外视图
def get_mastery(
    db: Session,
    *,
    student_id: uuid.UUID,
    force: bool = False,
    kp_ids: Optional[Sequence[str]] = None,
) -> MasteryView:
    """``GET /api/profile/mastery`` 的服务实现。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        force: 是否强制重算。
        kp_ids: 只返回这些知识点（字符串 ID 列表），``None`` 表示全部。

    Returns:
        ``MasteryView``；无任何数据时 ``items`` 为空数组（不报错）。
    """
    entries, stamp, mode = load_entries(db, student_id, force=force)

    wanted: Optional[Set[str]] = None
    if kp_ids:
        wanted = {str(item).strip() for item in kp_ids if str(item).strip()}
    if wanted:
        entries = [entry for entry in entries if entry.kp_id in wanted]

    level_count: Dict[str, int] = {"0": 0, "1": 0, "2": 0, "3": 0}
    items: List[Dict[str, Any]] = []
    for entry in entries:
        result = entry.result
        level_key = str(int(result.level))
        level_count[level_key] = level_count.get(level_key, 0) + 1
        last_practiced = as_utc(result.last_practiced_at)
        items.append(
            {
                "kp_id": entry.kp_id,
                "kp_name": entry.kp_name,
                "score": round(float(result.score), 4),
                "level": int(result.level),
                "attempts": int(result.attempts),
                "correct_count": int(result.correct_count),
                "error_rate": round(float(result.error_rate), 4),
                "effective_attempts": round(float(result.effective_attempts), 4),
                "last_practiced_at": last_practiced.isoformat() if last_practiced else None,
            }
        )

    items.sort(key=lambda item: (item["level"], -item["error_rate"], item["kp_name"]))

    return MasteryView(
        student_id=str(student_id),
        items=items,
        summary={"total_kp": len(items), "level_count": level_count},
        computed_at=stamp,
        mode=mode,
    )


def get_error_patterns(
    db: Session,
    *,
    student_id: uuid.UUID,
    range_days: Optional[int] = ERROR_PATTERN_DEFAULT_DAYS,
) -> ErrorPatternView:
    """``GET /api/profile/error-patterns`` 的服务实现。

    实现说明：本视图**恒为实时计算**（``mode="fresh"``）。
    原因是 ``range_days`` 是自由入参，按窗口做物化缓存的收益远低于复杂度；
    而错因聚合是纯 Python 字符串匹配，单学生百量级错题耗时可忽略。
    （每个知识点的错因分布仍会在算掌握度时写入 ``student_mastery.error_summary``，
      供推荐接口复用，OQ4 的要求已满足。）

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        range_days: 统计窗口天数。

    Returns:
        ``ErrorPatternView``；恒返回 6 类（count 可为 0）。
    """
    days = _clamp_range_days(range_days)
    collector = PracticeEventCollector(db, student_id).load_error_evidence(range_days=days)

    patterns = engine.aggregate_error_patterns(collector.all_error_evidence)

    return ErrorPatternView(
        student_id=str(student_id),
        patterns=patterns,
        total_mistakes=len(collector.all_error_evidence),
        unmapped_mistakes=collector.unmapped_mistakes,
        range_days=days,
        computed_at=utcnow(),
        mode=MODE_FRESH,
    )


def _suggested_questions(
    db: Session,
    kp_ids: Sequence[str],
    limit: int = RECOMMEND_QUESTION_LIMIT,
) -> Dict[str, List[str]]:
    """给每个知识点挑几道可用（``status='active'``）的题。

    Args:
        db: 数据库会话。
        kp_ids: 知识点字符串 ID 列表。
        limit: 每个知识点最多几道。

    Returns:
        ``{kp_id: [question_id, ...]}``。
    """
    if not kp_ids:
        return {}

    targets: List[uuid.UUID] = []
    for item in kp_ids:
        try:
            targets.append(to_uuid(item))
        except (TypeError, ValueError):  # pragma: no cover - 键恒由 UUID 生成
            continue
    if not targets:
        return {}

    rows = db.execute(
        select(Question.id, Question.kp_id)
        .where(Question.kp_id.in_(targets), Question.status == "active")
        .order_by(Question.created_at.desc())
    ).all()

    grouped: Dict[str, List[str]] = {}
    for question_id, kp_id in rows:
        key = _kp_key(kp_id)
        bucket = grouped.setdefault(key, [])
        if len(bucket) < limit:
            bucket.append(str(question_id))
    return grouped


def get_recommendations(
    db: Session,
    *,
    student_id: uuid.UUID,
    limit: int = RECOMMEND_TOP_N,
    force: bool = False,
) -> RecommendationView:
    """``GET /api/profile/recommendations`` 的服务实现。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        limit: 返回条数上限。
        force: 是否强制重算掌握度。

    Returns:
        ``RecommendationView``；无数据时 ``items`` 为空数组。
    """
    entries, stamp, mode = load_entries(db, student_id, force=force)
    if not entries:
        return RecommendationView(student_id=str(student_id), items=[], computed_at=stamp, mode=mode)

    patterns = get_error_patterns(db, student_id=student_id).patterns
    questions = _suggested_questions(db, [entry.kp_id for entry in entries])
    items = engine.rank_recommendations(entries, patterns, limit, questions)

    return RecommendationView(
        student_id=str(student_id),
        items=items,
        computed_at=stamp,
        mode=mode,
    )


def invalidate_cache_for_question(
    db: Session,
    *,
    student_id: uuid.UUID,
    question_id: uuid.UUID,
    practiced_at: Optional[datetime] = None,
) -> int:
    """按题目反查知识点并让对应的掌握度缓存失效（供 B11 复用）。

    Args:
        db: 数据库会话（不 commit）。
        student_id: 学生 ID。
        question_id: 题目 ID。
        practiced_at: 练习时间。

    Returns:
        被标记失效的行数。
    """
    kp_id = db.execute(select(Question.kp_id).where(Question.id == question_id)).scalar_one_or_none()
    if kp_id is None:
        return 0
    return touch_mastery_practiced(
        db,
        student_id=student_id,
        knowledge_point_ids=[kp_id],
        practiced_at=practiced_at,
    )


__all__ = [
    "MasteryView",
    "ErrorPatternView",
    "RecommendationView",
    "PracticeEventCollector",
    "upsert_mastery",
    "touch_mastery_practiced",
    "invalidate_cache_for_question",
    "load_entries",
    "get_mastery",
    "get_error_patterns",
    "get_recommendations",
    "to_uuid",
]

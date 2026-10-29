"""学生 DNA 服务（V6.5 Sprint 2：DNA-002/003/004/005）。

职责：
    * DNA-002 ``upsert_student_profile``：画像字段写入（学习目标八类枚举
      硬校验「非法枚举拒绝」，其余字段按建议枚举/JSON 落地，写入后读回一致）；
    * DNA-003 ``update_mastery_from_event``：掌握度增量更新（消费已埋点
      事件 ANSWER/CORRECT/ERROR，upsert 到 ``student_knowledge_mastery``，
      维护 mastery/confidence/计数/时间戳；规划 M-002/M-003 规则）；
    * C02 Phase 2 ``recompute_student_six_dim_mastery``：每日批量重算六维分维
      （concept/procedure/independent/transfer/retention）并 upsert 进
      ``student_knowledge_mastery`` 的五个预留列，口径走 ``engines.mastery_sixdim``；
    * C02 Phase 3 ``consolidate_mastery``：L3 综合洞察层落库（错因聚类 / 薄弱链路 /
      学习趋势三类），口径走纯函数 ``engines.mastery_synthesis``；表
      ``student_mastery_synthesis`` **无 ORM 模型**（raw SQL 维护，见
      ``migrations/025_student_mastery_synthesis_sqlite.sql``），带
      ``ref_kp_ids`` / ``ref_event_ids`` 溯源边，**溯源边缺失即拒写**；
    * DNA-004 ``record_learning_event``：行为事件落库（八类枚举校验，
      practice/chat 回调埋点统一走这里，落库后同事务联动 DNA-003）；
    * DNA-005 ``get_learning_dna``：画像 + 知识点掌握汇总 + 最近事件摘要
      聚合（无数据学生返回默认值，不报错）。

事务：只 ``flush``，``commit`` 由 ``app.db.get_db`` 收口
（与 student_service.py 同一约定；联动更新不额外 commit）。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.core.response import ApiError, CODE_BAD_REQUEST
from app.core import constants as C
from app.engines.config import read_mastery_dim_config
from app.engines.mastery_sixdim import (
    RetentionEvent,
    SixDimEvent,
    six_dim_mastery_for_student,
)
from app.engines.mastery_synthesis import (
    SYNTHESIS_TYPES,
    SynthesisEvent,
    synthesize_student,
)
from app.models.content import KnowledgePoint
from app.models.dna import (
    HINT_DEPENDENCY_VALUES,
    LEARNING_EVENT_TYPE_VALUES,
    LEARNING_GOAL_VALUES,
    StudentKnowledgeMastery,
    StudentLearningEvent,
    StudentLearningProfile,
)
from app.models.knowledge_prerequisite import KnowledgePrerequisite
from app.services.mastery_engine import (
    MasteryResult,
    PrerequisiteEdge,
    grade_level,
)


#: 六维事件 meta 键（与埋点 `_hint_context` / `mastery_delta_for_event` 同源）
META_KEY_TRANSFER_FLAG: str = "transfer"


def _parse_iso_ts(value: object) -> Optional[datetime]:
    """把可能带时区的 ISO 字符串 / datetime 统一成 UTC aware datetime。

    复习日志 ``review_at`` 在 SQLite 里存为 ISO 字符串（``datetime.isoformat``），
    可能带 ``+00:00`` 或末尾 ``Z``；``datetime.fromisoformat`` 在 3.11+ 已支持
    两种写法。无法解析时返回 None（该条召回事件降级丢弃）。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

#: 最近事件摘要条数上限
RECENT_EVENTS_LIMIT: int = 10


# ================================================================== 内部工具
def _check_enum(value: str, allowed: tuple, field: str) -> None:
    """通用枚举硬校验：不在白名单内一律 400。"""
    if value not in allowed:
        allowed_text = "/".join(allowed)
        raise ApiError(CODE_BAD_REQUEST, f"{field} 非法，可选值：{allowed_text}")


def _as_list(value: Optional[Any], field: str) -> Optional[List[Any]]:
    """JSON 字段只接受 list / dict / None，其余拒绝（防止脏数据混入）。"""
    if value is None:
        return None
    if not isinstance(value, (list, dict)):
        raise ApiError(CODE_BAD_REQUEST, f"{field} 必须是数组或对象")
    return value


# ================================================================== DNA-002 画像写入
def upsert_student_profile(
    db: Session,
    *,
    student_id: uuid.UUID,
    learning_goal: Optional[str] = None,
    learning_stage: Optional[str] = None,
    current_level: Optional[str] = None,
    learning_habits: Optional[Any] = None,
    common_errors: Optional[Any] = None,
    hint_dependency: Optional[str] = None,
    learning_trend: Optional[Any] = None,
) -> Dict[str, Any]:
    """写入（upsert）学生画像字段。

    校验规则（按 V6.5 规划 DNA-002 验收）：
        * ``learning_goal`` 学习目标**必填**且必须是八类枚举，
          非法值抛 ``ApiError(400)``（DB CHECK 二次兜底）；
        * ``hint_dependency`` 提示依赖若提供必须为 high/medium/low
          （自定义建议枚举，本项目内硬校验保持一致）；
        * ``learning_stage`` / ``current_level`` 建议枚举，非空字符串即可；
        * ``learning_habits`` / ``common_errors`` / ``learning_trend``
          必须是 list 或 dict（JSON 承载）。

    Returns:
        写入后从 DB 读回的画像 dict（与入参一致，证明落库可读回）。
    """
    if learning_goal is None:
        raise ApiError(CODE_BAD_REQUEST, "学习目标（learning_goal）必填")
    _check_enum(learning_goal, LEARNING_GOAL_VALUES, "学习目标")

    if hint_dependency is not None:
        _check_enum(hint_dependency, HINT_DEPENDENCY_VALUES, "提示依赖")

    habits = _as_list(learning_habits, "学习习惯")
    errors = _as_list(common_errors, "常见错误")
    trend = _as_list(learning_trend, "学习趋势")

    profile = db.execute(
        select(StudentLearningProfile).where(
            StudentLearningProfile.student_id == student_id
        )
    ).scalar_one_or_none()

    if profile is None:
        profile = StudentLearningProfile(
            student_id=student_id,
            learning_goal=learning_goal,
        )
        db.add(profile)

    profile.learning_goal = learning_goal
    if learning_stage is not None:
        profile.learning_stage = learning_stage.strip() or None
    if current_level is not None:
        profile.current_level = current_level.strip() or None
    if habits is not None:
        profile.learning_habits = habits
    if errors is not None:
        profile.common_errors = errors
    if hint_dependency is not None:
        profile.hint_dependency = hint_dependency
    if trend is not None:
        profile.learning_trend = trend

    db.flush()
    return get_student_profile(db, student_id=student_id)


# ================================================================== DNA-002 读回
def get_student_profile(db: Session, *, student_id: uuid.UUID) -> Dict[str, Any]:
    """读回学生画像；无数据返回默认空画像（不报错）。"""
    profile = db.execute(
        select(StudentLearningProfile).where(
            StudentLearningProfile.student_id == student_id
        )
    ).scalar_one_or_none()
    if profile is None:
        return {
            "student_id": str(student_id),
            "learning_goal": None,
            "learning_stage": None,
            "current_level": None,
            "learning_habits": None,
            "common_errors": None,
            "hint_dependency": None,
            "learning_trend": None,
            "updated_at": None,
        }
    return {
        "student_id": str(profile.student_id),
        "learning_goal": profile.learning_goal,
        "learning_stage": profile.learning_stage,
        "current_level": profile.current_level,
        "learning_habits": profile.learning_habits,
        "common_errors": profile.common_errors,
        "hint_dependency": profile.hint_dependency,
        "learning_trend": profile.learning_trend,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


# ================================================================== DNA-004 事件记录
def record_learning_event(
    db: Session,
    *,
    student_id: uuid.UUID,
    event_type: str,
    question_id: Optional[uuid.UUID] = None,
    knowledge_point_id: Optional[uuid.UUID] = None,
    meta: Optional[Dict[str, Any]] = None,
    mastery_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """记录一条学习行为事件。

    事件类型八类枚举硬校验（QUESTIONVIEW/ANSWER/HINT/ERROR/CORRECT/
    REVIEW/SESSIONSTART/SESSIONEND），非法值抛 ``ApiError(400)``。

    Args:
        db: 数据库会话（只 ``flush``，``commit`` 由调用方收口）。
        student_id: 学生 ID。
        event_type: 八类事件之一。
        question_id: 关联题目（可为空）。
        knowledge_point_id: 关联知识点（可为空）。
        meta: 事件上下文，原样落库到 ``student_learning_events.meta``。
        mastery_meta: 传给 ``update_mastery_from_event`` 的 meta **视图**；
            缺省（``None``）时与 ``meta`` 相同。存在的原因：判分事件 meta 里的
            ``independent`` 是**作答级**独立标志（无提示且未看答案且非引导，
            服务于「独立完成率」指标），而 ``mastery_delta_for_event`` 把顶层
            ``independent`` 读成**迁移题标记**（+0.12 档，M-002「独立迁移题」）。
            两种语义不同，调用方（practice_service.submit_answer）用本参数
            剔除作答级标志，保住「基础题独立正确 +0.10」口径，同时事件 meta
            仍带全量字段供指标侧消费。

    Returns:
        落库事件的 dict（含 created_at，便于埋点冒烟断言）。
    """
    _check_enum(event_type, LEARNING_EVENT_TYPE_VALUES, "事件类型")

    if meta is not None and not isinstance(meta, dict):
        raise ApiError(CODE_BAD_REQUEST, "事件上下文必须为对象")

    event = StudentLearningEvent(
        student_id=student_id,
        event_type=event_type,
        question_id=question_id,
        knowledge_point_id=knowledge_point_id,
        meta=meta,
    )
    db.add(event)
    db.flush()

    # ---- DNA-003 联动：同事务内增量更新掌握表（ANSWER/CORRECT/ERROR 驱动）----
    # 只 flush、不 commit：随事件所在事务由调用方一并提交；失败不吞——
    # 让外层埋点旁路（_emit_dna_event / _emit_chat_dna_event）统一兜底。
    update_mastery_from_event(
        db,
        student_id=student_id,
        event_type=event_type,
        knowledge_point_id=knowledge_point_id,
        meta=meta if mastery_meta is None else mastery_meta,
    )

    return {
        "id": str(event.id),
        "student_id": str(event.student_id),
        "event_type": event.event_type,
        "question_id": str(event.question_id) if event.question_id else None,
        "knowledge_point_id": str(event.knowledge_point_id)
        if event.knowledge_point_id
        else None,
        "meta": event.meta,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


# ================================================================== DNA-003 掌握度更新
# 增量规则源：V6.5 规划 M-002 初始规则（0~100 制 → 表口径 0~1 制 ÷100）。
# 数值全部进 ``app.core.constants``（MASTERY_DELTA_*），此处不写死。
#: 正确事件增量档位的 meta 键（埋点可传；缺省走基础档）
META_KEY_HINT_COUNT: str = "hint_count"
META_KEY_HINT_USED: str = "hint_used"
META_KEY_TRANSFER: str = "transfer"
META_KEY_INDEPENDENT: str = "independent"


def _clamp_mastery(value: float) -> float:
    """掌握度夹紧到 [0, 1]（M-003：0≤mastery≤100 → 0≤mastery≤1）。"""
    return min(C.MASTERY_CLAMP_HIGH, max(C.MASTERY_CLAMP_LOW, float(value)))


def _bounded_delta(value: float) -> float:
    """单次跳变上限（M-003：|delta| ≤ 15 → ≤ 0.15，防一次事件大幅跳变）。"""
    return min(C.MASTERY_DELTA_MAX_ABS, max(-C.MASTERY_DELTA_MAX_ABS, float(value)))


def mastery_delta_for_event(
    event_type: str, meta: Optional[Dict[str, Any]] = None
) -> float:
    """由事件类型 + 上下文算出掌握度增量（0~1 制）。

    规则（规划 M-002）：
        * ``CORRECT`` → 基础独立正确 +0.10；若 meta 携带迁移/独立标记
          （``transfer`` / ``independent``）按独立迁移题 +0.12；
          若 ``hint_count >= 3`` 按大量提示后正确 +0.02；
          若 ``hint_used`` 或 ``hint_count >= 1`` 按提示后正确 +0.05；
        * ``ERROR`` → 核心题错误 -0.08；
        * 其余事件 → 0（不驱动掌握表）。

    Returns:
        有符号增量；单次跳变已受 ±0.15 上限约束。
    """
    meta = meta or {}
    if event_type == "CORRECT":
        if meta.get(META_KEY_TRANSFER) or meta.get(META_KEY_INDEPENDENT):
            return _bounded_delta(C.MASTERY_DELTA_CORRECT_TRANSFER)
        try:
            hint_count = int(meta.get(META_KEY_HINT_COUNT) or 0)
        except (TypeError, ValueError):
            hint_count = 0
        if hint_count >= 3:
            return _bounded_delta(C.MASTERY_DELTA_CORRECT_HINTED_HEAVY)
        if meta.get(META_KEY_HINT_USED) or hint_count >= 1:
            return _bounded_delta(C.MASTERY_DELTA_CORRECT_HINTED)
        return _bounded_delta(C.MASTERY_DELTA_CORRECT_BASE)
    if event_type == "ERROR":
        return _bounded_delta(-C.MASTERY_DELTA_WRONG_CORE)
    return 0.0


def update_mastery_from_event(
    db: Session,
    *,
    student_id: uuid.UUID,
    event_type: str,
    knowledge_point_id: Optional[uuid.UUID] = None,
    meta: Optional[Dict[str, Any]] = None,
    occurred_at: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """DNA-003 掌握度增量更新：消费一条已埋点事件，upsert 掌握表一行。

    事件→字段映射（不驱动的事件直接跳过，不建行）：
        * ``ANSWER``  → ``practice_count +1``、``last_practice_at``；
        * ``CORRECT`` → ``correct_count +1``、``last_correct_at``、
          ``mastery += mastery_delta_for_event``；
        * ``ERROR``   → ``wrong_count +1``、``last_wrong_at``、
          ``mastery += mastery_delta_for_event``（负增量）；
        * 其余五类（QUESTIONVIEW/HINT/REVIEW/SESSIONSTART/SESSIONEND）
          只留在事件流里，不改变掌握表（防刷掌握）。

    upsert 幂等：按 ``(student_id, knowledge_point_id)`` 唯一键查行，
    已存在则更新、不存在则建默认行（mastery/confidence=0、计数=0）。

    confidence：随样本量增长趋近 1，``1 - 0.5 ** min(practice_count, 10)``
    （1 次作答 0.5，3 次 0.875，10 次及以上 ≈ 1）。

    事务：只 ``flush``，``commit`` 由调用方收口（与 DNA-002/004 同约定）。

    Returns:
        更新后掌握行 dict（无 kp 或不驱动事件返回 None）。
    """
    if knowledge_point_id is None or event_type not in ("ANSWER", "CORRECT", "ERROR"):
        return None

    now = occurred_at or datetime.now(timezone.utc)
    row = db.execute(
        select(StudentKnowledgeMastery).where(
            StudentKnowledgeMastery.student_id == student_id,
            StudentKnowledgeMastery.knowledge_point_id == knowledge_point_id,
        )
    ).scalar_one_or_none()

    if row is None:
        row = StudentKnowledgeMastery(
            student_id=student_id,
            knowledge_point_id=knowledge_point_id,
        )
        db.add(row)

    if event_type == "ANSWER":
        row.practice_count = int(row.practice_count or 0) + 1
        row.last_practice_at = now
    elif event_type == "CORRECT":
        row.correct_count = int(row.correct_count or 0) + 1
        row.last_correct_at = now
    elif event_type == "ERROR":
        row.wrong_count = int(row.wrong_count or 0) + 1
        row.last_wrong_at = now

    delta = mastery_delta_for_event(event_type, meta)
    row.mastery = _clamp_mastery(float(row.mastery or 0.0) + delta)

    practice_total = int(row.practice_count or 0)
    row.confidence = round(
        1.0 - 0.5 ** min(practice_total, 10),
        4,
    )

    db.flush()
    return {
        "student_id": str(row.student_id),
        "knowledge_point_id": str(row.knowledge_point_id),
        "mastery": round(float(row.mastery), 4),
        "confidence": round(float(row.confidence), 4),
        "practice_count": int(row.practice_count),
        "correct_count": int(row.correct_count),
        "wrong_count": int(row.wrong_count),
        "last_practice_at": row.last_practice_at.isoformat()
        if row.last_practice_at
        else None,
        "last_correct_at": row.last_correct_at.isoformat()
        if row.last_correct_at
        else None,
        "last_wrong_at": row.last_wrong_at.isoformat() if row.last_wrong_at else None,
    }


# ================================================================== C02 Phase2 六维批量重算
# 设计：增量 ``update_mastery_from_event`` 只维护 ``mastery``/计数/时间戳
# （会话内即时反馈）；六维分维（concept/procedure/independent/transfer/retention）
# 由**每日批量重算**从全量事件 + FSRS 复习日志一次性算出并落库，口径统一走
# ``app.engines.mastery_sixdim``（纯函数，阈值经 ``read_mastery_dim_config`` 注入）。
#
# 数据缺口说明（诚实标注，不编造）：
#   * ``error_type`` 只落在错题本 ``Mistake.error_type``，练习事件 meta 不带 →
#     D1/D2 当前对错误一律走「slip 半分」分支（概念/程序两维暂相等），待错因富化；
#   * ``transfer`` 全库无埋点设置（仅 AI 出题 role 概念）→ D4 当前恒为 None；
#   * 以上两项的激活属于「错因/迁移埋点增强」，列入 Phase 4 口径校准范围。
def _build_six_dim_events(
    db: Session, *, student_id: uuid.UUID
) -> List[SixDimEvent]:
    """读该生全量练习事件 → ``SixDimEvent`` 列表（仅 CORRECT/ERROR 带正确性）。

    时间用事件 ``created_at``（落库即发生）；``independent`` 取自事件 meta
    （埋点 ``_hint_context`` 已产出），``error_type`` / ``transfer`` 取 meta
    若有（当前多为 None，引擎按 slip / 缺失降级）。
    """
    rows = (
        db.execute(
            select(StudentLearningEvent).where(
                StudentLearningEvent.student_id == student_id,
                StudentLearningEvent.knowledge_point_id.isnot(None),
                StudentLearningEvent.event_type.in_(("CORRECT", "ERROR")),
            )
        )
        .scalars()
        .all()
    )
    events: List[SixDimEvent] = []
    for ev in rows:
        meta = ev.meta or {}
        kp_id = ev.knowledge_point_id
        if kp_id is None:
            continue
        events.append(
            SixDimEvent(
                kp_id=str(kp_id),
                is_correct=(ev.event_type == "CORRECT"),
                occurred_at=ev.created_at,
                weight=1.0,
                error_type=meta.get("error_type"),
                independent=bool(meta.get("independent", False)),
                transfer=bool(meta.get(META_KEY_TRANSFER_FLAG, False)),
            )
        )
    return events


def _build_retention_events(
    db: Session, *, student_id: uuid.UUID
) -> List[RetentionEvent]:
    """读该生 FSRS 复习召回 → ``RetentionEvent`` 列表（按 kp_id 聚合）。

    ``review_cards.kp_id`` 直接带知识点；缺失时回退 join ``questions.
    knowledge_point_id``。``rating == 1``（FSRS Again）记为未召回，``>= 2``
    记为召回。两张表无 ORM 模型（FSRS 模块 raw SQL 维护），若尚未建表
    （测试内存库 / schema_reconcile 未跑）则优雅返回空列表，不抛错。
    """
    # ⚠️ 用 ``_table_exists``（复用 session 自己的连接），不要用
    # ``inspect(db.bind).has_table()``——后者会向连接池再借一条连接，
    # StaticPool 单连接库归还时 rollback → 静默回滚调用方未提交的写入。
    if not _table_exists(db, "review_logs") or not _table_exists(db, "review_cards"):
        return []
    rows = db.execute(
        text(
            """
            SELECT COALESCE(rc.kp_id, q.kp_id) AS kp_id,
                   rl.rating, rl.review_at
            FROM review_logs rl
            JOIN review_cards rc ON rc.id = rl.card_id
            LEFT JOIN questions q ON q.id = rc.question_id
            WHERE rl.student_id = :sid
            """
        ),
        {"sid": str(student_id)},
    ).all()

    events: List[RetentionEvent] = []
    for kp_id, rating, review_at in rows:
        if kp_id is None:
            continue
        occurred = _parse_iso_ts(review_at)
        if occurred is None:
            continue
        try:
            rating_val = int(rating) if rating is not None else 0
        except (TypeError, ValueError):
            rating_val = 0
        events.append(
            RetentionEvent(
                kp_id=str(kp_id),
                recalled_correctly=rating_val >= 2,
                occurred_at=occurred,
                weight=1.0,
            )
        )
    return events


def recompute_student_six_dim_mastery(
    db: Session, *, student_id: uuid.UUID, now: Optional[datetime] = None
) -> int:
    """C02 Phase 2：对该生全量重算六维并 upsert 进 ``student_knowledge_mastery``。

    落库范围（严格按 Phase 2 范围，不碰 ``mastery``/计数/时间戳，避免回归
    增量口径）：
        * ``concept_mastery``    ← D1
        * ``procedure_mastery``  ← D2
        * ``independent_mastery``← D3
        * ``transfer_mastery``   ← D4（当前数据恒为 None）
        * ``retention_mastery``  ← D5（依赖 FSRS 复习日志）

    引擎算出的 D0（``mastery``）本函数**不写**，留待 Phase 4 口径校准时再决定
    是否由贝叶斯值接管增量 ``mastery``。

    事务：只 ``flush``，``commit`` 由调用方收口（与 DNA-002/003/004 同约定）。

    Returns:
        被更新的掌握行数。
    """
    base_now = now or datetime.now(timezone.utc)
    cfg = read_mastery_dim_config(db)
    events = _build_six_dim_events(db, student_id=student_id)
    retention = _build_retention_events(db, student_id=student_id)
    results = six_dim_mastery_for_student(
        events, retention=retention, now=base_now, config=cfg
    )

    updated = 0
    for kp_id, res in results.items():
        try:
            kp_uuid = uuid.UUID(kp_id)
        except (TypeError, ValueError):
            continue
        row = (
            db.execute(
                select(StudentKnowledgeMastery).where(
                    StudentKnowledgeMastery.student_id == student_id,
                    StudentKnowledgeMastery.knowledge_point_id == kp_uuid,
                )
            )
            .scalars()
            .one_or_none()
        )
        if row is None:
            # 正常流程下 CORRECT/ERROR 事件已由 update_mastery_from_event 建行；
            # 此处仅作安全网：即便缺行也补建并带入计数，保证行自洽可读。
            correct_n = sum(1 for e in events if e.kp_id == kp_id and e.is_correct)
            total_n = sum(1 for e in events if e.kp_id == kp_id)
            row = StudentKnowledgeMastery(
                student_id=student_id,
                knowledge_point_id=kp_uuid,
                practice_count=total_n,
                correct_count=correct_n,
                wrong_count=total_n - correct_n,
            )
            db.add(row)
        row.concept_mastery = res.concept
        row.procedure_mastery = res.procedure
        row.independent_mastery = res.independent
        row.transfer_mastery = res.transfer
        row.retention_mastery = res.retention
        updated += 1

    db.flush()
    return updated


# ================================================================== C02 Phase3 L3 综合层
# L3 = L1 事件 + L2 六维 → 跨知识点 / 跨时间的综合结论（错因聚类 / 薄弱链路 /
# 学习趋势）。设计文档：``docs/design-c02-three-layer-student-model.md`` §3/§5/§6.2。
#
# 与 Phase 2 的分工：
#   * Phase 2 写 L2 的 5 个分维列（有 ORM 模型，走 SQLAlchemy Core select）；
#   * Phase 3 写 L3 的独立表（**无 ORM 模型**，走 raw SQL upsert），因为本表
#     由本服务独占维护、列结构带 JSON 溯源数组，交给 ORM 会逼出一堆序列化字段。
#
# 落库表：``student_mastery_synthesis``（raw SQL，见
# ``migrations/025_student_mastery_synthesis_sqlite.sql``）——**没有 ORM 模型，
# 所以 ``create_all`` 永远建不出来**，必须靠 ``scripts/schema_reconcile.py``
# 对账补建 + 冷母库同步（铁律 3 / 11）。因此本函数对「表不存在」做优雅降级。
def _table_exists(db: Session, table_name: str) -> bool:
    """探测表是否存在（**必须复用 session 自己的连接**）。

    ⚠️ **不要用 ``inspect(db.bind).has_table()``**：那会从连接池**再借一条连接**。
    测试库是 ``StaticPool``（全局单连接），借出即归还，而连接池归还时的默认
    动作是 ``rollback`` → **把调用方当前事务里尚未提交的写入全部回滚掉**
    （静默丢数据，且只在同一事务有未提交写入时才暴露，极难定位）。
    实测踩坑：探测动作放在 ``get_learning_dna`` 里 → 同一事务里刚 flush 的
    掌握度行被回滚 → 前置缺口检测拿到空列表。

    ``db.connection()`` 返回的是 session **已在使用**的那条连接，不会触发
    连接池借还，探测查询也就在调用方自己的事务里跑，语义安全。
    """
    try:
        return bool(inspect(db.connection()).has_table(table_name))
    except Exception:  # noqa: BLE001 —— 探测失败按「不存在」处理，保守不写
        return False


def _synthesis_table_exists(db: Session) -> bool:
    """L3 表是否存在（无 ORM 模型 → ``create_all`` 建不出来，需探测）。"""
    return _table_exists(db, "student_mastery_synthesis")


def _build_synthesis_events(
    db: Session, *, student_id: uuid.UUID
) -> List[SynthesisEvent]:
    """读该生全量学习事件 → ``SynthesisEvent`` 列表（L1 溯源来源）。

    事件类型映射：
        * ``CORRECT`` → ``is_correct=True``；
        * ``ERROR``   → ``is_correct=False``；
        * 其余五类（ANSWER/HINT/REVIEW/QUESTIONVIEW/SESSION*）→ ``is_correct=None``
          （无判定意义，不进正确率口径，只计入活跃量）。
    """
    rows = (
        db.execute(
            select(StudentLearningEvent).where(
                StudentLearningEvent.student_id == student_id
            )
        )
        .scalars()
        .all()
    )
    events: List[SynthesisEvent] = []
    for ev in rows:
        meta = ev.meta or {}
        if ev.event_type == "CORRECT":
            is_correct: Optional[bool] = True
        elif ev.event_type == "ERROR":
            is_correct = False
        else:
            is_correct = None
        events.append(
            SynthesisEvent(
                event_id=str(ev.id),
                event_type=ev.event_type,
                kp_id=str(ev.knowledge_point_id) if ev.knowledge_point_id else None,
                occurred_at=ev.created_at,
                is_correct=is_correct,
                error_text=meta.get("error_text"),
                error_type=meta.get("error_type"),
                independent=bool(meta.get(META_KEY_INDEPENDENT, False)),
                transfer=bool(meta.get(META_KEY_TRANSFER, False)),
            )
        )
    return events


def _build_synthesis_mastery_map(
    db: Session, *, student_id: uuid.UUID
) -> Dict[str, MasteryResult]:
    """读该生 L2 掌握行 → ``{kp_id: MasteryResult}``（WEAK_CHAIN 的输入）。

    口径说明：``effective_attempts`` 用 ``practice_count`` 近似（不重算时间衰减，
    与 ``detect_prerequisite_gaps`` 的判定口径无关——它只看 ``score`` 与
    ``attempts``）；``level`` 由 ``grade_level`` 按同一口径补算，保持与既有看板
    档位一致。
    """
    rows = (
        db.execute(
            select(StudentKnowledgeMastery).where(
                StudentKnowledgeMastery.student_id == student_id
            )
        )
        .scalars()
        .all()
    )
    out: Dict[str, MasteryResult] = {}
    for row in rows:
        attempts = int(row.practice_count or 0)
        correct = int(row.correct_count or 0)
        score = float(row.mastery or 0.0)
        out[str(row.knowledge_point_id)] = MasteryResult(
            score=score,
            level=grade_level(score, float(attempts), attempts),
            attempts=attempts,
            correct_count=correct,
            error_rate=round(1.0 - correct / attempts, 4) if attempts > 0 else 0.0,
            effective_attempts=float(attempts),
            last_practiced_at=row.last_practice_at,
        )
    return out


def _build_prerequisite_edges(db: Session) -> List[PrerequisiteEdge]:
    """读知识图谱前置边 → ``List[PrerequisiteEdge]``（全图，缺口检测自带过滤）。

    本表当前只有「prerequisite」一种关系类型（见
    ``app/models/knowledge_prerequisite.py``），整表取用即可；若将来新增其它
    关系类型，需在此处按 ``relation_type`` 过滤，避免污染缺口判定。
    """
    rows = db.execute(select(KnowledgePrerequisite)).scalars().all()
    return [
        PrerequisiteEdge(
            pre_kp_id=str(row.pre_kp_id),
            kp_id=str(row.kp_id),
            weight=float(row.weight or 1.0),
        )
        for row in rows
    ]


def _upsert_synthesis(
    db: Session,
    *,
    student_id: uuid.UUID,
    result: Any,
    now: datetime,
) -> None:
    """L3 行 upsert（raw SQL；表无 ORM 模型）。

    幂等口径：``UNIQUE(student_id, synthesis_type)`` → 同一学生同一类型只保留
    一行，重复重算覆盖 ``ref_*`` / ``payload`` / ``confidence`` / ``updated_at``，
    ``created_at`` 保留首次写入时间。
    """
    stamp = now.isoformat() if isinstance(now, datetime) else str(now)
    db.execute(
        text(
            """
            INSERT INTO student_mastery_synthesis
                (id, student_id, synthesis_type, ref_kp_ids, ref_event_ids,
                 payload, confidence, created_at, updated_at)
            VALUES (:id, :sid, :stype, :ref_kp, :ref_evt, :payload, :conf, :ca, :ua)
            ON CONFLICT(student_id, synthesis_type) DO UPDATE SET
                ref_kp_ids   = excluded.ref_kp_ids,
                ref_event_ids= excluded.ref_event_ids,
                payload      = excluded.payload,
                confidence   = excluded.confidence,
                updated_at   = excluded.updated_at
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "sid": str(student_id),
            "stype": result.synthesis_type,
            "ref_kp": json.dumps(result.ref_kp_ids, ensure_ascii=False),
            "ref_evt": json.dumps(result.ref_event_ids, ensure_ascii=False),
            "payload": json.dumps(result.payload, ensure_ascii=False),
            "conf": float(result.confidence),
            "ca": stamp,
            "ua": stamp,
        },
    )


def consolidate_mastery(
    db: Session, *, student_id: uuid.UUID, now: Optional[datetime] = None
) -> int:
    """C02 Phase 3：对一名学生做 L3 综合并落库（每类一行，upsert）。

    三类综合（口径走纯函数 ``engines.mastery_synthesis``）：
        * ``ERROR_PATTERN`` 错因模式聚类（复用 ``aggregate_error_patterns``）；
        * ``WEAK_CHAIN``    薄弱链路（复用 ``detect_prerequisite_gaps``）；
        * ``TREND``         学习趋势（周窗正确率线性斜率）。

    **溯源边守卫（铁律 7）**：某类综合若 ``ref_kp_ids`` 与 ``ref_event_ids``
    **同时为空**，说明它既没有 L2 行也没有 L1 事件支撑——这类"空转结论"
    一律**不写库**（不编造无依据的 L3）。这是可变异的守卫，有专门测试反证。
    因此**表里每类最多一行、且可能少于 3 行**（例：没有前置缺口时
    ``WEAK_CHAIN`` 无证据 → 不写）。

    **陈旧行清理**：本次没有证据支撑的类型，其旧行会被删除——否则前置缺口
    被补上后，家长端仍会看到过期的「薄弱链路」结论。

    表 ``student_mastery_synthesis`` **无 ORM 模型**（``create_all`` 建不出来），
    表不存在时本函数优雅返回 0（不抛错），由 ``schema_reconcile.py`` 负责补建。

    事务：只 ``flush``，``commit`` 由调用方收口（与 DNA-002/003/004 同约定）。

    Returns:
        实际写入（upsert）的行数。
    """
    if not _synthesis_table_exists(db):
        return 0

    base_now = now or datetime.now(timezone.utc)
    events = _build_synthesis_events(db, student_id=student_id)
    mastery = _build_synthesis_mastery_map(db, student_id=student_id)
    edges = _build_prerequisite_edges(db)

    results = synthesize_student(events, mastery, edges, now=base_now)

    written = 0
    written_types: List[str] = []
    for result in results.values():
        if not result.ref_kp_ids and not result.ref_event_ids:
            # 溯源边缺失 → 拒写（该结论没有任何 L1/L2 证据支撑）
            continue
        _upsert_synthesis(
            db, student_id=student_id, result=result, now=base_now
        )
        written += 1
        written_types.append(result.synthesis_type)

    # 清理陈旧行：本次没有证据支撑的类型 → 旧行必须删掉。
    # （例：前置缺口被补上后 WEAK_CHAIN 结论已不成立，若只 upsert 不删，
    #   家长端会一直看到过期的「薄弱链路」结论。）
    stale = [t for t in SYNTHESIS_TYPES if t not in written_types]
    if stale:
        params: Dict[str, Any] = {"sid": str(student_id)}
        placeholders = []
        for idx, stype in enumerate(stale):
            key = f"st{idx}"
            params[key] = stype
            placeholders.append(f":{key}")
        db.execute(
            text(
                "DELETE FROM student_mastery_synthesis "
                f"WHERE student_id = :sid AND synthesis_type IN ({', '.join(placeholders)})"
            ),
            params,
        )

    db.flush()
    return written


def _read_synthesis(
    db: Session, *, student_id: uuid.UUID
) -> List[Dict[str, Any]]:
    """读该生 L3 三类综合（表不存在 → 空列表，不报错）。"""
    if not _synthesis_table_exists(db):
        return []

    rows = db.execute(
        text(
            """
            SELECT synthesis_type, ref_kp_ids, ref_event_ids, payload,
                   confidence, created_at, updated_at
            FROM student_mastery_synthesis
            WHERE student_id = :sid
            ORDER BY synthesis_type
            """
        ),
        {"sid": str(student_id)},
    ).all()

    out: List[Dict[str, Any]] = []
    for stype, ref_kp, ref_evt, payload, conf, created_at, updated_at in rows:
        out.append(
            {
                "synthesis_type": stype,
                "ref_kp_ids": _safe_json_list(ref_kp),
                "ref_event_ids": _safe_json_list(ref_evt),
                "payload": _safe_json_obj(payload),
                "confidence": float(conf) if conf is not None else 0.0,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    return out


def _safe_json_list(raw: object) -> List[Any]:
    """JSON 数组解析（脏数据降级为空列表，不抛错）。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _safe_json_obj(raw: object) -> Dict[str, Any]:
    """JSON 对象解析（脏数据降级为空字典，不抛错）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ================================================================== DNA-005 聚合
def _dna_level(row: StudentKnowledgeMastery) -> int:
    """DNA 掌握度 0~1 → 展示档位 0~3。

    复用 T04 看板同源 ``grade_level`` 的阈值与「样本不足降级」语义
    （0.40/0.60/0.85 + 有效样本 3/5 门槛），保证家长端/学生端看到的
    档位与既有看板口径一致、不产生两套规则打架；DNA 无时效衰减概念，
    以累计练习次数 ``practice_count`` 充当有效样本量输入。
    """
    attempts = int(row.practice_count)
    return grade_level(
        score=float(row.mastery),
        effective_attempts=float(attempts),
        attempts=attempts,
    )


def get_learning_dna(db: Session, *, student_id: uuid.UUID) -> Dict[str, Any]:
    """聚合学生 DNA：画像 + 知识点掌握汇总 + 最近事件摘要。

    * 无画像/无掌握/无事件均返回空默认值，**不报错**（与规划验收一致）；
    * 掌握汇总按 ``(kp_id, kp_name, mastery, level, confidence, error_rate,
      counts, last*)`` 列表返回——``kp_name`` 关联知识库，``level`` 复用
      T04 同源档位口径，便于家长端/学生端直接驱动展示；
    * 最近事件取 ``RECENT_EVENTS_LIMIT`` 条（学生 → 时间倒序）。
    """
    profile = get_student_profile(db, student_id=student_id)

    mastery_rows = (
        db.execute(
            select(StudentKnowledgeMastery).where(
                StudentKnowledgeMastery.student_id == student_id
            )
        )
        .scalars()
        .all()
    )
    # 一次查出全部关联知识点名称，避免 N+1（join 知识库，闭口表 name 必在）
    kp_ids = [row.knowledge_point_id for row in mastery_rows]
    kp_name_map: Dict[uuid.UUID, str] = {}
    if kp_ids:
        kp_rows = db.execute(
            select(KnowledgePoint.id, KnowledgePoint.name).where(
                KnowledgePoint.id.in_(kp_ids)
            )
        ).all()
        kp_name_map = {kp_id: name for kp_id, name in kp_rows}

    mastery_summary = [
        {
            "knowledge_point_id": str(row.knowledge_point_id),
            "kp_name": kp_name_map.get(row.knowledge_point_id, ""),
            "mastery": round(float(row.mastery), 4),
            "confidence": round(float(row.confidence), 4),
            # 展示档位与 T04 看板同源口径：DNA 连续值按同一 grade_level 映射，
            # 避免两套计算规则造成同一知识点档位打架（M4 一致）
            "level": _dna_level(row),
            # 错误率直接给出，前端无需再算（练习数为 0 时为 0）
            "error_rate": round(float(row.wrong_count) / float(row.practice_count), 4)
            if row.practice_count > 0
            else 0.0,
            "practice_count": int(row.practice_count),
            "correct_count": int(row.correct_count),
            "wrong_count": int(row.wrong_count),
            "last_practice_at": row.last_practice_at.isoformat()
            if row.last_practice_at
            else None,
            "last_correct_at": row.last_correct_at.isoformat()
            if row.last_correct_at
            else None,
            "last_wrong_at": row.last_wrong_at.isoformat()
            if row.last_wrong_at
            else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in mastery_rows
    ]

    event_rows = (
        db.execute(
            select(StudentLearningEvent)
            .where(StudentLearningEvent.student_id == student_id)
            .order_by(StudentLearningEvent.created_at.desc())
            .limit(RECENT_EVENTS_LIMIT)
        )
        .scalars()
        .all()
    )
    recent_events = [
        {
            "id": str(ev.id),
            "event_type": ev.event_type,
            "question_id": str(ev.question_id) if ev.question_id else None,
            "knowledge_point_id": str(ev.knowledge_point_id)
            if ev.knowledge_point_id
            else None,
            "meta": ev.meta,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
        }
        for ev in event_rows
    ]

    return {
        "student_id": str(student_id),
        "profile": profile,
        "mastery_summary": mastery_summary,
        "recent_events": recent_events,
        # C02 Phase 3：L3 综合洞察层（错因聚类 / 薄弱链路 / 学习趋势）。
        # 表无 ORM 模型 → 由 schema_reconcile 补建；表不存在时返回空列表。
        "synthesis": _read_synthesis(db, student_id=student_id),
    }


__all__ = [
    "RECENT_EVENTS_LIMIT",
    "get_student_profile",
    "upsert_student_profile",
    "record_learning_event",
    "mastery_delta_for_event",
    "update_mastery_from_event",
    "recompute_student_six_dim_mastery",
    "consolidate_mastery",
    "get_learning_dna",
]

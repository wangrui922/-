"""学习域落库服务：错题本与作答流水（T03 · 3.4）。

职责：
    * ``record_mistake`` / ``record_attempt`` 一律**幂等**，重复提交不产生重复行；
    * ``list_mistakes`` 提供错题本三态筛选（未掌握 / 练习中 / 已掌握）；
    * 全部为同步函数：mistake 类 sync 路由可直接调用，
      chat/exercise 类 async 路由须用 ``run_in_threadpool`` 包裹。

跨方言硬约定：
    三态判定用标准 SQL ``EXISTS`` 相关子查询，不出现任何 PostgreSQL 专属语法
    （``->>`` / ``@>`` / ``ARRAY[]`` / ``gen_random_uuid()``），SQLite 与 PG 行为一致。

事务边界：
    与 ``app.ai.orchestrator`` 保持一致——本模块内部 ``commit``，
    调用方拿到的都是已落库、可直接序列化的实例。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, List, Optional

from sqlalchemy import Exists, Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_NOT_FOUND
from app.models.base import utcnow
from app.models.learning import Attempt, Mistake

logger = logging.getLogger("app.services.learning")

# ------------------------------------------------------------------ 三态常量
#: 未掌握：未攻克，且该题从没答对过
STATE_UNSOLVED: str = "unsolved"
#: 练习中：未攻克，但该题已经答对过至少一次
STATE_PRACTICING: str = "practicing"
#: 已掌握：已标记攻克
STATE_MASTERED: str = "mastered"

#: 合法的 ``state`` 取值
MISTAKE_STATES: tuple[str, ...] = (STATE_UNSOLVED, STATE_PRACTICING, STATE_MASTERED)


# ------------------------------------------------------------------ 通用工具
def to_uuid(raw: Any, field: str = "id") -> uuid.UUID:
    """把字符串 / ``uuid.UUID`` 统一成 ``uuid.UUID``，非法值按 400 处理。

    Args:
        raw: 待转换的值。
        field: 字段名，仅用于拼错误提示。

    Returns:
        转换后的 ``uuid.UUID``。

    Raises:
        ApiError: code=400，值不是合法 UUID。
    """
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_BAD_REQUEST, f"{field} 非法") from exc


def require_student_id(sid: Optional[str]) -> uuid.UUID:
    """从登录态的 ``CurrentUser.sid`` 取学生 ID。

    Args:
        sid: JWT payload 里的关联学生 ID（字符串或 None）。

    Returns:
        学生 ID。

    Raises:
        ApiError: code=400，当前账号未绑定学生。
    """
    if not sid:
        raise ApiError(CODE_BAD_REQUEST, "未绑定学生")
    return to_uuid(sid, "student_id")


def _has_correct_attempt() -> Exists:
    """构造「该错题对应的题目已被答对过」的相关子查询。

    用标准 SQL ``EXISTS``，由 SQLAlchemy 自动与外层 ``mistakes`` 关联，
    两种方言下生成的 SQL 都合法。
    """
    return (
        select(Attempt.id)
        .where(
            Attempt.student_id == Mistake.student_id,
            Attempt.question_id == Mistake.question_id,
            Attempt.is_correct.is_(True),
        )
        .exists()
    )


# ------------------------------------------------------------------ 错题写入
def record_mistake(
    db: Session,
    *,
    student_id: Any,
    question_id: Any,
    error_type: Optional[str] = None,
    error_reason: Optional[str] = None,
) -> Mistake:
    """记录一道错题（幂等）。

    幂等口径：同一 ``(student_id, question_id)`` 只要还存在**未攻克**的记录，
    就直接返回该记录、不再新建；已攻克的历史记录不阻挡再次错题建档
    （同一题重新做错时可以再进一次错题本）。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        question_id: 题目 ID。
        error_type: 错误类型（如「审题不清」），可为空。
        error_reason: 错误原因描述，可为空。

    Returns:
        新建或已存在的 ``Mistake`` 实例。

    Raises:
        ApiError: code=400，ID 非法或关联的学生 / 题目不存在。
    """
    sid = to_uuid(student_id, "student_id")
    qid = to_uuid(question_id, "question_id")

    existing = (
        db.execute(
            select(Mistake)
            .where(
                Mistake.student_id == sid,
                Mistake.question_id == qid,
                Mistake.solved.is_(False),
            )
            .order_by(Mistake.created_at.desc())
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing

    mistake = Mistake(
        student_id=sid,
        question_id=qid,
        error_type=error_type,
        error_reason=error_reason,
        solved=False,
    )
    db.add(mistake)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.info("record_mistake 外键校验失败 student=%s question=%s", sid, qid)
        raise ApiError(CODE_BAD_REQUEST, "关联的学生或题目不存在") from exc

    db.refresh(mistake)
    return mistake


def list_mistakes(
    db: Session,
    *,
    student_id: Any,
    state: Optional[str] = None,
) -> List[Mistake]:
    """按三态筛选错题本，``created_at`` 倒序返回。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        state: ``unsolved`` / ``practicing`` / ``mastered``；``None`` 表示全部。

    Returns:
        ``Mistake`` 列表（可能为空）。

    Raises:
        ApiError: code=400，``state`` 取值非法或 ID 非法。
    """
    sid = to_uuid(student_id, "student_id")
    stmt: Select = select(Mistake).where(Mistake.student_id == sid)

    if state == STATE_MASTERED:
        stmt = stmt.where(Mistake.solved.is_(True))
    elif state == STATE_UNSOLVED:
        stmt = stmt.where(Mistake.solved.is_(False), ~_has_correct_attempt())
    elif state == STATE_PRACTICING:
        stmt = stmt.where(Mistake.solved.is_(False), _has_correct_attempt())
    elif state:
        raise ApiError(CODE_BAD_REQUEST, f"state 仅支持 {'/'.join(MISTAKE_STATES)}")

    return list(db.execute(stmt.order_by(Mistake.created_at.desc())).scalars())


def mark_mistake_solved(
    db: Session,
    *,
    mistake_id: Any,
    student_id: Any = None,
    note: Optional[str] = None,
) -> Mistake:
    """把一道错题标记为已攻克（T04 · B10/B11 扩展）。

    幂等口径：重复调用**不会**刷新 ``solved_at``；但 ``note`` 只要传了非空值
    就允许覆盖（学生可以补写/修改心得，这不算状态变更）。

    归属口径（T04 新增）：传了 ``student_id`` 时会校验错题归属，
    **不属于自己的错题一律按 404 处理**（而不是 403），避免通过状态码
    探测「某个 UUID 是否存在」。

    缓存联动（T04 · B11）：状态从「未攻克」翻成「已攻克」时，
    把该题所属知识点的 ``student_mastery.last_practiced_at`` 推到当前时间，
    使掌握度物化缓存立即失效，下次看板刷新会重算。

    Args:
        db: 数据库会话（本函数内部 commit）。
        mistake_id: 错题 ID。
        student_id: 归属校验用的学生 ID；``None`` 表示不校验（兼容 T03 老调用）。
        note: 攻克备注（学生自述），可为空。

    Returns:
        更新后的 ``Mistake`` 实例。

    Raises:
        ApiError: code=400 ID 非法；code=404 错题不存在或不属于当前学生。
    """
    # 延迟导入：profile_service 不反向依赖本模块，这里放函数内纯粹是为了
    # 让「学习域基础服务」不在 import 期就拉起整条画像链路。
    from app.services import profile_service

    mid = to_uuid(mistake_id, "mistake_id")
    mistake = db.execute(select(Mistake).where(Mistake.id == mid)).scalar_one_or_none()
    if mistake is None:
        raise ApiError(CODE_NOT_FOUND, "错题不存在")

    if student_id is not None:
        sid = to_uuid(student_id, "student_id")
        if mistake.student_id != sid:
            # 故意不返 403：不泄露「这条错题存在但不是你的」
            raise ApiError(CODE_NOT_FOUND, "错题不存在")

    clean_note = (note or "").strip() or None
    changed = False

    if clean_note is not None and clean_note != mistake.note:
        mistake.note = clean_note
        changed = True

    if not mistake.solved:
        stamp = utcnow()
        mistake.solved = True
        mistake.solved_at = stamp
        changed = True
        # ---- B11：让该知识点的掌握度缓存失效（内部只 flush，不 commit）----
        try:
            profile_service.invalidate_cache_for_question(
                db,
                student_id=mistake.student_id,
                question_id=mistake.question_id,
                practiced_at=stamp,
            )
        except Exception:  # noqa: BLE001 - 缓存失效失败不能拖垮「标记攻克」主流程
            logger.warning(
                "标记攻克后刷新画像缓存失败 mistake=%s student=%s",
                mid,
                mistake.student_id,
                exc_info=True,
            )

    if changed:
        db.commit()
        db.refresh(mistake)

    return mistake


# ------------------------------------------------------------------ 作答写入
def record_attempt(
    db: Session,
    *,
    student_id: Any,
    question_id: Any = None,
    session_id: Any = None,
    selected_answer: Optional[str] = None,
    is_correct: bool,
    client_event_id: Optional[str] = None,
) -> Attempt:
    """记录一条作答流水（按 ``client_event_id`` 幂等）。

    幂等口径：``client_event_id`` 非空且库中已存在时直接返回已有记录；
    并发下撞唯一键也会回查一次，保证「重复 client_event_id 只写一条」。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        question_id: 题目 ID，可为空（自由对话作答）。
        session_id: AI 会话 ID，可为空。
        selected_answer: 作答内容，可为空。
        is_correct: 是否正确（必填）。
        client_event_id: 客户端幂等键，可为空。

    Returns:
        新建或已存在的 ``Attempt`` 实例。

    Raises:
        ApiError: code=400，ID 非法或关联实体不存在。
    """
    sid = to_uuid(student_id, "student_id")
    event_key = (client_event_id or "").strip() or None

    if event_key:
        existing = (
            db.execute(select(Attempt).where(Attempt.client_event_id == event_key))
            .scalars()
            .first()
        )
        if existing is not None:
            return existing

    attempt = Attempt(
        student_id=sid,
        question_id=to_uuid(question_id, "question_id") if question_id else None,
        session_id=to_uuid(session_id, "session_id") if session_id else None,
        selected_answer=selected_answer,
        is_correct=bool(is_correct),
        client_event_id=event_key,
    )
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if event_key:
            # 并发写入撞唯一键：回查一次，仍然只留一条
            existing = (
                db.execute(select(Attempt).where(Attempt.client_event_id == event_key))
                .scalars()
                .first()
            )
            if existing is not None:
                return existing
        logger.info("record_attempt 外键校验失败 student=%s question=%s", sid, question_id)
        raise ApiError(CODE_BAD_REQUEST, "关联的学生、题目或会话不存在") from exc

    db.refresh(attempt)
    return attempt


__all__ = [
    "record_mistake",
    "list_mistakes",
    "mark_mistake_solved",
    "record_attempt",
    "require_student_id",
    "to_uuid",
    "STATE_UNSOLVED",
    "STATE_PRACTICING",
    "STATE_MASTERED",
    "MISTAKE_STATES",
]

"""AI 辅导编排层：把四个 Agent 串成"开场 → 多轮引导 → 出练习题"。

职责边界（必须守住）：
    * 本模块**只**落 ``ai_sessions`` / ``ai_messages`` 两张表；
      错题（``questions`` / ``mistakes``）与学生画像由上层 service 负责，这里不碰；
    * 所有写操作一律用**调用方传入**的 ``db``（API 层创建、请求结束统一提交/回滚），
      模块内不自建会话做写入；
    * 全部为同步函数，由 API 层用 ``run_in_threadpool`` 包裹，避免阻塞事件循环。
"""

from __future__ import annotations

import json
import logging
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import intent  # T2 意图识别：mode 的唯一定稿处
from app.ai import response_analyzer  # TASK-002 学生回答结构化（与 mode 并存不互替）
from app.ai.agents import diagnose, exercise, question, tutor  # noqa: F401  (diagnose 供上层复用)
from app.ai.agents._json_utils import extract_json as _extract_json
from app.ai.ark_client import ArkError
from app.ai.guards import check_ai_quota
from app.ai.intent import MODE_DEMO, MODE_GUIDE, MODE_HINT
from app.ai.state_machine import (  # T04B 状态机
    SESSION_STATE_GUIDING,
    SESSION_STATE_INTRO,
    SESSION_STATE_PRACTICING,
    is_strict_guard,
)
from app.core.config import settings
from app.core.error_categories import normalize_error  # 方向八：错因 → 诊断话术
from app.core.response import ApiError, CODE_BAD_REQUEST
from app.core.system_config import get_config  # 低置信度重拍阈值（灰度开关）
from app.db import SessionLocal  # noqa: F401  (只读脚本按需自建会话，写操作一律用传入的 db)
from app.models.ai import AiMessage, AiSession
from app.models.content import (
    QUESTION_STATUS_ARCHIVED,
    SUBJECT_DEFAULT,
    Answer,
    KnowledgePoint,
    Question,
)

logger = logging.getLogger("app.ai.orchestrator")

#: 分析结论写进 ai_messages 时的前缀（generate_exercise 靠它回捞 JSON）
ANALYSIS_PREFIX: str = "分析："

#: AI 练习题元数据写进 ai_messages 时的前缀（``guide_exercise`` 靠它校验归属）
#:
#: 与 ``ANALYSIS_PREFIX`` 同款回捞惯例，content 形如::
#:
#:     练习题：[{"question_id": "...", "role": "fix", "tag": "..."}]
EXERCISE_PREFIX: str = "练习题："
EXERCISE_GUIDE_PREFIX: str = "练习引导："
EXERCISE_JUDGE_PREFIX: str = "练习判定："

#: 分析结论解析不出时的兜底
DEFAULT_KP: str = "综合"
DEFAULT_DIFFICULTY: int = 3
DEFAULT_ERROR_TYPE: str = "概念混淆"
PHOTO_SOURCE_DOC: str = "ai_wrong"
STUDENT_QUESTION_SOURCE_DOC: str = "student_asked"

#: 低置信度重拍引导话术（``need_rephoto`` 时回给前端；温和、无 emoji，语义=引导重拍）
REPHOTO_MESSAGE: str = "这道题有些地方我没看清，重新拍一张给我看看好吗？"


def _has_no_real_question(analysis: Dict[str, Any]) -> bool:
    """题面为空或只是 OCR 元话语时，视为「这次拍照彻底失败」。

    2026-10-23 实测发现：重模糊图上 OCR 不返回空串，而是返回一句元话语
    （如「无有效题目内容，返回空。」），analyze JSON 也往往不带 confidence。
    两件事叠加 = 用户拍糊了，系统却拿着空分析硬答，还走「缺失放行」路径。
    这里与 ``question._is_ocr_meta`` 同判据（长度上限 60 字 + 标记包含），
    作为编排层兜底——源头（analyze_question）已拦，这里防其他路径漏网。
    """
    text = str(analysis.get("question_text") or "").strip()
    if not text:
        return True
    if len(text) > 60:
        return False
    return any(marker in text for marker in question.OCR_META_MARKERS)


def _confidence_gate(analysis: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """低置信度闸门：模型自评置信度低于阈值时，返回「引导重拍」代替硬答。

    阈值读 ``system_config`` 的 ``CONFIDENCE_RETHRESHOLD``（默认 -1 = 关闭，
    灰度开关）：值 <0 表示机制关闭；``confidence`` 缺失（模型没给 / 解析失败）
    或无法转 float 时一律放行——不拿「没有自评」当「没看清」，避免误伤。
    **例外**：题面为空 / 只是 OCR 元话语时，缺失也按 0 分拦截——那是拍照失败，
    不是「模型没给自评」，不能拿空分析硬答（见 ``_has_no_real_question``）。

    Args:
        analysis: ``question.analyze_question`` 的分析结论。

    Returns:
        需要重拍时返回 ``{"need_rephoto": True, "confidence": float, "message": str}``；
        放行时返回 ``None``。
    """
    try:
        threshold = float(get_config("CONFIDENCE_RETHRESHOLD"))
    except (TypeError, ValueError):
        return None
    if threshold < 0:
        return None

    no_content = _has_no_real_question(analysis)
    raw = analysis.get("confidence")
    if raw is None:
        if no_content:
            return {
                "need_rephoto": True,
                "confidence": 0.0,
                "message": REPHOTO_MESSAGE,
            }
        return None
    try:
        confidence = float(raw)
    except (TypeError, ValueError):
        if no_content:
            return {
                "need_rephoto": True,
                "confidence": 0.0,
                "message": REPHOTO_MESSAGE,
            }
        return None
    if not no_content and confidence >= threshold:
        return None

    logger.info(
        "confidence gate blocked: confidence=%.2f < threshold=%.2f，引导重新拍照",
        confidence,
        threshold,
    )
    return {
        "need_rephoto": True,
        "confidence": confidence,
        "message": REPHOTO_MESSAGE,
    }


def _emit_chat_dna_event(
    db: Session,
    *,
    student_id: uuid.UUID,
    event_type: str,
    meta: Dict[str, Any],
    question_id: Optional[uuid.UUID] = None,
    knowledge_point_id: Optional[uuid.UUID] = None,
) -> None:
    """DNA-004 行为事件埋点（Sprint 2 T2，chat 侧旁路）。

    与 practice 侧一致：任何异常只记日志，绝不拖垮辅导主流程；只 ``flush``，
    由调用方现有 ``db.commit()`` 一并收口（本模块统一约定）。

    断点2 修复：HINT/REVIEW 事件原先**不带任何题目归因**，提示依赖无法
    落到知识点上。本函数新增 ``question_id`` / ``knowledge_point_id`` 归参
    （写入事件表专用列）；取不到时传 ``None``，由调用方在 meta 里标
    ``*_unknown``，指标侧按「未知」处理，绝不编造。
    """
    try:
        from app.services.student_dna_service import record_learning_event

        record_learning_event(
            db,
            student_id=student_id,
            event_type=event_type,
            question_id=question_id,
            knowledge_point_id=knowledge_point_id,
            meta=meta,
        )
    except Exception:  # noqa: BLE001 - 埋点失败不影响辅导主流程
        logger.warning(
            "chat DNA 埋点失败 event=%s session=%s", event_type, meta, exc_info=True
        )


def _emit_answer_leak_trace(
    db: Session,
    *,
    student_id: uuid.UUID,
    session_id: uuid.UUID,
    question_id: Optional[uuid.UUID],
    reply: str,
    mode: Optional[str],
    state: Optional[str],
) -> None:
    """Benchmark 维度一（教学行为）：学生面回复命中答案泄漏特征 → 落 trace。

    检测口径与 ``app.ai.answer_guard`` **完全一致**：同一
    ``has_answer_leak``、同一严格度 ``is_strict_guard(state, mode)``
    （引导/开场严格，讲解/评判宽松只拦露骨宣告）。引导类模式下 guard 会
    重生成/兜底拦截，学生面回复不会命中，故天然不落；讲解（DEMO/JUDGE）
    模式下 guard 直通，若讲解仍出现露骨的「答案：x」宣告即落一条
    ``answer_leak`` trace（severity=warning）。

    旁路语义与 ``_emit_chat_dna_event`` 一致：任何异常只记 warning，绝不
    拖垮辅导主流程；只 ``flush``，由调用方现有 ``db.commit()`` 收口。
    """
    try:
        from app.ai.answer_guard import has_answer_leak
        from app.services import quality_metrics_service

        strict = is_strict_guard(state or SESSION_STATE_GUIDING, mode=mode)
        text = (reply or "").strip()
        if not text or not has_answer_leak(text, strict=strict):
            return
        quality_metrics_service.record_answer_leak_trace(
            db,
            student_id=student_id,
            session_id=session_id,
            question_id=question_id,
            payload={
                "mode": mode,
                "strict": strict,
                "excerpt": text[:200],
            },
        )
    except Exception:  # noqa: BLE001 - 指标埋点失败不影响辅导主流程
        logger.warning(
            "answer_leak trace 埋点失败 session=%s mode=%s",
            session_id,
            mode,
            exc_info=True,
        )


def _emit_explanation_quality_trace(
    db: Session,
    *,
    student_id: uuid.UUID,
    session_id: uuid.UUID,
    quality: Any,
    threshold: Optional[int] = None,
) -> None:
    """Benchmark 维度一（教学行为）：DEMO 讲解质量评分 → 落 trace。

    只在评分**解析成功**（``quality.parsed_ok=True``）时落库，携带 5 维
    分数 + overall + 阈值 + 是否达标；解析失败（裁判超时 / JSON 脏）不落
    ——默认 3 分不是真实评分，落进去会污染均分趋势。

    旁路语义同 ``_emit_answer_leak_trace``：异常只 warning 不拖垮主流程。
    """
    try:
        from app.services import quality_metrics_service

        if quality is None or not bool(getattr(quality, "parsed_ok", False)):
            return
        quality_metrics_service.record_explanation_quality_trace(
            db,
            student_id=student_id,
            session_id=session_id,
            quality=quality,
            threshold=threshold,
        )
    except Exception:  # noqa: BLE001 - 指标埋点失败不影响辅导主流程
        logger.warning(
            "explanation_quality trace 埋点失败 session=%s", session_id, exc_info=True
        )


def _session_question_attribution(
    db: Session, session: AiSession
) -> Tuple[Optional[uuid.UUID], Optional[uuid.UUID]]:
    """取 AI 会话绑定题目及其知识点（chat 侧事件归参用）。

    会话可能未绑定题目（早期数据 / 外键 ``ON DELETE SET NULL``），此时返回
    ``(None, None)``，调用方据此在 meta 里标 ``question_id_unknown``。

    Args:
        db: 调用方传入的数据库会话。
        session: 当前 AI 会话 ORM 对象。

    Returns:
        ``(question_id, knowledge_point_id)``；未绑定题目时为 ``(None, None)``。
    """
    if session.question_id is None:
        return None, None
    kp_id = db.execute(
        select(Question.kp_id).where(Question.id == session.question_id)
    ).scalar_one_or_none()
    return session.question_id, kp_id


def _attribution_markers(
    question_id: Optional[uuid.UUID], knowledge_point_id: Optional[uuid.UUID]
) -> Dict[str, Any]:
    """归参缺失时生成 meta 标记（question_id_unknown / kp_id_unknown）。"""
    if question_id is None:
        return {"question_id_unknown": True}
    if knowledge_point_id is None:
        return {"kp_id_unknown": True}
    return {}


def _file_url(image_key: Optional[str]) -> Optional[str]:
    """把上传接口返回的纯文件 key 转为可回看的站内地址。"""
    key = (image_key or "").strip()
    if not key or "/" in key or "\\" in key or key in {".", ".."}:
        return None
    return f"/api/file/{key}"


def _question_fingerprint(raw: Any) -> str:
    """轻量归一化题干，用于试运营规模下的重复题识别。"""
    text = unicodedata.normalize("NFKC", str(raw or "")).strip().lower()
    return "".join(text.split())


def _find_student_question(db: Session, content: str) -> Optional[Question]:
    """在学生咨询候选题中查找同题，兼容早期 ``ai_wrong`` 标记。"""
    fingerprint = _question_fingerprint(content)
    if not fingerprint:
        return None
    rows = db.execute(
        select(Question)
        .where(Question.source_doc.in_((STUDENT_QUESTION_SOURCE_DOC, PHOTO_SOURCE_DOC)))
        .order_by(Question.created_at.asc())
    ).scalars()
    return next(
        (row for row in rows if _question_fingerprint(row.content) == fingerprint), None
    )


def _archive_student_question(
    *,
    student_id: uuid.UUID,
    analysis: Dict[str, Any],
    image_url: Optional[str],
    db: Session,
) -> Question:
    """把咨询原题沉淀为去重的待复核题，并为当前学生建立错题记录。"""
    content = str(analysis.get("question_text") or "").strip()
    question_row = _find_student_question(db, content)
    knowledge = str(analysis.get("knowledge") or "").strip()
    kp_id = (
        db.execute(
            select(KnowledgePoint.id).where(KnowledgePoint.name == knowledge).limit(1)
        ).scalar_one_or_none()
        if knowledge
        else None
    )

    if question_row is None:
        question_row = Question(
            content=content,
            image_url=image_url,
            subject=SUBJECT_DEFAULT,
            difficulty=int(analysis.get("difficulty") or DEFAULT_DIFFICULTY),
            type_=str(analysis.get("type") or "").strip() or None,
            kp_id=kp_id,
            status=QUESTION_STATUS_ARCHIVED,
            source_doc=STUDENT_QUESTION_SOURCE_DOC,
            kp_verified=False,
            source_tag="red",
            ship_ready=False,
        )
        db.add(question_row)
        db.flush()
    else:
        question_row.source_doc = STUDENT_QUESTION_SOURCE_DOC
        question_row.status = QUESTION_STATUS_ARCHIVED
        question_row.source_tag = "red"
        question_row.ship_ready = False
        question_row.kp_verified = False
        question_row.image_url = question_row.image_url or image_url
        question_row.kp_id = question_row.kp_id or kp_id
        question_row.type_ = (
            question_row.type_ or str(analysis.get("type") or "").strip() or None
        )

    answer_row = db.execute(
        select(Answer).where(Answer.question_id == question_row.id)
    ).scalar_one_or_none()
    if answer_row is None:
        answer_row = Answer(question_id=question_row.id)
        db.add(answer_row)
    answer_row.correct_answer = (
        answer_row.correct_answer
        or str(analysis.get("correct_answer") or "").strip()
        or None
    )
    answer_row.analysis = (
        answer_row.analysis or str(analysis.get("solution") or "").strip() or None
    )

    # 错题落库统一走 learning_service.record_mistake：幂等口径与其余入本
    # 路径一致（同题有未攻克记录直接复用），顺带获得「入本即建档」的 FSRS
    # 题目粒度复习卡（Again(1)，灰度内核走 review_service 既有封装）。
    # ⚠️ 该函数内部自带 commit——归档的题目/答案/错题立即落库，后续 AI
    # 调用失败也不丢已沉淀的数据；幂等语义见 record_mistake 文档
    # （已攻克的历史记录不阻挡再次建档：学生重新咨询同一题视为再次错题）。
    from app.services import learning_service

    learning_service.record_mistake(
        db,
        student_id=student_id,
        question_id=question_row.id,
        error_reason="咨询求助题",
    )
    return question_row


def _to_uuid(raw: Any, field: str) -> uuid.UUID:
    """把字符串 / UUID 统一成 ``uuid.UUID``，非法值按 400 处理。"""
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_BAD_REQUEST, f"{field} 非法") from exc


def _get_session(session_id: Any, db: Session) -> AiSession:
    """按 ID 取会话，不存在抛 400。"""
    sid = _to_uuid(session_id, "session_id")
    session = db.execute(
        select(AiSession).where(AiSession.id == sid)
    ).scalar_one_or_none()
    if session is None:
        raise ApiError(CODE_BAD_REQUEST, "会话不存在")
    return session


def _load_messages(session_id: uuid.UUID, db: Session) -> List[AiMessage]:
    """读取该会话的全部消息，按 ``created_at`` 升序。"""
    rows = db.execute(
        select(AiMessage)
        .where(AiMessage.session_id == session_id)
        .order_by(AiMessage.created_at.asc())
    ).scalars()
    return list(rows)


def _to_history(messages: List[AiMessage]) -> List[Dict[str, str]]:
    """把 ORM 消息转成 Agent 需要的 ``[{"role","content"}]``。"""
    return [{"role": m.role, "content": m.content} for m in messages]


def _grounding_segment(
    *,
    db: Session,
    session: AiSession,
    mode: Optional[str],
) -> str:
    """取本轮的接地气素材段（A-1 轻量 RAG）。

    返回空串 = **不注入**，此时 tutor 的 system 提示与改造前逐字一致。
    本函数**绝不抛错**：素材是「锦上添花」，取不到就当没有。

    延迟导入 ``app.services.grounding_service``：与本文件里
    ``explanation_quality`` 同一处理，避免 ``app.ai`` ↔ ``app.services``
    在模块级形成 import 环。
    """
    try:
        from app.services import grounding_service

        return grounding_service.render_session_grounding_segment(
            db,
            mode=mode,
            question_id=session.question_id,
            student_id=session.student_id,
        )
    except Exception as exc:  # pragma: no cover - 兜底：素材取不到不影响主流程
        logger.warning("接地气素材段构建失败(session=%s): %s", session.id, exc)
        return ""


def start_session(
    *,
    student_id: Any,
    image_b64: Optional[str],
    text: Optional[str],
    image_key: Optional[str] = None,
    db: Session,
) -> Dict[str, Any]:
    """开启一次 AI 辅导：读题分析 + 首轮引导，并落库会话与两条 assistant 消息。

    Args:
        student_id: 学生 ID（``uuid.UUID`` 或可转换字符串）。
        image_b64: 题目照片 base64 / data URL，可为空。
        text: 题目文字，可为空（与 ``image_b64`` 不能同时为空）。
        db: 调用方传入的 SQLAlchemy 会话（本函数内 ``commit``）。

    Returns:
        ``{"session_id": str, "analysis": dict, "first_question": str}``；
        分析自评置信度低于 ``CONFIDENCE_RETHRESHOLD`` 阈值时改为返回
        ``{"need_rephoto": True, "confidence": float, "message": str}``，
        且不创建任何会话 / 错题 / 题目记录（没看清就不硬答，引导重新拍照）。

    Raises:
        ApiError: 400 题目为空 / 参数非法；429 今日额度已用完。
        ArkError: 方舟调用失败或返回无法解析。
    """
    sid = _to_uuid(student_id, "student_id")
    check_ai_quota(sid, db)

    analysis = question.analyze_question(text=text, image_b64=image_b64)

    # 低置信度重拍：没把握就不硬答——不建 ai_sessions / mistakes / questions，直接引导重拍
    gate = _confidence_gate(analysis)
    if gate is not None:
        return gate

    source_image_url = _file_url(image_key)
    source_question = _archive_student_question(
        student_id=sid,
        analysis=analysis,
        image_url=source_image_url,
        db=db,
    )
    source_question_id = source_question.id
    public_analysis = {
        key: value
        for key, value in analysis.items()
        if key not in {"correct_answer", "solution"}
    }

    session = AiSession(student_id=sid, question_id=source_question_id, status="active")
    session.session_state = SESSION_STATE_INTRO  # T04B 状态机：开场
    db.add(session)
    db.flush()  # 拿到 session.id，供两条消息挂载

    history = [
        {
            "role": "assistant",
            "content": (
                f"我先看了一下，这道题主要考：{analysis.get('knowledge')}，"
                f"难度约 {analysis.get('difficulty')}。"
            ),
        }
    ]
    first_question = tutor.next_question(
        session_messages=history, state=SESSION_STATE_INTRO
    )

    db.add(
        AiMessage(
            session_id=session.id,
            role="assistant",
            content=f"{ANALYSIS_PREFIX}{json.dumps(public_analysis, ensure_ascii=False)}",
        )
    )
    db.add(AiMessage(session_id=session.id, role="assistant", content=first_question))
    # T04B 状态机：首问后即进入引导阶段，记为第 1 步
    session.session_state = SESSION_STATE_GUIDING
    session.step = 1
    session.state_updated_at = datetime.now(timezone.utc)

    # DNA-004 埋点（旁路）：AI 会话创建 = SESSIONSTART（留存率口径）。
    # 必须在既有 commit 之前 emit——本模块埋点只 flush，由该 commit 收口。
    _emit_chat_dna_event(
        db,
        student_id=sid,
        event_type="SESSIONSTART",
        question_id=source_question_id,
        knowledge_point_id=source_question.kp_id,
        meta={
            "channel": "ai_chat",
            "state": session.session_state,
            "knowledge": str(analysis.get("knowledge") or "").strip() or None,
            **_attribution_markers(source_question_id, source_question.kp_id),
        },
    )
    db.commit()

    return {
        "session_id": str(session.id),
        "analysis": public_analysis,
        "first_question": first_question,
    }


async def start_session_async(
    *,
    student_id: Any,
    image_b64: Optional[str],
    text: Optional[str],
    image_key: Optional[str] = None,
    db: Session,
) -> Dict[str, Any]:
    """``start_session`` 的异步版：读写题分析用 async Agent，不阻塞事件循环。

    Args、Returns、Raises 与 ``start_session`` 完全一致（DB 写操作为了保持与同步
    版共享 Session 语义，仍是同步调用；耗尽时间集中在网络 I/O，已被异步化）。
    低置信度重拍语义一致：置信度低于 ``CONFIDENCE_RETHRESHOLD`` 阈值时返回
    ``need_rephoto`` 结果，不创建任何记录。
    """
    sid = _to_uuid(student_id, "student_id")
    check_ai_quota(sid, db)

    analysis = await question.analyze_question_async(text=text, image_b64=image_b64)

    # 低置信度重拍：没把握就不硬答——不建 ai_sessions / mistakes / questions，直接引导重拍
    gate = _confidence_gate(analysis)
    if gate is not None:
        return gate

    source_image_url = _file_url(image_key)
    source_question = _archive_student_question(
        student_id=sid,
        analysis=analysis,
        image_url=source_image_url,
        db=db,
    )
    source_question_id = source_question.id
    public_analysis = {
        key: value
        for key, value in analysis.items()
        if key not in {"correct_answer", "solution"}
    }

    session = AiSession(student_id=sid, question_id=source_question_id, status="active")
    session.session_state = SESSION_STATE_INTRO  # T04B 状态机：开场
    db.add(session)
    db.flush()  # 拿到 session.id，供两条消息挂载

    history = [
        {
            "role": "assistant",
            "content": (
                f"我先看了一下，这道题主要考：{analysis.get('knowledge')}，"
                f"难度约 {analysis.get('difficulty')}。"
            ),
        }
    ]
    first_question = await tutor.next_question_async(
        session_messages=history, state=SESSION_STATE_INTRO
    )

    db.add(
        AiMessage(
            session_id=session.id,
            role="assistant",
            content=f"{ANALYSIS_PREFIX}{json.dumps(public_analysis, ensure_ascii=False)}",
        )
    )
    db.add(AiMessage(session_id=session.id, role="assistant", content=first_question))
    # T04B 状态机：首问后即进入引导阶段，记为第 1 步
    session.session_state = SESSION_STATE_GUIDING
    session.step = 1
    session.state_updated_at = datetime.now(timezone.utc)

    # DNA-004 埋点（旁路）：AI 会话创建 = SESSIONSTART（留存率口径）。
    # 必须在既有 commit 之前 emit——本模块埋点只 flush，由该 commit 收口。
    _emit_chat_dna_event(
        db,
        student_id=sid,
        event_type="SESSIONSTART",
        question_id=source_question_id,
        knowledge_point_id=source_question.kp_id,
        meta={
            "channel": "ai_chat",
            "state": session.session_state,
            "knowledge": str(analysis.get("knowledge") or "").strip() or None,
            **_attribution_markers(source_question_id, source_question.kp_id),
        },
    )
    db.commit()

    return {
        "session_id": str(session.id),
        "analysis": public_analysis,
        "first_question": first_question,
    }


def continue_session(
    *,
    session_id: Any,
    user_text: str,
    mode: Optional[str] = None,
    db: Session,
) -> Dict[str, Any]:
    """多轮辅导：带着完整历史再问一句，并落库 user / assistant 两条消息。

    T2/T5 起本函数是 **mode 的唯一定稿处**：调 ``tutor.next_question()`` 之前先做
    一次意图识别，把"孩子这一轮到底想要什么"翻译成 ``GUIDE/HINT/DEMO/JUDGE``，
    再原样透传给 Agent → 状态机 → 泄漏守卫。

    Args:
        session_id: 会话 ID。
        user_text: 孩子这一轮说的话。
        mode: 前端显式指定的模式（快捷按钮），``None`` 表示交给意图识别自动判断。
            非法值会被 ``intent.detect_mode()`` 忽略，等同于 ``None``。
        db: 调用方传入的 SQLAlchemy 会话（本函数内 ``commit``）。

    Returns:
        ``{"reply": str, "mode": str, "next_mode": str}``。``mode`` 是本轮实际
        生效的模式；``next_mode`` 是建议前端下一轮默认带上的模式——本轮讲完
        （``DEMO``）之后自动回切 ``GUIDE``（D3-a），其余情况等于本轮 ``mode``。

    Raises:
        ApiError: 400 会话不存在 / 输入为空；429 今日额度已用完。
        ArkError: 方舟调用失败。
    """
    content = (user_text or "").strip()
    if not content:
        raise ApiError(CODE_BAD_REQUEST, "说点什么吧，比如你现在是怎么想的")

    session = _get_session(session_id, db)
    check_ai_quota(session.student_id, db)

    history = _to_history(_load_messages(session.id, db))
    state = session.session_state  # T04B 读回持久化的阶段
    step = session.step

    # T2 意图识别：override（前端快捷按钮）优先，其次按文本 + 历史自动判定
    detected = intent.detect(text=content, history=history, state=state, override=mode)
    active_mode = detected.mode
    logger.info(
        "intent session=%s mode=%s source=%s confidence=%.2f matched=%s",
        session.id,
        active_mode,
        detected.source,
        detected.confidence,
        detected.matched or "-",
    )

    # 方向八：从开场分析结论回捞错因 → 透传给引导层注入「诊断式追问」话术。
    # （此前 analysis 里的 error 只用于出题与画像，引导轮从未用到——诊断链断点已补）
    raw_error = str(
        (_pick_analysis(_load_messages(session.id, db)).get("error") or "")
    ).strip()
    error_type = normalize_error(raw_error) if raw_error else None

    reply = tutor.next_question(
        session_messages=history + [{"role": "user", "content": content}],
        state=state,
        mode=active_mode,
        error_type=error_type,
        grounding_segment=_grounding_segment(
            db=db, session=session, mode=active_mode
        ),
    )

    # #67→生产流：DEMO 讲解过质量闸门（低于阈值自动重讲一次），默认关闭需显式开启
    if active_mode == MODE_DEMO and settings.explanation_quality_gate_enabled:
        reply = _apply_demo_quality_gate(
            db=db,
            session=session,
            student_text=content,
            reply=reply,
            threshold=settings.explanation_quality_gate_threshold,
        )

    # TASK-002：学生消息顺带结构化（旁路容错，失败列落 NULL 不拖垮主链路）
    db.add(
        AiMessage(
            session_id=session.id,
            role="user",
            content=content,
            response_object=_safe_response_object(content, history),
        )
    )
    db.add(AiMessage(session_id=session.id, role="assistant", content=reply))
    # T04B 状态机：步数 +1；开场首轮后即转入引导阶段
    session.step = step + 1
    if state == SESSION_STATE_INTRO:
        session.session_state = SESSION_STATE_GUIDING
    session.state_updated_at = datetime.now(timezone.utc)

    # DNA-004 埋点（旁路不拖垮主流程）：HINT=学生要提示，DEMO=讲解复习
    # 断点2 修复：HINT/REVIEW 带 question_id/kp_id 归参，提示依赖可归因到
    # 知识点；取不到时 meta 标 unknown（见 _attribution_markers）。
    chat_question_id, chat_kp_id = _session_question_attribution(db, session)
    attribution = _attribution_markers(chat_question_id, chat_kp_id)
    # Benchmark 维度一（教学行为）：学生面回复仍命中答案泄漏特征 → 落 trace
    _emit_answer_leak_trace(
        db,
        student_id=session.student_id,
        session_id=session.id,
        question_id=chat_question_id,
        reply=reply,
        mode=active_mode,
        state=state,
    )
    if active_mode == MODE_HINT:
        _emit_chat_dna_event(
            db,
            student_id=session.student_id,
            event_type="HINT",
            question_id=chat_question_id,
            knowledge_point_id=chat_kp_id,
            meta={"session_id": str(session.id), "step": step + 1, **attribution},
        )
    elif active_mode == MODE_DEMO:
        _emit_chat_dna_event(
            db,
            student_id=session.student_id,
            event_type="REVIEW",
            question_id=chat_question_id,
            knowledge_point_id=chat_kp_id,
            meta={
                "session_id": str(session.id),
                "step": step + 1,
                "mode": "DEMO",
                **attribution,
            },
        )
    db.commit()

    # D3-a：讲解只讲这一轮，讲完立刻回到引导，避免"一路讲到底"
    next_mode = MODE_GUIDE if active_mode == MODE_DEMO else active_mode

    return {"reply": reply, "mode": active_mode, "next_mode": next_mode}


async def continue_session_async(
    *,
    session_id: Any,
    user_text: str,
    mode: Optional[str] = None,
    db: Session,
) -> Dict[str, Any]:
    """``continue_session`` 的异步版：await async tutor / 质量闸门。

    Args、Returns、Raises 与 ``continue_session`` 完全一致（DB 写操作仍是同步调用，
    网络 I/O 已异步化）。
    """
    content = (user_text or "").strip()
    if not content:
        raise ApiError(CODE_BAD_REQUEST, "说点什么吧，比如你现在是怎么想的")

    session = _get_session(session_id, db)
    check_ai_quota(session.student_id, db)

    history = _to_history(_load_messages(session.id, db))
    state = session.session_state  # T04B 读回持久化的阶段
    step = session.step

    # T2 意图识别：override（前端快捷按钮）优先，其次按文本 + 历史自动判定
    detected = intent.detect(text=content, history=history, state=state, override=mode)
    active_mode = detected.mode
    logger.info(
        "intent session=%s mode=%s source=%s confidence=%.2f matched=%s",
        session.id,
        active_mode,
        detected.source,
        detected.confidence,
        detected.matched or "-",
    )

    # 方向八：从开场分析结论回捞错因 → 透传给引导层注入「诊断式追问」话术
    raw_error = str(
        (_pick_analysis(_load_messages(session.id, db)).get("error") or "")
    ).strip()
    error_type = normalize_error(raw_error) if raw_error else None

    reply = await tutor.next_question_async(
        session_messages=history + [{"role": "user", "content": content}],
        state=state,
        mode=active_mode,
        error_type=error_type,
        grounding_segment=_grounding_segment(
            db=db, session=session, mode=active_mode
        ),
    )

    # #67→生产流：DEMO 讲解过质量闸门（低于阈值自动重讲一次），默认关闭需显式开启
    if active_mode == MODE_DEMO and settings.explanation_quality_gate_enabled:
        reply = await _apply_demo_quality_gate_async(
            db=db,
            session=session,
            student_text=content,
            reply=reply,
            threshold=settings.explanation_quality_gate_threshold,
        )

    # TASK-002：学生消息顺带结构化（旁路容错，失败列落 NULL 不拖垮主链路）
    db.add(
        AiMessage(
            session_id=session.id,
            role="user",
            content=content,
            response_object=_safe_response_object(content, history),
        )
    )
    db.add(AiMessage(session_id=session.id, role="assistant", content=reply))
    # T04B 状态机：步数 +1；开场首轮后即转入引导阶段
    session.step = step + 1
    if state == SESSION_STATE_INTRO:
        session.session_state = SESSION_STATE_GUIDING
    session.state_updated_at = datetime.now(timezone.utc)

    # DNA-004 埋点（旁路不拖垮主流程）：HINT=学生要提示，DEMO=讲解复习
    # 断点2 修复：HINT/REVIEW 带 question_id/kp_id 归参，提示依赖可归因到
    # 知识点；取不到时 meta 标 unknown（见 _attribution_markers）。
    chat_question_id, chat_kp_id = _session_question_attribution(db, session)
    attribution = _attribution_markers(chat_question_id, chat_kp_id)
    # Benchmark 维度一（教学行为）：学生面回复仍命中答案泄漏特征 → 落 trace
    _emit_answer_leak_trace(
        db,
        student_id=session.student_id,
        session_id=session.id,
        question_id=chat_question_id,
        reply=reply,
        mode=active_mode,
        state=state,
    )
    if active_mode == MODE_HINT:
        _emit_chat_dna_event(
            db,
            student_id=session.student_id,
            event_type="HINT",
            question_id=chat_question_id,
            knowledge_point_id=chat_kp_id,
            meta={"session_id": str(session.id), "step": step + 1, **attribution},
        )
    elif active_mode == MODE_DEMO:
        _emit_chat_dna_event(
            db,
            student_id=session.student_id,
            event_type="REVIEW",
            question_id=chat_question_id,
            knowledge_point_id=chat_kp_id,
            meta={
                "session_id": str(session.id),
                "step": step + 1,
                "mode": "DEMO",
                **attribution,
            },
        )
    db.commit()

    # D3-a：讲解只讲这一轮，讲完立刻回到引导，避免"一路讲到底"
    next_mode = MODE_GUIDE if active_mode == MODE_DEMO else active_mode

    return {"reply": reply, "mode": active_mode, "next_mode": next_mode}


def _apply_demo_quality_gate(
    *,
    db: Session,
    session: AiSession,
    student_text: str,
    reply: str,
    threshold: int,
) -> str:
    """DEMO 讲解的质量闸门（#67→生产流）：评测讲解，低于阈值自动重讲一次。

    非阻塞：评测或重生成任何异常都回落到原讲解。本函数延迟导入
    ``app.services.explanation_quality``，避免 ``app.ai`` ↔ ``app.services`` 在
    模块级形成 import 环（``ai_exercise_service`` 已用同款延迟导入）。

    Args:
        db: 调用方传入的 SQLAlchemy 会话。
        session: 当前 AI 会话 ORM 对象。
        student_text: 学生这一轮的话（作讲解上下文 + 重讲的 regenerate 输入）。
        reply: 已生成的 DEMO 讲解文本。
        threshold: 质量闸门阈值（综合分 1-5）。

    Returns:
        最终采用的讲解文本（达标的原讲解，或重讲后的新讲解，或异常时原讲解）。
    """
    from app.services import explanation_quality

    messages = _load_messages(session.id, db)
    history = _to_history(messages)
    first_user = next((m["content"] for m in history if m["role"] == "user"), "")
    analysis = _pick_analysis(messages)
    error_type = str(analysis.get("error") or "").strip() or None

    def _regenerate() -> str:
        return tutor.next_question(
            session_messages=history + [{"role": "user", "content": student_text}],
            state=session.session_state,
            mode=MODE_DEMO,
            grounding_segment=_grounding_segment(
                db=db, session=session, mode=MODE_DEMO
            ),
        )

    final_text, quality = explanation_quality.ensure_explanation_quality(
        question=first_user,
        student_attempt=student_text,
        ai_explanation=reply,
        error_type=error_type,
        regenerate=_regenerate,
        threshold=threshold,
        max_retries=1,
    )
    # Benchmark 维度一（教学行为）：讲解质量评分落 trace（解析失败不落分）
    _emit_explanation_quality_trace(
        db,
        student_id=session.student_id,
        session_id=session.id,
        quality=quality,
        threshold=threshold,
    )
    return final_text


async def _apply_demo_quality_gate_async(
    *,
    db: Session,
    session: AiSession,
    student_text: str,
    reply: str,
    threshold: int,
) -> str:
    """``_apply_demo_quality_gate`` 的异步版：评测与重讲均走 async Agent。

    非阻塞语义与同步版一致：任何异常都回落到原讲解。
    """
    from app.services import explanation_quality

    messages = _load_messages(session.id, db)
    history = _to_history(messages)
    first_user = next((m["content"] for m in history if m["role"] == "user"), "")
    analysis = _pick_analysis(messages)
    error_type = str(analysis.get("error") or "").strip() or None

    async def _regenerate() -> str:
        return await tutor.next_question_async(
            session_messages=history + [{"role": "user", "content": student_text}],
            state=session.session_state,
            mode=MODE_DEMO,
            grounding_segment=_grounding_segment(
                db=db, session=session, mode=MODE_DEMO
            ),
        )

    final_text, quality = await explanation_quality.ensure_explanation_quality_async(
        question=first_user,
        student_attempt=student_text,
        ai_explanation=reply,
        error_type=error_type,
        regenerate=_regenerate,
        threshold=threshold,
        max_retries=1,
    )
    # Benchmark 维度一（教学行为）：讲解质量评分落 trace（解析失败不落分）
    _emit_explanation_quality_trace(
        db,
        student_id=session.student_id,
        session_id=session.id,
        quality=quality,
        threshold=threshold,
    )
    return final_text


def _safe_response_object(
    text: str, history: List[Dict[str, str]]
) -> Optional[Dict[str, Any]]:
    """学生消息的结构化对象（**旁路容错**，TASK-002）。

    analyzer 自身已承诺永不抛异常（内部收敛 UNCLEAR 契约）；此处再兜一层——
    万一 analyzer 被替换/演进实现出错，消息照常落库、结构化列 NULL，
    绝不能影响聊天主链路。

    Args:
        text: 学生本轮原话。
        history: 会话历史（不含本轮）。

    Returns:
        结构化契约 dict；analyzer 异常时 ``None``（列落 NULL）。
    """
    try:
        return response_analyzer.analyze_response(text=text, history=history)
    except Exception:  # noqa: BLE001 - 结构化失败不拖垮辅导主流程
        logger.warning("response analyze 旁路失败，结构化列落 NULL", exc_info=True)
        return None


def _pick_analysis(messages: List[AiMessage]) -> Dict[str, Any]:
    """从第一条 assistant 消息里回捞分析 JSON；捞不到返回空 dict。"""
    for message in messages:
        if message.role != "assistant":
            continue
        try:
            data = _extract_json(message.content)
        except ArkError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def latest_response_objects(
    messages: List[AiMessage], limit: int = 1
) -> List[Optional[Dict[str, Any]]]:
    """按时间升序取最近 ``limit`` 条**学生消息**的结构化对象（#33 消费入口）。

    Args:
        messages: 会话消息（ORM 对象，按 ``created_at`` 升序，见 ``_load_messages``）。
        limit: 取最近几条；``<=0`` 返回空列表。

    Returns:
        与消息对应的结构化对象列表（最旧的在前）；老消息（列值 NULL）或
        脏 JSON（读出非 dict）对应位置为 ``None``，**不抛异常**。

    Note:
        assistant/system 消息的结构化列恒 NULL，直接跳过；调用方如需完整
        消息序列，请自行按 ``role`` 对齐。
    """
    objects: List[Optional[Dict[str, Any]]] = []
    for message in messages:
        if str(message.role or "").strip().lower() != "user":
            continue
        raw = getattr(message, "response_object", None)
        objects.append(raw if isinstance(raw, dict) else None)
    return objects[-limit:] if limit > 0 else []


def generate_exercise(*, session_id: Any, db: Session) -> Dict[str, Any]:
    """基于会话的分析结论生成两道练习题，并按 D1 落库成归档题（``source_doc='ai_gen'``）。

    T6 起练习题会写进 ``questions`` / ``answers``：``status='archived'`` 保证**永不进入
    题库抽题池**（``practice_service._candidate_rows()`` 硬条件是 ``status='active'``），
    只为让后续的「判错即时入本」能拿到合法的 ``question_id`` 外键。

    Args:
        session_id: 会话 ID。
        db: 调用方传入的 SQLAlchemy 会话（本函数内 ``commit``）。

    Returns:
        ``{"exercises": list[dict]}``，元素含
        ``question_id/q/a/type/knowledge/difficulty/role/tag``。
        落库失败时 ``question_id`` 为 ``None``（出题本身不因落库失败而失败）。

    Raises:
        ApiError: 400 会话不存在。
        ArkError: 方舟调用失败或返回无法解析。
    """
    # 分层纪律：``app.services`` 依赖 ``app.ai``（见 practice_service），反向不许在模块级
    # 依赖，否则 ai_exercise_service ← → orchestrator 形成 import 环。故此处延迟导入。
    from app.services import ai_exercise_service

    session = _get_session(session_id, db)
    messages = _load_messages(session.id, db)

    analysis = _pick_analysis(messages)
    kp = str(analysis.get("knowledge") or "").strip() or DEFAULT_KP
    error_type = str(analysis.get("error") or "").strip() or DEFAULT_ERROR_TYPE
    try:
        difficulty = int(float(str(analysis.get("difficulty")).strip()))
    except (TypeError, ValueError):
        difficulty = DEFAULT_DIFFICULTY

    # ★ T1 定向推题：先算薄弱画像（纯本地 SQL，零外网），再把 Top-2 弱项注入出题提示词。
    # 弱项为空（source=fallback）时 weak_context=""，走现有出题行为；kp 仍锚定当前错题。
    weak = ai_exercise_service.get_weak_profile(db, student_id=session.student_id)
    weak_context = ""
    if weak["source"] == "profile" and weak["weak_tags"]:
        labels = "、".join(tag["label"] for tag in weak["weak_tags"])
        weak_context = (
            f"【定向推题】该学生历史上反复出错的薄弱错因是：{labels}。"
            f"请在保持薄弱知识点「{kp}」不变的前提下，让这两道练习题的变式"
            f"专门去反复练习和踩这些薄弱错因的坑，帮他把易错点练扎实。"
        )

    exercises = exercise.gen_exercises(
        error_type=error_type,  # 仍锚定当前错题的 drill 目标
        kp=kp,  # 知识点锚定当前错题，主题不跑偏
        difficulty=difficulty,
        context=weak_context,  # ★ 弱项 Top-2 注入点（为空即不注入，走现有行为）
    )

    # D1 落库：拿到与 exercises 等长的 question_id 列表（失败位置为 None）
    question_ids = ai_exercise_service.persist_exercises(
        db,
        session=session,
        exercises=exercises,
        student_id=session.student_id,
    )
    for item, qid in zip(exercises, question_ids):
        item["question_id"] = str(qid) if qid else None

    # AI 侧元数据（role/tag/原始 type）不入 questions 表，单独落一条消息，
    # 既是审计痕迹，也是 ``guide_exercise`` 校验「这道题属不属于本会话」的依据。
    # ``answer_check`` 是落库前轻量合法性检查的结论（见
    # ``ai_exercise_service.persist_exercises``），只做审计标记，不影响出题。
    meta = [
        {
            "question_id": item.get("question_id"),
            "role": item.get("role"),
            "tag": item.get("tag"),
            "type": item.get("type"),
            "answer_check": item.get(ai_exercise_service.ANSWER_CHECK_KEY),
        }
        for item in exercises
    ]
    db.add(
        AiMessage(
            session_id=session.id,
            role="assistant",
            content=f"{EXERCISE_PREFIX}{json.dumps(meta, ensure_ascii=False)}",
        )
    )

    # ---- DNA-004 行为事件埋点：AI 练习题展示 = QUESTIONVIEW（学习次数口径）----
    # 挂点理由：本函数出参 exercises 由前端原样渲染成练习卡，是 AI 链路唯一
    # 的题目展示路径。只对**落库成功**的题埋点——question_id 为 None 时无实体
    # 可归因，跳过而非编造（对齐 persist_exercises 的降级口径）。
    view_ids = [qid for qid in question_ids if qid is not None]
    view_kp: Dict[uuid.UUID, Optional[uuid.UUID]] = {}
    if view_ids:
        view_kp = {
            qid: kp
            for qid, kp in db.execute(
                select(Question.id, Question.kp_id).where(Question.id.in_(view_ids))
            ).all()
        }
    for item, qid in zip(exercises, question_ids):
        if qid is None:
            continue
        _emit_chat_dna_event(
            db,
            student_id=session.student_id,
            event_type="QUESTIONVIEW",
            question_id=qid,
            knowledge_point_id=view_kp.get(qid),
            meta={
                "session_id": str(session.id),
                "channel": "ai_exercise",
                "role": item.get("role"),
                "tag": item.get("tag"),
            },
        )

    # T04B 状态机：已出题进入练习阶段
    session.session_state = SESSION_STATE_PRACTICING
    session.summary_text = None
    session.state_updated_at = datetime.now(timezone.utc)
    db.commit()
    # weak_tags / weak_source 随响应回带，供前端渲染薄弱点横幅（snake_case 透传）
    return {
        "exercises": exercises,
        "weak_tags": weak["weak_tags"],
        "weak_source": weak["source"],
    }


async def generate_exercise_async(*, session_id: Any, db: Session) -> Dict[str, Any]:
    """``generate_exercise`` 的异步版：await ``exercise.gen_exercises_async``。

    Args、Returns、Raises 与 ``generate_exercise`` 完全一致（DB 写操作仍是同步调用）。
    """
    # 分层纪律：``app.services`` 依赖 ``app.ai``（见 practice_service），反向不许在模块级
    # 依赖，否则 ai_exercise_service ← → orchestrator 形成 import 环。故此处延迟导入。
    from app.services import ai_exercise_service

    session = _get_session(session_id, db)
    messages = _load_messages(session.id, db)

    analysis = _pick_analysis(messages)
    kp = str(analysis.get("knowledge") or "").strip() or DEFAULT_KP
    error_type = str(analysis.get("error") or "").strip() or DEFAULT_ERROR_TYPE
    try:
        difficulty = int(float(str(analysis.get("difficulty")).strip()))
    except (TypeError, ValueError):
        difficulty = DEFAULT_DIFFICULTY

    # ★ T1 定向推题：先算薄弱画像（纯本地 SQL，零外网），再把 Top-2 弱项注入出题提示词。
    # 弱项为空（source=fallback）时 weak_context=""，走现有出题行为；kp 仍锚定当前错题。
    weak = ai_exercise_service.get_weak_profile(db, student_id=session.student_id)
    weak_context = ""
    if weak["source"] == "profile" and weak["weak_tags"]:
        labels = "、".join(tag["label"] for tag in weak["weak_tags"])
        weak_context = (
            f"【定向推题】该学生历史上反复出错的薄弱错因是：{labels}。"
            f"请在保持薄弱知识点「{kp}」不变的前提下，让这两道练习题的变式"
            f"专门去反复练习和踩这些薄弱错因的坑，帮他把易错点练扎实。"
        )

    exercises = await exercise.gen_exercises_async(
        error_type=error_type,  # 仍锚定当前错题的 drill 目标
        kp=kp,  # 知识点锚定当前错题，主题不跑偏
        difficulty=difficulty,
        context=weak_context,  # ★ 弱项 Top-2 注入点（为空即不注入，走现有行为）
    )

    # D1 落库：拿到与 exercises 等长的 question_id 列表（失败位置为 None）
    question_ids = ai_exercise_service.persist_exercises(
        db,
        session=session,
        exercises=exercises,
        student_id=session.student_id,
    )
    for item, qid in zip(exercises, question_ids):
        item["question_id"] = str(qid) if qid else None

    meta = [
        {
            "question_id": item.get("question_id"),
            "role": item.get("role"),
            "tag": item.get("tag"),
            "type": item.get("type"),
            "answer_check": item.get(ai_exercise_service.ANSWER_CHECK_KEY),
        }
        for item in exercises
    ]
    db.add(
        AiMessage(
            session_id=session.id,
            role="assistant",
            content=f"{EXERCISE_PREFIX}{json.dumps(meta, ensure_ascii=False)}",
        )
    )

    # ---- DNA-004 行为事件埋点：AI 练习题展示 = QUESTIONVIEW（学习次数口径）----
    # 挂点理由：本函数出参 exercises 由前端原样渲染成练习卡，是 AI 链路唯一
    # 的题目展示路径。只对**落库成功**的题埋点——question_id 为 None 时无实体
    # 可归因，跳过而非编造（对齐 persist_exercises 的降级口径）。
    view_ids = [qid for qid in question_ids if qid is not None]
    view_kp: Dict[uuid.UUID, Optional[uuid.UUID]] = {}
    if view_ids:
        view_kp = {
            qid: kp
            for qid, kp in db.execute(
                select(Question.id, Question.kp_id).where(Question.id.in_(view_ids))
            ).all()
        }
    for item, qid in zip(exercises, question_ids):
        if qid is None:
            continue
        _emit_chat_dna_event(
            db,
            student_id=session.student_id,
            event_type="QUESTIONVIEW",
            question_id=qid,
            knowledge_point_id=view_kp.get(qid),
            meta={
                "session_id": str(session.id),
                "channel": "ai_exercise",
                "role": item.get("role"),
                "tag": item.get("tag"),
            },
        )

    # T04B 状态机：已出题进入练习阶段
    session.session_state = SESSION_STATE_PRACTICING
    session.summary_text = None
    session.state_updated_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "exercises": exercises,
        "weak_tags": weak["weak_tags"],
        "weak_source": weak["source"],
    }


__all__ = [
    "start_session",
    "start_session_async",
    "continue_session",
    "continue_session_async",
    "generate_exercise",
    "generate_exercise_async",
    "ANALYSIS_PREFIX",
    "EXERCISE_PREFIX",
    "EXERCISE_GUIDE_PREFIX",
    "EXERCISE_JUDGE_PREFIX",
]

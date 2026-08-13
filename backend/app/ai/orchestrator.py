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
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import intent  # T2 意图识别：mode 的唯一定稿处
from app.ai.agents import diagnose, exercise, question, tutor  # noqa: F401  (diagnose 供上层复用)
from app.ai.agents._json_utils import extract_json as _extract_json
from app.ai.ark_client import ArkError
from app.ai.guards import check_ai_quota
from app.ai.intent import MODE_DEMO, MODE_GUIDE
from app.ai.state_machine import (  # T04B 状态机
    SESSION_STATE_GUIDING,
    SESSION_STATE_INTRO,
    SESSION_STATE_PRACTICING,
)
from app.core.config import settings
from app.core.response import ApiError, CODE_BAD_REQUEST
from app.db import SessionLocal  # noqa: F401  (只读脚本按需自建会话，写操作一律用传入的 db)
from app.models.ai import AiMessage, AiSession

logger = logging.getLogger("app.ai.orchestrator")

#: 分析结论写进 ai_messages 时的前缀（generate_exercise 靠它回捞 JSON）
ANALYSIS_PREFIX: str = "分析："

#: AI 练习题元数据写进 ai_messages 时的前缀（``guide_exercise`` 靠它校验归属）
#:
#: 与 ``ANALYSIS_PREFIX`` 同款回捞惯例，content 形如::
#:
#:     练习题：[{"question_id": "...", "role": "fix", "tag": "..."}]
EXERCISE_PREFIX: str = "练习题："

#: 分析结论解析不出时的兜底
DEFAULT_KP: str = "综合"
DEFAULT_DIFFICULTY: int = 3
DEFAULT_ERROR_TYPE: str = "概念混淆"


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
    session = db.execute(select(AiSession).where(AiSession.id == sid)).scalar_one_or_none()
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


def start_session(
    *,
    student_id: Any,
    image_b64: Optional[str],
    text: Optional[str],
    db: Session,
) -> Dict[str, Any]:
    """开启一次 AI 辅导：读题分析 + 首轮引导，并落库会话与两条 assistant 消息。

    Args:
        student_id: 学生 ID（``uuid.UUID`` 或可转换字符串）。
        image_b64: 题目照片 base64 / data URL，可为空。
        text: 题目文字，可为空（与 ``image_b64`` 不能同时为空）。
        db: 调用方传入的 SQLAlchemy 会话（本函数内 ``commit``）。

    Returns:
        ``{"session_id": str, "analysis": dict, "first_question": str}``。

    Raises:
        ApiError: 400 题目为空 / 参数非法；429 今日额度已用完。
        ArkError: 方舟调用失败或返回无法解析。
    """
    sid = _to_uuid(student_id, "student_id")
    check_ai_quota(sid, db)

    analysis = question.analyze_question(text=text, image_b64=image_b64)

    session = AiSession(student_id=sid, question_id=None, status="active")
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
    first_question = tutor.next_question(session_messages=history, state=SESSION_STATE_INTRO)

    db.add(
        AiMessage(
            session_id=session.id,
            role="assistant",
            content=f"{ANALYSIS_PREFIX}{json.dumps(analysis, ensure_ascii=False)}",
        )
    )
    db.add(AiMessage(session_id=session.id, role="assistant", content=first_question))
    # T04B 状态机：首问后即进入引导阶段，记为第 1 步
    session.session_state = SESSION_STATE_GUIDING
    session.step = 1
    session.state_updated_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "session_id": str(session.id),
        "analysis": analysis,
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

    reply = tutor.next_question(
        session_messages=history + [{"role": "user", "content": content}],
        state=state,
        mode=active_mode,
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

    db.add(AiMessage(session_id=session.id, role="user", content=content))
    db.add(AiMessage(session_id=session.id, role="assistant", content=reply))
    # T04B 状态机：步数 +1；开场首轮后即转入引导阶段
    session.step = step + 1
    if state == SESSION_STATE_INTRO:
        session.session_state = SESSION_STATE_GUIDING
    session.state_updated_at = datetime.now(timezone.utc)
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
        )

    final_text, _quality = explanation_quality.ensure_explanation_quality(
        question=first_user,
        student_attempt=student_text,
        ai_explanation=reply,
        error_type=error_type,
        regenerate=_regenerate,
        threshold=threshold,
        max_retries=1,
    )
    return final_text


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
    meta = [
        {
            "question_id": item.get("question_id"),
            "role": item.get("role"),
            "tag": item.get("tag"),
            "type": item.get("type"),
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

    # T04B 状态机：已出题进入练习阶段
    session.session_state = SESSION_STATE_PRACTICING
    session.state_updated_at = datetime.now(timezone.utc)
    db.commit()
    # weak_tags / weak_source 随响应回带，供前端渲染薄弱点横幅（snake_case 透传）
    return {
        "exercises": exercises,
        "weak_tags": weak["weak_tags"],
        "weak_source": weak["source"],
    }


__all__ = [
    "start_session",
    "continue_session",
    "generate_exercise",
    "ANALYSIS_PREFIX",
    "EXERCISE_PREFIX",
]

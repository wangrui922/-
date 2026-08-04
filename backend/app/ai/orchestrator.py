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

from app.ai.agents import diagnose, exercise, question, tutor  # noqa: F401  (diagnose 供上层复用)
from app.ai.agents._json_utils import extract_json as _extract_json
from app.ai.ark_client import ArkError
from app.ai.guards import check_ai_quota
from app.ai.state_machine import (  # T04B 状态机
    SESSION_STATE_GUIDING,
    SESSION_STATE_INTRO,
    SESSION_STATE_PRACTICING,
)
from app.core.response import ApiError, CODE_BAD_REQUEST
from app.db import SessionLocal  # noqa: F401  (只读脚本按需自建会话，写操作一律用传入的 db)
from app.models.ai import AiMessage, AiSession

logger = logging.getLogger("app.ai.orchestrator")

#: 分析结论写进 ai_messages 时的前缀（generate_exercise 靠它回捞 JSON）
ANALYSIS_PREFIX: str = "分析："

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


def continue_session(*, session_id: Any, user_text: str, db: Session) -> Dict[str, Any]:
    """多轮辅导：带着完整历史再问一句，并落库 user / assistant 两条消息。

    Args:
        session_id: 会话 ID。
        user_text: 孩子这一轮说的话。
        db: 调用方传入的 SQLAlchemy 会话（本函数内 ``commit``）。

    Returns:
        ``{"reply": str}``。

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
    reply = tutor.next_question(
        session_messages=history + [{"role": "user", "content": content}],
        state=state,
    )

    db.add(AiMessage(session_id=session.id, role="user", content=content))
    db.add(AiMessage(session_id=session.id, role="assistant", content=reply))
    # T04B 状态机：步数 +1；开场首轮后即转入引导阶段
    session.step = step + 1
    if state == SESSION_STATE_INTRO:
        session.session_state = SESSION_STATE_GUIDING
    session.state_updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"reply": reply}


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
    """基于会话的分析结论生成两道练习题（**不落库**，由上层决定是否入题库）。

    Args:
        session_id: 会话 ID。
        db: 调用方传入的 SQLAlchemy 会话（本函数只读）。

    Returns:
        ``{"exercises": list[dict]}``，元素含 ``q/a/type/knowledge/difficulty/role/tag``。

    Raises:
        ApiError: 400 会话不存在。
        ArkError: 方舟调用失败或返回无法解析。
    """
    session = _get_session(session_id, db)
    messages = _load_messages(session.id, db)

    analysis = _pick_analysis(messages)
    kp = str(analysis.get("knowledge") or "").strip() or DEFAULT_KP
    error_type = str(analysis.get("error") or "").strip() or DEFAULT_ERROR_TYPE
    try:
        difficulty = int(float(str(analysis.get("difficulty")).strip()))
    except (TypeError, ValueError):
        difficulty = DEFAULT_DIFFICULTY

    exercises = exercise.gen_exercises(error_type=error_type, kp=kp, difficulty=difficulty)

    # T04B 状态机：已出题进入练习阶段
    session.session_state = SESSION_STATE_PRACTICING
    session.state_updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"exercises": exercises}


__all__ = ["start_session", "continue_session", "generate_exercise", "ANALYSIS_PREFIX"]

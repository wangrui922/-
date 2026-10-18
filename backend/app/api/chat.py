"""AI 辅导对话接口（T03 · 3.4）。

    POST /api/chat                       拍题开场：读题分析 + 首轮苏格拉底引导
    POST /api/chat/{session_id}/message  多轮引导：带完整历史继续对话

硬约定：
    * 两个接口均 ``Depends(get_current_user)``，且要求 ``user.sid`` 非空（未绑定学生返 400）；
    * ``orchestrator`` 的 ``*_async`` 编排是**纯 async**（内部 await async Agent），
      路由直接 ``await``，不再包 ``run_in_threadpool``；
    * 响应恒为 ``{"code":0,"msg":"ok","data":{}}``，data 直接透传编排器结果
      （``session_id`` / ``analysis`` / ``first_question``，见 docs/v4-db-api.md §B.2）；
    * 低置信度重拍（``CONFIDENCE_RETHRESHOLD`` 灰度开启时）：data 为
      ``{"need_rephoto":true,"confidence","message"}``，仍是 HTTP 200 + code 0
      的正常业务结果（不是错误码），前端据此引导重新拍照、不创建会话。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.ai import orchestrator
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_BAD_REQUEST, ok
from app.db import get_db
from app.schemas.common import BaseSchema
from app.services.learning_service import require_student_id, to_uuid

router = APIRouter(tags=["chat"])


# ---------------------------------------------------------------- 出入参模型
class ChatCreate(BaseSchema):
    """``POST /api/chat`` 请求体（camelCase 入参）。

    Attributes:
        image: 题目照片，``data:image/jpeg;base64,...`` 或纯 base64，可为空。
        text: 题目文字 / 孩子的手写解答，可为空（与 ``image`` 不能同时为空）。
        client_event_id: 客户端幂等键（别名 ``clientEventId``），预留给重试去重。
    """

    image: Optional[str] = None
    text: Optional[str] = None
    client_event_id: Optional[str] = None
    image_key: Optional[str] = None


class ChatMessage(BaseSchema):
    """``POST /api/chat/{session_id}/message`` 请求体。

    Attributes:
        text: 孩子这一轮说的话。
        mode: 前端快捷按钮显式指定的响应模式（``GUIDE``/``HINT``/``DEMO``/``JUDGE``）。
            **可选**：自然输入时省略，由后端 ``intent.detect_mode()`` 自动判定；
            非法值一律静默降级为自动判定，不报错（保证旧版前端零感知）。
        client_event_id: 客户端幂等键（别名 ``clientEventId``）。
    """

    text: Optional[str] = None
    mode: Optional[str] = None
    client_event_id: Optional[str] = None


def _strip_data_url(raw: Optional[str]) -> Optional[str]:
    """把 ``data:image/png;base64,xxxx`` 剥成纯 base64；非 data URL 原样返回。

    Args:
        raw: 前端传来的图片字段。

    Returns:
        纯 base64 字符串；输入为空或剥完为空时返回 ``None``。
    """
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("data:"):
        _, _, payload = value.partition(",")
        return payload.strip() or None
    return value


# -------------------------------------------------------------------- 路由
@router.post("/chat")
async def create_chat(
    body: ChatCreate = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """拍题开场：读题分析 + 首轮引导，并落库 ``ai_sessions`` / ``ai_messages``。

    Returns:
        ``{"code":0,"msg":"ok","data":{"session_id","analysis","first_question"}}``；
        低置信度重拍时 data 为 ``{"need_rephoto":true,"confidence","message"}``
        （HTTP 200 + code 0，前端引导重新拍照，不创建会话）。

    Raises:
        ApiError: 400 未绑定学生 / 题目为空；429 今日 AI 额度已用完。
    """
    student_id = require_student_id(current.sid)
    image_b64 = _strip_data_url(body.image)
    text = (body.text or "").strip() or None
    if not image_b64 and not text:
        raise ApiError(CODE_BAD_REQUEST, "请上传题目照片或输入题目文字")

    result = await orchestrator.start_session_async(
        student_id=student_id,
        image_b64=image_b64,
        text=text,
        image_key=body.image_key,
        db=db,
    )
    return ok(result)


@router.post("/chat/{session_id}/message")
async def send_message(
    session_id: str,
    body: ChatMessage = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """多轮引导：把孩子这一轮的话接进会话，返回下一句引导。

    Returns:
        ``{"code":0,"msg":"ok","data":{"reply","mode","next_mode"}}``。
        ``mode`` 是本轮实际生效的响应模式（前端渲染指示器用），
        ``next_mode`` 是下一轮建议默认模式（讲完一轮自动回切 ``GUIDE``）。

    Raises:
        ApiError: 400 未绑定学生 / 会话不存在 / 输入为空；429 今日 AI 额度已用完。
    """
    require_student_id(current.sid)

    result = await orchestrator.continue_session_async(
        session_id=to_uuid(session_id, "session_id"),
        user_text=body.text or "",
        mode=body.mode,
        db=db,
    )
    return ok(result)


__all__ = ["router", "ChatCreate", "ChatMessage"]

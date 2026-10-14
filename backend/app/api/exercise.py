"""AI 练习题接口（T03 · 3.4；T6/T7 扩充对话式引导与判错入本）。

    POST /api/exercise/gen    基于会话的分析结论，生成修复题 + 迁移题
    POST /api/exercise/guide  练习题的对话式逐步引导（多轮）
    POST /api/exercise/judge  提交最终答案，判对错 + 判错即时入本

硬约定：
    * 需要登录且 ``user.sid`` 非空；
    * ``orchestrator`` / ``ai_exercise_service`` 的 ``*_async`` 版本均为**纯 async**
      （内部 await async Agent），路由直接 ``await``，不再包 ``run_in_threadpool``；
    * 生成结果按 D1 落成 ``status='archived'`` + ``source_doc='ai_gen'`` 的归档题
      （**不会**进入题库抽题池），响应里回带 ``questionId``，供后续引导 / 判错入本
      挂外键；错题建档由 ``POST /api/exercise/judge`` 自动完成，前端无需再调
      ``POST /api/mistake``。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from app.ai import orchestrator
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_BAD_REQUEST, ok
from app.db import get_db
from app.schemas.common import BaseSchema
from app.services import ai_exercise_service
from app.services.learning_service import require_student_id, to_uuid

router = APIRouter(tags=["exercise"])


class ExerciseGen(BaseSchema):
    """``POST /api/exercise/gen`` 请求体。

    Attributes:
        session_id: 辅导会话 ID（别名 ``sessionId``）。
        client_event_id: 客户端幂等键（别名 ``clientEventId``）。
    """

    session_id: str
    client_event_id: Optional[str] = None


class ExerciseGuide(BaseSchema):
    """``POST /api/exercise/guide`` 请求体。

    Attributes:
        session_id: 辅导会话 ID（别名 ``sessionId``）。
        question_id: 练习题 ID（别名 ``questionId``）。
        work: 孩子当前写下的过程，可为空。
        mode: 前端快捷按钮显式指定的模式，非法值降级为自动识别。
    """

    session_id: str
    question_id: str
    work: Optional[str] = None
    mode: Optional[str] = None
    work_image: Optional[str] = None
    work_image_key: Optional[str] = None


class ExerciseJudge(BaseSchema):
    """``POST /api/exercise/judge`` 请求体。

    Attributes:
        session_id: 辅导会话 ID（别名 ``sessionId``）。
        question_id: 练习题 ID（别名 ``questionId``）。
        answer: 孩子提交的最终答案。
        work: 孩子的解题过程，可为空。
    """

    session_id: str
    question_id: str
    answer: Optional[str] = None
    work: Optional[str] = None
    work_image: Optional[str] = None
    work_image_key: Optional[str] = None
    work_image_keys: List[str] = Field(default_factory=list)
    final_confirmed: bool = False


class ExerciseSummary(BaseSchema):
    """``POST /api/exercise/summary`` 请求体。"""

    session_id: str


def _strip_data_url(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    return value.strip() or None


@router.post("/exercise/gen")
async def gen_exercise(
    body: ExerciseGen = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """基于会话分析结论生成两道练习题（生成即落库为归档题）。

    Returns:
        ``{"code":0,"msg":"ok","data":{"exercises":[
        {questionId,q,a,type,knowledge,difficulty,role,tag}],
        "weak_tags":[{"code","label"}], "weak_source":"profile"|"fallback"}}``。

        ``weak_tags`` / ``weak_source`` 由 ``orchestrator.generate_exercise()``
        一并回带（定向推题）：``weak_source="profile"`` 表示该批练习专门 drill 的
        历史薄弱错因；``"fallback"`` 表示画像为空、仅按当前错题类型练习。
        两字段均向后兼容（老客户端忽略即可，不崩）。

    Raises:
        ApiError: 400 未绑定学生 / 会话不存在 / sessionId 非法。
    """
    require_student_id(current.sid)

    result = await orchestrator.generate_exercise_async(
        session_id=to_uuid(body.session_id, "sessionId"),
        db=db,
    )
    return ok(result)


@router.post("/exercise/guide")
async def guide_exercise(
    body: ExerciseGuide = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """AI 练习题的对话式逐步引导（可多轮往复）。

    Returns:
        ``{"code":0,"msg":"ok","data":{question_id,reply,ask_final_confirm,
        verdict,error_type,mode,prompt_version}}``。

    Raises:
        ApiError: 400 未绑定学生 / 题目不属于本会话；404 会话或题目不存在；
            500 方舟调用失败。
    """
    require_student_id(current.sid)

    result = await ai_exercise_service.guide_exercise_async(
        db,
        student_id=current.sid,
        session_id=to_uuid(body.session_id, "sessionId"),
        question_id=to_uuid(body.question_id, "questionId"),
        work=body.work,
        work_image_b64=_strip_data_url(body.work_image),
        work_image_key=body.work_image_key,
        mode=body.mode,
    )
    return ok(result)


@router.post("/exercise/judge")
async def judge_exercise(
    body: ExerciseJudge = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交练习题的最终答案：判对错，判错则即时写入错题本。

    Returns:
        ``{"code":0,"msg":"ok","data":{question_id,correct,verdict_text,
        error_type,mistake_id,mastery_refreshed}}``。

    Raises:
        ApiError: 400 未绑定学生 / 题目不属于本会话；404 会话或题目不存在；
            500 方舟调用失败。
    """
    require_student_id(current.sid)
    if not body.final_confirmed:
        raise ApiError(CODE_BAD_REQUEST, "先确认这道题已经答完，再请老师判断最终答案")

    result = await ai_exercise_service.judge_and_record_async(
        db,
        student_id=current.sid,
        session_id=to_uuid(body.session_id, "sessionId"),
        question_id=to_uuid(body.question_id, "questionId"),
        answer=body.answer,
        work=body.work,
        work_image_b64=_strip_data_url(body.work_image),
        work_image_keys=list(dict.fromkeys([
            *(body.work_image_keys or []),
            *([body.work_image_key] if body.work_image_key else []),
        ])),
    )
    return ok(result)


@router.post("/exercise/summary")
async def summarize_exercise(
    body: ExerciseSummary = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """两道相似题都最终判定后生成一次学习小结。"""
    require_student_id(current.sid)
    result = await ai_exercise_service.generate_summary_async(
        db,
        student_id=current.sid,
        session_id=to_uuid(body.session_id, "sessionId"),
    )
    return ok(result)


__all__ = ["router", "ExerciseGen", "ExerciseGuide", "ExerciseJudge", "ExerciseSummary"]

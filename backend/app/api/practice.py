"""题库练习接口（题库消费层 · B4）。

    POST /api/practice/session  建会话 / 抽题
    POST /api/practice/answer   提交单题作答（L1 规则判分 + attempts 落库）
    POST /api/practice/reveal   看参考答案（Q3 分级解锁，仅做错的题）
    POST /api/practice/finish   结算（错题入本 + 掌握度缓存失效 + 统计）
    POST /api/practice/guide    让老师带我想一想（AI 过程引导）

⚠️ 端点命名以 ``docs/exercise-bank-consumer-design.md`` §接口契约为准：
``/session``、``/answer``（**不是** ``/draw``、``/submit``），其余三个不变。
前端按这套命名对接，改名请先改设计文档。

硬约定：
    * 需要登录且 ``user.sid`` 非空（``require_student_id`` 返 400，沿用 T03 口径）；
    * 出参一律 **snake_case**，由 ``app.core.response.ok()`` 包裹（C10）；
    * ``session``/``answer``/``reveal``/``finish`` 是纯本地计算，用 sync ``def`` 路由，
      FastAPI 会自动丢线程池执行，不阻塞事件循环；
    * 只有 ``guide`` 内含**同步阻塞**的方舟调用，必须写成 async 路由并用
      ``run_in_threadpool`` 包裹（与 ``api/chat.py``、``api/exercise.py`` 一致）；
    * 别人的练习会话一律按 **404** 处理（不返 403），不泄露资源存在性。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.db import get_db
from app.schemas.practice import (
    DrawRequest,
    GuideRequest,
    QuestionRefRequest,
    SessionRequest,
    SubmitRequest,
)
from app.services import practice_service
from app.services.learning_service import require_student_id

logger = logging.getLogger("app.api.practice")

router = APIRouter(prefix="/practice", tags=["practice"])


@router.post("/session")
def create_practice_session(
    body: DrawRequest = Body(default_factory=DrawRequest),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """抽题并开一个练习会话。

    Returns:
        ``{"code":0,"msg":"ok","data":{session_id,questions,pool_size,unmapped_count,auto_relaxed,requested_count}}``。

    Raises:
        ApiError: 400 未绑定学生 / 参数非法；404 该筛选条件下无可练题目。
    """
    student_id = require_student_id(current.sid)
    data = practice_service.draw_questions(
        db,
        student_id=student_id,
        grade=body.grade,
        edition=body.edition,
        section=body.section,
        kp_id=body.kp_id,
        difficulty=body.difficulty,
        count=body.count,
    )
    return ok(data)


@router.post("/answer")
def answer(
    body: SubmitRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """提交单题作答，返回 L1 判分结论。

    Returns:
        ``{"code":0,"msg":"ok","data":{question_id,is_correct,level,reason,duration_sec}}``。

    Raises:
        ApiError: 400 未绑定学生 / 题目不在本次练习中；404 会话或题目不存在。
    """
    student_id = require_student_id(current.sid)
    data = practice_service.submit_answer(
        db,
        student_id=student_id,
        session_id=body.session_id,
        question_id=body.question_id,
        selected_answer=body.selected_answer,
        duration_sec=body.duration_sec,
        client_event_id=body.client_event_id,
    )
    return ok(data)


@router.post("/reveal")
def reveal(
    body: QuestionRefRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """看参考答案（Q3 分级解锁：只对做错的题开放）。

    Returns:
        ``{"code":0,"msg":"ok","data":{question_id,correct_answer,analysis}}``。

    Raises:
        ApiError: 400 还没作答；403 这道题已经做对；404 会话或题目不存在。
    """
    student_id = require_student_id(current.sid)
    data = practice_service.reveal_answer(
        db,
        student_id=student_id,
        session_id=body.session_id,
        question_id=body.question_id,
    )
    return ok(data)


@router.post("/finish")
def finish(
    body: SessionRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """结算本次练习：错题入本 + 掌握度缓存失效 + 统计。

    Returns:
        ``{"code":0,"msg":"ok","data":{session_id,total,correct,wrong,mistakes_created,
        mastery_refreshed,answered,accuracy,unmapped_count,items,weak_points}}``。

    Raises:
        ApiError: 400 未绑定学生；404 会话不存在或不属于当前学生。
    """
    student_id = require_student_id(current.sid)
    data = practice_service.finish_session(
        db,
        student_id=student_id,
        session_id=body.session_id,
    )
    return ok(data)


@router.post("/guide")
async def guide(
    body: GuideRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """「让老师带我想一想」：AI 过程引导，首轮不公布答案。

    ``practice_service.guide_question`` 内部是**同步阻塞**的方舟调用，
    这里必须用 ``run_in_threadpool`` 包裹，避免占满事件循环。

    Returns:
        ``{"code":0,"msg":"ok","data":{question_id,reply,ask_final_confirm,prompt_version}}``。

    Raises:
        ApiError: 400 题目不在本次练习中；404 会话或题目不存在；500 方舟调用失败。
    """
    student_id = require_student_id(current.sid)
    data = await run_in_threadpool(
        practice_service.guide_question,
        db,
        student_id=student_id,
        session_id=body.session_id,
        question_id=body.question_id,
        work=body.work,
    )
    return ok(data)


__all__ = ["router"]

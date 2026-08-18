"""Makers 练习云函数入口（FastAPI / ASGI）。

挂载路径：Makers 把 ``cloud-functions/api/index.py`` 的 ``app`` 暴露为 ``/api``，
因此本文件里的 ``/practice/session`` 实际对外是 ``/api/practice/session``，
与前端 ``src/api/practice.ts`` 的调用路径**完全一致**（前端 baseURL 已是 ``/api``）。

响应外壳严格对齐本地后端 ``app.core.response``：
    {"code": 0, "msg": "ok", "data": {...}}
HTTP 状态码与 ``code`` 同步（成功 200，404 未找到，403 禁止，400 参数非法，500 故障）。
前端的 axios 拦截器（src/api/http.ts）按 body.code 拆封，因此这里既给正确外壳也给同步状态码。

路由（与本地 backend/app/api/practice.py 一一对应）：
    GET  /health                 健康检查
    POST /practice/session       抽题建会话
    POST /practice/answer        提交单题作答（L1 规则判分）
    POST /practice/reveal        看参考答案（只对做错的题开放，服务端复核）
    POST /practice/finish        结算（stateless，从前端作答列表重算）
    POST /practice/guide         「让老师带我想一想」（AI 过程引导，慢）

⚠️ 本文件不依赖任何数据库 / Blob：题库来自同目录 ``bank_data.py``（内存），
判分 / 抽题 / 结算逻辑全部在 ``bank.py``。扩展知识点持久化请走 Node 函数 ``progress.js``。
"""
from __future__ import annotations

import logging
import traceback
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

import bank

logger = logging.getLogger("makers.index")

app = FastAPI(title="错题小老师 · 练习云函数", version="serverless-1.0.0")

# 同源部署（前端 /api 直连）无需 CORS；放开便于本地联调与后续跨域灰度。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================================================================= 响应封装
class ApiError(Exception):
    def __init__(self, code: int, msg: str, data: Any = None):
        self.code = code
        self.msg = msg
        self.data = data if data is not None else {}


def ok(data: Any = None) -> Dict[str, Any]:
    return {"code": 0, "msg": "ok", "data": data if data is not None else {}}


def _http_status_for(code: int) -> int:
    if code == 0:
        return 200
    if code in (400, 401, 403, 404, 429, 500):
        return code
    return 500


def _json(code: int, msg: str, data: Any) -> JSONResponse:
    return JSONResponse(status_code=_http_status_for(code), content={"code": code, "msg": msg, "data": data})


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    logger.info("ApiError %s %s -> %s %s", request.method, request.url.path, exc.code, exc.msg)
    return _json(exc.code, exc.msg, exc.data)


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = exc.status_code if exc.status_code in (400, 401, 403, 404, 429, 500) else 500
    detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
    return _json(code, detail, {})


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _json(400, "参数非法", {"errors": exc.errors()})


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled %s %s: %s\n%s", request.method, request.url.path, exc, traceback.format_exc())
    return _json(500, "服务开小差了，请稍后再试", {})


# ================================================================= 请求模型
class SessionBody(BaseModel):
    grade: Optional[int] = None
    grade_band: Optional[str] = None
    edition: Optional[str] = None
    section: Optional[str] = None
    kp_id: Optional[str] = None
    difficulty: Optional[int] = None
    count: Optional[int] = None


class AnswerBody(BaseModel):
    session_id: Optional[str] = None
    question_id: str
    selected_answer: Optional[str] = None
    duration_sec: Optional[int] = None
    client_event_id: Optional[str] = None


class RevealBody(BaseModel):
    session_id: Optional[str] = None
    question_id: str
    selected_answer: Optional[str] = None


class FinishBody(BaseModel):
    session_id: Optional[str] = None
    answers: List[Dict[str, Any]] = Field(default_factory=list)


class GuideBody(BaseModel):
    session_id: Optional[str] = None
    question_id: str
    work: Optional[str] = None


# ================================================================= 路由
@app.get("/health")
async def health() -> Dict[str, Any]:
    return ok({"status": "ok", "bank_size": len(bank.bank().by_id)})


@app.post("/practice/session")
async def practice_session(body: SessionBody) -> Dict[str, Any]:
    data = bank.draw_questions(
        grade=body.grade,
        grade_band=body.grade_band,
        edition=body.edition,
        section=body.section,
        kp_id=body.kp_id,
        difficulty=body.difficulty,
        count=body.count,
    )
    return ok(data)


@app.post("/practice/answer")
async def practice_answer(body: AnswerBody) -> Dict[str, Any]:
    data = bank.submit_answer(
        question_id=body.question_id,
        selected_answer=body.selected_answer,
        duration_sec=body.duration_sec,
    )
    return ok(data)


@app.post("/practice/reveal")
async def practice_reveal(body: RevealBody) -> Dict[str, Any]:
    data = bank.reveal_answer(
        question_id=body.question_id,
        selected_answer=body.selected_answer,
    )
    return ok(data)


@app.post("/practice/finish")
async def practice_finish(body: FinishBody) -> Dict[str, Any]:
    if not body.answers:
        raise ApiError(400, "结算需要作答列表（前端请带 answers）")
    data = bank.finish_session(answers=body.answers)
    return ok(data)


@app.post("/practice/guide")
async def practice_guide(body: GuideBody) -> Dict[str, Any]:
    # 引导走大模型，慢；放到线程池避免阻塞事件循环
    data = await run_in_threadpool(
        bank.guide_question, question_id=body.question_id, work=body.work
    )
    return ok(data)

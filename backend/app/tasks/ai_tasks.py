"""Celery 异步任务定义（AI 调用队列）。

所有 "慢调用"（方舟 AI、文件分析等）走 Celery worker 执行，前端通过轮询
``GET /api/tasks/{task_id}`` 获取结果。

队列设计：
    * ai_chat  - AI 辅导对话（开场 / 多轮）
    * ai_gen   - 练习题生成
    * ai_guide - 练习题引导
    * ai_judge - 练习题判错
    * ai_summary - 学习小结生成
    * ocr      - OCR 图片识别（预留）
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.core.celery_app import celery_app
from app.db import SessionLocal
from app.services.learning_service import to_uuid as _to_uuid

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------- 工具

def _db_session():
    """任务函数里手动管理 Session（Celery 无 FastAPI 依赖注入）。"""
    return SessionLocal()

# ---------------------------------------------------------------------- 任务定义

@celery_app.task(name="ai_chat_start", bind=True, max_retries=3)
def ai_chat_start(
    self,
    student_id_str: str,
    image_b64: Optional[str] = None,
    text: Optional[str] = None,
    image_key: Optional[str] = None,
) -> Dict[str, Any]:
    """异步执行 AI 辅导开场（拍题 + 首轮引导）。"""
    import app.ai.orchestrator as orchestrator

    db = _db_session()
    try:
        student_id = _to_uuid(student_id_str, "student_id")
        result = asyncio.run(orchestrator.start_session_async(
            student_id=student_id,
            image_b64=image_b64,
            text=text,
            image_key=image_key,
            db=db,
        ))
        db.commit()
        return {"status": "ok", "result": result}
    except Exception as exc:
        db.rollback()
        logger.warning("ai_chat_start failed (retry %s): %s", self.request.retries, exc)
        try:
            self.retry(countdown=10 * (self.request.retries + 1))
        except self.MaxRetriesExceededError:
            return {"status": "error", "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="ai_chat_message", bind=True, max_retries=3)
def ai_chat_message(
    self,
    session_id_str: str,
    user_text: str,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """异步执行 AI 辅导多轮对话。"""
    import app.ai.orchestrator as orchestrator

    db = _db_session()
    try:
        session_id = _to_uuid(session_id_str, "session_id")
        result = asyncio.run(orchestrator.continue_session_async(
            session_id=session_id,
            user_text=user_text,
            mode=mode,
            db=db,
        ))
        db.commit()
        return {"status": "ok", "result": result}
    except Exception as exc:
        db.rollback()
        logger.warning("ai_chat_message failed (retry %s): %s", self.request.retries, exc)
        try:
            self.retry(countdown=10 * (self.request.retries + 1))
        except self.MaxRetriesExceededError:
            return {"status": "error", "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="ai_exercise_gen", bind=True, max_retries=3)
def ai_exercise_gen(
    self,
    session_id_str: str,
) -> Dict[str, Any]:
    """异步执行练习题生成。"""
    import app.ai.orchestrator as orchestrator

    db = _db_session()
    try:
        session_id = _to_uuid(session_id_str, "session_id")
        result = asyncio.run(orchestrator.generate_exercise_async(
            session_id=session_id,
            db=db,
        ))
        db.commit()
        return {"status": "ok", "result": result}
    except Exception as exc:
        db.rollback()
        logger.warning("ai_exercise_gen failed (retry %s): %s", self.request.retries, exc)
        try:
            self.retry(countdown=10 * (self.request.retries + 1))
        except self.MaxRetriesExceededError:
            return {"status": "error", "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="ai_exercise_guide", bind=True, max_retries=3)
def ai_exercise_guide(
    self,
    student_id_str: str,
    session_id_str: str,
    question_id_str: str,
    work: Optional[str] = None,
    work_image_b64: Optional[str] = None,
    work_image_key: Optional[str] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """异步执行练习题引导。"""
    from app.services import ai_exercise_service

    db = _db_session()
    try:
        student_id = _to_uuid(student_id_str, "student_id")
        session_id = _to_uuid(session_id_str, "session_id")
        question_id = _to_uuid(question_id_str, "question_id")
        result = asyncio.run(ai_exercise_service.guide_exercise_async(
            db,
            student_id=student_id,
            session_id=session_id,
            question_id=question_id,
            work=work,
            work_image_b64=work_image_b64,
            work_image_key=work_image_key,
            mode=mode,
        ))
        db.commit()
        return {"status": "ok", "result": result}
    except Exception as exc:
        db.rollback()
        logger.warning("ai_exercise_guide failed (retry %s): %s", self.request.retries, exc)
        try:
            self.retry(countdown=10 * (self.request.retries + 1))
        except self.MaxRetriesExceededError:
            return {"status": "error", "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="ai_exercise_judge", bind=True, max_retries=3)
def ai_exercise_judge(
    self,
    student_id_str: str,
    session_id_str: str,
    question_id_str: str,
    answer: Optional[str] = None,
    work: Optional[str] = None,
    work_image_b64: Optional[str] = None,
    work_image_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """异步执行练习题判错。"""
    from app.services import ai_exercise_service

    db = _db_session()
    try:
        student_id = _to_uuid(student_id_str, "student_id")
        session_id = _to_uuid(session_id_str, "session_id")
        question_id = _to_uuid(question_id_str, "question_id")
        result = asyncio.run(ai_exercise_service.judge_and_record_async(
            db,
            student_id=student_id,
            session_id=session_id,
            question_id=question_id,
            answer=answer,
            work=work,
            work_image_b64=work_image_b64,
            work_image_keys=work_image_keys or [],
        ))
        db.commit()
        return {"status": "ok", "result": result}
    except Exception as exc:
        db.rollback()
        logger.warning("ai_exercise_judge failed (retry %s): %s", self.request.retries, exc)
        try:
            self.retry(countdown=10 * (self.request.retries + 1))
        except self.MaxRetriesExceededError:
            return {"status": "error", "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="ai_exercise_summary", bind=True, max_retries=3)
def ai_exercise_summary(
    self,
    student_id_str: str,
    session_id_str: str,
) -> Dict[str, Any]:
    """异步执行学习小结生成。"""
    from app.services import ai_exercise_service

    db = _db_session()
    try:
        student_id = _to_uuid(student_id_str, "student_id")
        session_id = _to_uuid(session_id_str, "session_id")
        result = asyncio.run(ai_exercise_service.generate_summary_async(
            db,
            student_id=student_id,
            session_id=session_id,
        ))
        db.commit()
        return {"status": "ok", "result": result}
    except Exception as exc:
        db.rollback()
        logger.warning("ai_exercise_summary failed (retry %s): %s", self.request.retries, exc)
        try:
            self.retry(countdown=10 * (self.request.retries + 1))
        except self.MaxRetriesExceededError:
            return {"status": "error", "error": str(exc)}
    finally:
        db.close()


# ---------------------------------------------------------------------- 工具函数

def strip_data_url(raw: Optional[str]) -> Optional[str]:
    """把 data URL 剥成纯 base64。"""
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    return value.strip() or None

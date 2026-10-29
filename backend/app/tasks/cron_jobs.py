"""定时任务：每日/每周数据同步、清理。

注册点：app/main.py 启动时 APScheduler 调度（Worker 侧也会注册相同任务，
避免重复触发时只需在 compose 中只开一处调度）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

from sqlalchemy import delete

from app.db import SessionLocal
from app.models.ai import AiSession
from app.models.learning import StudentProfile
from app.services import report_service

logger = logging.getLogger(__name__)


def daily_sync() -> Dict[str, Any]:
    """每日同步：全量重算学生掌握度 + 周日生成周报。

    Returns:
        {"mastery_updated": int, "reports_generated": int}
    """
    db = SessionLocal()
    try:
        from app.services.profile_service import _compute_and_store

        students = db.query(StudentProfile).all()
        mastery_count = 0
        for s in students:
            try:
                _compute_and_store(db, s.student_id)
                mastery_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("refresh mastery for %s failed: %s", s.student_id, exc)
                db.rollback()
            # C02 Phase 2：六维分维批量重算落库（独立 try，失败不连累上面缓存落库）
            try:
                from app.services.student_dna_service import (
                    recompute_student_six_dim_mastery,
                )

                recompute_student_six_dim_mastery(db, student_id=s.student_id)
                db.flush()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "recompute six-dim mastery for %s failed: %s", s.student_id, exc
                )
                db.rollback()
        db.commit()

        # 周日生成全部学生的本周周报
        reports_count = 0
        if datetime.now(timezone.utc).weekday() == 6:  # Sunday
            for s in students:
                try:
                    report_service.generate_weekly_report(db, s.student_id)
                    reports_count += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("generate report for %s failed: %s", s.student_id, exc)
                    db.rollback()
            db.commit()

        return {"mastery_updated": mastery_count, "reports_generated": reports_count}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("daily_sync failed: %s", exc)
        raise
    finally:
        db.close()


def cleanup_old_sessions() -> int:
    """清理 30 天前的 AI 会话（保留错题、保留画像）。

    Returns:
        删除的会话数。
    """
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        result = db.execute(
            delete(AiSession).where(AiSession.state_updated_at < cutoff)
        )
        db.commit()
        deleted = getattr(result, "rowcount", 0) or 0
        logger.info("cleanup_old_sessions: deleted %s", deleted)
        return int(deleted)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("cleanup_old_sessions failed: %s", exc)
        raise
    finally:
        db.close()
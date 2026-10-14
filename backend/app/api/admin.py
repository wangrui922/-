"""管理后台 API（admin 专属接口）。

仅对 ``role == 'admin'`` 的用户开放；越权返 403。
当前覆盖：
    * 激活码 CRUD（生成、列表、绑定记录）
    * 学生/家长账户管理（列表、冻结/解冻、详情）
    * 错题/掌握度/练习统计看板
    * AI 会话调用日志（暂只列出会话与消息数，后续对接真实日志）
"""

from __future__ import annotations

import hashlib
import logging
import random
import string
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, require_role
from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_FORBIDDEN, CODE_NOT_FOUND, ok
from app.db import get_db
from app.models.activation import ActivationCode
from app.models.user import ROLE_VALUES, User, Student, GRADE_MIN, GRADE_MAX
from app.models.learning import Mistake, Attempt, LearningTask, StudentProfile
from app.models.student_mastery import StudentMastery
from app.models.weekly_report import WeeklyReport
from app.models.ai import AiSession, AiMessage
from app.schemas.admin import (
    ActivationCodeCreate,
    ActivationCodeItem,
    DashboardStats,
    UserListItem,
    UserListQuery,
)


_MASTERY_LEVEL_MAP = {0: "weak", 1: "weak", 2: "medium", 3: "strong"}


def _mastery_level_str(level: int) -> str:
    """0 未入门 / 1 薄弱 / 2 基本掌握 / 3 熟练 → weak / medium / strong。"""
    return _MASTERY_LEVEL_MAP.get(level, "weak")

logger = logging.getLogger("app.api.admin")

router = APIRouter(prefix="/admin", tags=["admin"])

# ---- 激活码 -----------------------------------------------------------------

ACTIVATION_CODE_PREFIX = "YY"
ACTIVATION_CODE_GROUPS = 4
ACTIVATION_CODE_GROUP_SIZE = 4


def _generate_activation_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    compact = ACTIVATION_CODE_PREFIX + "".join(
        random.choice(alphabet) for _ in range(ACTIVATION_CODE_GROUPS * ACTIVATION_CODE_GROUP_SIZE)
    )
    return "-".join(
        [compact[: len(ACTIVATION_CODE_PREFIX)]]
        + [
            compact[
                len(ACTIVATION_CODE_PREFIX) + i * ACTIVATION_CODE_GROUP_SIZE : len(ACTIVATION_CODE_PREFIX) + (i + 1) * ACTIVATION_CODE_GROUP_SIZE
            ]
            for i in range(ACTIVATION_CODE_GROUPS)
        ]
    )


def _activation_digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


@router.post("/activation-codes")
def create_activation_codes(
    body: ActivationCodeCreate,
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """批量生成激活码。

    Returns:
        ``{"code":0,"msg":"ok","data":{"created":int,"codes":[str]}}``。
    """
    count = max(1, min(body.count or 1, 500))
    label = (body.label or "").strip()[:50] or None
    codes: List[str] = []
    created = 0
    for _ in range(count):
        code = _generate_activation_code()
        digest = _activation_digest(code)
        # 避免哈希碰撞（概率极低，但保护一下）
        exists = db.execute(select(ActivationCode).where(ActivationCode.code_digest == digest)).scalar_one_or_none()
        if exists:
            continue
        row = ActivationCode(
            code_digest=digest,
            label=label,
            expires_at=body.expires_at,
        )
        db.add(row)
        created += 1
        codes.append(code)
    db.commit()
    return ok({"created": created, "codes": codes})


@router.get("/activation-codes")
def list_activation_codes(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    used: Optional[bool] = Query(None),
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """激活码分页列表。"""
    base = select(ActivationCode)
    if used is not None:
        base = base.where(
            ActivationCode.user_id.isnot(None) if used else ActivationCode.user_id.is_(None)
        )
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = db.execute(base.order_by(ActivationCode.created_at.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "id": str(r.id),
                    "label": r.label,
                    "used": bool(r.user_id),
                    "user_id": str(r.user_id) if r.user_id else None,
                    "activated_at": r.activated_at.isoformat() if r.activated_at else None,
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }
    )


# ---- 用户/学生管理 -----------------------------------------------------------

@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    role: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """用户分页列表（含学生档案扩展信息）。"""
    if role and role not in ROLE_VALUES:
        raise ApiError(CODE_BAD_REQUEST, f"角色只能是 {ROLE_VALUES}")
    base = select(User)
    if role:
        base = base.where(User.role == role)
    if keyword:
        kw = f"%{keyword}%"
        base = base.where((User.phone.like(kw)) | (User.nickname.like(kw)))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = db.execute(base.order_by(User.created_at.desc()).offset((page - 1) * size).limit(size)).scalars().all()

    items: List[Dict[str, Any]] = []
    for u in rows:
        item: Dict[str, Any] = {
            "id": str(u.id),
            "phone": u.phone,
            "nickname": u.nickname,
            "role": u.role,
            "status": u.status,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        if u.student:
            item["student"] = {
                "id": str(u.student.id),
                "grade": u.student.grade,
                "stage": u.student.stage,
                "daily_target": u.student.daily_target,
                "streak_days": u.student.streak_days,
                "total_points": u.student.total_points,
            }
        items.append(item)

    return ok({"total": total, "page": page, "size": size, "items": items})


@router.post("/users/{user_id}/freeze")
def freeze_user(
    user_id: str,
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """冻结账号。"""
    uid = uuid.UUID(user_id)
    user = db.get(User, uid)
    if not user:
        raise ApiError(CODE_NOT_FOUND, "用户不存在")
    user.status = "frozen"
    db.commit()
    return ok({"status": "frozen"})


@router.post("/users/{user_id}/unfreeze")
def unfreeze_user(
    user_id: str,
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """解冻账号。"""
    uid = uuid.UUID(user_id)
    user = db.get(User, uid)
    if not user:
        raise ApiError(CODE_NOT_FOUND, "用户不存在")
    user.status = "active"
    db.commit()
    return ok({"status": "active"})


# ---- 看板统计 ---------------------------------------------------------------

@router.get("/dashboard")
def dashboard_stats(
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """管理后台首页看板：总用户数、今日活跃、错题总数、AI 会话数。"""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_users = db.execute(select(func.count()).select_from(User)).scalar() or 0
    active_users = (
        db.execute(
            select(func.count())
            .select_from(User)
            .where(User.last_login_at >= today_start)
        ).scalar()
        or 0
    )
    total_mistakes = db.execute(select(func.count()).select_from(Mistake)).scalar() or 0
    total_ai_sessions = db.execute(select(func.count()).select_from(AiSession)).scalar() or 0
    total_ai_messages = db.execute(select(func.count()).select_from(AiMessage)).scalar() or 0
    total_attempts = db.execute(select(func.count()).select_from(Attempt)).scalar() or 0
    weekly_reports = db.execute(select(func.count()).select_from(WeeklyReport)).scalar() or 0

    return ok(
        {
            "total_users": total_users,
            "active_today": active_users,
            "total_mistakes": total_mistakes,
            "total_ai_sessions": total_ai_sessions,
            "total_ai_messages": total_ai_messages,
            "total_attempts": total_attempts,
            "weekly_reports": weekly_reports,
        }
    )


# ---- 错题与掌握度看板 -------------------------------------------------------

@router.get("/mistakes")
def list_mistakes(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    student_id: Optional[str] = Query(None),
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """错题列表（管理员视角）。"""
    base = select(Mistake)
    if student_id:
        base = base.where(Mistake.student_id == uuid.UUID(student_id))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = db.execute(base.order_by(Mistake.created_at.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "id": str(r.id),
                    "student_id": str(r.student_id),
                    "question_id": str(r.question_id) if r.question_id else None,
                    "error_type": r.error_type,
                    "status": "solved" if r.solved else "pending",
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }
    )


@router.get("/mastery")
def list_mastery(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    student_id: Optional[str] = Query(None),
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """学生掌握度列表。"""
    base = select(StudentMastery)
    if student_id:
        base = base.where(StudentMastery.student_id == uuid.UUID(student_id))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = db.execute(base.order_by(StudentMastery.computed_at.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return ok(
        {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "id": str(r.id),
                    "student_id": str(r.student_id),
                    "kp_id": str(r.knowledge_point_id) if r.knowledge_point_id else None,
                    "score": r.mastery_score,
                    "level": _mastery_level_str(r.mastery_level),
                    "attempts": r.attempts,
                    "updated_at": r.computed_at.isoformat() if r.computed_at else None,
                }
                for r in rows
            ],
        }
    )


# ---- AI 会话日志 ------------------------------------------------------------

@router.get("/ai-sessions")
def list_ai_sessions(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    student_id: Optional[str] = Query(None),
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """AI 辅导会话列表（含消息数统计）。"""
    base = select(AiSession)
    if student_id:
        base = base.where(AiSession.student_id == uuid.UUID(student_id))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = db.execute(base.order_by(AiSession.created_at.desc()).offset((page - 1) * size).limit(size)).scalars().all()

    items = []
    for r in rows:
        msg_count = (
            db.execute(
                select(func.count()).where(AiMessage.session_id == r.id)
            ).scalar()
            or 0
        )
        items.append(
            {
                "id": str(r.id),
                "student_id": str(r.student_id),
                "status": r.status,
                "msg_count": msg_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return ok({"total": total, "page": page, "size": size, "items": items})


# ---- FSRS 复习排期统计（方向三 · 教师端看板）-------------------------------

@router.get("/review-stats")
def review_stats(
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """复习系统全景统计：总卡数 / 今日到期 / 近30天复习 / 平均评分 / 状态分布。

    表 review_cards / review_logs 由 FSRS 模块以 raw SQL 维护（无 ORM 模型），
    这里沿用 text() 直查，避免为看板引入模型迁移。
    """
    now = datetime.now(timezone.utc)
    since_30 = now - timedelta(days=30)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    from sqlalchemy import text

    def scalar(q: str, **params):
        row = db.execute(text(q), params).fetchone()
        return row[0] if row and row[0] is not None else 0

    def has_table(name: str) -> bool:
        try:
            db.execute(text(f"SELECT 1 FROM {name} LIMIT 1")).fetchone()
            return True
        except Exception:
            return False

    total_cards = scalar("SELECT COUNT(*) FROM review_cards") if has_table("review_cards") else 0
    due_today = (
        scalar(
            "SELECT COUNT(*) FROM review_cards WHERE due <= :now",
            now=now,
        )
        if has_table("review_cards")
        else 0
    )
    reviews_30d = (
        scalar(
            "SELECT COUNT(*) FROM review_logs WHERE review_at >= :since",
            since=since_30,
        )
        if has_table("review_logs")
        else 0
    )
    avg_30d = (
        scalar(
            "SELECT AVG(rating) FROM review_logs WHERE review_at >= :since",
            since=since_30,
        )
        if has_table("review_logs")
        else 0
    )
    # 状态分布：0=未学 1=学习中 2=已掌握 3=重学
    state_rows = (
        db.execute(text("SELECT state, COUNT(*) FROM review_cards GROUP BY state")).fetchall()
        if has_table("review_cards")
        else []
    )
    state_distribution = {str(r[0]): r[1] for r in state_rows}

    # 今日复习提交数（用于日活卡）
    reviews_today = (
        scalar(
            "SELECT COUNT(*) FROM review_logs WHERE review_at >= :ts",
            ts=today_start,
        )
        if has_table("review_logs")
        else 0
    )

    # 近 14 天每日复习次数（趋势图）
    daily_rows = (
        db.execute(
            text(
                "SELECT DATE(review_at) AS d, COUNT(*) FROM review_logs "
                "WHERE review_at >= :since GROUP BY d ORDER BY d",
            ),
            {"since": now - timedelta(days=13)},
        ).fetchall()
        if has_table("review_logs")
        else []
    )
    daily_reviews = []
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        count = next((r[1] for r in daily_rows if r[0] == day), 0)
        daily_reviews.append({"date": day, "count": count})

    return ok(
        {
            "total_cards": total_cards,
            "due_today": due_today,
            "reviews_30d": reviews_30d,
            "reviews_today": reviews_today,
            "average_rating_30d": round(float(avg_30d), 2) if avg_30d else 0,
            "state_distribution": state_distribution,
            "daily_reviews": daily_reviews,
        }
    )

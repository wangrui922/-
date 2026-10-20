"""管理后台 API（admin 专属接口）。

仅对 ``role == 'admin'`` 的用户开放；越权返 403。
当前覆盖：
    * 激活码 CRUD（生成、列表、绑定记录）
    * 学生/家长账户管理（列表、冻结/解冻、详情）
    * 错题/掌握度/练习统计看板
    * AI 会话调用日志（暂只列出会话与消息数，后续对接真实日志）
    * AI 质量监控（Benchmark 维度一：直接给答案率 / 讲解质量 / trace 流水）
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
from app.models.quality_trace import (
    QUALITY_TRACE_EVENT_TYPES,
    QUALITY_TRACE_SEVERITIES,
    QualityTrace,
)
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
from app.services import quality_metrics_service as qms


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


# ---- AI 质量监控（Benchmark 维度一 · 内测看板）---------------------------
#
# 口径唯一真相源是 ``app.services.quality_metrics_service``（qms）：本模块只做
# 参数校验、编排与序列化，**不另写任何聚合口径**；分母为 0 时按既有约定返回
# ``None``（不编造 0%）。后台规划的 6 项指标中当前只有「直接给答案率」有数据
# 源，其余明确标注「数据源待建」——看板展示真相，不造假数据。

#: 后台规划的 6 项 AI 质量指标（key 稳定，前端按 key 渲染）。
#: ``pending_note`` 说明数据源缺口，供看板直接展示。
_QUALITY_METRIC_PLAN: List[Dict[str, str]] = [
    {
        "key": "recognition_accuracy",
        "name": "识别正确率",
        "pending_note": (
            "数据源待建：原拟以「需重拍率」作代理指标（低置信度重拍机制 #29），"
            "但重拍事件未落库（gate 仅返回引导、不创建记录），暂无法量化"
        ),
    },
    {
        "key": "answer_accuracy",
        "name": "答案正确率",
        "pending_note": "数据源待建：学生作答正误与 AI 判定结论暂无可关联的落库事件",
    },
    {
        "key": "error_cause_accuracy",
        "name": "错因识别准确率",
        "pending_note": "数据源待建：错因识别结论缺少人工标注样本可核对",
    },
    {
        "key": "answer_leak_rate",
        "name": "直接给答案率",
        "pending_note": "answer_leak 事件数 ÷ 同期 AI 应答数（assistant 消息）",
    },
    {
        "key": "practice_generation_success_rate",
        "name": "练习生成成功率",
        "pending_note": "数据源待建：AI 练习生成结果未按成败落库",
    },
    {
        "key": "validation_failure_rate",
        "name": "验证失败率",
        "pending_note": "数据源待建：sympy 验证结果未按事件落库",
    },
]


def _weighted_bucket_avg(
    buckets: List[Dict[str, Any]], field: str
) -> Optional[float]:
    """按 ``count`` 加权求窗内某字段均值（数据取自 service 趋势桶，不另立口径）。

    桶内该字段为 ``None``（当日无有效评分）或计数为非正时跳过；全窗无有效
    样本时返回 ``None``（不编造 0）。
    """
    weight = 0
    acc = 0.0
    for bucket in buckets:
        value = bucket.get(field)
        count = int(bucket.get("count") or 0)
        if value is None or count <= 0:
            continue
        acc += float(value) * count
        weight += count
    if weight == 0:
        return None
    return round(acc / weight, 4)


def _weighted_dimension_avgs(
    buckets: List[Dict[str, Any]], keys: List[str]
) -> Dict[str, Optional[float]]:
    """5 维 rubric 的窗内加权均分（同样只加工 service 的趋势桶）。"""
    return {key: _dimension_weighted_avg(buckets, key) for key in keys}


def _dimension_weighted_avg(
    buckets: List[Dict[str, Any]], dim_key: str
) -> Optional[float]:
    """单个维度的窗内加权均分（维度值嵌在桶的 ``dimensions`` 子对象里）。"""
    weight = 0
    acc = 0.0
    for bucket in buckets:
        count = int(bucket.get("count") or 0)
        if count <= 0:
            continue
        dims = bucket.get("dimensions") or {}
        value = dims.get(dim_key)
        if value is None:
            continue
        acc += float(value) * count
        weight += count
    if weight == 0:
        return None
    return round(acc / weight, 4)


def _parse_optional_uuid(raw: Optional[str], field: str) -> Optional[uuid.UUID]:
    """可选的 UUID 查询参数：缺省 None，非法值抛 400（不进 500）。"""
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_BAD_REQUEST, f"{field} 不是合法 UUID") from exc


@router.get("/quality-metrics")
def quality_metrics_overview(
    window_days: int = Query(7, ge=1, le=90),
    student_id: Optional[str] = Query(None),
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """AI 质量监控总览（Benchmark 维度一）。

    总览 = 6 项规划指标各自状态（有数据/待建）+ 讲解质量五维均分 + 近 N 天趋势。
    所有比率口径均来自 ``quality_metrics_service``；分母为 0 → ``None``。

    Args:
        window_days: 聚合时间窗（天），1~90，默认 7。
        student_id: 可选的学生过滤（下钻单个学生）；缺省为全量。

    Returns:
        ``{"code":0,"msg":"ok","data":{...}}``，data 结构::

            {
                "window_days": int,
                "since": iso 时间串,
                "student_id": str | None,
                "metrics": [                 # 恒 6 项，顺序稳定
                    {
                        "key": str,           # 指标 key（前端按此渲染）
                        "name": str,          # 指标名
                        "status": "available" | "pending",
                        "value": float | None, # 比率/均分；无数据源或分母 0 → None
                        "numerator": int | None,
                        "denominator": int | None,
                        "unit": "ratio",
                        "note": str,           # 口径说明或「数据源待建」原因
                    }
                ],
                "explanation_quality": {
                    "total_count": int,
                    "overall_avg": float | None,      # 窗内 overall 加权均分
                    "dimension_avgs": {key: float | None},
                    "buckets": [...],                 # service 趋势桶（按日升序）
                },
            }
    """
    sid = _parse_optional_uuid(student_id, "student_id")

    leak = qms.answer_leak_rate(db, window_days=window_days, student_id=sid)
    trend = qms.explanation_quality_trend(db, window_days=window_days, student_id=sid)
    buckets: List[Dict[str, Any]] = list(trend["buckets"])

    metrics: List[Dict[str, Any]] = []
    for plan in _QUALITY_METRIC_PLAN:
        row: Dict[str, Any] = {
            "key": plan["key"],
            "name": plan["name"],
            "status": "pending",
            "value": None,
            "numerator": None,
            "denominator": None,
            "unit": "ratio",
            "note": plan["pending_note"],
        }
        if plan["key"] == "answer_leak_rate":
            row["status"] = "available"
            row["value"] = leak["rate"]
            row["numerator"] = leak["answer_leak_count"]
            row["denominator"] = leak["ai_response_count"]
        metrics.append(row)

    explanation_quality: Dict[str, Any] = {
        "total_count": trend["total_count"],
        "overall_avg": _weighted_bucket_avg(buckets, "overall_avg"),
        "dimension_avgs": _weighted_dimension_avgs(buckets, qms.DIMENSION_KEYS),
        "buckets": buckets,
    }

    return ok(
        {
            "window_days": leak["window_days"],
            "since": leak["since"].isoformat(),
            "student_id": leak["student_id"],
            "metrics": metrics,
            "explanation_quality": explanation_quality,
        }
    )


@router.get("/quality/traces")
def list_quality_traces(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    event_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    student_id: Optional[str] = Query(None),
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """教学质量 trace 流水（分页 + event_type / severity 筛选）。

    与 ``/admin/mistakes`` 等同款分页范式；按 ``created_at`` 倒序。

    Args:
        page: 页码，从 1 起。
        size: 每页条数，1~200。
        event_type: 可选，``answer_leak`` / ``explanation_quality``。
        severity: 可选，``info`` / ``warning`` / ``error``。
        student_id: 可选的学生过滤（合法 UUID，否则 400）。

    Raises:
        ApiError: 400 筛选值非法 / student_id 非法。
    """
    if event_type is not None and event_type not in QUALITY_TRACE_EVENT_TYPES:
        raise ApiError(
            CODE_BAD_REQUEST, f"event_type 只能是 {QUALITY_TRACE_EVENT_TYPES}"
        )
    if severity is not None and severity not in QUALITY_TRACE_SEVERITIES:
        raise ApiError(CODE_BAD_REQUEST, f"severity 只能是 {QUALITY_TRACE_SEVERITIES}")
    sid = _parse_optional_uuid(student_id, "student_id")

    base = select(QualityTrace)
    if event_type:
        base = base.where(QualityTrace.event_type == event_type)
    if severity:
        base = base.where(QualityTrace.severity == severity)
    if sid is not None:
        base = base.where(QualityTrace.student_id == sid)

    total = db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    rows = (
        db.execute(
            base.order_by(QualityTrace.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        .scalars()
        .all()
    )
    items: List[Dict[str, Any]] = [
        {
            "id": str(r.id),
            "event_type": r.event_type,
            "severity": r.severity,
            "student_id": str(r.student_id) if r.student_id else None,
            "session_id": str(r.session_id) if r.session_id else None,
            "question_id": str(r.question_id) if r.question_id else None,
            "payload": r.payload if isinstance(r.payload, dict) else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
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

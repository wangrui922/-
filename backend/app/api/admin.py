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
import math
import random
import string
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.guards import CN_TZ, cn_day_start_utc
from app.core.deps import CurrentUser, require_role
from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_FORBIDDEN, CODE_NOT_FOUND, ok
from app.core.system_config import DEFAULTS, _to_bool, reload_config, reset_configs
from app.db import get_db
from app.models.activation import ActivationCode
from app.models.subscription import (
    SUBSCRIPTION_STATUS_ACTIVE,
    Plan,
    Subscription,
    PaymentTransaction,
)
from app.models.quality_trace import (
    QUALITY_TRACE_EVENT_TYPES,
    QUALITY_TRACE_SEVERITIES,
    QualityTrace,
)
from app.models.system_config import SystemConfig
from app.models.user import ROLE_VALUES, User, Student, GRADE_MIN, GRADE_MAX
from app.models.learning import Mistake, Attempt, LearningTask, StudentProfile
from app.models.student_mastery import StudentMastery
from app.models.weekly_report import WeeklyReport
from app.models.ai import AiSession, AiMessage
from app.schemas.admin import (
    ActivationCodeCreate,
    ActivationCodeItem,
    ConfigUpdate,
    DashboardStats,
    UserListItem,
    UserListQuery,
)
from app.services import quality_metrics_service as qms
from app.services.auth_service import USER_STATUS_ACTIVE


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


# ---- 运营总览看板（服务器版 admin-web 依赖）---------------------------------
#
# 口径说明：
#     * 「今天 / 每天」一律按**北京时间（UTC+8）**切分，复用
#       ``app.ai.guards.cn_day_start_utc``（不另写一份时区口径）；
#     * 趋势分桶在 Python 侧做（按日期字符串归并），不依赖方言专属
#       ``DATE_TRUNC`` / ``strftime``，SQLite 与 PG 结果一致；
#     * 三个趋势数组恒为 14 个点，无数据的日期补 ``count=0``——前端直接
#       按下标画线，缺项会把图画歪。

#: 趋势窗口长度（天）
_TREND_DAYS = 14


def _cn_day_keys(now: datetime) -> List[str]:
    """返回近 ``_TREND_DAYS`` 天的日期键（``YYYY-MM-DD``，升序、北京时间）。"""
    today = now.astimezone(CN_TZ).date()
    return [
        (today - timedelta(days=_TREND_DAYS - 1 - i)).isoformat()
        for i in range(_TREND_DAYS)
    ]


def _cn_day_key(value: Optional[datetime]) -> Optional[str]:
    """把 UTC 存储的时间折算成北京日期键；``None`` / naive 视为无效。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CN_TZ).date().isoformat()


def _count_by_day(values: List[Optional[datetime]], keys: List[str]) -> List[Dict[str, Any]]:
    """按北京日期分桶计数，补齐窗口内每一天（无数据为 0）。"""
    buckets: Dict[str, int] = {key: 0 for key in keys}
    for value in values:
        key = _cn_day_key(value)
        if key in buckets:
            buckets[key] += 1
    return [{"date": key, "count": buckets[key]} for key in keys]


def _sum_by_day(
    values: List[Any], keys: List[str], day_field: int = 0
) -> List[Dict[str, Any]]:
    """按北京日期分桶求和，补齐窗口内每一天（无数据为 0）。"""
    buckets: Dict[str, int] = {key: 0 for key in keys}
    for row in values:
        key = _cn_day_key(row[day_field])
        if key in buckets:
            buckets[key] += int(row[1] or 0)
    return [{"date": key, "amount": buckets[key]} for key in keys]


@router.get("/dashboard/overview")
def dashboard_overview(
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """运营总览看板：用户/订阅/付费/激活码概览 + 近 14 天趋势。

    服务器版 admin-web ``views/dashboard/index.vue`` 的 ``loadOverview()``
    直接读下列字段渲染 statCards 与 ECharts，**任一字段缺失都会渲染
    undefined**，故此处字段清单与前端强绑定。

    金额单位统一为**分**（前端自行 ``/100`` 转元）。

    Returns:
        ``{"code":0,"msg":"ok","data":{...}}``，data 见下方各键注释。
    """
    now = datetime.now(timezone.utc)
    today_start = cn_day_start_utc(now)
    week_start = cn_day_start_utc(now - timedelta(days=_TREND_DAYS - 1))
    keys = _cn_day_keys(now)

    # 用户类统计口径：**排除软删除与冻结账号**。
    # ``User`` 同时有 ``deleted_at``（SoftDeleteMixin）与 ``status``
    # （active/frozen）两套「不算数」的标记：前者是账号已注销、后者是运营
    # 主动冻结，两者都不该出现在「总用户数 / 今日活跃 / 注册趋势」里——
    # 否则运营会以为冻结的僵尸号还在贡献 UV。
    user_where = (User.deleted_at.is_(None), User.status == USER_STATUS_ACTIVE)

    total_users = (
        db.execute(select(func.count()).select_from(User).where(*user_where)).scalar()
        or 0
    )
    active_today = (
        db.execute(
            select(func.count())
            .select_from(User)
            .where(*user_where, User.last_login_at >= today_start)
        ).scalar()
        or 0
    )
    active_7d = (
        db.execute(
            select(func.count())
            .select_from(User)
            .where(*user_where, User.last_login_at >= week_start)
        ).scalar()
        or 0
    )

    active_subscriptions = (
        db.execute(
            select(func.count())
            .select_from(Subscription)
            .where(
                Subscription.status == SUBSCRIPTION_STATUS_ACTIVE,
                Subscription.current_period_end > now,
            )
        ).scalar()
        or 0
    )

    # 付费笔数 / 金额：只认 succeeded 流水
    pay_where = (PaymentTransaction.status == "succeeded",)
    payment_count = (
        db.execute(
            select(func.count()).select_from(PaymentTransaction).where(*pay_where)
        ).scalar()
        or 0
    )
    payment_amount = (
        db.execute(
            select(func.coalesce(func.sum(PaymentTransaction.amount), 0))
            .select_from(PaymentTransaction)
            .where(*pay_where)
        ).scalar()
        or 0
    )

    # 激活码三态：已用（已绑定账号）/ 未用（未绑定且未吊销）/ 已吊销
    activation_used = (
        db.execute(
            select(func.count())
            .select_from(ActivationCode)
            .where(ActivationCode.user_id.isnot(None))
        ).scalar()
        or 0
    )
    activation_revoked = (
        db.execute(
            select(func.count())
            .select_from(ActivationCode)
            .where(ActivationCode.revoked_at.isnot(None))
        ).scalar()
        or 0
    )
    activation_unused = (
        db.execute(
            select(func.count())
            .select_from(ActivationCode)
            .where(
                ActivationCode.user_id.is_(None),
                ActivationCode.revoked_at.is_(None),
            )
        ).scalar()
        or 0
    )

    # register_trend：按注册日分桶，与 total_users 同口径（排除软删/冻结）
    register_trend = _count_by_day(
        list(
            db.execute(
                select(User.created_at).where(*user_where, User.created_at >= week_start)
            ).scalars().all()
        ),
        keys,
    )
    # dau_trend：**当日有登录行为的用户数**（口径 = ``users.last_login_at``
    # 落在该自然日的去重账号数，已排除软删/冻结）。
    # ⚠️ 这是「最后一次登录时间」近似，不是真正的日活跃曲线——同一用户当天
    # 登录多次只落一个点，跨天不再回补历史。之所以不用 ``ai_sessions``
    # 去重学生数：那个口径把「做题但没提问」的用户判成不活跃，与 DAU 的
    # 通行定义（登录/使用即活跃）不符，且内测期 ai_sessions 样本量极小，
    # 看板会长期显示接近 0。
    dau_trend = _count_by_day(
        list(
            db.execute(
                select(User.last_login_at).where(
                    *user_where, User.last_login_at >= week_start
                )
            ).scalars().all()
        ),
        keys,
    )
    activation_trend = _count_by_day(
        list(
            db.execute(
                select(ActivationCode.activated_at).where(
                    ActivationCode.activated_at >= week_start
                )
            ).scalars().all()
        ),
        keys,
    )
    payment_trend = _sum_by_day(
        list(
            db.execute(
                select(PaymentTransaction.created_at, PaymentTransaction.amount)
                .where(*pay_where, PaymentTransaction.created_at >= week_start)
            ).all()
        ),
        keys,
    )

    sub_by_status = [
        {"status": str(status), "count": int(count)}
        for status, count in db.execute(
            select(Subscription.status, func.count()).group_by(Subscription.status)
        ).all()
    ]
    plan_name_by_slug = {
        slug: name
        for slug, name in db.execute(select(Plan.slug, Plan.name)).all()
    }
    sub_raw_by_plan = db.execute(
        select(Subscription.plan_slug, func.count()).group_by(Subscription.plan_slug)
    ).all()
    sub_by_plan = [
        {
            "name": plan_name_by_slug.get(str(slug), str(slug)),
            "plan": str(slug),
            "slug": str(slug),
            "count": int(count),
        }
        for slug, count in sub_raw_by_plan
    ]

    return ok(
        {
            "total_users": int(total_users),
            "active_today": int(active_today),
            "active_7d": int(active_7d),
            "active_subscriptions": int(active_subscriptions),
            "payment_count": int(payment_count),
            "payment_amount": int(payment_amount),
            "activation_used": int(activation_used),
            "activation_unused": int(activation_unused),
            "activation_revoked": int(activation_revoked),
            "register_trend": register_trend,
            "dau_trend": dau_trend,
            "activation_trend": activation_trend,
            "sub_by_status": sub_by_status,
            "sub_by_plan": sub_by_plan,
            "payment_trend": payment_trend,
        }
    )


@router.get("/dashboard/members")
def dashboard_members(
    limit: int = Query(10, ge=1, le=200),
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """会员订阅列表（看板「会员管理」入口）。

    数据源为 ``Subscription`` JOIN ``User`` JOIN ``Plan``（按 ``plan_slug``
    关联 ``Plan.slug``），按到期时间倒序取 ``limit`` 条。

    Returns:
        ``{"code":0,"msg":"ok","data":{"list":[...]}}``；``amount_paid`` 单位为分。
    """
    rows = db.execute(
        select(Subscription, User, Plan)
        .join(User, User.id == Subscription.user_id)
        .join(Plan, Plan.slug == Subscription.plan_slug)
        .order_by(Subscription.current_period_end.desc())
        .limit(limit)
    ).all()

    return ok(
        {
            "list": [
                {
                    "user_id": str(user.id),
                    "phone": user.phone,
                    "nickname": user.nickname,
                    "plan_name": plan.name,
                    "plan_slug": sub.plan_slug,
                    "status": sub.status,
                    "amount_paid": int(sub.amount_paid or 0),
                    "current_period_start": (
                        sub.current_period_start.isoformat()
                        if sub.current_period_start
                        else None
                    ),
                    "current_period_end": (
                        sub.current_period_end.isoformat()
                        if sub.current_period_end
                        else None
                    ),
                    "subscription_id": str(sub.id),
                }
                for sub, user, plan in rows
            ]
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


# ---- 系统配置（运营阈值 / 灰度开关）-----------------------------------------
#
# 存储层是 ``system_config`` key-value 表；读取侧缓存与转型的唯一真相源是
# ``app.core.system_config``（DEFAULTS 含 V6.5 的监护人同意门控 / 低置信度
# 重拍等键）。本模块只做管理面的查与改，**不另立默认值表**。
#
# 写入门禁（较服务器版严格）：PUT 的值必须能通过该键的 cast 函数转型，
# bool 键只认白名单 token、float 键拒绝 nan/inf——杜绝「存得进、读时
# 静默回退默认值」的运维坑。

#: 配置项中文说明（键与 DEFAULTS 对齐；DEFAULTS 新增键要同步这里，
#: 未配的键兜底返空串，不 KeyError）。
_CONFIG_DESCRIPTIONS: Dict[str, str] = {
    "REPEAT_SIMILARITY": "重复回答判定阈值（字符重叠率 ≥ 此值视为重复）",
    "STUCK_TO_L2": "连续卡壳次数 ≥ 此值 → 升级到半引导(L2)",
    "STUCK_TO_L3": "连续卡壳次数 ≥ 此值 → 升级到边讲边问(L3)",
    "STUCK_TO_L4": "连续卡壳次数 ≥ 此值 → 升级到直接讲解(L4)",
    "REPEAT_TO_L2": "连续重复回答次数 ≥ 此值 → 升级到半引导(L2)",
    "REPEAT_TO_L3": "连续重复回答次数 ≥ 此值 → 升级到边讲边问(L3)",
    "NOPROGRESS_TO_L3": "连续无进展次数 ≥ 此值 → 升级到边讲边问(L3)",
    "NOPROGRESS_TO_L4": "连续无进展次数 ≥ 此值 → 升级到直接讲解(L4)",
    "TURNS_TO_L4": "单题求助轮次 ≥ 此值 → 升级到直接讲解(L4)",
    "HAS_SUBSTANCE_MIN_LEN": "回答有实质内容的最小字符长度",
    "CONSENT_GATE_ENABLED": "监护人同意门控总开关（灰度，默认关）",
    "CONSENT_VERSION": "监护人同意规则版本号（变更后旧版本同意记录一律失效）",
    "CONFIDENCE_RETHRESHOLD": "拍题分析置信度阈值（低于此值引导重拍，-1 关闭）",
    "RAG_GROUNDING_ENABLED": "轻量RAG接地气讲解开关（灰度，默认关；开启后仅在讲解/评判模式追加素材）",
}

#: bool 键认得的 token（与 ``_to_bool`` 语义一致）。不设白名单的话，
#: PUT "maybe" 存得进、读出来是 False——运营以为关了，其实从没开过。
_BOOL_TOKENS = {"1", "true", "yes", "on", "0", "false", "no", "off"}


def _config_description(key: str) -> str:
    """配置项中文说明（未配置的键返空串）。"""
    return _CONFIG_DESCRIPTIONS.get(key, "")


def _validate_config_value(key: str, raw: str) -> str:
    """写入门禁：值必须能被该键的 cast 解析，否则抛 400。

    * bool 键（``cast is _to_bool``）只认白名单 token；
    * float 键拒绝 nan/inf（阈值比较遇 nan 恒假，语义不确定）；
    * int/float 键 cast 失败（如 ``"abc"``）直接 400，不落库。
    """
    _default_str, cast = DEFAULTS[key]
    if cast is _to_bool and raw.lower() not in _BOOL_TOKENS:
        raise ApiError(
            CODE_BAD_REQUEST,
            f"{key} 是开关型配置，只能取 {sorted(_BOOL_TOKENS)}，当前: {raw!r}",
        )
    try:
        parsed = cast(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(
            CODE_BAD_REQUEST,
            f"{key} 的值无法按 {cast.__name__} 解析: {raw!r}",
        ) from exc
    if isinstance(parsed, float) and not math.isfinite(parsed):
        raise ApiError(CODE_BAD_REQUEST, f"{key} 不接受 nan/inf，当前: {raw!r}")
    return raw


@router.get("/configs")
def list_configs(
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """获取全部系统配置（DB 无记录的键回退代码默认值）。"""
    rows = db.execute(select(SystemConfig)).scalars().all()
    db_values = {r.key: r.value for r in rows}

    items = []
    for key, (default_str, _cast) in DEFAULTS.items():
        items.append(
            {
                "key": key,
                "value": db_values.get(key, default_str),
                "default_value": default_str,
                "description": _config_description(key),
            }
        )
    return ok({"items": items})


@router.put("/configs/{key}")
def update_config(
    key: str,
    body: ConfigUpdate,
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """更新单个配置项（写入门禁 + 清读取缓存）。

    未知键 400；值无法被该键 cast 解析时 400 且 DB 保持原值；成功后
    ``reload_config()`` 清进程内缓存，下一次 ``get_config`` 即读新值
    （缓存不清是这类接口最静的坑，单测直接盯）。
    """
    if key not in DEFAULTS:
        raise ApiError(CODE_BAD_REQUEST, f"未知配置项: {key}")
    raw = _validate_config_value(key, (body.value or "").strip())

    row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
    if row is None:
        db.add(SystemConfig(key=key, value=raw, description=_config_description(key)))
    else:
        row.value = raw
        row.description = _config_description(key) or row.description
    db.commit()

    reload_config()
    return ok({"key": key, "value": raw})


@router.post("/configs/reset")
def reset_all_configs(
    current: CurrentUser = Depends(require_role("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """重置所有配置为默认值（reset_configs 内部已清缓存）。"""
    reset_configs(db)
    db.commit()
    return ok({"reset": True, "items": list(DEFAULTS.keys())})


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

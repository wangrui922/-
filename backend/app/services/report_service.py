"""家长周报服务层（Epic6 · T2/T4/T5）。

分层：
    ``app.services.profile_service``  既有画像服务（掌握度 / 错因 / 推荐）—— 本层直接复用
    ``app.services.report_service``   本层：周期计算 → 快照聚合 → 环比 diff → 落库编排
    ``app.ai.agents.report``          叙述生成（含 AI 兜底，永不抛异常）
    ``app.api.report``                路由：鉴权 + 序列化

━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 硬约定 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
S3  库内一律 UTC；只有算「自然周」时才转北京时间（``LOCAL_TZ_OFFSET_HOURS``）。
S4  **禁止在 SQL 里用 date() / DATE_TRUNC / strftime**（PG 与 SQLite 语法不同），
    日期聚合一律取出后在 Python 内存里算（单学生周内作答量级 < 200 行）。
S12 零数据 ≠ 0：``attempt_count == 0`` 时 ``correct_rate`` 必须是 ``None``。
S13 令牌 = ``secrets.token_urlsafe(24)``，不用 uuid4、不用 JWT。
S16 重新生成周报**不换 token**（家长手上的链接必须持续可用），
    仅当 ``expires_at`` 已过期才续期到 ``now + 30d``。
S17 分享载荷只出现 ``real_name``（空则「孩子」）+ ``grade``，
    绝不出现手机号 / student_id / user_id / 题目原文。
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import (
    LOCAL_TZ_OFFSET_HOURS,
    REPORT_DELTA_THRESHOLD,
    REPORT_LOW_DATA_THRESHOLD,
    REPORT_PERIOD_DAYS,
    REPORT_SHARE_TTL_DAYS,
    REPORT_SNAPSHOT_SCHEMA_VERSION,
    REPORT_TOKEN_BYTES,
    REPORT_WEAK_POINT_TOP_N,
)
from app.models.ai import AiSession
from app.models.base import utcnow
from app.models.learning import Attempt, Mistake
from app.models.user import Student
from app.models.weekly_report import WeeklyReport
from app.services import profile_service
from app.services.mastery_engine import as_utc

logger = logging.getLogger("app.services.report")

#: 北京时间（东八区）。不引 pytz、不用 zoneinfo（Windows 需额外装 tzdata）。
LOCAL_TZ = timezone(timedelta(hours=LOCAL_TZ_OFFSET_HOURS))

#: 分享载荷里学生姓名为空时的兜底称呼
DEFAULT_STUDENT_NAME = "孩子"

#: token 合法长度上限（超长直接判无效，不进 DB）
MAX_TOKEN_LENGTH = 64


# ==================================================================== 数据结构
@dataclass(frozen=True)
class ReportPeriod:
    """一个统计周期（北京时间自然周，边界以 UTC 存储）。

    Attributes:
        start_utc: 周期起点（UTC，对应北京时间周一 00:00:00）。
        end_utc: 周期终点（UTC，对应北京时间周日 23:59:59.999999）。
        start_local: 北京时间周一的日期。
        end_local: 北京时间周日的日期。
        is_partial: 本周尚未结束（``now < end``）时为 True。
        days: 周期长度（天）。
    """

    start_utc: datetime
    end_utc: datetime
    start_local: date
    end_local: date
    is_partial: bool
    days: int = REPORT_PERIOD_DAYS


@dataclass
class EngagementStat:
    """本周学习投入的真实计数（**绝不估算学习时长**，见设计文档 K3）。"""

    study_days: int = 0
    attempt_count: int = 0
    correct_count: int = 0
    correct_rate: Optional[float] = None
    new_mistake_count: int = 0
    solved_mistake_count: int = 0
    tutor_session_count: int = 0

    def is_empty(self) -> bool:
        """本周零练习（整页走鼓励性空态，不显示任何 0%）。"""
        return self.attempt_count <= 0

    def is_low_data(self) -> bool:
        """练习量偏少（0 < n < 3），能力判断仅供参考。"""
        return 0 < self.attempt_count < REPORT_LOW_DATA_THRESHOLD

    def to_dict(self) -> Dict[str, Any]:
        """序列化成快照片段（snake_case）。"""
        return {
            "study_days": int(self.study_days),
            "attempt_count": int(self.attempt_count),
            "correct_count": int(self.correct_count),
            "correct_rate": (None if self.correct_rate is None else round(float(self.correct_rate), 4)),
            "new_mistake_count": int(self.new_mistake_count),
            "solved_mistake_count": int(self.solved_mistake_count),
            "tutor_session_count": int(self.tutor_session_count),
        }

    @staticmethod
    def from_dict(raw: Any) -> "EngagementStat":
        """从快照片段还原（兼容缺字段的老快照）。"""
        data = raw if isinstance(raw, dict) else {}
        rate = data.get("correct_rate")
        return EngagementStat(
            study_days=_safe_int(data.get("study_days")),
            attempt_count=_safe_int(data.get("attempt_count")),
            correct_count=_safe_int(data.get("correct_count")),
            correct_rate=(None if rate is None else _safe_float(rate)),
            new_mistake_count=_safe_int(data.get("new_mistake_count")),
            solved_mistake_count=_safe_int(data.get("solved_mistake_count")),
            tutor_session_count=_safe_int(data.get("tutor_session_count")),
        )


@dataclass
class WeeklySnapshot:
    """当周快照（跨周比较的基石），结构见设计文档 §4.2。"""

    schema_version: int = REPORT_SNAPSHOT_SCHEMA_VERSION
    engagement: EngagementStat = field(default_factory=EngagementStat)
    mastery: List[Dict[str, Any]] = field(default_factory=list)
    error_patterns: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[Dict[str, Any]] = field(default_factory=list)
    radar: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化成可落 ``snapshot_json`` 的纯 dict。"""
        return {
            "schema_version": int(self.schema_version),
            "engagement": self.engagement.to_dict(),
            "mastery": list(self.mastery),
            "error_patterns": dict(self.error_patterns),
            "recommendations": list(self.recommendations),
            "radar": dict(self.radar),
        }

    @staticmethod
    def from_dict(raw: Any) -> "WeeklySnapshot":
        """从 ``snapshot_json`` 还原（兼容老 ``schema_version`` 与脏数据）。"""
        data = raw if isinstance(raw, dict) else {}
        mastery = data.get("mastery")
        recommendations = data.get("recommendations")
        patterns = data.get("error_patterns")
        radar = data.get("radar")
        return WeeklySnapshot(
            schema_version=_safe_int(data.get("schema_version"), REPORT_SNAPSHOT_SCHEMA_VERSION),
            engagement=EngagementStat.from_dict(data.get("engagement")),
            mastery=[item for item in mastery if isinstance(item, dict)] if isinstance(mastery, list) else [],
            error_patterns=patterns if isinstance(patterns, dict) else {},
            recommendations=(
                [item for item in recommendations if isinstance(item, dict)]
                if isinstance(recommendations, list)
                else []
            ),
            radar=radar if isinstance(radar, dict) else {},
        )

    def weak_points(self, top_n: int = REPORT_WEAK_POINT_TOP_N) -> List[Dict[str, Any]]:
        """取 Top N 薄弱知识点（``mastery`` 已按「等级升序 → 错误率降序」排好）。"""
        return list(self.mastery[: max(0, int(top_n))])


@dataclass
class ComparisonResult:
    """与上一份快照的环比结果。"""

    available: bool = False
    reason: str = "first_report"
    prev_period_start: Optional[str] = None
    correct_rate_delta: Optional[float] = None
    improved: List[Dict[str, Any]] = field(default_factory=list)
    declined: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化成接口出参（snake_case）。"""
        return {
            "available": bool(self.available),
            "reason": self.reason,
            "prev_period_start": self.prev_period_start,
            "correct_rate_delta": (
                None if self.correct_rate_delta is None else round(float(self.correct_rate_delta), 4)
            ),
            "improved": list(self.improved),
            "declined": list(self.declined),
        }


# ==================================================================== 通用工具
def _safe_int(raw: Any, fallback: int = 0) -> int:
    """宽松转 int，失败返回 ``fallback``。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def _safe_float(raw: Any, fallback: float = 0.0) -> float:
    """宽松转 float，失败返回 ``fallback``。"""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def _iso(value: Optional[datetime]) -> Optional[str]:
    """datetime → ISO 8601 字符串（None 透传）。"""
    return value.isoformat() if value is not None else None


def _new_token() -> str:
    """生成分享令牌：``secrets.token_urlsafe(24)`` → 32 字符 URL-safe。"""
    return secrets.token_urlsafe(REPORT_TOKEN_BYTES)


def _to_local(value: Optional[datetime]) -> Optional[datetime]:
    """把（可能 naive 的）UTC 时间转成北京时间。"""
    moment = as_utc(value)
    if moment is None:
        return None
    return moment.astimezone(LOCAL_TZ)


# ==================================================================== T2 周期
def resolve_period(
    now: Optional[datetime] = None,
    days: int = REPORT_PERIOD_DAYS,
) -> ReportPeriod:
    """算出「当前所在的北京时间自然周」，边界转成 UTC 返回。

    为什么必须转时区：库里全是 UTC，而「这周」是北京时间概念。
    不处理会导致周日晚上生成的报告把周日整天算进「下周」，家长直接看出数字不对。

    Args:
        now: 参照时刻（tz-aware 或 naive UTC）；缺省取当前 UTC 时间。
        days: 周期长度，默认 7 天。

    Returns:
        ``ReportPeriod``，``start_utc`` 对应北京时间周一 00:00:00，
        ``end_utc`` 对应北京时间周日 23:59:59.999999。
    """
    span = max(1, _safe_int(days, REPORT_PERIOD_DAYS))
    moment = as_utc(now) or utcnow()
    local_now = moment.astimezone(LOCAL_TZ)

    # weekday(): 周一=0 … 周日=6
    start_local_date = local_now.date() - timedelta(days=local_now.weekday())
    end_local_date = start_local_date + timedelta(days=span - 1)

    start_local = datetime.combine(start_local_date, datetime.min.time(), tzinfo=LOCAL_TZ)
    end_local = datetime.combine(end_local_date, datetime.max.time(), tzinfo=LOCAL_TZ)

    return ReportPeriod(
        start_utc=start_local.astimezone(timezone.utc),
        end_utc=end_local.astimezone(timezone.utc),
        start_local=start_local_date,
        end_local=end_local_date,
        is_partial=local_now < end_local,
        days=span,
    )


# ================================================================ T2 学习投入
def count_engagement(
    db: Session,
    student_id: uuid.UUID,
    period: ReportPeriod,
) -> EngagementStat:
    """统计窗口内的真实学习投入计数。

    ⚠️ 三条硬约束：
        * **不估算学习时长**（``attempts.duration_sec`` 全仓库零写入，见 K3）；
        * ``study_days`` 在 Python 内存里按北京时间日期去重，**不用 SQL date()**；
        * ``attempt_count == 0`` 时 ``correct_rate`` 必须是 ``None``，不是 ``0.0``。

    Args:
        db: 数据库会话（只读，不 commit）。
        student_id: 学生 ID。
        period: 统计周期。

    Returns:
        ``EngagementStat``。
    """
    start, end = period.start_utc, period.end_utc

    # ---- attempts：题数 / 正确数 / 学习天数 -------------------------------
    attempt_rows = db.execute(
        select(Attempt.created_at, Attempt.is_correct).where(
            Attempt.student_id == student_id,
            Attempt.created_at >= start,
            Attempt.created_at <= end,
        )
    ).all()

    attempt_count = len(attempt_rows)
    correct_count = sum(1 for _, is_correct in attempt_rows if bool(is_correct))

    local_days = set()
    for created_at, _ in attempt_rows:
        local = _to_local(created_at)
        if local is not None:
            local_days.add(local.date())

    correct_rate: Optional[float] = None
    if attempt_count > 0:
        correct_rate = round(correct_count / attempt_count, 4)

    # ---- mistakes：新增 / 攻克 --------------------------------------------
    new_mistake_count = len(
        db.execute(
            select(Mistake.id).where(
                Mistake.student_id == student_id,
                Mistake.created_at >= start,
                Mistake.created_at <= end,
            )
        ).all()
    )
    solved_mistake_count = len(
        db.execute(
            select(Mistake.id).where(
                Mistake.student_id == student_id,
                Mistake.solved.is_(True),
                Mistake.solved_at.is_not(None),
                Mistake.solved_at >= start,
                Mistake.solved_at <= end,
            )
        ).all()
    )

    # ---- ai_sessions：辅导次数 -------------------------------------------
    tutor_session_count = len(
        db.execute(
            select(AiSession.id).where(
                AiSession.student_id == student_id,
                AiSession.created_at >= start,
                AiSession.created_at <= end,
            )
        ).all()
    )

    return EngagementStat(
        study_days=len(local_days),
        attempt_count=attempt_count,
        correct_count=correct_count,
        correct_rate=correct_rate,
        new_mistake_count=new_mistake_count,
        solved_mistake_count=solved_mistake_count,
        tutor_session_count=tutor_session_count,
    )


# ==================================================================== T2 快照
def _mastery_snapshot_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """把 ``get_mastery().items`` 的一条压成快照所需的最小字段集。"""
    return {
        "kp_id": str(item.get("kp_id") or ""),
        "kp_name": str(item.get("kp_name") or "未命名知识点"),
        "score": round(_safe_float(item.get("score")), 4),
        "level": _safe_int(item.get("level")),
        "error_rate": round(_safe_float(item.get("error_rate")), 4),
    }


def _error_patterns_snapshot(view: Any) -> Dict[str, Any]:
    """把 ``ErrorPatternView`` 序列化成**与 /api/profile/error-patterns 完全同构**的 dict。

    为什么要同构：前端 ``ErrorPatternChart.vue`` 的 props 就是这个完整结构，
    多包一层可以让它**零改造**复用（设计文档 §4.2 说明）。
    """
    return {
        "student_id": str(view.student_id),
        "error_patterns": [
            {
                "category": pattern.category,
                "label": pattern.label,
                "count": int(pattern.count),
                "ratio": round(float(pattern.ratio), 4),
                "sample_hits": list(pattern.sample_hits),
            }
            for pattern in view.patterns
        ],
        "total_mistakes": int(view.total_mistakes),
        "unmapped_mistakes": int(view.unmapped_mistakes),
        "range_days": int(view.range_days),
        "computed_at": _iso(view.computed_at),
        "mode": view.mode,
    }


def _radar_snapshot(view: Any) -> Dict[str, Any]:
    """把 ``RadarView`` 序列化成可落 ``snapshot_json`` 的 dict。

    与 ``GET /api/profile/radar`` 返回结构一致（前端 ``AbilityRadar`` 的 props 即它），
    这样分享页直接读快照，家长每次打开都不需要重算掌握度。
    """
    return {
        "student_id": str(view.student_id),
        "axes": [
            {
                "kp_id": str(a.kp_id),
                "kp_name": str(a.kp_name),
                "score": round(float(a.score), 1),
                "level": int(a.level),
                "practiced": bool(a.practiced),
            }
            for a in view.axes
        ],
        "summary": dict(view.summary),
        "computed_at": _iso(view.computed_at),
        "mode": view.mode,
    }


def build_weekly_snapshot(
    db: Session,
    student_id: uuid.UUID,
    days: int = REPORT_PERIOD_DAYS,
    *,
    period: Optional[ReportPeriod] = None,
) -> WeeklySnapshot:
    """聚合当周快照：学习投入 + 掌握度 + 错因分布 + 推荐理由。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        days: 统计窗口天数，默认 7。
        period: 已算好的周期；缺省时内部调 ``resolve_period()``。

    Returns:
        ``WeeklySnapshot``（结构见设计文档 §4.2）。
    """
    window = period or resolve_period(days=days)

    engagement = count_engagement(db, student_id, window)

    mastery_view = profile_service.get_mastery(db, student_id=student_id)
    mastery = [_mastery_snapshot_item(item) for item in mastery_view.items]

    pattern_view = profile_service.get_error_patterns(db, student_id=student_id, range_days=window.days)
    error_patterns = _error_patterns_snapshot(pattern_view)

    recommend_view = profile_service.get_recommendations(db, student_id=student_id)
    recommendations = [
        {
            "kp_id": str(item.kp_id),
            "kp_name": str(item.kp_name),
            "reason": str(item.reason or ""),
            "top_error_category": item.top_error_category,
        }
        for item in recommend_view.items
    ]

    radar_view = profile_service.get_radar(db, student_id=student_id)
    radar = _radar_snapshot(radar_view)

    return WeeklySnapshot(
        schema_version=REPORT_SNAPSHOT_SCHEMA_VERSION,
        engagement=engagement,
        mastery=mastery,
        error_patterns=error_patterns,
        recommendations=recommendations,
        radar=radar,
    )


# ==================================================================== T2 环比
def load_prev_snapshot(
    db: Session,
    student_id: uuid.UUID,
    period: ReportPeriod,
) -> Tuple[Optional[WeeklySnapshot], Optional[datetime]]:
    """取上一份快照（``period_start`` 严格早于本周）。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        period: 本周周期。

    Returns:
        ``(快照, 上一份的 period_start)``；没有历史时为 ``(None, None)``。
    """
    row = db.execute(
        select(WeeklyReport)
        .where(
            WeeklyReport.student_id == student_id,
            WeeklyReport.period_start < period.start_utc,
        )
        .order_by(WeeklyReport.period_start.desc())
        .limit(1)
    ).scalar_one_or_none()

    if row is None:
        return None, None
    return WeeklySnapshot.from_dict(row.snapshot_json), as_utc(row.period_start)


def diff_snapshots(
    curr: WeeklySnapshot,
    prev: Optional[WeeklySnapshot],
    *,
    prev_period_start: Optional[datetime] = None,
) -> ComparisonResult:
    """算本周相对上周的变化。

    规则：
        * ``prev is None`` → ``available=False, reason="first_report"``（首份周报）；
        * 任一侧 ``correct_rate`` 为 ``None`` → ``correct_rate_delta`` 也给 ``None``
          （零练习不能当成 0% 去做减法，见 S12）；
        * 知识点按 ``kp_id`` 匹配，``|delta| >= 0.05`` 才算变化（抑制噪声）。

    Args:
        curr: 本周快照。
        prev: 上周快照，可为 None。
        prev_period_start: 上周周期起点，用于出参回显。

    Returns:
        ``ComparisonResult``。
    """
    if prev is None:
        return ComparisonResult(available=False, reason="first_report")

    curr_rate = curr.engagement.correct_rate
    prev_rate = prev.engagement.correct_rate
    rate_delta: Optional[float] = None
    if curr_rate is not None and prev_rate is not None:
        rate_delta = round(curr_rate - prev_rate, 4)

    prev_scores = {
        str(item.get("kp_id")): _safe_float(item.get("score"))
        for item in prev.mastery
        if item.get("kp_id")
    }

    improved: List[Dict[str, Any]] = []
    declined: List[Dict[str, Any]] = []
    for item in curr.mastery:
        kp_id = str(item.get("kp_id") or "")
        if not kp_id or kp_id not in prev_scores:
            continue
        delta = round(_safe_float(item.get("score")) - prev_scores[kp_id], 4)
        if abs(delta) < REPORT_DELTA_THRESHOLD:
            continue
        entry = {"kp_id": kp_id, "kp_name": str(item.get("kp_name") or ""), "delta": delta}
        (improved if delta > 0 else declined).append(entry)

    improved.sort(key=lambda row: -row["delta"])
    declined.sort(key=lambda row: row["delta"])

    return ComparisonResult(
        available=True,
        reason="ok",
        prev_period_start=_iso(prev_period_start),
        correct_rate_delta=rate_delta,
        improved=improved,
        declined=declined,
    )


# ================================================================ T4 生成编排
def _student_display_name(db: Session, student_id: uuid.UUID) -> str:
    """取学生展示名：``real_name`` 为空时降级为「孩子」。

    ⚠️ S17：**绝不回显手机号或任何 ID**。
    """
    row = db.execute(select(Student.real_name).where(Student.id == student_id)).scalar_one_or_none()
    return str(row or "").strip() or DEFAULT_STUDENT_NAME


def _student_grade(db: Session, student_id: uuid.UUID) -> Optional[int]:
    """取学生年级（无记录时返回 None）。"""
    row = db.execute(select(Student.grade).where(Student.id == student_id)).scalar_one_or_none()
    return None if row is None else _safe_int(row)


def generate_weekly_report(
    db: Session,
    student_id: uuid.UUID,
    *,
    force: bool = False,
    now: Optional[datetime] = None,
) -> Tuple[WeeklyReport, bool]:
    """生成（或复用）本周周报，并落库。

    UPSERT 规则（关键，别改）：
        * 已有本周记录且 ``force=False`` → **直接返回缓存**，不调 AI、不写库；
        * 已有本周记录且 ``force=True``  → 原地更新 ``snapshot_json`` / ``report_text``，
          **保留原 ``token``**（家长手上的链接必须一直可用，S16），
          仅当 ``expires_at`` 已过期才顺手续到 ``now + 30d``；
        * 无记录 → 新建，``token = secrets.token_urlsafe(24)``，``expires_at = now + 30d``。

    Args:
        db: 数据库会话（本函数内部 ``commit()``，与 profile_service 风格一致）。
        student_id: 学生 ID。
        force: True 时跳过缓存强制重算 + 重调 AI。
        now: 参照时刻，测试用；缺省取当前 UTC。

    Returns:
        ``(WeeklyReport, cached)``，``cached=True`` 表示命中缓存未重算。
    """
    # 延迟导入：避免 services ←→ ai 之间形成模块级循环依赖
    from app.ai.agents import report as report_agent

    moment = as_utc(now) or utcnow()
    period = resolve_period(moment)

    existing = db.execute(
        select(WeeklyReport).where(
            WeeklyReport.student_id == student_id,
            WeeklyReport.period_start == period.start_utc,
        )
    ).scalar_one_or_none()

    if existing is not None and not force:
        return existing, True

    snapshot = build_weekly_snapshot(db, student_id, days=period.days, period=period)
    prev_snapshot, prev_start = load_prev_snapshot(db, student_id, period)
    comparison = diff_snapshots(snapshot, prev_snapshot, prev_period_start=prev_start)

    student_name = _student_display_name(db, student_id)
    narrative = report_agent.generate_report_text(snapshot, comparison, student_name)

    snapshot_payload = snapshot.to_dict()
    # 环比结果一并存档，公开页直接读，避免家长每次打开都重算
    snapshot_payload["comparison"] = comparison.to_dict()

    if existing is None:
        record = WeeklyReport(
            student_id=student_id,
            period_start=period.start_utc,
            period_end=period.end_utc,
            token=_new_token(),
            expires_at=moment + timedelta(days=REPORT_SHARE_TTL_DAYS),
            view_count=0,
            snapshot_json=snapshot_payload,
            report_text=narrative,
        )
        db.add(record)
    else:
        record = existing
        record.period_end = period.end_utc
        record.snapshot_json = snapshot_payload
        record.report_text = narrative
        record.updated_at = moment
        # 只有已过期才续期；未过期一律保留原 expires_at，不给家长「链接突然变了」的错觉
        if record.is_expired(moment):
            record.expires_at = moment + timedelta(days=REPORT_SHARE_TTL_DAYS)

    db.commit()
    db.refresh(record)
    return record, False


def build_share_url(token: str) -> str:
    """拼出发给家长的完整分享链接。

    生产环境必须把 ``PUBLIC_BASE_URL`` 配成真实域名，否则链接指向 localhost。
    """
    from app.core.config import settings

    base = str(getattr(settings, "public_base_url", "") or "").rstrip("/")
    return f"{base}/r/{token}"


# ================================================================ T5 公开读取
def get_report_by_token(
    db: Session,
    token: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[WeeklyReport]:
    """按分享令牌取未过期的周报。

    空 token / 超长 token 直接返回 ``None``，不进 DB（防扫描）。
    调用方对 ``None`` 一律返回同一句 404 文案，**不区分「不存在」与「已过期」**。

    Args:
        db: 数据库会话。
        token: 分享令牌。
        now: 参照时刻，测试用。

    Returns:
        ``WeeklyReport`` 或 ``None``。
    """
    value = str(token or "").strip()
    if not value or len(value) > MAX_TOKEN_LENGTH:
        return None

    moment = as_utc(now) or utcnow()
    record = db.execute(
        select(WeeklyReport).where(WeeklyReport.token == value)
    ).scalar_one_or_none()

    if record is None or record.is_expired(moment):
        return None
    return record


def touch_view_count(db: Session, report: WeeklyReport) -> int:
    """家长每打开一次分享页 ``view_count += 1``（U4：MVP 核心假设验证指标）。

    Args:
        db: 数据库会话（本函数内部 commit）。
        report: 目标周报。

    Returns:
        累加后的次数；写失败时静默回滚并返回原值（打点绝不能影响家长看报告）。
    """
    try:
        report.view_count = _safe_int(report.view_count) + 1
        db.commit()
        return int(report.view_count)
    except Exception:  # noqa: BLE001 - 打点失败不能影响正文渲染
        db.rollback()
        logger.warning("view_count 累加失败 report=%s", report.id)
        return _safe_int(getattr(report, "view_count", 0))


def build_report_payload(db: Session, report: WeeklyReport) -> Dict[str, Any]:
    """把一条 ``weekly_reports`` 组装成公开分享页的完整载荷（§4.4②）。

    前端直接渲染，不做二次计算。载荷里**只出现** ``real_name``（空则「孩子」）
    与 ``grade``，绝不出现手机号 / student_id / user_id / 题目原文（S17）。

    Args:
        db: 数据库会话。
        report: 周报记录。

    Returns:
        完整载荷 dict。
    """
    raw = report.snapshot_json if isinstance(report.snapshot_json, dict) else {}
    snapshot = WeeklySnapshot.from_dict(raw)
    engagement = snapshot.engagement

    comparison_raw = raw.get("comparison")
    comparison = comparison_raw if isinstance(comparison_raw, dict) else ComparisonResult().to_dict()

    # 上周分数映射，用于给 Top3 附 prev_score / delta
    prev_scores: Dict[str, float] = {}
    prev_snapshot, _ = load_prev_snapshot(
        db,
        report.student_id,
        ReportPeriod(
            start_utc=as_utc(report.period_start) or utcnow(),
            end_utc=as_utc(report.period_end) or utcnow(),
            start_local=(as_utc(report.period_start) or utcnow()).astimezone(LOCAL_TZ).date(),
            end_local=(as_utc(report.period_end) or utcnow()).astimezone(LOCAL_TZ).date(),
            is_partial=False,
        ),
    )
    if prev_snapshot is not None:
        prev_scores = {
            str(item.get("kp_id")): _safe_float(item.get("score"))
            for item in prev_snapshot.mastery
            if item.get("kp_id")
        }

    weak_points: List[Dict[str, Any]] = []
    for item in snapshot.weak_points():
        kp_id = str(item.get("kp_id") or "")
        score = round(_safe_float(item.get("score")), 4)
        prev_score = prev_scores.get(kp_id)
        weak_points.append(
            {
                "kp_id": kp_id,
                "kp_name": str(item.get("kp_name") or "未命名知识点"),
                "score": score,
                "level": _safe_int(item.get("level")),
                "error_rate": round(_safe_float(item.get("error_rate")), 4),
                "prev_score": (None if prev_score is None else round(prev_score, 4)),
                "delta": (None if prev_score is None else round(score - prev_score, 4)),
            }
        )

    narrative_raw = report.report_text if isinstance(report.report_text, dict) else {}
    advice = narrative_raw.get("advice_items")
    narrative = {
        "headline": str(narrative_raw.get("headline") or ""),
        "overview_text": str(narrative_raw.get("overview_text") or ""),
        "progress_text": str(narrative_raw.get("progress_text") or ""),
        "advice_items": [str(item) for item in advice if str(item).strip()] if isinstance(advice, list) else [],
        "source": str(narrative_raw.get("source") or "fallback"),
    }

    # 雷达图：优先读快照（新报告生成时已存）；老快照无雷达数据则实时补算一次
    # （纯算法、开销小，避免家长必须手动重新生成才能看到）。
    radar_raw = snapshot.radar
    if not isinstance(radar_raw, dict) or not radar_raw.get("axes"):
        try:
            radar_view = profile_service.get_radar(db, student_id=report.student_id)
            radar_raw = _radar_snapshot(radar_view)
        except Exception:  # noqa: BLE001 - 雷达缺失绝不能影响正文渲染
            logger.warning("雷达补算失败 report=%s", report.id)
            radar_raw = {}

    period_start = as_utc(report.period_start)
    period_end = as_utc(report.period_end)
    start_local = _to_local(period_start)
    end_local = _to_local(period_end)
    now_local = utcnow().astimezone(LOCAL_TZ)

    is_empty = engagement.is_empty()
    is_low = engagement.is_low_data()

    return {
        "student": {
            "display_name": _student_display_name(db, report.student_id),
            "grade": _student_grade(db, report.student_id),
        },
        "period": {
            "start": start_local.date().isoformat() if start_local else None,
            "end": end_local.date().isoformat() if end_local else None,
            "is_partial": bool(end_local is not None and now_local < end_local),
        },
        "engagement": engagement.to_dict(),
        "error_patterns": snapshot.error_patterns,
        "weak_points": weak_points,
        "comparison": comparison,
        "radar": radar_raw,
        "narrative": narrative,
        "empty_state": {
            "is_empty": is_empty,
            "message": "这周还没有练习记录～周末陪孩子做几道题，下周就能看到变化啦" if is_empty else None,
        },
        "low_data": {
            "is_low": is_low,
            "message": "本周练习较少，能力判断仅供参考" if is_low else None,
        },
        "generated_at": _iso(_to_local(as_utc(report.updated_at) or as_utc(report.created_at))),
        "expires_at": _iso(as_utc(report.expires_at)),
    }


def list_reports(
    db: Session,
    student_id: uuid.UUID,
    *,
    limit: int = 20,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """历史周报摘要列表，按 ``period_start`` 倒序（P1 · R-08）。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        limit: 返回条数上限。
        now: 参照时刻，用于判定 ``is_expired``。

    Returns:
        摘要 dict 列表。
    """
    moment = as_utc(now) or utcnow()
    rows = list(
        db.execute(
            select(WeeklyReport)
            .where(WeeklyReport.student_id == student_id)
            .order_by(WeeklyReport.period_start.desc())
            .limit(max(1, _safe_int(limit, 20)))
        ).scalars()
    )

    items: List[Dict[str, Any]] = []
    for row in rows:
        start_local = _to_local(as_utc(row.period_start))
        end_local = _to_local(as_utc(row.period_end))
        items.append(
            {
                "report_id": str(row.id),
                "period_start": start_local.date().isoformat() if start_local else None,
                "period_end": end_local.date().isoformat() if end_local else None,
                "share_token": row.token,
                "share_url": build_share_url(row.token),
                "expires_at": _iso(as_utc(row.expires_at)),
                "created_at": _iso(as_utc(row.created_at)),
                "view_count": _safe_int(row.view_count),
                "is_expired": row.is_expired(moment),
            }
        )
    return items


__all__ = [
    "LOCAL_TZ",
    "ReportPeriod",
    "EngagementStat",
    "WeeklySnapshot",
    "ComparisonResult",
    "resolve_period",
    "count_engagement",
    "build_weekly_snapshot",
    "load_prev_snapshot",
    "diff_snapshots",
    "generate_weekly_report",
    "build_share_url",
    "get_report_by_token",
    "touch_view_count",
    "build_report_payload",
    "list_reports",
]

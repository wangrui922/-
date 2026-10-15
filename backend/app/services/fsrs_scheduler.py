"""复习调度统一入口（方向三）—— review_cards / review_logs 表对应的调度核心。

双版本裁决结论（详见 ``docs/scheduler-py-fsrs-migration.md``）：
    仓库历史上一度存在两套复习调度（本模块的自研 FSRS 语义适配 + ``review_scheduler``
    的 SM-2），排程口径不一致。现**统一入口收在本模块**，内核可在两者间切换：

        * ``sm2``     —— 既有 SM-2 内核（默认，灰度基线，零风险）；
        * ``fsrs_py`` —— py-fsrs（社区验证的 FSRS 实现，MIT 许可，可商用）；
                        **lazy import**，未安装时自动降级 SM-2，绝不阻断应用启动。

    切换开关在 ``app.core.config.Settings``：
        * ``scheduler`` = ``fsrs_py`` | ``sm2``（默认 ``sm2``）；
        * ``scheduler_fsrs_rollout_ratio`` = 0~1，按 ``student_id`` 稳定哈希分批放量，
          同一学生哈希恒定 → 体验始终一致（详见 ``resolve_scheduler_mode``）。

    回滚：把 ``SCHEDULER`` 改回 ``sm2``（或 ratio 调成 0）即可，**无需改代码、无需改数据**。

对外契约（不得变更）：
    ``FSRSScheduler`` / ``Card`` / ``ReviewLog`` 的方法名、参数、返回结构保持原样，
    只替换内部算法内核。既有单测（``tests/test_fsrs_scheduler.py``）与
    ``tests/test_review_service_fsrs.py`` 不感知内核切换。

数据口径（保证回滚无损）：
    ``review_cards.difficulty`` 列在**两种内核下都存 SM-2 难度系数 EF**（不做
    py-fsrs 的 1~10 difficulty 落库）。py-fsrs 的 difficulty 由 EF 线性可逆映射而来
    （见 ``_ef_to_pyfsrs_difficulty`` / ``_pyfsrs_difficulty_to_ef``），
    因此学生从 fsrs_py 切回 sm2 时已存学习状态不丢、不乱。

硬约束（与 review_scheduler 一致）：本模块**不 import 任何 ORM / Session /
FastAPI**，只吃 dataclass 吐 dataclass，可被纯单测覆盖。

时区约定：入参时间统一按 UTC 处理（与 review_scheduler / mastery_engine 一致）；
naive datetime 视为 UTC。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from app.services.review_scheduler import (
    INITIAL_EASE_FACTOR,
    LAPSE_QUALITY_THRESHOLD,
    MAX_EASE_FACTOR,
    MIN_EASE_FACTOR,
    ReviewPlan,
    ReviewState,
    _sm2_review_core,
)

logger = logging.getLogger("app.services.fsrs_scheduler")

# ------------------------------------------------------------------- 常量
#: rating（1=Again, 2=Hard, 3=Good, 4=Easy）→ SM-2 quality（0-5）
RATING_TO_QUALITY: dict[int, int] = {1: 1, 2: 3, 3: 4, 4: 5}
#: 默认 quality（rating 非法时的兜底，等价于 Good）
DEFAULT_QUALITY: int = RATING_TO_QUALITY[3]

#: 状态机取值（review_cards.state / review_logs.state）
STATE_NEW = 0  # 新建未调度
STATE_LEARNING = 1  # 学习中（首次成功 / 重学后首次成功）
STATE_REVIEW = 2  # 稳定复习中
STATE_RELEARNING = 3  # 遗忘 / 答错，重新学习中

# --------------------------------------------------------- 调度内核开关取值
MODE_SM2 = "sm2"  # 既有 SM-2 内核（默认）
MODE_FSRS_PY = "fsrs_py"  # py-fsrs 内核（未安装时自动降级 sm2）

#: py-fsrs 目标保留率。
#: ⚠️ 这是**对齐 SM-2 基线的校准值**，不是 FSRS 社区默认（0.9）。
#: 实测（fsrs 6.3.2 / 1000 次双跑 / seed=20261001 / 真实重建路径）：
#:     0.930 → 26.4%，0.936 → 7.6%，**0.938 → 2.24%**，0.940 → 6.4%，0.950 → 39.7%。
#: 该曲线对保留率**极敏感**（±0.002 就能从 2% 跳到 6%），属 knife-edge 拟合，
#: 仅保证在这条评分分布下与 SM-2 同节奏。内测积累真实 review_logs 后必须重新拟合，
#: 并优先改用「真实留存率」而非「与 SM-2 对齐」作为目标函数。
PYFSRS_DESIRED_RETENTION: float = 0.938
#: py-fsrs 学习步（天）。本项目排程是**天粒度**（review_cards.scheduled_days 是整数天），
#: 默认「分钟级」学习步会导致首几次复习挤在同一天、间隔 0 天。
#:
#: ⚠️ **必须保持单步**：我们的 Card 契约不保存 fsrs 6.x 的 ``step``（学习步进度），
#: 每次复习都要从 Card 重建 py-fsrs Card，`step` 恒为 0。实测多步配置会让卡片
#: **永久卡死**在学习步上——例如 (1,4) 天时连续 Good 的间隔恒为 [4,4,4,4,4,4]，
#: 卡片永远无法毕业到 Review。单步 (1,) 时首次复习即毕业，重建无损。
#: （详见 docs/scheduler-py-fsrs-migration.md §6.4「已知陷阱」）
PYFSRS_LEARNING_STEPS_DAYS: tuple = (1.0,)
#: 遗忘后的重学步（天）
PYFSRS_RELEARNING_STEPS_DAYS: tuple = (1.0,)
#: py-fsrs 最大间隔（天）上限，与本项目 K-12 学期尺度匹配
PYFSRS_MAXIMUM_INTERVAL_DAYS: int = 365


# ==================================================================== 数据结构
@dataclass
class Card:
    """FSRS 复习卡片（与 review_cards 表 15 列对齐，不含时间戳列）。

    支持无参构造（``Card()`` → 全新未调度卡片）与具名构造
    （review_service.submit_review 从库里重建时使用）。
    """

    id: Optional[str] = None
    due: Optional[datetime] = None
    stability: float = 0.0
    difficulty: float = 0.0
    elapsed_days: int = 0
    scheduled_days: int = 0
    reps: int = 0
    lapses: int = 0
    state: int = STATE_NEW
    last_review: Optional[datetime] = None


@dataclass
class ReviewLog:
    """一次复习的调度日志（与 review_logs 表 13 列对齐，不含 id/card_id/
    student_id/review_at——这些由调用方填写）。

    字段顺序与表列一致，方便将来直接落库。
    """

    rating: int = 0
    state: int = STATE_NEW
    elapsed_days: int = 0
    scheduled_days: int = 0
    stability_before: float = 0.0
    stability_after: float = 0.0
    difficulty_before: float = 0.0
    difficulty_after: float = 0.0


# ==================================================================== 工具函数
def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """naive datetime 兜底补 UTC，aware 原样返回。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _rating_to_quality(rating: int) -> int:
    """rating（1-4）→ SM-2 quality（0-5）。非法值兜底到 Good。"""
    q = RATING_TO_QUALITY.get(int(rating))
    return DEFAULT_QUALITY if q is None else q


def _ef_to_difficulty(ef: float) -> float:
    """SM-2 EF → difficulty 列编码（直接存 EF 本身）。

    0~10 归一化会在 EF>2.5（连续 Easy 上浮）时饱和而丢失信息，破坏
    跨复习可逆性，因此不做归一化：difficulty 列 = SM-2 难度系数 EF。
    语义上「EF 高 = 更容易记」与「difficulty 低 = 难度小」方向一致。
    """
    return round(float(ef), 4)


def _next_state(lapsed: bool, reps: int) -> int:
    """依据本review是否遗忘与新的连续成功次数，决定新的 state。

    * 遗忘（q < 3）：进入 Relearning，reps 归零；
    * 成功：第 1 次成功 → Learning（含重学后首次成功）；
      连续第 2 次及以上 → Review。
    """
    if lapsed:
        return STATE_RELEARNING
    return STATE_LEARNING if reps <= 1 else STATE_REVIEW


def _sm2_restore_ef(card: Card) -> float:
    """把库里存的 difficulty（SM-2 EF 编码）还原为 EF。

    difficulty 列直接编码 SM-2 难度系数 EF（初始 2.5、下限 1.3、无上限），
    保证跨复习 EF 连续可逆；新卡（difficulty=0 且未复习过）→ 初始 EF。

    该函数同时是**旧 SM-2 状态回填**的唯一入口：py-fsrs 内核也用它把库里的
    difficulty 还原成难度，从而保证已存学习状态在内核切换时不丢失。
    """
    if card.difficulty <= 0 and card.reps == 0:
        return INITIAL_EASE_FACTOR
    return max(MIN_EASE_FACTOR, card.difficulty or INITIAL_EASE_FACTOR)


# ================================================================ 内核裁决
def _stable_ratio(key: str) -> float:
    """把任意 key 稳定映射到 [0, 1)（SHA-256 前 8 字节 → uint64 归一化）。

    同一 key 恒定得到同一值，用于按学生分批灰度放量：保证同一学生
    每次调度都命中同一内核，复习体验一致。
    """
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def resolve_scheduler_mode(student_id: Optional[str] = None) -> str:
    """统一裁决：本次调度应使用哪个内核（``fsrs_py`` | ``sm2``）。

    裁决顺序：
        1. ``Settings.scheduler`` 归一化后不是 ``fsrs_py`` → ``sm2``；
        2. 灰度比例 >= 1 → 全量 ``fsrs_py``（py-fsrs 未安装则降级 ``sm2``）；
        3. 灰度比例 <= 0 或拿不到 student_id → ``sm2``；
        4. 否则按 ``_stable_ratio(student_id) < ratio`` 决定。

    Args:
        student_id: 学生 ID（用于稳定哈希分批放量）；缺省则只按全局开关裁决。

    Returns:
        ``MODE_FSRS_PY`` 或 ``MODE_SM2``。
    """
    try:
        from app.core.config import get_settings

        settings = get_settings()
        mode = str(getattr(settings, "scheduler_mode", MODE_SM2))
        ratio = float(getattr(settings, "scheduler_fsrs_rollout_ratio_safe", 0.0))
    except Exception as exc:  # 配置不可用（极少见）→ 保守回落 SM-2
        logger.warning("复习调度配置读取失败，回落 SM-2：%s", exc)
        return MODE_SM2

    if mode != MODE_FSRS_PY:
        return MODE_SM2
    if not pyfsrs_available():
        # 开关打开了但包没装 —— 静默降级，绝不阻断应用
        return MODE_SM2
    if ratio >= 1.0:
        return MODE_FSRS_PY
    if ratio <= 0.0 or not student_id:
        return MODE_SM2
    return MODE_FSRS_PY if _stable_ratio(str(student_id)) < ratio else MODE_SM2


# ============================================================ py-fsrs 适配层
#: py-fsrs 模块/对象缓存（lazy import 结果，None 表示尚未探测）
_PYFSRS_CACHE: Optional[Dict[str, Any]] = None
#: py-fsrs 导入失败原因（缓存后不再重复尝试，避免每次调度都付 import 代价）
_PYFSRS_IMPORT_ERROR: Optional[str] = None


def _import_pyfsrs() -> Optional[Dict[str, Any]]:
    """lazy import py-fsrs；未安装或不可用时返回 ``None``（**绝不抛异常**）。

    返回的字典含 ``Card`` / ``Rating`` / ``State`` / ``Scheduler`` / ``scheduler``
    五个键；``scheduler`` 是按本项目「天粒度」口径配置好的实例。

    缓存策略：
        * 成功 → 缓存复用；
        * 失败 → 缓存失败原因，后续直接返回 ``None``（不再重复 import）。
        需要重新探测（例如单测注入桩）时调用 ``reset_pyfsrs_cache()``。
    """
    global _PYFSRS_CACHE, _PYFSRS_IMPORT_ERROR

    if _PYFSRS_CACHE is not None:
        return _PYFSRS_CACHE
    if _PYFSRS_IMPORT_ERROR is not None:
        return None

    try:
        from fsrs import Card as PyCard  # type: ignore[import-not-found]
        from fsrs import Rating as PyRating  # type: ignore[import-not-found]
        from fsrs import Scheduler as PyScheduler  # type: ignore[import-not-found]
        from fsrs import State as PyState  # type: ignore[import-not-found]
    except ImportError as exc:
        _PYFSRS_IMPORT_ERROR = str(exc)
        logger.warning(
            "py-fsrs 未安装（%s），复习调度自动降级为 SM-2；"
            "如需启用请安装 py-fsrs>=5.0,<6 并把 SCHEDULER 设为 fsrs_py",
            exc,
        )
        return None
    except Exception as exc:  # 防御：包存在但依赖损坏（如 pydantic 版本冲突）
        _PYFSRS_IMPORT_ERROR = str(exc)
        logger.warning("py-fsrs 导入异常，复习调度自动降级为 SM-2：%s", exc)
        return None

    _PYFSRS_CACHE = {
        "Card": PyCard,
        "Rating": PyRating,
        "State": PyState,
        "Scheduler": PyScheduler,
        "scheduler": _build_pyfsrs_scheduler(PyScheduler),
    }
    return _PYFSRS_CACHE


def _build_pyfsrs_scheduler(scheduler_cls: Any) -> Any:
    """构造 py-fsrs ``Scheduler`` 实例，参数按本项目口径对齐。

    对齐要点：
        * ``learning_steps`` 用**天**粒度，与本项目整数天排程同尺度，
          避免默认「分钟级」学习步导致首几次复习挤在同一天（间隔 0 天）；
        * ``desired_retention`` 与学习步经 1000 次双跑实测校准（见常量处注释），
          目的是让新内核排出的间隔与现状 SM-2 对齐，切换对学生无感；
        * ``enable_fuzzing=False`` —— 关闭随机模糊化，保证同输入同输出可回归；
        * ``maximum_interval=365`` —— K-12 学期尺度上限，避免间隔膨胀到数十年。

    参数不被当前版本支持时（跨版本差异）回落默认构造。
    """
    try:
        return scheduler_cls(
            desired_retention=PYFSRS_DESIRED_RETENTION,
            learning_steps=tuple(
                timedelta(days=float(d)) for d in PYFSRS_LEARNING_STEPS_DAYS
            ),
            relearning_steps=tuple(
                timedelta(days=float(d)) for d in PYFSRS_RELEARNING_STEPS_DAYS
            ),
            maximum_interval=PYFSRS_MAXIMUM_INTERVAL_DAYS,
            enable_fuzzing=False,
        )
    except TypeError as exc:
        logger.warning("py-fsrs Scheduler 自定义参数不被支持，回落默认配置：%s", exc)
        return scheduler_cls()


def pyfsrs_available() -> bool:
    """py-fsrs 是否可用（lazy import 成功且拿到 Scheduler 实例）。"""
    return _import_pyfsrs() is not None


def reset_pyfsrs_cache() -> None:
    """清空 py-fsrs 探测缓存（仅供单测注入桩 / 运维热重载使用）。"""
    global _PYFSRS_CACHE, _PYFSRS_IMPORT_ERROR
    _PYFSRS_CACHE = None
    _PYFSRS_IMPORT_ERROR = None


def _ef_to_pyfsrs_difficulty(ef: float) -> float:
    """SM-2 EF → py-fsrs difficulty（1~10），线性可逆映射。

    EF 取值域钳到 ``[MIN_EASE_FACTOR, MAX_EASE_FACTOR]``（1.3~3.0）后线性映射到
    ``[10, 1]``：EF 越高（越好记）→ py-fsrs difficulty 越低。
    与 :func:`_pyfsrs_difficulty_to_ef` 互为逆运算，保证状态不失真。
    """
    span: float = MAX_EASE_FACTOR - MIN_EASE_FACTOR
    ef = min(MAX_EASE_FACTOR, max(MIN_EASE_FACTOR, float(ef)))
    return round(1.0 + (MAX_EASE_FACTOR - ef) * 9.0 / span, 4)


def _pyfsrs_difficulty_to_ef(difficulty: float) -> float:
    """py-fsrs difficulty（1~10）→ SM-2 EF，:func:`_ef_to_pyfsrs_difficulty` 的逆运算。"""
    span: float = MAX_EASE_FACTOR - MIN_EASE_FACTOR
    d = min(10.0, max(1.0, float(difficulty)))
    return round(MAX_EASE_FACTOR - (d - 1.0) * span / 9.0, 4)


def _to_pyfsrs_state(state_cls: Any, state: int) -> Any:
    """我们的 state（0-3）→ py-fsrs ``State``。

    版本差异：fsrs 5.x 有 ``State.New == 0``；**6.x 移除了 New**
    （只剩 Learning=1 / Review=2 / Relearning=3）。因此新卡（0）在 6.x 上
    要落成 ``Learning``，否则 ``State(0)`` 会抛 ValueError。
    """
    value: int = int(state)
    try:
        return state_cls(value)
    except (TypeError, ValueError):
        pass
    # 新卡在 6.x 上没有 New：按 Learning 处理（step 由 Card 默认给出）
    try:
        return state_cls(STATE_LEARNING)
    except (TypeError, ValueError):
        return STATE_LEARNING


def _from_pyfsrs_state(state_obj: Any, lapsed: bool, reps: int) -> int:
    """py-fsrs ``State`` → 我们的 state（0-3）；不可解析时用状态机兜底。"""
    value = getattr(state_obj, "value", state_obj)
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        return _next_state(lapsed, reps)
    if value_int in (STATE_NEW, STATE_LEARNING, STATE_REVIEW, STATE_RELEARNING):
        return value_int
    return _next_state(lapsed, reps)


def _to_pyfsrs_rating(rating_cls: Any, rating: int) -> Any:
    """rating（1-4）→ py-fsrs ``Rating``；非法值兜底 Good(3)。"""
    try:
        value = int(rating)
    except (TypeError, ValueError):
        value = 3
    if value not in (1, 2, 3, 4):
        value = 3
    try:
        return rating_cls(value)
    except (TypeError, ValueError):
        return value


def _to_pyfsrs_card(mod: Dict[str, Any], card: Card) -> Any:
    """把我们的 :class:`Card` 转成 py-fsrs 的 Card。

    difficulty 走 EF→py-fsrs 映射（不直接落 1~10 值），保证与 review_cards
    既有列语义一致、回滚无损。

    版本差异：fsrs 6.x 的 Card **没有** ``elapsed_days`` / ``scheduled_days`` /
    ``reps`` / ``lapses`` 四个字段（5.x 有）。这里统一按「先构造、缺字段跳过」
    处理；同时 6.x 新卡的 ``stability`` / ``difficulty`` 默认是 ``None``
    （由首次 rating 反推），所以**新卡不传这两个值**，避免用我们的 EF 去污染
    FSRS 的初始难度估计。
    """
    #: 从未复习过的全新卡片（有 last_review 但 reps=0 属于「遗忘后重学」，不算全新）
    brand_new: bool = card.last_review is None and int(card.reps or 0) == 0

    kwargs: Dict[str, Any] = {
        "due": _as_utc(card.due),
        "elapsed_days": int(card.elapsed_days or 0),
        "scheduled_days": int(card.scheduled_days or 0),
        "reps": int(card.reps or 0),
        "lapses": int(card.lapses or 0),
        "state": _to_pyfsrs_state(mod["State"], card.state),
        "last_review": _as_utc(card.last_review),
    }
    if not brand_new:
        kwargs["stability"] = float(card.stability or 0.0)
        kwargs["difficulty"] = _ef_to_pyfsrs_difficulty(_sm2_restore_ef(card))
    if kwargs["due"] is None:
        # 全新卡片没有 due：交给 py-fsrs 用默认值填充，避免 None 触发类型校验
        kwargs.pop("due")
    try:
        return mod["Card"](**kwargs)
    except TypeError:
        # 版本差异：逐个字段赋值，缺失字段跳过（保持最大兼容）
        py_card = mod["Card"]()
        for key, value in kwargs.items():
            if hasattr(py_card, key):
                setattr(py_card, key, value)
        return py_card


# ================================================================ 内核实现
def _sm2_review_card(
    card: Card, rating: int, now: datetime
) -> Tuple[Card, ReviewLog]:
    """SM-2 内核：把一次复习 rating 推进为新的卡片排程（既有着法，行为不变）。"""
    q = _rating_to_quality(rating)

    # elapsed_days：距上次复习的自然日（首复习为 0）
    last = _as_utc(card.last_review)
    elapsed_days = max(0, (now - last).days) if last else (card.elapsed_days or 0)

    # 委托 SM-2 核心计算新的 EF / 间隔 / 连续次数 / 到期时间
    plan = _sm2_review_core(
        ReviewState(
            key=str(card.id or "card"),
            ease_factor=_sm2_restore_ef(card),
            interval_days=card.scheduled_days,
            repetitions=card.reps,
            last_reviewed_at=card.last_review,
            due_at=card.due,
        ),
        q,
        now,
    )

    lapsed = q < LAPSE_QUALITY_THRESHOLD
    reps = plan.repetitions
    lapses = card.lapses + (1 if lapsed else 0)

    new_card = Card(
        id=card.id,
        due=plan.due_at,
        stability=float(plan.interval_days),
        difficulty=_ef_to_difficulty(plan.ease_factor),
        elapsed_days=elapsed_days,
        scheduled_days=plan.interval_days,
        reps=reps,
        lapses=lapses,
        state=_next_state(lapsed, reps),
        last_review=plan.last_reviewed_at,
    )
    log = ReviewLog(
        rating=int(rating),
        state=new_card.state,
        elapsed_days=elapsed_days,
        scheduled_days=plan.interval_days,
        stability_before=card.stability,
        stability_after=new_card.stability,
        difficulty_before=card.difficulty,
        difficulty_after=new_card.difficulty,
    )
    return new_card, log


def _pyfsrs_review_card(
    card: Card, rating: int, now: datetime
) -> Tuple[Card, ReviewLog]:
    """py-fsrs 内核：把一次复习 rating 推进为新的卡片排程。

    py-fsrs 不可用时**自动降级**到 :func:`_sm2_review_card`，保证任何环境下
    复习链路都不会中断（这是本项目「绝不阻断启动/主流程」的硬要求）。
    """
    mod = _import_pyfsrs()
    if mod is None:
        return _sm2_review_card(card, rating, now)

    lapsed = _rating_to_quality(rating) < LAPSE_QUALITY_THRESHOLD
    # elapsed_days：距上次复习的自然日（首复习为 0），与 SM-2 内核口径一致
    last = _as_utc(card.last_review)
    elapsed_days = max(0, (now - last).days) if last else (card.elapsed_days or 0)

    try:
        py_card = _to_pyfsrs_card(mod, card)
        py_next, _py_log = mod["scheduler"].review_card(
            py_card,
            _to_pyfsrs_rating(mod["Rating"], rating),
            now,
        )
    except Exception as exc:  # 兜底：任何 py-fsrs 运行时异常都降级，不阻塞复习
        logger.warning("py-fsrs 调度失败，本次降级 SM-2：%s", exc)
        return _sm2_review_card(card, rating, now)

    # ---- 读回结果（fsrs 5.x / 6.x 字段差异在这里收敛）-------------------
    # 6.x 的 Card 没有 scheduled_days / reps / lapses，需自行推导或自维护
    due = _as_utc(getattr(py_next, "due", None))
    if due is None:
        due = now + timedelta(days=1)

    raw_scheduled = getattr(py_next, "scheduled_days", None)
    if raw_scheduled is not None:
        scheduled_days = int(round(float(raw_scheduled or 0)))
    else:
        # 6.x：间隔 = due - 本次复习时刻（天）
        scheduled_days = int(
            round(max(0.0, (due - now).total_seconds()) / 86400.0)
        )

    stability = float(getattr(py_next, "stability", None) or 0.0)

    raw_reps = getattr(py_next, "reps", None)
    reps = int(raw_reps) if raw_reps is not None else (0 if lapsed else card.reps + 1)
    raw_lapses = getattr(py_next, "lapses", None)
    lapses = (
        int(raw_lapses)
        if raw_lapses is not None
        else card.lapses + (1 if lapsed else 0)
    )
    state = _from_pyfsrs_state(getattr(py_next, "state", None), lapsed, reps)

    # difficulty 列统一存 SM-2 EF（回滚无损的核心约定）。
    # 6.x 新卡首复习前 difficulty 可能为 None → 沿用卡上既有 EF。
    raw_difficulty = getattr(py_next, "difficulty", None)
    if raw_difficulty is None:
        difficulty = _ef_to_difficulty(_sm2_restore_ef(card))
    else:
        difficulty = _ef_to_difficulty(_pyfsrs_difficulty_to_ef(raw_difficulty))

    new_card = Card(
        id=card.id,
        due=_as_utc(due),
        stability=stability,
        difficulty=difficulty,
        elapsed_days=elapsed_days,
        scheduled_days=scheduled_days,
        reps=reps,
        lapses=lapses,
        state=state,
        last_review=getattr(py_next, "last_review", None) or now,
    )
    log = ReviewLog(
        rating=int(rating),
        state=new_card.state,
        elapsed_days=elapsed_days,
        scheduled_days=scheduled_days,
        stability_before=card.stability,
        stability_after=new_card.stability,
        difficulty_before=card.difficulty,
        difficulty_after=new_card.difficulty,
    )
    return new_card, log


def _pyfsrs_review_state(
    state: ReviewState, quality: int, now: datetime
) -> ReviewPlan:
    """py-fsrs 内核的 ``ReviewState`` 形态（供 review_scheduler 兼容壳转发）。

    把 SM-2 的 ``ReviewState`` 映射成 Card → 跑 py-fsrs → 再映射回 ``ReviewPlan``，
    字段语义与 SM-2 内核保持一致（``ease_factor`` 仍是 EF，不是 py-fsrs difficulty）。
    """
    card = Card(
        id=state.key,
        due=_as_utc(state.due_at),
        stability=float(state.interval_days or 0),
        difficulty=_ef_to_difficulty(state.ease_factor or INITIAL_EASE_FACTOR),
        elapsed_days=0,
        scheduled_days=int(state.interval_days or 0),
        reps=int(state.repetitions or 0),
        lapses=0,
        state=STATE_REVIEW if int(state.repetitions or 0) > 1 else STATE_LEARNING,
        last_review=_as_utc(state.last_reviewed_at),
    )
    new_card, _log = _pyfsrs_review_card(card, _quality_to_rating(quality), now)
    lapsed = int(quality) < LAPSE_QUALITY_THRESHOLD
    return ReviewPlan(
        key=state.key,
        ease_factor=new_card.difficulty,
        interval_days=new_card.scheduled_days,
        repetitions=new_card.reps,
        last_reviewed_at=new_card.last_review or now,
        due_at=new_card.due or now,
        lapsed=lapsed,
    )


def _quality_to_rating(quality: int) -> int:
    """SM-2 quality（0-5）→ rating（1-4），:data:`RATING_TO_QUALITY` 的反向映射。

    映射口径（取「最接近的档位」）：
        * q <= 1 → 1 Again；q == 2 → 2 Hard；
        * q == 3 → 2 Hard（刚好过线，按 Hard 处理更保守）；
        * q == 4 → 3 Good；q >= 5 → 4 Easy。
    """
    try:
        q = int(quality)
    except (TypeError, ValueError):
        return 3
    if q <= 1:
        return 1
    if q <= 3:
        return 2
    if q == 4:
        return 3
    return 4


# ==================================================================== 调度器
class FSRSScheduler:
    """复习调度器（统一入口）：把一次复习 rating 推进为新的卡片排程。

    内核由 ``Settings.scheduler`` + 灰度比例裁决（见 :func:`resolve_scheduler_mode`）：
        * ``fsrs_py`` —— py-fsrs（未安装自动降级）；
        * ``sm2``     —— 既有 SM-2（默认）。

    ``student_id`` 只用于稳定哈希分批放量；缺省（``FSRSScheduler()``）时按全局
    开关裁决，与历史行为完全兼容。
    """

    def __init__(self, student_id: Optional[str] = None) -> None:
        self._student_id: Optional[str] = (
            str(student_id) if student_id is not None else None
        )

    @property
    def student_id(self) -> Optional[str]:
        """本调度器绑定的学生 ID（灰度放量用），未绑定为 ``None``。"""
        return self._student_id

    @property
    def mode(self) -> str:
        """本调度器当前命中的内核（``fsrs_py`` | ``sm2``）。"""
        return resolve_scheduler_mode(self._student_id)

    def review_card(
        self,
        card: Card,
        rating: int,
        now: Optional[datetime] = None,
    ) -> Tuple[Card, ReviewLog]:
        """根据一次复习的 rating（1=Again 2=Hard 3=Good 4=Easy）推进排程。

        Args:
            card: 当前卡片（不修改入参，返回新对象）。
            rating: 1=Again, 2=Hard, 3=Good, 4=Easy。
            now: 当前时间（UTC）；缺省取 ``datetime.now(timezone.utc)``。

        Returns:
            ``(new_card, log)``：新的卡片状态与本次调度日志。
        """
        now = _as_utc(now) or datetime.now(timezone.utc)
        if resolve_scheduler_mode(self._student_id) == MODE_FSRS_PY:
            return _pyfsrs_review_card(card, rating, now)
        return _sm2_review_card(card, rating, now)

    def get_retrievability(
        self,
        card: Card,
        now: Optional[datetime] = None,
    ) -> float:
        """计算卡片当前记忆保留概率 R(t)（0~1）。

        * SM-2 内核（默认）：指数遗忘曲线 ``R = 0.9 ** (t / stability)``，
          t 为距上次复习的天数；距上次复习 t = stability 天时 R ≈ 0.9（到期边界）。
        * py-fsrs 内核：FSRS 幂遗忘曲线 ``R = (1 + t / (9 * S)) ** -1``，
          t = S 时同样 R ≈ 0.9，与 90% 目标保留率口径一致。

        无稳定性或从未复习 → 返回 0.0。
        """
        if card.stability <= 0 or card.last_review is None:
            return 0.0
        now_utc = _as_utc(now) or datetime.now(timezone.utc)
        last = _as_utc(card.last_review)
        if last is None:  # 防御：上面的分支已排除，此处仅为类型收窄
            return 0.0
        t_days = max(0.0, (now_utc - last).total_seconds() / 86400.0)
        if resolve_scheduler_mode(self._student_id) == MODE_FSRS_PY:
            return round((1.0 + t_days / (9.0 * card.stability)) ** -1, 6)
        return round(0.9 ** (t_days / card.stability), 6)


# ============================================== 统一入口（SM-2 形态，兼容壳用）
def review_review_state(
    state: ReviewState,
    quality: int,
    now: Optional[datetime] = None,
    student_id: Optional[str] = None,
) -> ReviewPlan:
    """统一入口：以 SM-2 ``ReviewState`` 形态推进一次复习。

    ``app.services.review_scheduler.review`` 已降级为兼容壳，转发到这里；
    真正的内核裁决（py-fsrs / SM-2 + 灰度）只在本模块发生一次。
    """
    now = _as_utc(now) or datetime.now(timezone.utc)
    if resolve_scheduler_mode(student_id) == MODE_FSRS_PY:
        return _pyfsrs_review_state(state, quality, now)
    return _sm2_review_core(state, quality, now)


__all__ = [
    "Card",
    "ReviewLog",
    "FSRSScheduler",
    "RATING_TO_QUALITY",
    "DEFAULT_QUALITY",
    "STATE_NEW",
    "STATE_LEARNING",
    "STATE_REVIEW",
    "STATE_RELEARNING",
    "MODE_SM2",
    "MODE_FSRS_PY",
    "PYFSRS_DESIRED_RETENTION",
    "PYFSRS_MAXIMUM_INTERVAL_DAYS",
    "resolve_scheduler_mode",
    "review_review_state",
    "pyfsrs_available",
    "reset_pyfsrs_cache",
]

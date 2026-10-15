# -*- coding: utf-8 -*-
"""自适应学习计划编排层（V6.5 Sprint 5 · T07）。

定位
----
这是「让 AI 真正决定教什么」的**唯一编排入口**。此前 T05/T06 已建成四个纯函数
引擎（难度 / 优先级 / 出题编排 / 图谱缺口），但它们彼此孤立——本模块负责把它们
串成一份「今日学习计划」，并向上暴露给 API 与前端。

架构铁律（与引擎层的接缝）
--------------------------
* **引擎不碰数据库**：``rank_priorities`` / ``plan_exercises`` / ``decide_difficulty``
  都是纯函数，本模块负责取数并注入，链路**不得**反向出现在 ``app/engines/`` 内；
* **配置只读一次**：入口 ``read_adaptive_config(db)``，之后全程注入 ``AdaptiveConfig``；
* **跨方言**：SQL 只用标准语法。布尔求和用 ``func.sum(cast(col, Integer))``，
  **不用 PG 专属 ``FILTER``**；时间边界在 Python 侧算好再下发参数；
* **可降级优先于精巧**：任一环取数失败只记 warning 并退化为「该信号缺失」，
  最外层再兜一层「整条链路降级到普通抽题」——编排层挂掉绝不能让孩子白屏。

取数口径（架构师裁决②，勿改）
----------------------------
* ``mastery_items`` 用 ``mastery_rows(get_learning_dna(...))``——读已落库的
  ``StudentKnowledgeMastery`` 汇总表（一次查询），与家长端看板**同一口径**；
  不用 ``compute_mastery()`` 逐点捞事件（N 次查询且可能与看板打架）；
* ``results`` 只补「近窗口对错次数」这一项 ``mastery_items`` 没有的信息，
  时间维度的主口径仍在 ``mastery_items.last_practice_at``。

回滚开关（四个，各自独立生效）
------------------------------
* ``adaptive.enabled=false``   → 整条链路短路，走 ``_legacy_plan``；
* ``results`` 不传 / 传 ``None`` → 优先级退回 T06 算法（``BOOST=0``）；
* ``priority.wrong_boost=0``    → 只保留「取消近期降权」，去掉加分；
* ``priority.saturation_penalty=1.0`` → 关闭单点刷量抑制。
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.engines.config import AdaptiveConfig, read_adaptive_config
from app.engines.difficulty import decide_difficulty, difficulty_input_from_dna
from app.engines.generation import plan_exercises
from app.engines.priority import rank_priorities
from app.engines.types import DEFAULT_DIFFICULTY, clamp, mastery_rows, to_float, to_int
from app.models.base import utcnow
from app.models.content import KnowledgePoint, Question
from app.models.knowledge_prerequisite import KnowledgePrerequisite
from app.models.learning import Attempt
from app.services import student_dna_service
from app.services.learning_service import to_uuid
from app.services.mastery_engine import (
    MasteryResult,
    PrerequisiteEdge,
    PrerequisiteGap,
    detect_prerequisite_gaps,
)
from app.services.practice_service import (
    DEFAULT_COUNT,
    MAX_COUNT,
    MIN_COUNT,
    RECENT_DAYS,
    _draw_with_relaxation,  # noqa: PLC2701 - 刻意复用，禁止重写放宽逻辑
    _recent_question_ids,  # noqa: PLC2701
    draw_questions_by_plan,
)

logger = logging.getLogger("app.services.adaptive_plan")

#: 负责人任务号
OWNER_TASK: str = "T07"

#: 自适应编排产出的版本号（前端可按它判断是否走了新链路）
ENGINE_VERSION: str = "t07.1"
#: ``adaptive.enabled=false`` 时的降级版本号
ENGINE_VERSION_LEGACY: str = "legacy-draw"

#: 队列为空（综合摸底计划）时给前端的兜底理由
FALLBACK_REASON: str = "先做一轮综合摸底，看看哪里还需要补。"

#: 图谱出度缓存有效期（秒）。图谱极少变动，60s 内复用可以省掉每请求一次全表扫描；
#: **首次一定真实查库**，且缓存的是纯 dict（不是 ORM 对象，避免跨会话游离）。
GRAPH_CACHE_TTL_SEC: float = 60.0
_GRAPH_CACHE: Dict[str, Any] = {"at": None, "degrees": {}}

#: 掌握度行里「知识点 ID」的候选键名
_KP_ID_KEYS: Tuple[str, ...] = ("knowledge_point_id", "kp_id", "id")


# ============================================================ 通用小工具
def _text(value: Any, default: str = "") -> str:
    """把任意值安全转成去空白字符串；不可转换回退 ``default``。"""
    if value is None:
        return default
    try:
        text = str(value).strip()
    except Exception:  # noqa: BLE001 - 脏对象的 __str__ 可能抛异常
        return default
    return text or default


def _field(source: Any, names: Sequence[str], default: Any = None) -> Any:
    """同时兼容 dict 与对象：按顺序尝试键名 / 属性名。"""
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source.get(name)
        return default
    for name in names:
        try:
            if hasattr(source, name):
                return getattr(source, name)
        except Exception:  # noqa: BLE001 - 外部对象属性访问失败按缺失处理
            continue
    return default


def _engine_now(now: Optional[datetime] = None) -> datetime:
    """把时间基准归一成 **naive UTC**（引擎的时间解析契约）。

    优先级引擎的 ``_parse_time`` 会把 ``...+00:00`` / ``...Z`` 后缀剥掉后解析成
    naive datetime；若这里传 aware _datetime，两侧相减会直接 ``TypeError``。
    剥掉 tzinfo 与剥掉 ``+00:00`` 语义等价（同一时刻），故安全。
    """
    moment = now if isinstance(now, datetime) else utcnow()
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def _clamp_total(count: Any, settings: AdaptiveConfig) -> int:
    """归一化本批题数：非法值回退 ``DEFAULT_COUNT``，上界取配置 ``generation_max_total``。"""
    upper = max(1, to_int(getattr(settings, "generation_max_total", MAX_COUNT), MAX_COUNT))
    if count is None:
        return min(max(1, DEFAULT_COUNT), upper)
    value = to_int(count, DEFAULT_COUNT)
    return min(max(MIN_COUNT, value), upper)


# ============================================================ S1 results 聚合
def aggregate_recent_results(
    db: Session,
    *,
    student_id: Any,
    window_days: int = 7,
) -> Dict[str, Dict[str, Any]]:
    """按知识点聚合近 ``window_days`` 天的作答结果（T07-S1）。

    口径：

    * 窗口 ``[utcnow() - window_days, +∞)``，**闭左端**（``>= cutoff``）；
    * 只统计 ``Question.kp_id`` 非空的题（脏数据丢弃，不进任何推荐）；
    * ``recent_correct`` 夹到 ``[0, recent_attempts]``，防止脏流水算负错题；
    * ``wrong`` / ``wrong_rate`` **由引擎派生**，这里不产出——单一推导口径。

    Args:
        db: 数据库会话（只读，不 commit）。
        student_id: 学生 ID。
        window_days: 窗口天数；负数按 0 处理。

    Returns:
        ``{kp_id(str): {"recent_attempts", "recent_correct", "last_result_at"}}``。
        查库失败 / 无数据时返回空字典（**不抛异常**，编排层按「信号缺失」处理）。
    """
    sid = to_uuid(student_id, "student_id")
    days = max(0, int(to_int(window_days, 7)))
    cutoff = utcnow() - timedelta(days=days)

    results: Dict[str, Dict[str, Any]] = {}
    try:
        rows = db.execute(
            select(
                Question.kp_id,
                func.count(Attempt.id),
                # ⚠️ 跨方言：布尔求和用 CAST(col AS INTEGER)，不用 PG 专属 FILTER
                func.sum(cast(Attempt.is_correct, Integer)),
                func.max(Attempt.created_at),
            )
            .select_from(Attempt)
            .join(Question, Question.id == Attempt.question_id)
            .where(
                Attempt.student_id == sid,
                Attempt.created_at >= cutoff,
                Question.kp_id.isnot(None),
            )
            .group_by(Question.kp_id)
        ).all()
    except Exception as exc:  # noqa: BLE001 - 取数失败按「信号缺失」降级，不打挂编排
        logger.warning("近 %d 天作答聚合失败，本次不带 results 信号: %s", days, exc)
        return results

    for kp_id, attempts, correct, last_at in rows:
        if kp_id is None:
            continue
        total = max(0, to_int(attempts, 0))
        if total <= 0:
            continue
        right = min(max(0, to_int(correct, 0)), total)
        results[str(kp_id)] = {
            "recent_attempts": total,
            "recent_correct": right,
            "last_result_at": last_at.isoformat() if last_at is not None else None,
        }
    return results


# ============================================================ S2 图谱取数
def _prerequisite_edges(db: Session) -> List[PrerequisiteEdge]:
    """取全部前置依赖边（纯字符串，便于缓存与纯函数消费）。"""
    rows = db.execute(
        select(
            KnowledgePrerequisite.pre_kp_id,
            KnowledgePrerequisite.kp_id,
            KnowledgePrerequisite.weight,
        )
    ).all()
    edges: List[PrerequisiteEdge] = []
    for pre_kp_id, kp_id, weight in rows:
        if pre_kp_id is None or kp_id is None:
            continue  # 脏外键，丢弃
        edges.append(
            PrerequisiteEdge(
                pre_kp_id=str(pre_kp_id),
                kp_id=str(kp_id),
                weight=clamp(to_float(weight, 1.0), 0.0, 1.0),
            )
        )
    return edges


def build_graph_degrees(db: Session, *, use_cache: bool = True) -> Dict[str, int]:
    """取图谱出度 ``{kp_id: 被多少后置知识点依赖}``（T07-S2）。

    出度直接喂给优先级引擎的 ``graph`` 入参（它认 ``{kp_id: 出度}`` 映射）。
    进程内缓存 ``GRAPH_CACHE_TTL_SEC`` 秒；**首次调用一定真实查库**，且缓存的是
    纯 ``dict``（不是 ORM 行，避免跨会话游离实例）。

    Args:
        db: 数据库会话（只读）。
        use_cache: 关掉可做「强制查库」的测试。

    Returns:
        出度映射；查库失败返回空字典（该权重项自然失效，不抛异常）。
    """
    now_ts = time.monotonic()
    cached = _GRAPH_CACHE.get("degrees")
    cached_at = _GRAPH_CACHE.get("at")
    if (
        use_cache
        and isinstance(cached, dict)
        and isinstance(cached_at, float)
        and (now_ts - cached_at) < GRAPH_CACHE_TTL_SEC
    ):
        return dict(cached)

    degrees: Dict[str, int] = {}
    try:
        for edge in _prerequisite_edges(db):
            degrees[edge.pre_kp_id] = degrees.get(edge.pre_kp_id, 0) + 1
    except Exception as exc:  # noqa: BLE001 - 图谱取数失败只让 degree 权重失效
        logger.warning("图谱出度取数失败，本次不计入 degree 权重: %s", exc)
        return {}

    _GRAPH_CACHE["at"] = now_ts
    _GRAPH_CACHE["degrees"] = degrees
    return dict(degrees)


def clear_graph_cache() -> None:
    """清空图谱缓存（单测隔离用）。"""
    _GRAPH_CACHE["at"] = None
    _GRAPH_CACHE["degrees"] = {}


def _mastery_map(items: Any) -> Dict[str, MasteryResult]:
    """把 ``mastery_rows()`` 出来的行转成 ``detect_prerequisite_gaps`` 要的掌握度映射。

    ⚠️ 关键语义：``attempts`` 取 ``practice_count``。**练过 0 次的知识点
    ``attempts=0``**，``detect_prerequisite_gaps`` 据此实现「后置零样本不判缺口」——
    这条语义是本函数必须保住的，改动前请先跑
    ``tests/engines/test_prerequisite_gaps.py``。
    """
    mapping: Dict[str, MasteryResult] = {}
    for row in items if isinstance(items, (list, tuple)) else ():
        if not isinstance(row, Mapping):
            continue
        kp_id = _text(_field(row, _KP_ID_KEYS))
        if not kp_id:
            continue
        practice = max(0, to_int(_field(row, ("practice_count", "attempts")), 0))
        correct = min(max(0, to_int(_field(row, ("correct_count",)), 0)), practice)
        mapping[kp_id] = MasteryResult(
            score=clamp(to_float(_field(row, ("mastery", "mastery_score")), 0.0), 0.0, 1.0),
            level=max(0, to_int(_field(row, ("level",)), 0)),
            attempts=practice,
            correct_count=correct,
            error_rate=clamp(to_float(_field(row, ("error_rate",)), 0.0), 0.0, 1.0),
            effective_attempts=float(practice),
            last_practiced_at=None,
        )
    return mapping


def _kp_names(db: Session, raw_ids: Sequence[str]) -> Dict[str, str]:
    """按知识点 ID 批量取名（一次查询，避免 N+1）。"""
    ids: List[uuid.UUID] = []
    for raw in raw_ids:
        try:
            ids.append(uuid.UUID(_text(raw)))
        except (TypeError, ValueError):
            continue  # 非 UUID 的脏 ID 直接跳过
    if not ids:
        return {}
    try:
        rows = db.execute(
            select(KnowledgePoint.id, KnowledgePoint.name).where(
                KnowledgePoint.id.in_(ids)
            )
        ).all()
    except Exception as exc:  # noqa: BLE001 - 名称只是展示用，取不到就留空
        logger.warning("知识点名称取数失败，缺口文案里的名称留空: %s", exc)
        return {}
    return {str(kp_id): _text(name) for kp_id, name in rows}


def build_prerequisite_gaps(
    db: Session,
    *,
    student_id: Any,
    items: Any = None,
) -> List[PrerequisiteGap]:
    """检测前置知识点缺口（T07-S2，``detect_prerequisite_gaps`` 的首个真实调用方）。

    Args:
        db: 数据库会话（只读）。
        student_id: 学生 ID（仅用于日志定位；掌握度由 ``items`` 提供）。
        items: ``mastery_rows(get_learning_dna(...))`` 产出的掌握度行。

    Returns:
        缺口列表；无掌握度行 / 无依赖边 / 取数失败时返回空列表（不抛异常）。
    """
    mastery_map = _mastery_map(items)
    if not mastery_map:
        return []

    try:
        edges = _prerequisite_edges(db)
    except Exception as exc:  # noqa: BLE001 - 图谱取数失败按「无图」降级
        logger.warning("前置依赖边取数失败，本次不做缺口检测: %s", exc)
        return []
    if not edges:
        return []

    gaps = detect_prerequisite_gaps(mastery_map, edges)
    if not gaps:
        return []

    names = _kp_names(db, [gap.kp_id for gap in gaps] + [gap.pre_kp_id for gap in gaps])
    enriched: List[PrerequisiteGap] = []
    for gap in gaps:
        enriched.append(
            PrerequisiteGap(
                kp_id=gap.kp_id,
                pre_kp_id=gap.pre_kp_id,
                kp_name=names.get(gap.kp_id, ""),
                pre_kp_name=names.get(gap.pre_kp_id, ""),
                kp_score=gap.kp_score,
                kp_level=gap.kp_level,
                pre_score=gap.pre_score,
                pre_level=gap.pre_level,
                weight=gap.weight,
                missing=gap.missing,
            )
        )
    logger.debug(
        "前置缺口检测 student=%s 掌握度行 %d 条 / 依赖边 %d 条 → 缺口 %d 条",
        student_id,
        len(mastery_map),
        len(edges),
        len(enriched),
    )
    return enriched


# ============================================================ S5 编排主入口
def _bands_for(
    dna: Mapping[str, Any],
    ranked: Sequence[Any],
    settings: AdaptiveConfig,
    current_difficulty: float,
) -> List[Any]:
    """逐条优先级项算难度带（T05 引擎，纯计算）。"""
    bands: List[Any] = []
    for item in ranked:
        kp_id = _text(getattr(item, "knowledge_point_id", ""))
        payload = difficulty_input_from_dna(
            dna,
            knowledge_point_id=kp_id or None,
            current_difficulty=current_difficulty,
        )
        bands.append(decide_difficulty(payload, settings))
    return bands


def _plan_items(plans: Sequence[Any], ranked: Sequence[Any]) -> List[Dict[str, Any]]:
    """把出题计划与优先级队列拼成前端可直接展示的条目。"""
    by_id: Dict[str, Any] = {}
    for item in ranked:
        by_id[_text(getattr(item, "knowledge_point_id", ""))] = item

    payload: List[Dict[str, Any]] = []
    for plan in plans:
        source = by_id.get(_text(getattr(plan, "knowledge_point_id", "")))
        payload.append(
            {
                "knowledge_point_id": _text(getattr(plan, "knowledge_point_id", "")),
                "kp_name": _text(getattr(source, "kp_name", "")) if source else "",
                "question_count": max(0, to_int(getattr(plan, "question_count", 0), 0)),
                "difficulty_levels": [
                    int(level) for level in getattr(plan, "difficulty_levels", ())
                ],
                "strategy": _text(getattr(plan, "strategy", "")),
                "reason": _text(getattr(source, "reason", "")) if source else FALLBACK_REASON,
            }
        )
    return payload


def _legacy_plan(
    db: Session,
    *,
    student_id: uuid.UUID,
    want: int,
    grade: Optional[int],
    grade_band: Optional[str],
    edition: Optional[str],
    section: Optional[str],
    generated_at: datetime,
) -> Dict[str, Any]:
    """降级路径：``adaptive.enabled=false`` 或编排异常时的兜底。

    复用 ``_recent_question_ids`` + ``_draw_with_relaxation``，即 ``draw_questions``
    内部同一套放宽语义（**不重写**）。与 ``draw_questions`` 的唯一差别是不建
    ``LearningTask`` 会话——本编排层是 ``GET`` 语义，必须保持只读无副作用。

    Returns:
        与 ``build_adaptive_plan`` 同构的信封，``engine_version`` 为
        ``legacy-draw``、``items`` 为空列表。
    """
    recent = _recent_question_ids(db, student_id, days=RECENT_DAYS)
    picked, steps, _meta = _draw_with_relaxation(
        db,
        common={
            "grade": grade,
            "grade_band": grade_band,
            "edition": edition,
            "section": section,
            "kp_id": None,
            "difficulty": None,
        },
        recent=recent,
        want=want,
    )
    return {
        "generated_at": generated_at.isoformat(),
        "engine_version": ENGINE_VERSION_LEGACY,
        "total": want,
        "actual_count": len(picked),
        "miss_count": max(0, want - len(picked)),
        "question_ids": [str(qid) for qid in picked],
        "items": [],
        "relaxation_steps": steps,
    }


def build_adaptive_plan(
    db: Session,
    *,
    student_id: Any,
    grade: Optional[int] = None,
    grade_band: Optional[str] = None,
    edition: Optional[str] = None,
    section: Optional[str] = None,
    count: Optional[int] = None,
    config: Optional[AdaptiveConfig] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """编排「今日学习计划」（T07-S5，**本模块唯一对外入口**）。

    调用链（架构师裁决② 定死的唯一形态）::

        cfg      = read_adaptive_config(db)
        dna      = student_dna_service.get_learning_dna(db, student_id=...)
        items    = mastery_rows(dna)
        gaps     = build_prerequisite_gaps(db, student_id=..., items=items)
        graph    = build_graph_degrees(db)
        results  = aggregate_recent_results(db, student_id=..., window_days=cfg.priority_recent_window)
        ranked   = rank_priorities(items, gaps, graph, results, adaptive_config=cfg, now=..., top_n=cfg.priority_top_n)
        bands    = [decide_difficulty(difficulty_input_from_dna(dna, kp), cfg) for kp in ranked]
        plans    = plan_exercises(ranked, bands, total=count, config=cfg)
        drawn    = draw_questions_by_plan(db, student_id=..., plans=plans, ...)

    Args:
        db: 数据库会话（只读；本函数不 commit）。
        student_id: 学生 ID。
        grade: 年级筛选（透传给抽题层）。
        grade_band: 学段筛选。
        edition: 教材版本筛选。
        section: 册内分区筛选。
        count: 本批题数；``None`` 取 ``DEFAULT_COUNT``，上界取配置
            ``generation_max_total``。
        config: 阈值配置；``None`` 时现读 ``system_config``。
        now: 时间基准；``None`` 时取当前 UTC。

    Returns:
        snake_case 信封（API 层负责转 camelCase）::

            {"generated_at", "engine_version", "total", "actual_count",
             "miss_count", "question_ids", "items", "relaxation_steps"}

        ``items`` 每项形如 ``{"knowledge_point_id", "kp_name", "question_count",
        "difficulty_levels", "strategy", "reason"}``。

    降级约定：
        * ``cfg.enabled is False`` → 直接走 ``_legacy_plan``；
        * 编排链路任一步抛异常 → 记 warning 后同样走 ``_legacy_plan``；
        * ``items``/``gaps``/``graph``/``results`` 任一为空只影响对应权重，
          不打断整条链路（四个回滚开关因此可各自独立生效）。
    """
    sid = to_uuid(student_id, "student_id")
    settings: AdaptiveConfig = (
        config if isinstance(config, AdaptiveConfig) else read_adaptive_config(db)
    )
    want = _clamp_total(count, settings)
    generated_at = utcnow()

    # ---- 总开关：关掉即整条链路短路（回滚用）--------------------------------
    if not getattr(settings, "enabled", True):
        logger.info("adaptive.enabled=false，走普通抽题回滚路径 student=%s", sid)
        return _legacy_plan(
            db,
            student_id=sid,
            want=want,
            grade=grade,
            grade_band=grade_band,
            edition=edition,
            section=section,
            generated_at=generated_at,
        )

    fallback_kwargs: Dict[str, Any] = {
        "student_id": sid,
        "want": want,
        "grade": grade,
        "grade_band": grade_band,
        "edition": edition,
        "section": section,
        "generated_at": generated_at,
    }
    try:
        dna = student_dna_service.get_learning_dna(db, student_id=sid)
        items = mastery_rows(dna)
        gaps = build_prerequisite_gaps(db, student_id=sid, items=items)
        graph = build_graph_degrees(db)
        results = aggregate_recent_results(
            db,
            student_id=sid,
            window_days=getattr(settings, "priority_recent_window", 7),
        )

        ranked = rank_priorities(
            items,
            gaps,
            graph,
            results,
            adaptive_config=settings,
            now=_engine_now(now),
            top_n=getattr(settings, "priority_top_n", 5),
        )

        current_difficulty = _current_difficulty(db, student_id=sid)
        bands = _bands_for(dna, ranked, settings, current_difficulty)
        plans = plan_exercises(ranked, bands, total=want, config=settings)
        drawn = draw_questions_by_plan(
            db,
            student_id=sid,
            plans=plans,
            grade=grade,
            grade_band=grade_band,
            edition=edition,
            section=section,
            recent_days=RECENT_DAYS,
        )
    except Exception as exc:  # noqa: BLE001 - 编排层挂掉必须降级，不能让孩子白屏
        logger.warning("自适应编排失败，降级到普通抽题 student=%s: %s", sid, exc, exc_info=True)
        return _legacy_plan(db, **fallback_kwargs)

    return {
        "generated_at": generated_at.isoformat(),
        "engine_version": ENGINE_VERSION,
        "total": want,
        "actual_count": int(drawn["actual_count"]),
        "miss_count": int(drawn["miss_count"]),
        "question_ids": list(drawn["question_ids"]),
        "items": _plan_items(plans, ranked),
        "relaxation_steps": list(drawn["relaxation_steps"]),
    }


def _current_difficulty(db: Session, *, student_id: uuid.UUID) -> float:
    """取学生当前主难度（难度引擎的惯性基准）；失败回退 ``DEFAULT_DIFFICULTY``。"""
    try:
        from app.services import adaptive_service  # noqa: PLC0415 - 避免模块级循环依赖

        return clamp(
            to_float(adaptive_service.recommend_difficulty(db, student_id=student_id), DEFAULT_DIFFICULTY),
            1.0,
            5.0,
        )
    except Exception as exc:  # noqa: BLE001 - 难度取不到只影响惯性项，不打断编排
        logger.warning("自适应主难度取数失败，回退默认难度 %s: %s", DEFAULT_DIFFICULTY, exc)
        return DEFAULT_DIFFICULTY


__all__ = [
    "OWNER_TASK",
    "ENGINE_VERSION",
    "ENGINE_VERSION_LEGACY",
    "FALLBACK_REASON",
    "GRAPH_CACHE_TTL_SEC",
    "aggregate_recent_results",
    "build_adaptive_plan",
    "build_graph_degrees",
    "build_prerequisite_gaps",
    "clear_graph_cache",
]

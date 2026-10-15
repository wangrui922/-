# -*- coding: utf-8 -*-
"""优先级引擎（V6.5 Sprint 6 · T06）：决定「下一步先补哪个知识点」。

纯函数铁律
----------
* 不 import ORM / Session、不读 env 与配置文件，只吃普通 ``Mapping`` / 序列；
* 阈值来自模块常量与 ``AdaptiveConfig.priority_*``，由调用方注入；
* 无随机数、无 ``now()``（时间基准由 ``now`` 注入），同输入必同输出；
* 脏入参一律兜底（夹紧到合法区间），不抛异常。

排序规则与教学依据
------------------
R1 ``gap``：**前置缺口优先**。后置知识点练不动的根因往往在前置没补上，
   先补地基比在后置上反复刷更有效；
R2 ``mastery``：掌握度越低越该练，把有限的练习时间投到真实短板上；
R3 ``confidence``：置信度越低说明结论越不可靠，需要先做题补样本；
R4 ``degree``：图谱出度高的知识点能解锁更多后续内容，基础价值更高；
R5 ``stale``：超过 ``PRIORITY_STALE_AFTER`` 天没练的知识点按天数加分
   （遗忘曲线：久不练的能力会回退，需要及时回顾）；
R6 ``recent``：``PRIORITY_RECENT_WINDOW`` 天内刚练过的按「离今天数越近降权越多」
   递减系数降权，用间隔练习代替同一知识点机械连刷；
R6b ``C1 错题回炉``（来自 ``results``）：近窗口错 ≥1 题且错误率 ≥
   ``priority_wrong_rate_floor`` 时**取消 R6 降权**，并按错题数加分
   ``wrong_boost × min(1, wrong / wrong_saturation)``——错题当天就该排得上；
R6c ``C2 题量饱和``（来自 ``results``）：近窗口作答数 > ``priority_recent_attempts_cap``
   时整体乘 ``priority_saturation_penalty``，抑制单点连刷；
R7 稳定排序与去重：同一知识点只保留一项，同分按掌握度、ID 字典序，跨进程可复现。

``results`` 为 ``None`` 时 R6b / R6c 全部失效（BOOST=0、SATURATION=1），
退化成本模块不含 results 的历史算法——这条等价性由单测钉死。

缺省权重 ``PRIORITY_WEIGHTS_DEFAULT`` 可由调用方通过
``adaptive_config.priority_weights`` 覆盖（dict 或 JSON 字符串均可，缺 key 回退缺省）。
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from app.engines.config import AdaptiveConfig, DEFAULT_ADAPTIVE_CONFIG
from app.engines.types import PriorityItem, clamp, to_float, to_int

#: 负责人任务号（便于后续接线时定位）
OWNER_TASK: str = "T06"

#: 优先级缺省权重（四项之和为 1.0）
PRIORITY_WEIGHTS_DEFAULT: Dict[str, float] = {
    "mastery": 0.40,
    "confidence": 0.25,
    "gap": 0.20,
    "degree": 0.15,
}

#: 前置缺口条数归一化除数（1~2 条缺口已接近「必须优先」）
PRIORITY_GAP_DIVISOR: float = 3.0
#: 图谱出度归一化除数
PRIORITY_OUTDEG_DIVISOR: int = 5
#: 近期练习窗口（天）：窗口内练过才降权
PRIORITY_RECENT_WINDOW: int = 7
#: 陈旧阈值（天）：超过该天数没练则按超出比例加分
PRIORITY_STALE_AFTER: int = 14
#: 低掌握度线（判定「薄弱」并生成对应理由）
PRIORITY_LOW_MASTERY: float = 0.5
#: 低置信度线
PRIORITY_LOW_CONFIDENCE: float = 0.5
#: 缺省返回条数
PRIORITY_TOP_N_DEFAULT: int = 5

#: 掌握度 / 置信度等 0~1 字段的候选键名（兼容多种上游口径）
_MASTERY_KEYS: Tuple[str, ...] = ("mastery", "mastery_score", "score")
_CONFIDENCE_KEYS: Tuple[str, ...] = ("confidence", "conf")
_ID_KEYS: Tuple[str, ...] = ("knowledge_point_id", "kp_id", "id")
_NAME_KEYS: Tuple[str, ...] = ("kp_name", "name", "knowledge_point_name")
_LAST_PRACTICED_KEYS: Tuple[str, ...] = (
    "last_practiced_at",
    "last_practice_at",
    "last_reviewed_at",
    "updated_at",
)

#: ``results`` 里近窗口作答指标的键名（T07 聚合产出）
_RECENT_ATTEMPTS_KEYS: Tuple[str, ...] = ("recent_attempts", "attempts")
_RECENT_CORRECT_KEYS: Tuple[str, ...] = ("recent_correct", "correct_count")
#: ``results`` 里最近作答时间的键名（仅当掌握度行缺时间字段时兜底）
_LAST_RESULT_KEYS: Tuple[str, ...] = ("last_result_at", "last_attempt_at")

#: 一天的秒数（把时间差换算成天）
_SECONDS_PER_DAY: float = 86400.0

#: 时间字符串解析格式（按长度尝试，失败即视为无时间）
_TIME_FORMATS: Tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


# ============================================================ 取数工具
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


def _as_sequence(value: Any) -> Tuple[Any, ...]:
    """把入参归一成元组；``None`` / 非序列（字符串等）按空或单元素处理。"""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(value)
    return (value,)


def _parse_time(value: Any) -> Optional[datetime]:
    """把 datetime / ISO 字符串解析成 datetime；无法解析返回 ``None``。"""
    if isinstance(value, datetime):
        return value
    text = _text(value)
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text.replace("Z", "").replace("+00:00", ""), fmt)
        except ValueError:
            continue
    return None


def _days_between(now: Optional[datetime], stamp: Any) -> Optional[float]:
    """``now - stamp`` 的天数差；任一端缺失返回 ``None``，负数截断为 0。"""
    if now is None:
        return None
    moment = _parse_time(stamp)
    if moment is None:
        return None
    delta = (now - moment).total_seconds() / _SECONDS_PER_DAY
    return delta if delta > 0.0 else 0.0


# ============================================================ 权重与配置
def _weights(settings: AdaptiveConfig) -> Dict[str, float]:
    """取优先级权重：支持 dict 与 JSON 字符串，缺 key / 解析失败回退缺省。"""
    raw = getattr(settings, "priority_weights", None)
    if isinstance(raw, Mapping):
        source: Mapping[str, Any] = raw
    elif isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError):
            parsed = None
        source = parsed if isinstance(parsed, Mapping) else {}
    else:
        source = {}

    weights: Dict[str, float] = {}
    for name, default in PRIORITY_WEIGHTS_DEFAULT.items():
        weights[name] = clamp(to_float(source.get(name), default), 0.0, 1.0)
    return weights


def _divisor(value: float, default: float) -> float:
    """除数保护：非正数回退缺省值（避免被配成 0 时除零）。"""
    number = to_float(value, default)
    return number if number > 0.0 else float(default)


# ============================================================ 上下文统计
def _gap_counts(gaps: Any) -> Dict[str, int]:
    """统计每个知识点作为「前置缺口」被点名的次数。"""
    counts: Dict[str, int] = {}
    for gap in _as_sequence(gaps):
        pre_id = _text(_field(gap, ("pre_kp_id", "prerequisite_id", "pre_id")))
        if not pre_id:
            continue
        counts[pre_id] = counts.get(pre_id, 0) + 1
    return counts


def _degrees(graph: Any) -> Dict[str, int]:
    """把图谱入参归一成 ``{kp_id: 出度}``。

    支持两种形态：
        * 映射：``{kp_id: 出度}``（或 ``{kp_id: {"out_degree": n}}``）；
        * 序列：前置边 ``[(pre_kp_id, kp_id), ...]``，按 pre 的出现次数计出度。
    """
    degrees: Dict[str, int] = {}
    if graph is None:
        return degrees
    if isinstance(graph, Mapping):
        for raw_key, raw_value in graph.items():
            kp_id = _text(raw_key)
            if not kp_id:
                continue
            value = _field(raw_value, ("out_degree", "degree", "count"), raw_value)
            degrees[kp_id] = degrees.get(kp_id, 0) + max(0, to_int(value, 0))
        return degrees

    for edge in _as_sequence(graph):
        if isinstance(edge, Mapping):
            pre_id = _text(_field(edge, ("pre_kp_id", "from", "source")))
            raw_value = _field(edge, ("out_degree", "degree", "count"))
        else:
            pre_id = _text(_field(edge, ("pre_kp_id",)))
            raw_value = 1
        if not pre_id:
            continue
        degrees[pre_id] = degrees.get(pre_id, 0) + max(0, to_int(raw_value, 1))
    return degrees


def _result_map(results: Any) -> Dict[str, Mapping[str, Any]]:
    """把 ``results``（T07 聚合的作答结果）归一成 ``{kp_id: 指标}``。"""
    mapping: Dict[str, Mapping[str, Any]] = {}
    if results is None:
        return mapping
    if isinstance(results, Mapping):
        items: Sequence[Tuple[Any, Any]] = tuple(results.items())
    else:
        items = tuple(
            (kp_id, value)
            for kp_id, value in (
                (_field(item, _ID_KEYS), item) for item in _as_sequence(results)
            )
            if kp_id is not None
        )
    for raw_key, raw_value in items:
        kp_id = _text(raw_key)
        if not kp_id:
            continue
        mapping[kp_id] = raw_value if isinstance(raw_value, Mapping) else {}
    return mapping


# ============================================================ 评分
def _reason(
    kp_name: str,
    mastery: float,
    gap_count: int,
    stale: bool,
    recent: bool,
    attempts: int = 0,
    wrong: int = 0,
) -> str:
    """生成前端可直接展示的中文教学理由（按最关键的信号排序）。

    优先级：``gap``（前置缺口）→ ``wrong``（近窗口错题回炉）→ ``stale``（遗忘）
    → 掌握度偏低 → 近期练过 → 兜底。新增理由插在既有分支之间时，
    必须保证既有文案在对应信号下**仍然可达**（由单测钉死）。
    """
    display_name = kp_name or "该知识点"
    if gap_count > 0:
        return f"「{display_name}」是未掌握的前置基础，建议先补牢再往后学。"
    if wrong >= 1 and attempts > 0:
        return f"「{display_name}」刚做过 {attempts} 题错了 {wrong} 题，今天先回炉订正。"
    if stale:
        return f"「{display_name}」很久没练了，先做几道把它捡回来。"
    if mastery < PRIORITY_LOW_MASTERY:
        return f"「{display_name}」掌握度仅 {int(round(mastery * 100))}%，优先补齐薄弱基础。"
    if recent:
        return f"「{display_name}」近期已经练过，适当错峰后再复习。"
    return f"「{display_name}」掌握还不稳，建议安排一轮针对性练习。"


def _time_factors(
    days: Optional[float], recent_window: int, stale_after: int
) -> Tuple[float, float, bool, bool]:
    """把时间维度拆成两个**正交**的修正量（重构自原先的互斥分支）。

    Args:
        days: 距上次练习的天数；``None`` 表示无时间数据。
        recent_window: 近期窗口（天），窗口内练过才降权。
        stale_after: 陈旧阈值（天），超过则加分。

    Returns:
        ``(stale, recent, is_stale, is_recent)``——
        ``stale`` 是**加法**项，``recent`` 是**乘法**项，两个布尔值供理由文案使用。
        无时间数据时 ``(0.0, 1.0, False, False)``（两个维度都不生效）。
    """
    if days is None:
        return 0.0, 1.0, False, False

    is_stale = days > stale_after
    stale = (days - stale_after) / stale_after if (is_stale and stale_after > 0) else 0.0

    is_recent = (not is_stale) and recent_window > 0 and days <= recent_window
    recent = (days + 1.0) / (recent_window + 1.0) if is_recent else 1.0
    return stale, recent, is_stale, is_recent


def _result_boost(
    metrics: Optional[Mapping[str, Any]],
    settings: AdaptiveConfig,
) -> Tuple[float, float, float, int, int]:
    """由近窗口作答结果算出三项后处理修正因子（C1 错题回炉 + C2 题量饱和）。

    Args:
        metrics: 该知识点在 ``results`` 里的指标；``None`` 表示该点无结果数据。
        settings: 阈值配置。

    Returns:
        ``(recent, saturation, boost, attempts, wrong)``——
        * ``recent``：C1 命中时置 ``1.0``（取消「近期练过」的降权），否则 ``None``
          语义用 ``-1.0`` 表示「不改动」（调用方据此保留原值）；
        * ``saturation``：C2 命中时为抑制系数，否则 ``1.0``；
        * ``boost``：C1 的加分（``0`` 表示未命中）；
        * ``attempts`` / ``wrong``：供理由文案使用的近窗口题量。

    ⚠️ 不变量：``metrics`` 缺失或 ``recent_attempts <= 0`` 时必须返回
    ``(-1.0, 1.0, 0.0, 0, 0)``，使最终得分与不传 ``results`` 时**逐位一致**。
    """
    noop: Tuple[float, float, float, int, int] = (-1.0, 1.0, 0.0, 0, 0)
    if not isinstance(metrics, Mapping):
        return noop

    attempts = max(0, to_int(_field(metrics, _RECENT_ATTEMPTS_KEYS), 0))
    if attempts <= 0:
        return noop
    correct = clamp(to_float(_field(metrics, _RECENT_CORRECT_KEYS), 0.0), 0.0, float(attempts))
    wrong = int(round(attempts - correct))
    wrong_rate = wrong / float(attempts)

    # ---- C1 错题回炉：错得多就取消近期降权，并按错题数加分 ----
    floor = clamp(to_float(settings.priority_wrong_rate_floor, 0.5), 0.0, 1.0)
    boost_cap = _divisor(settings.priority_wrong_saturation, 3.0)
    boost = 0.0
    recent = -1.0
    if wrong >= 1 and wrong_rate >= floor:
        recent = 1.0
        boost = max(0.0, to_float(settings.priority_wrong_boost, 0.25)) * min(1.0, wrong / boost_cap)

    # ---- C2 题量饱和：单一知识点近期刷太多就整体乘抑制系数 ----
    cap = max(0, to_int(settings.priority_recent_attempts_cap, 10))
    saturation = 1.0
    if cap > 0 and attempts > cap:
        saturation = clamp(to_float(settings.priority_saturation_penalty, 0.85), 0.0, 1.0)

    return recent, saturation, boost, attempts, wrong


def _score(
    *,
    mastery: float,
    confidence: float,
    gap_count: int,
    degree: int,
    days: Optional[float],
    weights: Mapping[str, float],
    gap_divisor: float,
    degree_divisor: float,
    recent_window: int,
    stale_after: int,
    metrics: Optional[Mapping[str, Any]] = None,
    settings: Optional[AdaptiveConfig] = None,
) -> Tuple[float, bool, bool, int, int]:
    """算单个知识点的优先级得分。

    公式（与 ``docs`` / 施工图一致）::

        BASE   = (1-mastery)·w_mastery + (1-confidence)·w_confidence
               + gap_ratio·w_gap + degree_ratio·w_degree
        raw    = (BASE + STALE) × RECENT × SATURATION + BOOST
        score  = clamp(raw, 0, 1) 保留 4 位小数

    其中 ``STALE`` / ``RECENT`` 来自 :func:`_time_factors`，
    ``SATURATION`` / ``BOOST`` 来自 :func:`_result_boost`（``results`` 缺失时
    分别为 ``1.0`` / ``0.0``，即退化为不含 results 的历史算法）。

    Returns:
        ``(score, is_stale, is_recent, attempts, wrong)``——后四项供理由文案使用。
    """
    gap_ratio = min(float(gap_count), gap_divisor) / gap_divisor
    degree_ratio = min(float(degree), degree_divisor) / degree_divisor
    base = (
        (1.0 - mastery) * weights.get("mastery", 0.0)
        + (1.0 - confidence) * weights.get("confidence", 0.0)
        + gap_ratio * weights.get("gap", 0.0)
        + degree_ratio * weights.get("degree", 0.0)
    )

    stale, recent, is_stale, is_recent = _time_factors(days, recent_window, stale_after)

    saturation = 1.0
    boost = 0.0
    attempts = 0
    wrong = 0
    if settings is not None:
        boost_recent, saturation, boost, attempts, wrong = _result_boost(metrics, settings)
        if boost_recent >= 0.0:
            # C1 命中：取消「近期练过」的降权
            recent = boost_recent
            is_recent = False

    raw = (base + stale) * recent * saturation + boost
    return round(clamp(raw, 0.0, 1.0), 4), is_stale, is_recent, attempts, wrong


def rank_priorities(
    mastery_items: Any = None,
    gaps: Any = None,
    graph: Any = None,
    results: Any = None,
    *,
    adaptive_config: Optional[AdaptiveConfig] = None,
    now: Optional[datetime] = None,
    top_n: Optional[int] = None,
) -> List[PriorityItem]:
    """按教学价值排出知识点优先级（**纯函数**）。

    Args:
        mastery_items: 掌握度条目序列。每项可以是 dict 也可以是对象，
            键名兼容 ``knowledge_point_id`` / ``kp_id`` / ``id``、
            ``mastery`` / ``mastery_score`` / ``score``、
            ``confidence`` / ``conf``、``kp_name`` / ``name``、
            ``last_practiced_at`` / ``last_practice_at`` / ``last_reviewed_at``。
        gaps: 前置缺口列表（``detect_prerequisite_gaps`` 的结果，数据类或字典均可）。
            该项作为**前置**被点名的次数越多，得分越高。
        graph: 图谱结构。支持 ``{kp_id: 出度}`` 映射，或前置边序列
            （按 pre 出现次数计出度）。``None`` 时该项权重失效。
        results: T07 从作答流水聚合的 ``{kp_id: 指标}``（可选）。每项字段全部可选：
            ``recent_attempts``（近窗口作答次数）、``recent_correct``（近窗口答对次数，
            会被夹到 ``[0, recent_attempts]``）、``last_result_at``（最近作答时间，
            **仅当掌握度行缺时间字段时兜底**）。``wrong`` / ``wrong_rate`` 由引擎
            从上述两个字段派生，上游不要传。``None`` 时 BOOST=0、SATURATION=1，
            得分与不传 results 逐位一致。
        adaptive_config: 阈值配置；``None`` 时用缺省配置。
        now: 时间基准（UTC），用于「陈旧 / 近期」判定；``None`` 时跳过该维度。
        top_n: 返回条数；``None`` 或非法值取 ``PRIORITY_TOP_N_DEFAULT``。

    Returns:
        ``List[PriorityItem]``，按 ``score`` 降序；``mastery_items`` 为空时返回 ``[]``。
    """
    settings: AdaptiveConfig = (
        adaptive_config if isinstance(adaptive_config, AdaptiveConfig) else DEFAULT_ADAPTIVE_CONFIG
    )
    weights = _weights(settings)
    gap_divisor = _divisor(settings.priority_gap_divisor, PRIORITY_GAP_DIVISOR)
    degree_divisor = _divisor(settings.priority_out_degree_divisor, PRIORITY_OUTDEG_DIVISOR)
    recent_window = max(0, to_int(settings.priority_recent_window, PRIORITY_RECENT_WINDOW))
    stale_after = max(0, to_int(settings.priority_stale_after, PRIORITY_STALE_AFTER))

    items = _as_sequence(mastery_items)
    if not items:
        return []

    gap_counts = _gap_counts(gaps)
    degrees = _degrees(graph)
    result_map = _result_map(results)

    ranked: List[PriorityItem] = []
    seen: FrozenSet[str] = frozenset()
    for item in items:
        kp_id = _text(_field(item, _ID_KEYS))
        if not kp_id or kp_id in seen:
            continue
        seen = seen | {kp_id}

        mastery = clamp(to_float(_field(item, _MASTERY_KEYS), 0.0), 0.0, 1.0)
        confidence = clamp(
            to_float(_field(item, _CONFIDENCE_KEYS), PRIORITY_LOW_CONFIDENCE), 0.0, 1.0
        )
        kp_name = _text(_field(item, _NAME_KEYS))

        extra = result_map.get(kp_id)
        stamp = _field(item, _LAST_PRACTICED_KEYS)
        if extra is not None:
            # 掌握度行自带的时间是**主口径**，results 的时间只是缺时间字段时的兜底
            stamp = (
                stamp
                or _field(extra, _LAST_PRACTICED_KEYS, None)
                or _field(extra, _LAST_RESULT_KEYS, None)
            )
        days = _days_between(now, stamp)

        gap_count = gap_counts.get(kp_id, 0)
        degree = degrees.get(kp_id, 0)
        score, is_stale, is_recent, attempts, wrong = _score(
            mastery=mastery,
            confidence=confidence,
            gap_count=gap_count,
            degree=degree,
            days=days,
            weights=weights,
            gap_divisor=gap_divisor,
            degree_divisor=degree_divisor,
            recent_window=recent_window,
            stale_after=stale_after,
            metrics=extra,
            settings=settings,
        )
        ranked.append(
            PriorityItem(
                knowledge_point_id=kp_id,
                kp_name=kp_name,
                score=score,
                mastery=mastery,
                level=int(clamp(to_float(_field(item, ("level", "mastery_level")), 0.0), 0.0, 3.0)),
                reason=_reason(kp_name, mastery, gap_count, is_stale, is_recent, attempts, wrong),
            )
        )

    # 同分：更薄弱 → 等级更低 → ID 字典序，保证跨进程可复现。
    ranked.sort(
        key=lambda entry: (-entry.score, entry.mastery, entry.level, entry.knowledge_point_id)
    )
    size = to_int(top_n, PRIORITY_TOP_N_DEFAULT)
    if size <= 0:
        size = PRIORITY_TOP_N_DEFAULT
    return ranked[:size]


__all__ = [
    "OWNER_TASK",
    "PRIORITY_GAP_DIVISOR",
    "PRIORITY_LOW_CONFIDENCE",
    "PRIORITY_LOW_MASTERY",
    "PRIORITY_OUTDEG_DIVISOR",
    "PRIORITY_RECENT_WINDOW",
    "PRIORITY_STALE_AFTER",
    "PRIORITY_TOP_N_DEFAULT",
    "PRIORITY_WEIGHTS_DEFAULT",
    "rank_priorities",
]

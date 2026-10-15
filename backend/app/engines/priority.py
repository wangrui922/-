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
R7 稳定排序与去重：同一知识点只保留一项，同分按掌握度、ID 字典序，跨进程可复现。
R8 ``results`` 后处理修正（T07 裁决①）：近窗口作答流水只在**时间/强度修正层**
   起作用，不新增权重分量——``mastery`` 已是平滑正确率，再加一份「准不准」
   等于同一信号算三遍。两条规则：

   * **C1 错题回炉**：近窗口 ``wrong >= 1`` 且 ``wrong_rate >= wrong_rate_floor``
     → 取消 R6 的「近期练过」降权（``RECENT := 1.0``），并加
     ``wrong_boost × min(1, wrong / wrong_saturation)`` 分；
   * **C2 题量饱和**：近窗口 ``recent_attempts > recent_attempts_cap``
     → 乘 ``saturation_penalty`` 抑制，防止一天全砸在同一个知识点上。

   落地公式 ``raw = (BASE + STALE) × RECENT × SATURATION + BOOST``。
   **``results=None`` 时 BOOST=0、SATURATION=1、RECENT 不变，输出与改造前逐位相同。**

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

# ------------------------------------------- T07 裁决①：results 修正因子缺省值
#: C1 触发的错误率门槛（近窗口错题占比）
PRIORITY_WRONG_RATE_FLOOR: float = 0.5
#: C1 的加分上限（在「取消近期降权」之外再往上加）
PRIORITY_WRONG_BOOST: float = 0.25
#: C1 加几分打满（近 3 次错 → 满分）
PRIORITY_WRONG_SATURATION: float = 3.0
#: C2 触发阈值：近窗口作答次数超过它即视为单点刷量
PRIORITY_RECENT_ATTEMPTS_CAP: int = 10
#: C2 的抑制系数（配成 1.0 = 关闭抑制）
PRIORITY_SATURATION_PENALTY: float = 0.85

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

#: ``results`` 里的作答时间字段（T07）：**只是兜底**，主口径永远是掌握度行的时间。
#: 单独列一组而``不``并进 ``_LAST_PRACTICED_KEYS``，是为了让「仅当掌握度行缺
#: 时间字段时兜底」这条语义在代码里显式可读——混在一起就是隐式覆盖了。
_LAST_RESULT_KEYS: Tuple[str, ...] = ("last_result_at", "last_attempt_at")

#: ``results`` 每项里「近窗口作答次数」的候选键名
_RESULT_ATTEMPTS_KEYS: Tuple[str, ...] = ("recent_attempts", "attempts", "count")
#: ``results`` 每项里「近窗口答对次数」的候选键名
_RESULT_CORRECT_KEYS: Tuple[str, ...] = ("recent_correct", "correct", "correct_count")

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


def _non_negative(value: Any, default: float) -> float:
    """非负钳制：负数回退缺省值（脏配置不得把得分算成负数）。"""
    number = to_float(value, default)
    return number if number >= 0.0 else float(default)


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
    *,
    wrong: int = 0,
    attempts: int = 0,
) -> str:
    """生成前端可直接展示的中文教学理由（按最关键的信号排序）。

    T07 新增一路「错题回炉」，排在 ``gap`` 之后、``stale`` 之前——
    **错题优先于遗忘**：刚错过的题当天就该回炉，比「很久没练」更急。
    ``wrong``/``attempts`` 为 0（即未触发 C1）时该分支不生效，
    与改造前的文案完全一致。

    Args:
        kp_name: 知识点名称。
        mastery: 掌握度。
        gap_count: 该知识点作为前置缺口被点名的次数。
        stale: 是否久未练习。
        recent: 是否近期练过。
        wrong: 近窗口答错次数（仅在 C1 触发时由调用方传入，否则为 0）。
        attempts: 近窗口作答次数（同上）。

    Returns:
        一句话中文理由。
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


# ============================================================ results 修正
def _wrong_metrics(metrics: Any, settings: AdaptiveConfig) -> Tuple[int, int, bool]:
    """从近窗口作答指标派生 ``(attempts, wrong, c1_triggered)``。

    ``wrong`` / ``wrong_rate`` 一律由本函数从 ``recent_attempts`` /
    ``recent_correct`` 派生（**上游禁止直接传**），保证单一推导口径。

    Args:
        metrics: ``results`` 里该知识点那一项（dict）；非 Mapping 视作缺失。
        settings: 阈值配置（读 ``priority_wrong_rate_floor``）。

    Returns:
        ``(attempts, wrong, triggered)``。``attempts <= 0`` 时恒为
        ``(0, 0, False)``——没有近窗口数据就不参与修正。
    """
    if not isinstance(metrics, Mapping):
        return 0, 0, False

    attempts = max(0, to_int(_field(metrics, _RESULT_ATTEMPTS_KEYS), 0))
    if attempts <= 0:
        return 0, 0, False

    correct = clamp(
        to_float(_field(metrics, _RESULT_CORRECT_KEYS), 0.0), 0.0, float(attempts)
    )
    wrong = int(clamp(float(attempts) - correct, 0.0, float(attempts)))
    floor = clamp(
        to_float(
            getattr(settings, "priority_wrong_rate_floor", PRIORITY_WRONG_RATE_FLOOR),
            PRIORITY_WRONG_RATE_FLOOR,
        ),
        0.0,
        1.0,
    )
    triggered = wrong >= 1 and (wrong / float(attempts)) >= floor
    return attempts, wrong, triggered


def _result_boost(
    metrics: Any,
    cfg: AdaptiveConfig,
    *,
    recent: float = 1.0,
) -> Tuple[float, float, float]:
    """按近窗口作答结果给出三个后处理修正因子 ``(RECENT, SATURATION, BOOST)``。

    T07 裁决① 的落地：``raw = (BASE + STALE) × RECENT × SATURATION + BOOST``。
    三个返回值**只在真的触发时**才偏离中立项（``RECENT`` 原值 / ``1.0`` / ``0.0``），
    因此 ``metrics`` 缺失时输出与改造前逐位一致。

    Args:
        metrics: ``results`` 里该知识点那一项（dict）；缺失时三因子全为中立项。
        cfg: 阈值配置（读五个 ``priority_*`` 项，缺属性回退模块缺省值）。
        recent: 由 R6 算出的「近期练过」降权系数，作为 ``RECENT`` 的初值。

    Returns:
        ``(recent_factor, saturation, boost)``。
    """
    attempts, wrong, triggered = _wrong_metrics(metrics, cfg)
    factor = clamp(to_float(recent, 1.0), 0.0, 1.0)
    if attempts <= 0:
        return factor, 1.0, 0.0

    boost_cap = _non_negative(
        getattr(cfg, "priority_wrong_boost", PRIORITY_WRONG_BOOST),
        PRIORITY_WRONG_BOOST,
    )
    saturation_divisor = _divisor(
        getattr(cfg, "priority_wrong_saturation", PRIORITY_WRONG_SATURATION),
        PRIORITY_WRONG_SATURATION,
    )
    cap = max(
        0,
        to_int(
            getattr(cfg, "priority_recent_attempts_cap", PRIORITY_RECENT_ATTEMPTS_CAP),
            PRIORITY_RECENT_ATTEMPTS_CAP,
        ),
    )
    penalty = clamp(
        to_float(
            getattr(cfg, "priority_saturation_penalty", PRIORITY_SATURATION_PENALTY),
            PRIORITY_SATURATION_PENALTY,
        ),
        0.0,
        1.0,
    )

    # C1 错题回炉：取消 R6 的近期降权，并按错题量加分（近 3 次错即打满）
    boost = 0.0
    if triggered:
        factor = 1.0
        boost = boost_cap * min(1.0, wrong / saturation_divisor)

    # C2 题量饱和：单点刷太多就整体压一档，避免一天全砸在同一个知识点上
    saturation = penalty if attempts > cap else 1.0
    return factor, saturation, boost


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
    metrics: Any = None,
    settings: Optional[AdaptiveConfig] = None,
) -> Tuple[float, bool, bool]:
    """算单个知识点的优先级得分。

    先把原本 ``if/elif`` 互斥的两个分支拆成正交量 ``STALE`` / ``RECENT``
    （数学上与原写法等价），**再**插入 ``results`` 的三个修正因子::

        BASE  = 四项加权和
        STALE = days > stale_after ? (days - stale_after)/stale_after : 0
        RECENT= 窗口内 ? (days+1)/(recent_window+1) : 1
        raw   = (BASE + STALE) × RECENT × SATURATION + BOOST

    Args:
        mastery: 掌握度（已夹紧到 0~1）。
        confidence: 置信度（已夹紧到 0~1）。
        gap_count: 前置缺口被点名次数。
        degree: 图谱出度。
        days: 距上次练习天数；``None`` 表示无时间数据。
        weights: 四项权重。
        gap_divisor: 缺口归一化除数。
        degree_divisor: 出度归一化除数。
        recent_window: 近期窗口（天）。
        stale_after: 陈旧阈值（天）。
        metrics: 该知识点在 ``results`` 里的指标项；``None`` 时不参与修正。
        settings: 阈值配置；``None`` 时用缺省配置。

    Returns:
        ``(score, is_stale, is_recent)``——后两个布尔值供理由文案使用。
    """
    gap_ratio = min(float(gap_count), gap_divisor) / gap_divisor
    degree_ratio = min(float(degree), degree_divisor) / degree_divisor
    base = (
        (1.0 - mastery) * weights.get("mastery", 0.0)
        + (1.0 - confidence) * weights.get("confidence", 0.0)
        + gap_ratio * weights.get("gap", 0.0)
        + degree_ratio * weights.get("degree", 0.0)
    )

    is_stale = days is not None and days > stale_after
    is_recent = (
        days is not None and not is_stale and recent_window > 0 and days <= recent_window
    )
    stale = (days - stale_after) / stale_after if is_stale else 0.0
    recent_factor = (days + 1.0) / (recent_window + 1.0) if is_recent else 1.0

    cfg: AdaptiveConfig = (
        settings if isinstance(settings, AdaptiveConfig) else DEFAULT_ADAPTIVE_CONFIG
    )
    recent_factor, saturation, boost = _result_boost(
        metrics, cfg, recent=recent_factor
    )

    raw = (base + stale) * recent_factor * saturation + boost
    return round(clamp(raw, 0.0, 1.0), 4), is_stale, is_recent


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
        results: T07 从作答流水聚合的 ``{kp_id: 指标}``（可选）。
            本次只留出入口，``None`` 时忽略；提供时其中的
            ``last_practiced_at`` 可用于覆盖陈旧/近期判定。
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
            override = _field(extra, _LAST_PRACTICED_KEYS)
            if override is None and stamp is None:
                # results.last_result_at 只是兜底：掌握度行有时间时以它为准
                override = _field(extra, _LAST_RESULT_KEYS)
            if override is not None:
                stamp = override
        days = _days_between(now, stamp)

        gap_count = gap_counts.get(kp_id, 0)
        degree = degrees.get(kp_id, 0)
        score, is_stale, is_recent = _score(
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
        # 错题回炉文案只在 C1 真的触发时才给（wrong=0 → 走原有分支，文案不变）
        hint_attempts, hint_wrong, hint_triggered = _wrong_metrics(extra, settings)
        ranked.append(
            PriorityItem(
                knowledge_point_id=kp_id,
                kp_name=kp_name,
                score=score,
                mastery=mastery,
                level=int(clamp(to_float(_field(item, ("level", "mastery_level")), 0.0), 0.0, 3.0)),
                reason=_reason(
                    kp_name,
                    mastery,
                    gap_count,
                    is_stale,
                    is_recent,
                    wrong=hint_wrong if hint_triggered else 0,
                    attempts=hint_attempts if hint_triggered else 0,
                ),
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
    "PRIORITY_RECENT_ATTEMPTS_CAP",
    "PRIORITY_SATURATION_PENALTY",
    "PRIORITY_STALE_AFTER",
    "PRIORITY_TOP_N_DEFAULT",
    "PRIORITY_WEIGHTS_DEFAULT",
    "PRIORITY_WRONG_BOOST",
    "PRIORITY_WRONG_RATE_FLOOR",
    "PRIORITY_WRONG_SATURATION",
    "rank_priorities",
]

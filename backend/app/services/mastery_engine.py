"""掌握度 / 错因 / 推荐 的**纯算法层**（T04 · B5）。

硬约束：
    * 本模块**不 import 任何 ORM / Session / FastAPI**，只吃 dataclass 吐 dataclass；
      好处是可以被纯单测覆盖（``tests/test_t04_mastery_engine.py``），
      也天然避开跨方言问题（聚合全在 Python 内存里做）。
    * 不调用方舟（OQ1/OQ2：规则统计 + 本地错因）。

三块能力：
    1. ``compute_mastery``        —— 正确率 × 时效衰减 × 贝叶斯平滑 → 分值/等级；
    2. ``classify_error`` 系列    —— 关键词表 + 优先级仲裁 → 六类错因；
    3. ``rank_recommendations``   —— 薄弱度 × 错误率 × 样本量 → Top N 推荐 + 中文理由。

注：错因分类已统一为五类枚举（缺口②），``classify_error`` 与关键词表来自
``app.core.error_categories`` 的唯一真相源。

时区约定：
    所有入参时间统一按 UTC 处理。SQLite 读回来的是 **naive datetime**，
    这里用 ``as_utc`` 兜底补上 tzinfo，避免 aware/naive 相减直接 TypeError。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Mapping, Optional, Sequence

from app.core.constants import (
    ERROR_EVIDENCE_LIMIT,
    MASTERY_CONFIDENCE_K,
    MASTERY_GAP_THRESHOLD,
    MASTERY_HALF_LIFE_DAYS,
    MASTERY_LEVEL1_MIN,
    MASTERY_LEVEL2_MIN,
    MASTERY_LEVEL2_MIN_EFFECTIVE,
    MASTERY_LEVEL3_MIN,
    MASTERY_LEVEL3_MIN_EFFECTIVE,
    MASTERY_PRIOR_SCORE,
    RECOMMEND_QUESTION_LIMIT,
    RECOMMEND_TOP_N,
)
from app.core.error_categories import (
    ERROR_CATEGORIES,
    ERROR_KEYWORDS,
    ERROR_UNCLASSIFIED,
    classify_error,
    error_label,
)

#: 错因优先级：数值越小优先级越高（命中多类时取最小值那一类）
_CATEGORY_PRIORITY: Dict[str, int] = {name: idx for idx, name in enumerate(ERROR_CATEGORIES)}

#: 一天的秒数（把时间差换算成天）
_SECONDS_PER_DAY: float = 86400.0


# ==================================================================== 数据结构
@dataclass(frozen=True)
class AttemptEvent:
    """一次「练习事件」（已按知识点展开，一题多知识点会拆成多条）。

    Attributes:
        kp_id: 知识点 ID（字符串形态，算法层不关心 UUID）。
        is_correct: 本次是否判定为正确。
        occurred_at: 事件发生时间（UTC）。
        weight: 事件源可信度权重，见 ``constants.EVENT_WEIGHT_*``。
        source: 事件来源标记，便于排查（attempt / mistake / mistake_solved）。
    """

    kp_id: str
    is_correct: bool
    occurred_at: datetime
    weight: float = 1.0
    source: str = "attempt"


@dataclass(frozen=True)
class MasteryResult:
    """单个知识点的掌握度计算结果。"""

    score: float = 0.0
    level: int = 0
    attempts: int = 0
    correct_count: int = 0
    error_rate: float = 0.0
    effective_attempts: float = 0.0
    last_practiced_at: Optional[datetime] = None


@dataclass(frozen=True)
class ErrorEvidence:
    """一条待分类的错因证据（原始文本 + 发生时间）。"""

    text: str
    occurred_at: Optional[datetime] = None
    kp_id: Optional[str] = None


@dataclass(frozen=True)
class ErrorPattern:
    """某一类错因的聚合结果。"""

    category: str
    label: str
    count: int
    ratio: float
    sample_hits: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MasteryEntry:
    """推荐算法的输入项：一个知识点的掌握度 + 该点错因分布。"""

    kp_id: str
    kp_name: str
    result: MasteryResult
    error_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Recommendation:
    """一条推荐结果。"""

    kp_id: str
    kp_name: str
    reason: str
    priority: float
    score: float
    error_rate: float
    top_error_category: Optional[str]
    suggested_question_ids: List[str] = field(default_factory=list)


# ====================================================== 知识图谱传播（P0·Phase2）
@dataclass(frozen=True)
class PrerequisiteEdge:
    """一条前置依赖边（GKT 图骨架）。

    Attributes:
        pre_kp_id: 前置知识点 ID（须先掌握）。
        kp_id: 后置知识点 ID（依赖前置）。
        weight: 依赖强度 0~1；>= ``MASTERY_GAP_MIN_HARD_WEIGHT`` 视为硬门槛。
    """

    pre_kp_id: str
    kp_id: str
    weight: float = 1.0


@dataclass(frozen=True)
class PrerequisiteGap:
    """一条「前置知识点缺口」。

    语义：学生在练（``kp_id`` 有练习数据）时，其前置知识点
    （``pre_kp_id``）的掌握度低于缺口线 → 建议先补前置再练后置。

    Attributes:
        kp_id: 后置知识点 ID（正在练）。
        kp_name: 后置知识点名称（调用方补齐，算法层可为空）。
        kp_score: 后置知识点当前掌握度（0~1）。
        kp_level: 后置知识点当前等级（0~3）。
        pre_kp_id: 前置知识点 ID。
        pre_kp_name: 前置知识点名称（调用方补齐）。
        pre_score: 前置知识点当前掌握度；完全没练过视为 0。
        pre_level: 前置知识点等级；没练过视为 0。
        weight: 依赖强度 0~1（取自 ``knowledge_prerequisite``）。
        missing: True 表示前置知识点完全没有练习数据。
    """

    kp_id: str
    pre_kp_id: str
    kp_name: str = ""
    kp_score: float = 0.0
    kp_level: int = 0
    pre_kp_name: str = ""
    pre_score: float = 0.0
    pre_level: int = 0
    weight: float = 1.0
    missing: bool = False


# ==================================================================== 时间工具
def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """把可能是 naive 的 datetime 统一成 UTC aware。

    SQLite 的 ``DateTime(timezone=True)`` 读回来会丢掉 tzinfo，
    直接与 ``datetime.now(timezone.utc)`` 相减会抛 TypeError，故统一在此兜底。

    Args:
        value: 任意 datetime 或 None。

    Returns:
        UTC aware 的 datetime；入参为 None 时返回 None。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_between(later: datetime, earlier: datetime) -> float:
    """``later - earlier`` 的天数差（负数截断为 0）。"""
    left = as_utc(later)
    right = as_utc(earlier)
    if left is None or right is None:
        return 0.0
    delta = (left - right).total_seconds() / _SECONDS_PER_DAY
    return delta if delta > 0.0 else 0.0


# ================================================================ 掌握度算法
def decay_weight(days: float, half_life_days: float = MASTERY_HALF_LIFE_DAYS) -> float:
    """时效衰减权重 ``0.5 ** (days / half_life)``。

    Args:
        days: 距今天数，负值按 0 处理（未来时间不加成）。
        half_life_days: 半衰期天数，必须为正。

    Returns:
        (0, 1] 区间的权重；``days=0`` 时为 1.0，``days=half_life`` 时为 0.5。

    Examples:
        >>> round(decay_weight(0), 4)
        1.0
        >>> round(decay_weight(14), 4)
        0.5
        >>> round(decay_weight(28), 4)
        0.25
    """
    span = float(days) if days and days > 0 else 0.0
    half = float(half_life_days) if half_life_days and half_life_days > 0 else MASTERY_HALF_LIFE_DAYS
    return math.pow(0.5, span / half)


def compute_mastery(
    events: Sequence[AttemptEvent],
    *,
    now: Optional[datetime] = None,
    prior_score: float = MASTERY_PRIOR_SCORE,
    confidence_k: float = MASTERY_CONFIDENCE_K,
    half_life_days: float = MASTERY_HALF_LIFE_DAYS,
) -> MasteryResult:
    """由练习事件序列算出掌握度。

    算法（贝叶斯式平滑）::

        acc   = correct_count / attempts                 # 原始正确率
        eff   = Σ (decay_weight(距今天数) × 事件权重)      # 有效样本量
        score = (prior × K + acc × eff) / (K + eff)

    直觉：样本越少 / 越久远，``eff`` 越小，``score`` 越被拽回先验 0.5，
    避免「只做过 1 题就判定熟练」这种过拟合。

    Args:
        events: 该知识点下的全部练习事件（顺序无关）。
        now: 计算基准时刻，缺省取当前 UTC 时间（测试可注入固定值）。
        prior_score: 先验分。
        confidence_k: 先验强度。
        half_life_days: 衰减半衰期。

    Returns:
        ``MasteryResult``；``events`` 为空时返回全零结果（level=0）。
    """
    base_now = as_utc(now) or datetime.now(timezone.utc)

    attempts: int = len(events)
    if attempts == 0:
        return MasteryResult()

    correct_count: int = 0
    effective: float = 0.0
    last_practiced: Optional[datetime] = None

    for event in events:
        if event.is_correct:
            correct_count += 1

        occurred = as_utc(event.occurred_at) or base_now
        weight = float(event.weight) if event.weight and event.weight > 0 else 1.0
        effective += decay_weight(_days_between(base_now, occurred), half_life_days) * weight

        if last_practiced is None or occurred > last_practiced:
            last_practiced = occurred

    accuracy: float = correct_count / attempts
    error_rate: float = 1.0 - accuracy

    denominator = confidence_k + effective
    score = (prior_score * confidence_k + accuracy * effective) / denominator if denominator > 0 else prior_score
    score = min(1.0, max(0.0, score))

    return MasteryResult(
        score=round(score, 4),
        level=grade_level(score, effective, attempts),
        attempts=attempts,
        correct_count=correct_count,
        error_rate=round(error_rate, 4),
        effective_attempts=round(effective, 4),
        last_practiced_at=last_practiced,
    )


def grade_level(score: float, effective_attempts: float, attempts: int) -> int:
    """把分值 + 有效样本量映射成 0~3 的掌握等级。

    分级表::

        attempts == 0 或 score < 0.40                  -> 0
        0.40 <= score < 0.60                           -> 1
        0.60 <= score < 0.85 且 eff >= 3               -> 2
        score >= 0.85 且 eff >= 5                      -> 3

    「样本不足降一级」：达到分数线但 ``eff`` 不够时逐级下调，
    例如 score=0.9 但 eff=3.2 → 只能给 2；eff=1 → 只能给 1。

    Args:
        score: 掌握度分值 0~1。
        effective_attempts: 有效样本量。
        attempts: 原始事件数。

    Returns:
        0~3 的整数等级。
    """
    if attempts <= 0 or score < MASTERY_LEVEL1_MIN:
        return 0
    if score < MASTERY_LEVEL2_MIN:
        return 1
    if score < MASTERY_LEVEL3_MIN:
        return 2 if effective_attempts >= MASTERY_LEVEL2_MIN_EFFECTIVE else 1
    # score >= 0.85：满足样本量才给 3，否则逐级降
    if effective_attempts >= MASTERY_LEVEL3_MIN_EFFECTIVE:
        return 3
    if effective_attempts >= MASTERY_LEVEL2_MIN_EFFECTIVE:
        return 2
    return 1


# ==================================================================== 错因分类
# ``classify_error`` 已从 ``app.core.error_categories`` 导入（见上方 import），
# 保证画像与诊断 Agent 共用同一套五类枚举与关键词表（缺口②统一）。
def classify_batch(texts: Sequence[Optional[str]]) -> List[str]:
    """批量分类，返回与入参等长的类别列表。

    Args:
        texts: 错因文本序列。

    Returns:
        类别列表，顺序与入参一一对应。
    """
    return [classify_error(item) for item in texts]


def aggregate_error_patterns(
    evidences: Sequence[ErrorEvidence],
    *,
    evidence_limit: int = ERROR_EVIDENCE_LIMIT,
) -> List[ErrorPattern]:
    """把错因证据聚合成六类分布（**count=0 的类别也会返回**）。

    契约要求前端能画出完整的六段环形图，所以这里恒定返回 6 条，
    按 ``count`` 降序排列，同 count 时按预定义优先级稳定排序。

    Args:
        evidences: 错因证据列表。
        evidence_limit: 每类最多保留几条样例文本。

    Returns:
        长度恒为 6 的 ``ErrorPattern`` 列表。
    """
    counts: Dict[str, int] = {category: 0 for category in ERROR_CATEGORIES}
    samples: Dict[str, List[str]] = {category: [] for category in ERROR_CATEGORIES}

    for evidence in evidences:
        category = classify_error(evidence.text)
        if category not in counts:
            continue  # 未归类（UNCLASSIFIED），不计入五类分布
        counts[category] = counts.get(category, 0) + 1

        snippet = (evidence.text or "").strip()
        bucket = samples.setdefault(category, [])
        if snippet and len(bucket) < max(0, evidence_limit) and snippet not in bucket:
            bucket.append(snippet)

    total = sum(counts.values())
    patterns = [
        ErrorPattern(
            category=category,
            label=error_label(category),
            count=counts.get(category, 0),
            ratio=round(counts.get(category, 0) / total, 4) if total > 0 else 0.0,
            sample_hits=list(samples.get(category, [])),
        )
        for category in ERROR_CATEGORIES
    ]

    patterns.sort(key=lambda item: (-item.count, _CATEGORY_PRIORITY.get(item.category, 99)))
    return patterns


def top_error_category(error_counts: Mapping[str, int]) -> Optional[str]:
    """取错因分布里占比最高的类别（并列时按优先级仲裁）。

    Args:
        error_counts: ``{类别: 次数}``。

    Returns:
        类别枚举值；全为 0 或空字典时返回 None。
    """
    if not error_counts:
        return None
    ranked = sorted(
        (
            (name, int(value))
            for name, value in error_counts.items()
            if int(value) > 0 and name in ERROR_CATEGORIES
        ),
        key=lambda pair: (-pair[1], _CATEGORY_PRIORITY.get(pair[0], 99)),
    )
    return ranked[0][0] if ranked else None


# ================================================== 图谱传播与缺口检测（P0·Phase2）
def rollup_mastery(children: Sequence[MasteryResult]) -> MasteryResult:
    """子知识点掌握度 → 父知识点（**有效样本量加权传播**）。

    规则（GKT 图中父节点 = 子节点聚合）::

        score   = Σ(子 score × 子 effective_attempts) / Σ(effective_attempts)
        attempts / correct_count / effective_attempts = 直接求和
        error_rate = 1 - Σcorrect / Σattempts   （按题量重算，不加权 error_rate）
        level   = grade_level(聚合后的 score / eff / attempts)

    设计取舍：
        * 用 ``effective_attempts`` 做权重 = 「样本越多、越近期的子知识点
          越有发言权」，避免 1 道题 100% 正确率的小样本拉高整个父节点；
        * 所有子节点 ``effective_attempts`` 全为 0 时退化为算术平均（防御）；
        * 父节点等级**不取子节点的众数/最大值**，而是用聚合分重新走
          ``grade_level``，保证「父等级」与「父分数」口径一致。

    Args:
        children: 该父节点下全部**有练习数据**的子节点结果
                  （attempts>0）；为空返回全零 ``MasteryResult``。

    Returns:
        聚合后的父节点掌握度。
    """
    raw = [r for r in children if r is not None and int(r.attempts) > 0]
    if not raw:
        return MasteryResult()

    total_eff = sum(max(0.0, float(r.effective_attempts)) for r in raw)
    total_attempts = sum(int(r.attempts) for r in raw)
    total_correct = sum(int(r.correct_count) for r in raw)

    if total_eff > 0.0:
        score = sum(float(r.score) * max(0.0, float(r.effective_attempts)) for r in raw) / total_eff
    else:  # pragma: no cover - eff>0 恒成立，仅防御
        score = sum(float(r.score) for r in raw) / len(raw)

    last_practiced: Optional[datetime] = None
    for r in raw:
        stamp = as_utc(r.last_practiced_at)
        if stamp is not None and (last_practiced is None or stamp > last_practiced):
            last_practiced = stamp

    return MasteryResult(
        score=round(min(1.0, max(0.0, score)), 4),
        level=grade_level(score, total_eff, total_attempts),
        attempts=total_attempts,
        correct_count=total_correct,
        error_rate=round(1.0 - total_correct / total_attempts, 4) if total_attempts else 0.0,
        effective_attempts=round(total_eff, 4),
        last_practiced_at=last_practiced,
    )


def detect_prerequisite_gaps(
    mastery: Mapping[str, MasteryResult],
    edges: Sequence[PrerequisiteEdge],
    *,
    threshold: float = MASTERY_GAP_THRESHOLD,
) -> List[PrerequisiteGap]:
    """前置知识点缺口检测（GKT 图上的「依赖未满足」）。

    判定口径：
        1. 只考察**后置已练过**的边（``mastery[kp_id].attempts > 0``）——
           后置完全没学的不判缺口，避免把整张图全标红；
        2. 前置掌握度 >= threshold → 不缺口；
        3. 前置完全没练过 or 掌握度 < threshold → 缺口，
           且 ``missing=True`` 表示「前置零样本」；
        4. 边按 ``weight`` 降序、``pre_score`` 升序排序——
           最强依赖、最严重的缺口排最前，方便抽题直接取前 N。

    Args:
        mastery: ``{kp_id(str): MasteryResult}``，本学生当前掌握度（L2 粒度）。
        edges: 前置依赖边序列（仅需含 ``PrerequisiteEdge`` 三字段）。
        threshold: 前置缺口线（默认 :data:`MASTERY_GAP_THRESHOLD`）。

    Returns:
        缺口列表（仅含后置有练习的边）；无缺口返回空列表。
    """
    gaps: List[PrerequisiteGap] = []
    for edge in edges:
        target = mastery.get(edge.kp_id)
        if target is None or int(target.attempts) <= 0:
            continue  # 后置没练过 → 不下缺口结论
        pre = mastery.get(edge.pre_kp_id)
        if pre is not None and float(pre.score) >= threshold:
            continue  # 前置已达标
        gaps.append(
            PrerequisiteGap(
                kp_id=edge.kp_id,
                pre_kp_id=edge.pre_kp_id,
                kp_score=float(target.score),
                kp_level=int(target.level),
                pre_score=float(pre.score) if pre is not None else 0.0,
                pre_level=int(pre.level) if pre is not None else 0,
                weight=float(edge.weight),
                missing=pre is None or int(pre.attempts) <= 0,
            )
        )

    gaps.sort(key=lambda gap: (-gap.weight, gap.pre_score, gap.kp_id))
    return gaps


# ==================================================================== 推荐排序
def _build_reason(entry: MasteryEntry, category: Optional[str]) -> str:
    """按模板生成中文推荐理由（后端生成，前端直接展示）。

    Args:
        entry: 知识点掌握度条目。
        category: 该点最突出的错因类别，可为 None。

    Returns:
        一句话推荐理由。
    """
    result = entry.result
    error_percent = int(round(result.error_rate * 100))
    hint = f"，主要卡在「{error_label(category)}」" if category else ""

    if result.attempts == 0:
        return f"「{entry.kp_name}」还没有练习记录，先做两道摸摸底吧。"
    if result.level == 0:
        return f"「{entry.kp_name}」错了 {error_percent}%{hint}，属于最该优先补的薄弱点。"
    if result.level == 1:
        return f"「{entry.kp_name}」掌握还不稳，错误率 {error_percent}%{hint}，建议再练一轮。"
    if result.effective_attempts < MASTERY_LEVEL2_MIN_EFFECTIVE:
        return f"「{entry.kp_name}」做对过但练习量偏少{hint}，多练几道才算真会。"
    return f"「{entry.kp_name}」有点生疏了，错误率 {error_percent}%{hint}，回顾一下巩固住。"


def compute_priority(result: MasteryResult) -> float:
    """算推荐优先级（越大越该练）。

    权重设计::

        priority = 0.60 × (1 - score)          # 越不会越该练
                 + 0.30 × error_rate           # 错得越多越该练
                 + 0.10 × min(eff, 5) / 5      # 有足够样本佐证的，结论更可信

    Args:
        result: 掌握度结果。

    Returns:
        0~1 的优先级分值（保留 4 位小数）。
    """
    weakness = 1.0 - float(result.score)
    confidence = min(float(result.effective_attempts), 5.0) / 5.0
    priority = 0.60 * weakness + 0.30 * float(result.error_rate) + 0.10 * confidence
    return round(min(1.0, max(0.0, priority)), 4)


def rank_recommendations(
    masteries: Sequence[MasteryEntry],
    patterns: Sequence[ErrorPattern] = (),
    limit: int = RECOMMEND_TOP_N,
    questions: Optional[Mapping[str, Sequence[str]]] = None,
) -> List[Recommendation]:
    """挑出最该练的 Top N 知识点。

    过滤口径：已经 ``level == 3``（熟练）的知识点默认不推；
    但如果过滤后一条都不剩（学霸场景），则放开限制按优先级给前 N 条，
    保证看板不出现「无推荐」的空白。

    Args:
        masteries: 全部知识点的掌握度条目。
        patterns: 全局错因分布，用于给「该点无错因记录」的条目兜底。
        limit: 返回条数上限。
        questions: ``{kp_id: [question_id, ...]}``，用于填 ``suggested_question_ids``。

    Returns:
        按 ``priority`` 降序的推荐列表（可能为空）。
    """
    if not masteries:
        return []

    size = int(limit) if limit and limit > 0 else RECOMMEND_TOP_N
    question_map: Mapping[str, Sequence[str]] = questions or {}
    global_top: Optional[str] = None
    for pattern in patterns:
        if pattern.count > 0:
            global_top = pattern.category
            break

    candidates: List[Recommendation] = []
    for entry in masteries:
        category = top_error_category(entry.error_counts) or global_top
        qids = [str(item) for item in question_map.get(entry.kp_id, [])][:RECOMMEND_QUESTION_LIMIT]
        candidates.append(
            Recommendation(
                kp_id=entry.kp_id,
                kp_name=entry.kp_name,
                reason=_build_reason(entry, category),
                priority=compute_priority(entry.result),
                score=float(entry.result.score),
                error_rate=float(entry.result.error_rate),
                top_error_category=category,
                suggested_question_ids=qids,
            )
        )

    # 主口径：过滤掉已熟练的；兜底：全熟练时不过滤，仍给出 Top N
    pending = [item for item, entry in zip(candidates, masteries) if entry.result.level < 3]
    pool = pending or candidates

    # 稳定排序：优先级降序 → 错误率降序 → 知识点名升序
    pool.sort(key=lambda item: (-item.priority, -item.error_rate, item.kp_name))
    return pool[:size]


__all__ = [
    "AttemptEvent",
    "MasteryResult",
    "ErrorEvidence",
    "ErrorPattern",
    "MasteryEntry",
    "Recommendation",
    "PrerequisiteEdge",
    "PrerequisiteGap",
    "as_utc",
    "decay_weight",
    "compute_mastery",
    "grade_level",
    "classify_error",
    "classify_batch",
    "aggregate_error_patterns",
    "top_error_category",
    "compute_priority",
    "rank_recommendations",
    "rollup_mastery",
    "detect_prerequisite_gaps",
]

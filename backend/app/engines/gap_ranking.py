# -*- coding: utf-8 -*-
"""知识点缺口排序引擎（gap ranking）——**纯函数层**。

用途
----
把「**零题量知识点**」按「补题价值」排成一个队列，告诉补题引擎先补哪些 KP。

排序因子链（本轮定稿）
--------------------
    1. **错因命中数**（``error_hit_count``）——学生在该 KP 上踩过的错因次数。
       ⚠️ 本轮恒为 0：错因富化埋点尚未上线，**因子位保留但无数据**，
       这样等数据源就绪时不必改排序骨架。
    2. **重要度**（``importance``，1~5，来自 ``knowledge_points.importance``）。
    3. **难度**（``difficulty``，1/3/5，来自 ``knowledge_points.difficulty``）——
       越难越难出题，越该优先补。
    4. **零题量时长**（``zero_duration_days``）——该 KP 已经多少天一道题都没有。

排序是**字典序**（不是加权求和）：因子 1 压倒因子 2，factor 2 压倒 factor 3……
这样"错因命中"一旦有数据，能立刻把真正出过错的 KP 顶到队首。

「前置被依赖」因子：本轮**不参与排序**
-----------------------------------------
被多个 KP 依赖的知识点（前置 KP）缺口更致命，理论上应当进因子链。但：

* ``knowledge_relations`` 表当前只有 **4** 行；
* ``knowledge_prerequisite`` 表当前 **0** 行。

也就是说「谁依赖谁」这件事在数据层**几乎不存在**，此时把它放进因子链等于
给一个恒为 0 的因子加权——不但没用，还会让人误以为这个维度已经被考虑过。
所以本轮的做法是：

* **保留接口位**：``GapCandidate.prerequisite_count`` /
  ``prerequisite_kp_ids`` 字段照常存在，报告里也照常输出；
* **因子链里显式标记 ``enabled=False``**，并写清启用条件；
* **不编造数据**：宁可让这一维缺席，也不拿假数据糊弄排序。

等依赖关系数据补齐后，把 ``FACTOR_CHAIN`` 里 ``prerequisite_count`` 的
``enabled`` 改成 ``True`` 即可（建议插在 ``error_hit_count`` 之后）。

学段处理
--------
本轮补题只面向**小学**段知识点。初中（``CZ_`` 前缀 / 7~9 年级）与
高中（``HS_`` 前缀 / 10~12 年级）的知识点标记为**暂缓**，不进补题队列，
但**不丢弃**——它们会以 ``deferred=True`` 出现在结果里，便于人工过目。

纯层铁律
--------
本模块**只依赖标准库**：禁止 import ``sqlalchemy`` / ``Session`` /
``os.environ`` / 配置文件；数据由调用方（脚本 / 服务层）以
``GapCandidate`` 数据类喂进来。阈值一律走模块常量，
**不新增任何 ``adaptive.`` 配置键**（``CONFIG_SPECS`` 被 31 键 golden 守卫锁死）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "RankingFactor",
    "FACTOR_CHAIN",
    "GapCandidate",
    "GapRankResult",
    "GapRanking",
    "grade_band_of",
    "band_of_candidate",
    "candidate_from_mapping",
    "rank_gaps",
    "ENABLED_FACTOR_KEYS",
]


# ============================================================ 学段常量
BAND_PRIMARY = "primary"
BAND_JUNIOR = "junior"
BAND_SENIOR = "senior"
BAND_UNKNOWN = "unknown"

#: 小学段年级区间（含端点）
PRIMARY_GRADE_RANGE: Tuple[int, int] = (1, 6)
#: 初中段年级区间（含端点）
JUNIOR_GRADE_RANGE: Tuple[int, int] = (7, 9)
#: 高中段年级区间（含端点）
SENIOR_GRADE_RANGE: Tuple[int, int] = (10, 12)

#: 知识点 code 前缀约定（与 ``app/scripts/seed_kp_cz.py`` / ``seed_kp_hs.py``
#: 保持一致）：``CZ_`` = 初中、``HS_`` = 高中、``KP_`` = 小学。
CODE_PREFIX_BAND: Mapping[str, str] = {
    "CZ_": BAND_JUNIOR,
    "HS_": BAND_SENIOR,
    "KP_": BAND_PRIMARY,
}

#: 本轮补题只做小学段；初 / 高中段标记暂缓。
ACTIVE_BANDS: Tuple[str, ...] = (BAND_PRIMARY,)


# ============================================================ 因子链
@dataclass(frozen=True)
class RankingFactor:
    """一个排序因子的定义。

    Attributes:
        key: 因子在 ``GapCandidate`` / ``GapRankResult.factors`` 里的字段名。
        label: 中文标签（报告里用）。
        order: 因子链中的次序（**越大越靠后**，1 是最强因子）。
        enabled: 本轮是否参与排序。
        descending: 是否按降序（True）还是升序（False）排列。
        note: 启用 / 停用原因，写进报告供人工过目。
    """

    key: str
    label: str
    order: int
    enabled: bool = True
    descending: bool = True
    note: str = ""


#: 本轮定稿的因子链（**顺序即优先级**，字典序比较）。
FACTOR_CHAIN: Tuple[RankingFactor, ...] = (
    RankingFactor(
        key="error_hit_count",
        label="错因命中数",
        order=1,
        enabled=True,
        descending=True,
        note="学生在该 KP 上踩过的错因次数；本轮错因富化埋点未上线，恒为 0，"
        "因子位保留以便数据就绪后直接生效。",
    ),
    RankingFactor(
        key="importance",
        label="重要度",
        order=2,
        enabled=True,
        descending=True,
        note="knowledge_points.importance（1~5），越大越该先补。",
    ),
    RankingFactor(
        key="difficulty",
        label="难度",
        order=3,
        enabled=True,
        descending=True,
        note="knowledge_points.difficulty（1/3/5），越难越难出题，越该先补。",
    ),
    RankingFactor(
        key="zero_duration_days",
        label="零题量时长",
        order=4,
        enabled=True,
        descending=True,
        note="该 KP 已经多少天一道题都没有；缺题越久越该先补。",
    ),
    RankingFactor(
        key="prerequisite_count",
        label="前置被依赖",
        order=5,
        enabled=False,
        descending=True,
        note="本轮不参与排序：knowledge_relations 仅 4 行、knowledge_prerequisite 0 行，"
        "「谁依赖谁」在数据层几乎不存在，此时加权等于给恒 0 因子加权。"
        "接口位保留——数据源补齐后把 enabled 改成 True 即可（建议插在错因命中数之后）。",
    ),
)

#: 本轮**实际参与排序**的因子 key（按优先级排序）。
ENABLED_FACTOR_KEYS: Tuple[str, ...] = tuple(
    factor.key for factor in sorted(FACTOR_CHAIN, key=lambda item: item.order)
    if factor.enabled
)


# ============================================================ 数据类
@dataclass(frozen=True)
class GapCandidate:
    """一个待排序的知识点缺口候选（**入参**）。

    Attributes:
        knowledge_point_id: 知识点 id。
        kp_code: 知识点 code（用于学段判定与报告）。
        kp_name: 知识点名称。
        grade: 升入年级（``knowledge_points.grade``）。
        subject: 学科。
        importance: 重要度（1~5）。
        difficulty: 难度（1/3/5）。
        question_count: 当前题量（``status='active'`` 的题数）。
        zero_duration_days: 已经零题量的天数。
        error_hit_count: 错因命中次数（本轮恒为 0）。
        error_reason_tags: 命中的错因标签（本轮恒为空）。
        prerequisite_count: 被依赖次数（本轮恒为 0；接口位）。
        prerequisite_kp_ids: 依赖它的知识点 id 列表（本轮恒为空；接口位）。
        deferred: 显式暂缓标记（``True`` 时一定不进补题队列）。
        deferred_reason: 暂缓原因（中文）。
    """

    knowledge_point_id: str
    kp_code: str = ""
    kp_name: str = ""
    grade: Optional[int] = None
    subject: str = ""
    importance: int = 0
    difficulty: int = 0
    question_count: int = 0
    zero_duration_days: int = 0
    error_hit_count: int = 0
    error_reason_tags: Tuple[str, ...] = ()
    prerequisite_count: int = 0
    prerequisite_kp_ids: Tuple[str, ...] = ()
    deferred: bool = False
    deferred_reason: str = ""

    def __post_init__(self) -> None:
        """入参归一化：数值下限兜底，字符串去空白，None 归一。"""
        object.__setattr__(self, "knowledge_point_id", str(self.knowledge_point_id).strip())
        object.__setattr__(self, "kp_code", str(self.kp_code or "").strip())
        object.__setattr__(self, "kp_name", str(self.kp_name or "").strip())
        object.__setattr__(self, "subject", str(self.subject or "").strip())
        object.__setattr__(self, "importance", max(0, int(self.importance or 0)))
        object.__setattr__(self, "difficulty", max(0, int(self.difficulty or 0)))
        object.__setattr__(self, "question_count", max(0, int(self.question_count or 0)))
        object.__setattr__(
            self, "zero_duration_days", max(0, int(self.zero_duration_days or 0))
        )
        object.__setattr__(
            self, "error_hit_count", max(0, int(self.error_hit_count or 0))
        )
        object.__setattr__(
            self, "prerequisite_count", max(0, int(self.prerequisite_count or 0))
        )
        object.__setattr__(self, "error_reason_tags", tuple(self.error_reason_tags or ()))
        object.__setattr__(
            self, "prerequisite_kp_ids", tuple(self.prerequisite_kp_ids or ())
        )
        object.__setattr__(self, "deferred", bool(self.deferred))
        object.__setattr__(
            self, "deferred_reason", str(self.deferred_reason or "").strip()
        )

    @property
    def band(self) -> str:
        """学段：``primary`` / ``junior`` / ``senior`` / ``unknown``。"""
        return band_of_candidate(self)


@dataclass(frozen=True)
class GapRankResult:
    """一个排序结果条目（**出参**）。

    Attributes:
        rank: 补题队列里的名次（1 起）；暂缓条目为 ``None``。
        knowledge_point_id: 知识点 id。
        kp_code: 知识点 code。
        kp_name: 知识点名称。
        band: 学段。
        factors: 各因子的原始值（键 = 因子 key）。
        sort_key: 实际用于排序的比较键（元组）。
        reason: 中文理由（可展示）。
        deferred: 是否被暂缓。
        deferred_reason: 暂缓原因。
    """

    rank: Optional[int]
    knowledge_point_id: str
    kp_code: str
    kp_name: str
    band: str
    factors: Dict[str, int] = field(default_factory=dict)
    sort_key: Tuple[Any, ...] = ()
    reason: str = ""
    deferred: bool = False
    deferred_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict（键名 camelCase，便于 API 直出）。"""
        return {
            "rank": self.rank,
            "knowledgePointId": self.knowledge_point_id,
            "kpCode": self.kp_code,
            "kpName": self.kp_name,
            "band": self.band,
            "factors": dict(self.factors),
            "sortKey": list(self.sort_key),
            "reason": self.reason,
            "deferred": bool(self.deferred),
            "deferredReason": self.deferred_reason,
        }


@dataclass(frozen=True)
class GapRanking:
    """一次排序的完整结论（**出参**）。

    Attributes:
        ranked: 进入补题队列的条目（按名次升序）。
        deferred: 被暂缓的条目（不进队列，但**不丢弃**）。
        excluded: 因已有题量而被排除的条目（``only_zero_question=True`` 时）。
        factor_chain: 本轮生效的因子链描述（供报告留痕）。
    """

    ranked: Tuple[GapRankResult, ...] = ()
    deferred: Tuple[GapRankResult, ...] = ()
    excluded: Tuple[GapRankResult, ...] = ()
    factor_chain: Tuple[RankingFactor, ...] = FACTOR_CHAIN

    @property
    def total(self) -> int:
        """候选总数（入参条数）。"""
        return len(self.ranked) + len(self.deferred) + len(self.excluded)

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict。"""
        return {
            "ranked": [item.to_dict() for item in self.ranked],
            "deferred": [item.to_dict() for item in self.deferred],
            "excluded": [item.to_dict() for item in self.excluded],
            "factorChain": [
                {
                    "key": factor.key,
                    "label": factor.label,
                    "order": factor.order,
                    "enabled": factor.enabled,
                    "note": factor.note,
                }
                for factor in sorted(self.factor_chain, key=lambda item: item.order)
            ],
        }


# ============================================================ 学段判定
def grade_band_of(grade: Optional[int]) -> str:
    """按年级判定学段。

    ⚠️ 口径与 ``app/models/content.py::BAND_GRADE_RANGE`` 一致：
    ``grade`` 是**升入年级**（小学 1~6 / 初中 7~9 / 高中 10~12），
    **绝不**做 ``grade - 1`` 换算。

    Args:
        grade: 年级；``None`` / 非法值返回 ``"unknown"``。

    Returns:
        ``"primary"`` / ``"junior"`` / ``"senior"`` / ``"unknown"``。
    """
    if grade is None:
        return BAND_UNKNOWN
    try:
        value = int(grade)
    except (TypeError, ValueError):
        return BAND_UNKNOWN
    if PRIMARY_GRADE_RANGE[0] <= value <= PRIMARY_GRADE_RANGE[1]:
        return BAND_PRIMARY
    if JUNIOR_GRADE_RANGE[0] <= value <= JUNIOR_GRADE_RANGE[1]:
        return BAND_JUNIOR
    if SENIOR_GRADE_RANGE[0] <= value <= SENIOR_GRADE_RANGE[1]:
        return BAND_SENIOR
    return BAND_UNKNOWN


def band_of_candidate(candidate: GapCandidate) -> str:
    """判定候选知识点的学段。

    **年级优先**；年级缺失（``NULL``）时用 code 前缀兜底——
    这正是库里 59 个「零题量且年级为空」的知识点的判定方式
    （它们的 code 前缀就编码了学段）。

    Args:
        candidate: 候选知识点。

    Returns:
        学段字符串。
    """
    band = grade_band_of(candidate.grade)
    if band != BAND_UNKNOWN:
        return band
    code = (candidate.kp_code or "").upper()
    for prefix, prefix_band in CODE_PREFIX_BAND.items():
        if code.startswith(prefix):
            return prefix_band
    return BAND_UNKNOWN


# ============================================================ 适配器
def candidate_from_mapping(row: Mapping[str, Any]) -> GapCandidate:
    """把一行「字典形态」的知识点数据适配成 :class:`GapCandidate`。

    这是纯函数层与数据层之间的**唯一缝**：调用方（脚本 / 服务层）负责查库，
    把行整理成 dict 喂进来；引擎本身不碰 DB。

    识别的键（大小写均可，缺省即缺省值）：

        * ``knowledge_point_id`` / ``kp_id`` / ``id``
        * ``kp_code`` / ``code``
        * ``kp_name`` / ``name``
        * ``grade``
        * ``subject``
        * ``importance``
        * ``difficulty``
        * ``question_count``
        * ``zero_duration_days``
        * ``error_hit_count``
        * ``error_reason_tags``
        * ``prerequisite_count``
        * ``prerequisite_kp_ids``

    Args:
        row: 字典形态的数据行。

    Returns:
        归一化后的候选对象。
    """

    def pick(*names: str, default: Any = None) -> Any:
        for name in names:
            if name in row and row[name] is not None:
                return row[name]
        return default

    def as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    grade_raw = pick("grade")
    grade: Optional[int]
    try:
        grade = int(grade_raw) if grade_raw is not None else None
    except (TypeError, ValueError):
        grade = None

    tags_raw = pick("error_reason_tags", default=())
    if isinstance(tags_raw, str):
        tags: Tuple[str, ...] = (tags_raw,)
    else:
        tags = tuple(str(item) for item in (tags_raw or ()))

    prereq_raw = pick("prerequisite_kp_ids", default=())
    if isinstance(prereq_raw, str):
        prereqs: Tuple[str, ...] = (prereq_raw,)
    else:
        prereqs = tuple(str(item) for item in (prereq_raw or ()))

    return GapCandidate(
        knowledge_point_id=str(
            pick("knowledge_point_id", "kp_id", "id", default="")
        ),
        kp_code=str(pick("kp_code", "code", default="")),
        kp_name=str(pick("kp_name", "name", default="")),
        grade=grade,
        subject=str(pick("subject", default="")),
        importance=as_int(pick("importance", default=0)),
        difficulty=as_int(pick("difficulty", default=0)),
        question_count=as_int(pick("question_count", default=0)),
        zero_duration_days=as_int(pick("zero_duration_days", default=0)),
        error_hit_count=as_int(pick("error_hit_count", default=0)),
        error_reason_tags=tags,
        prerequisite_count=as_int(pick("prerequisite_count", default=0)),
        prerequisite_kp_ids=prereqs,
    )


# ============================================================ 排序
def _sort_key(candidate: GapCandidate) -> Tuple[Any, ...]:
    """构造候选的比较键：启用因子按链序降序取负，最后按 id 升序稳定收尾。"""
    key: List[Any] = []
    for factor in sorted(FACTOR_CHAIN, key=lambda item: item.order):
        if not factor.enabled:
            continue
        value = int(getattr(candidate, factor.key, 0) or 0)
        key.append(-value if factor.descending else value)
    key.append(candidate.knowledge_point_id)
    return tuple(key)


def _reason_text(candidate: GapCandidate) -> str:
    """生成中文理由（给报告 / 人工过目用）。"""
    parts: List[str] = []
    for factor in sorted(FACTOR_CHAIN, key=lambda item: item.order):
        if not factor.enabled:
            continue
        value = int(getattr(candidate, factor.key, 0) or 0)
        parts.append(f"{factor.label} {value}")
    return "；".join(parts)


def _factor_snapshot(candidate: GapCandidate) -> Dict[str, int]:
    """因子快照（含**停用因子**，便于报告留痕说明"为什么这一维没参与"）。"""
    snapshot: Dict[str, int] = {}
    for factor in sorted(FACTOR_CHAIN, key=lambda item: item.order):
        snapshot[factor.key] = int(getattr(candidate, factor.key, 0) or 0)
    return snapshot


def rank_gaps(
    candidates: Iterable[GapCandidate],
    *,
    only_zero_question: bool = True,
    active_bands: Sequence[str] = ACTIVE_BANDS,
) -> GapRanking:
    """给知识点缺口候选排序（**纯函数**）。

    Args:
        candidates: 候选知识点。
        only_zero_question: ``True`` 时只把 ``question_count <= 0`` 的候选
            放进补题队列（缺口排序的本义）；已有题量的候选进 ``excluded``。
        active_bands: 本轮补题覆盖的学段（默认只有小学段）；
            其它学段的候选进 ``deferred``。

    Returns:
        :class:`GapRanking`（``ranked`` / ``deferred`` / ``excluded`` 三段，
        三段合起来 = 入参条数，**一条都不丢**）。
    """
    all_candidates = list(candidates)
    ranked_items: List[GapRankResult] = []
    deferred_items: List[GapRankResult] = []
    excluded_items: List[GapRankResult] = []

    for candidate in all_candidates:
        band = band_of_candidate(candidate)
        base = GapRankResult(
            rank=None,
            knowledge_point_id=candidate.knowledge_point_id,
            kp_code=candidate.kp_code,
            kp_name=candidate.kp_name,
            band=band,
            factors=_factor_snapshot(candidate),
            sort_key=_sort_key(candidate),
            reason=_reason_text(candidate),
            deferred=False,
            deferred_reason="",
        )
        if candidate.deferred:
            deferred_items.append(
                GapRankResult(
                    rank=None,
                    knowledge_point_id=base.knowledge_point_id,
                    kp_code=base.kp_code,
                    kp_name=base.kp_name,
                    band=band,
                    factors=base.factors,
                    sort_key=base.sort_key,
                    reason=base.reason,
                    deferred=True,
                    deferred_reason=candidate.deferred_reason or "显式暂缓",
                )
            )
            continue
        if band not in tuple(active_bands):
            if band == BAND_JUNIOR:
                deferred_reason = "初中段本轮暂缓（补题队列只做小学段）"
            elif band == BAND_SENIOR:
                deferred_reason = "高中段本轮暂缓（补题队列只做小学段）"
            else:
                deferred_reason = (
                    "学段未识别（年级为空且 code 前缀不在 CZ_/HS_/KP_ 之列）——"
                    "本轮暂缓，待人工确认学段后再入队"
                )
            deferred_items.append(
                GapRankResult(
                    rank=None,
                    knowledge_point_id=base.knowledge_point_id,
                    kp_code=base.kp_code,
                    kp_name=base.kp_name,
                    band=band,
                    factors=base.factors,
                    sort_key=base.sort_key,
                    reason=base.reason,
                    deferred=True,
                    deferred_reason=deferred_reason,
                )
            )
            continue
        if only_zero_question and candidate.question_count > 0:
            excluded_items.append(base)
            continue
        ranked_items.append(base)

    ranked_items.sort(key=lambda item: item.sort_key)
    for index, item in enumerate(ranked_items, start=1):
        ranked_items[index - 1] = GapRankResult(
            rank=index,
            knowledge_point_id=item.knowledge_point_id,
            kp_code=item.kp_code,
            kp_name=item.kp_name,
            band=item.band,
            factors=item.factors,
            sort_key=item.sort_key,
            reason=item.reason,
            deferred=item.deferred,
            deferred_reason=item.deferred_reason,
        )

    deferred_items.sort(key=lambda item: item.sort_key)
    excluded_items.sort(key=lambda item: item.sort_key)

    return GapRanking(
        ranked=tuple(ranked_items),
        deferred=tuple(deferred_items),
        excluded=tuple(excluded_items),
    )

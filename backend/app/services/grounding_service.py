"""接地气素材检索服务（**唯一碰 ORM 的地方**）。

职责：从题库里检索「接地气讲解素材」，产出
:class:`app.engines.grounding_materials.GroundingMaterials` 四段包。

层次边界（铁律）
----------------
* ``app/engines/grounding_materials.py`` / ``grounding_retrieval.py`` 是**纯函数层**：
  不 import sqlalchemy、不读 env、不查库；
* **本模块是唯一允许 import ORM 的地方**；
* 上层（``app/ai/agents/tutor.py``）只接收渲染好的文本，不碰 DB。

两池口径（必须分开，绝不混用）
------------------------------
* **讲解素材池（teaching pool）** = 全量 ``active`` 题，**含 kp_id 为空的题**
  （实测 864 道 scenario 题——``shopping`` / ``cake`` / ``distance`` / ``time``
  这类生活场景题大量集中在这一批，它们的场景词最丰富，正是「接地气」的来源）。
* **归因类素材池（attribution pool）** = ``kp_id IS NOT NULL`` 的 ``active`` 题。
  归因类素材（同伴视角句、个体错因）必须挂到具体知识点上，所以这一池
  **必须**限定 kp_id 非空 —— 挂不上知识点的题对归因没有意义。

年级过滤（硬验收项，100% 正确）
-------------------------------
内测目标 1-6 年级，题库里躺着 **881 条初高中语料**
（g7:158 / g8:172 / g9:187 / g10:113 / g11:121 / g12:130）。
**绝不能把它们注入小学孩子**。三道防线：

1. SQL 层：``grade IN (:window)``，窗口由
   :func:`app.engines.grounding_materials.grade_window` 给出，上限恒为 6；
2. Python 层：结果里再过一遍 ``grade <= PRIMARY_MAX_GRADE``；
3. 服务层：学生年级已知且 > 6 时，**直接返回空素材包**（本期只服务小学）。

为什么不上 FTS5
----------------
题量仅 4166 条，结构化粗筛后候选只有几百行；实测无索引 LIKE 扫描
median **0.391ms**，而 bigram + FTS5 median **0.018ms** —— 差 0.37ms。
为 0.37ms 引入一张需要与 ``questions`` 表**保持同步**的 FTS5 虚拟表
（漏同步一次 = 静默检索错误），且 FTS5 默认 ``unicode61`` 不切中文、
``trigram`` 卡 2 字词（本库高频词恰恰是「平均」「一共」这类 2 字词），
选型风险高于收益。**结论：不建 FTS5，用 LIKE + bigram 打分。**

进程内缓存
----------
检索结果按 ``(池, kp_id, 年级窗口, 类型, 难度, 查询指纹)`` 缓存，TTL
:data:`CACHE_TTL_SECONDS` 秒；题库导入后可显式调
:func:`clear_grounding_cache` 立即失效。
缓存里只存**纯数据**（dataclass），不存 ORM 对象 —— 会话关闭后依然安全。
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import inspect, or_, select
from sqlalchemy.orm import Session

from app.engines.grounding_materials import (
    PRIMARY_MAX_GRADE,
    GroundingMaterials,
    build_analogy,
    build_kp_anchor,
    extract_scene_anchor,
    grade_window,
    render_materials_for_prompt,
)
from app.engines.grounding_retrieval import bigrams, like_terms, score_text
from app.models.content import KnowledgePoint, Question

logger = logging.getLogger("app.services.grounding_service")

# =============================================================================
# 常量
# =============================================================================

#: 灰度开关的 system_config 键名（唯一真相源在 ``app.core.system_config.DEFAULTS``）。
GROUNDING_CONFIG_KEY: str = "RAG_GROUNDING_ENABLED"

#: A-2 期同伴视角语料表名。本期**不落表**，本模块靠
#: ``inspect(db.connection()).has_table(...)`` 探测，探测不到就当 peer 段为空。
#:
#: 为什么必须用 ``inspect(db.connection())`` 而不是 ``inspect(db.bind)``：
#: ``db.bind`` 会从连接池**再借一条连接**；对 StaticPool 单连接库（测试用的
#: 内存 SQLite 就是它），那条连接归还时会把事务 rollback —— **静默回滚调用方
#: 同事务里尚未提交的写入**，不报错，是最难查的一类 bug。
PEER_CORPUS_TABLE: str = "peer_view_corpus"

#: 进程内缓存 TTL（秒）。
CACHE_TTL_SECONDS: int = 300

#: 单次检索在 SQL 层最多取多少行进 Python 打分（粗筛 + LIKE 之后）。
SQL_CANDIDATE_LIMIT: int = 500

#: 单次检索最终返回的候选数。
DEFAULT_TOP_K: int = 5

#: 两个池的名字（测试与日志里用同一套字面量）。
POOL_TEACHING: str = "teaching"
POOL_ATTRIBUTION: str = "attribution"

# =============================================================================
# 输入 / 输出数据结构
# =============================================================================


@dataclass(frozen=True)
class GroundingQuery:
    """一次检索的输入。

    Attributes:
        text: 检索目标串（题面 / 错因描述）。用于 LIKE 粗筛与 bigram 打分。
        kp_id: 知识点 id（字符串或 UUID）。归因池命中它时优先同 KP 题。
        student_grade: 学生年级（1-6）。``None`` 表示未知 → 窗口取 1~6 全段。
        question_type: 题型（``选择`` / ``填空`` / ``应用`` …），结构化粗筛字段。
        difficulty: 难度（1/3/5），结构化粗筛字段。
        grade_width: 年级窗口半宽，默认 1。
        top_k: 最终返回候选数。
    """

    text: str = ""
    kp_id: Optional[str] = None
    student_grade: Optional[int] = None
    question_type: Optional[str] = None
    difficulty: Optional[int] = None
    grade_width: int = 1
    top_k: int = DEFAULT_TOP_K

    def fingerprint(self, pool: str) -> str:
        """检索缓存键（不含 TTL / 库版本）。

        口径：池 + 目标串 + kp + 年级窗口 + 类型 + 难度。
        **年级窗口进键**是硬要求 —— 年级过滤是最重要的安全边界，
        不同年级窗口的结果绝不能互相复用。
        """
        window = ",".join(str(g) for g in self.grade_window())
        raw = "|".join(
            [
                pool,
                (self.text or "").strip(),
                str(self.kp_id or ""),
                window,
                str(self.question_type or ""),
                str(self.difficulty if self.difficulty is not None else ""),
            ]
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def grade_window(self) -> Tuple[int, ...]:
        """本查询允许的年级窗口（上限恒为 6）。"""
        return grade_window(self.student_grade, width=self.grade_width)


@dataclass(frozen=True)
class GroundingCandidate:
    """一条检索命中的候选题（纯数据，可安全缓存）。"""

    question_id: str
    content: str
    grade: Optional[int]
    kp_id: Optional[str]
    score: float
    pool: str
    source_tag: str = ""
    ship_ready: bool = False

    def has_kp(self) -> bool:
        """是否挂了知识点（归因池的硬条件）。"""
        return bool(self.kp_id)


# =============================================================================
# 进程内缓存
# =============================================================================

#: 缓存键 → (写入时刻, 候选列表)。
_CACHE: Dict[str, Tuple[float, List[GroundingCandidate]]] = {}


def clear_grounding_cache() -> None:
    """清空进程内检索缓存（题库导入 / 语料放行后应显式调用）。"""
    _CACHE.clear()


def _cache_get(key: str) -> Optional[List[GroundingCandidate]]:
    """读缓存；过期或不存在返回 ``None``。"""
    item = _CACHE.get(key)
    if item is None:
        return None
    written_at, payload = item
    if time.monotonic() - written_at > CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: Sequence[GroundingCandidate]) -> None:
    """写缓存（顺手清掉过期项，避免无限增长）。"""
    now = time.monotonic()
    for stale_key in [k for k, (t, _) in _CACHE.items() if now - t > CACHE_TTL_SECONDS]:
        _CACHE.pop(stale_key, None)
    _CACHE[key] = (now, list(payload))


# =============================================================================
# 表存在性探测
# =============================================================================


def table_exists(db: Session, table_name: str) -> bool:
    """探测表是否存在（**必须**走 ``inspect(db.connection())``）。

    为什么不能用 ``inspect(db.bind).has_table()``：``db.bind`` 会从连接池
    **再借一条连接**；对 StaticPool 单连接库（测试用的内存 SQLite），
    那条连接归还时会 rollback —— **静默回滚调用方同事务里未提交的写入**，
    不报错、极难查。``db.connection()`` 复用调用方当前这条连接，没有这个问题。

    Args:
        db: 调用方传入的数据库会话。
        table_name: 表名。

    Returns:
        表存在返回 ``True``；探测异常一律返回 ``False``（优雅降级）。
    """
    try:
        return bool(inspect(db.connection()).has_table(table_name))
    except Exception as exc:  # pragma: no cover - 探测失败按「不存在」处理
        logger.warning("探测表 %s 失败: %s", table_name, exc)
        return False


# =============================================================================
# 检索
# =============================================================================


def _build_candidates_query(
    *,
    pool: str,
    window: Sequence[int],
    kp_id: Optional[str],
    question_type: Optional[str],
    difficulty: Optional[int],
    like_words: Sequence[str],
):
    """组装结构化粗筛 + LIKE 精排的 SQL（**不执行**，返回可执行语句）。

    结构化粗筛字段：``status`` / ``grade`` / ``kp_id`` / ``type`` / ``difficulty``。
    LIKE 精排：目标串里最长的几个词做「或」匹配（无索引，全表扫，
    实测 median 0.391ms，4166 行规模完全够用）。
    """
    stmt = select(Question).where(
        Question.status == "active",
        Question.grade.in_(list(window)),
    )
    if pool == POOL_ATTRIBUTION:
        # 归因类素材池：必须挂得上知识点
        stmt = stmt.where(Question.kp_id.isnot(None))
    elif kp_id:
        # 讲解池：给了 kp 就收窄到同 KP；没给就保持全量（含 864 道无 kp 题）
        stmt = stmt.where(Question.kp_id == kp_id)
    if question_type:
        stmt = stmt.where(Question.type_ == question_type)
    if difficulty is not None:
        stmt = stmt.where(Question.difficulty == difficulty)
    if like_words:
        stmt = stmt.where(
            or_(*[Question.content.like(f"%{word}%") for word in like_words])
        )
    return stmt.limit(SQL_CANDIDATE_LIMIT)


def _row_to_candidate(row: Any, pool: str, score: float) -> GroundingCandidate:
    """ORM 行 → 候选 dataclass（纯数据，便于缓存）。"""
    return GroundingCandidate(
        question_id=str(row.id),
        content=row.content or "",
        grade=row.grade,
        kp_id=str(row.kp_id) if row.kp_id else None,
        score=score,
        pool=pool,
        source_tag=getattr(row, "source_tag", "") or "",
        ship_ready=bool(getattr(row, "ship_ready", False)),
    )


def search_candidates(
    db: Session,
    query: GroundingQuery,
    *,
    pool: str = POOL_TEACHING,
) -> List[GroundingCandidate]:
    """检索候选题（结构化粗筛 + LIKE 精排 + bigram 打分）。

    Args:
        db: 调用方传入的数据库会话。
        query: 检索输入。
        pool: ``POOL_TEACHING``（全量 active 题，含无 kp 的 864 道）或
            ``POOL_ATTRIBUTION``（强制 ``kp_id IS NOT NULL``）。

    Returns:
        按 bigram 覆盖度降序的候选列表；检索不到返回空列表（**不抛错**）。
    """
    if pool not in (POOL_TEACHING, POOL_ATTRIBUTION):
        raise ValueError(f"未知素材池: {pool!r}")

    window = query.grade_window()
    if not window:
        return []

    key = query.fingerprint(pool)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        like_words = like_terms(query.text or "", limit=3)
        stmt = _build_candidates_query(
            pool=pool,
            window=window,
            kp_id=query.kp_id,
            question_type=query.question_type,
            difficulty=query.difficulty,
            like_words=like_words,
        )
        rows = db.execute(stmt).scalars().all()
    except Exception as exc:
        # 检索失败**绝不**阻断辅导主流程
        logger.warning("接地气素材检索失败(pool=%s): %s", pool, exc)
        return []

    # Python 层二次过滤：年级上限（硬安全边界，不依赖 SQL 写对）
    safe_rows = [row for row in rows if row.grade is None or row.grade <= PRIMARY_MAX_GRADE]
    if pool == POOL_ATTRIBUTION:
        safe_rows = [row for row in safe_rows if row.kp_id is not None]

    q_tokens = bigrams(query.text or "")
    candidates: List[GroundingCandidate] = []
    for row in safe_rows:
        score = score_text(q_tokens, bigrams(row.content or ""))
        candidates.append(_row_to_candidate(row, pool, score))

    # 稳定排序：分数降序，同分保持 SQL 返回顺序（可复现）
    candidates.sort(key=lambda item: item.score, reverse=True)
    top = candidates[: max(1, query.top_k)]
    _cache_put(key, top)
    return top


# =============================================================================
# 素材包组装
# =============================================================================


def _fetch_kp_description(db: Session, kp_id: Optional[str]) -> str:
    """按 kp_id 取知识点释义（取不到返回空串）。"""
    if not kp_id:
        return ""
    try:
        row = db.execute(
            select(KnowledgePoint.description).where(KnowledgePoint.id == kp_id)
        ).scalar_one_or_none()
    except Exception as exc:
        logger.warning("取知识点释义失败(kp_id=%s): %s", kp_id, exc)
        return ""
    return (row or "").strip()


def _peer_corpus_available(db: Session) -> bool:
    """同伴视角语料表是否已存在（A-2 期落表后为 True）。

    本期 A-1 **不落表**，所以恒为 ``False`` → peer 段恒空、不报错。
    落表后只要表在，peer 段就能从「已放行」行里取内容。
    """
    return table_exists(db, PEER_CORPUS_TABLE)


def build_grounding_materials(
    db: Session,
    query: GroundingQuery,
) -> GroundingMaterials:
    """检索并组装四段素材包。

    素材来源优先级（**关键设计**）：

    1. **analogy（生活类比）**：先看当前题面自身有没有生活场景；
       没有就**检索同 KP / 同场景的兄弟题**，借它们的场景骨架做类比载体。
       —— 这正是「轻量 RAG」的意义：当前题可能是「在括号里填上 > 、 < 或 =」，
       这种题面本身没有生活场景，但同 KP 的兄弟题往往是「把 12 个苹果平均分给
       4 个小朋友」，借它的场景，讲解立刻就接地气了。
    2. **kp_anchor（知识点释义）**：来自 ``knowledge_points.description``，
       数字已剥离（释义里的数字可能是本题答案）。
    3. **peer_misconception / personal_error**：本期恒空（无放行语料、无个体数据）。

    Args:
        db: 调用方传入的数据库会话。
        query: 检索输入。

    Returns:
        四段素材包；检索不到时是**空包**（四段皆空），**不抛错**。
    """
    # 年级硬边界：学生年级已知且 > 6 → 本期只服务小学，直接返回空包
    if query.student_grade is not None and query.student_grade > PRIMARY_MAX_GRADE:
        return GroundingMaterials()

    # 1) 当前题面自身的生活场景
    anchor = extract_scene_anchor(query.text)

    # 2) 借兄弟题的场景（归因池优先：同 KP 的题更贴题）
    if not anchor:
        for pool in (POOL_ATTRIBUTION, POOL_TEACHING):
            for candidate in search_candidates(db, query, pool=pool):
                candidate_anchor = extract_scene_anchor(candidate.content)
                if candidate_anchor:
                    anchor = candidate_anchor
                    break
            if anchor:
                break

    # 3) 知识点释义
    kp_id = query.kp_id
    if not kp_id:
        for candidate in search_candidates(db, query, pool=POOL_ATTRIBUTION):
            if candidate.kp_id:
                kp_id = candidate.kp_id
                break
    kp_description = _fetch_kp_description(db, kp_id)

    # 4) peer 段：语料表未落 → 空段（不报错）
    peer_sentence = ""
    if _peer_corpus_available(db):
        # A-2 期在这里接「已放行（active）语料」的检索结果；
        # 本期不落表，分支恒不进入。
        peer_sentence = ""

    return GroundingMaterials(
        peer_misconception=peer_sentence,
        analogy=build_analogy(anchor),
        kp_anchor=build_kp_anchor(kp_description),
        personal_error="",
    )


def render_grounding_segment(db: Session, query: GroundingQuery) -> str:
    """检索 → 组装 → 渲染成可注入 tutor 提示词的文本。

    开关关闭 → 返回 ``""``（调用方据此完全不追加后缀）。
    检索失败 / 查不到 → 返回 ``""``（**不抛错**，绝不阻断辅导主流程）。

    Args:
        db: 调用方传入的数据库会话。
        query: 检索输入。

    Returns:
        渲染文本；无可用素材时返回 ``""``。
    """
    try:
        if not grounding_enabled():
            return ""
        materials = build_grounding_materials(db, query)
        return render_materials_for_prompt(materials)
    except Exception as exc:
        logger.warning("接地气素材渲染失败: %s", exc)
        return ""


# =============================================================================
# 灰度开关
# =============================================================================


def grounding_enabled(db: Optional[Session] = None) -> bool:
    """读取 ``RAG_GROUNDING_ENABLED`` 开关（默认关）。

    优先用**调用方的 session** 读 ``system_config``（同一事务内可见）；
    没有 session 时退回 ``app.core.system_config.get_config``。

    Returns:
        开关是否打开。任何异常都按「关闭」处理（fail-safe）。
    """
    try:
        if db is not None:
            from app.models.system_config import SystemConfig

            raw = db.execute(
                select(SystemConfig.value).where(SystemConfig.key == GROUNDING_CONFIG_KEY)
            ).scalar_one_or_none()
            if raw is not None:
                return _to_bool(raw)
            return False
        from app.core.system_config import get_config

        return bool(get_config(GROUNDING_CONFIG_KEY))
    except Exception as exc:
        logger.warning("读取 %s 失败: %s", GROUNDING_CONFIG_KEY, exc)
        return False


def _to_bool(raw: Any) -> bool:
    """把配置字符串转 bool（``1/true/yes/on`` 为真，忽略大小写与空白）。

    与 ``app.core.system_config._to_bool`` 同语义 —— 不能用内置 ``bool``：
    ``bool("false")`` 是 ``True``，字符串配置会把「关闭」误读成「开启」。
    """
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


__all__ = [
    "GROUNDING_CONFIG_KEY",
    "PEER_CORPUS_TABLE",
    "CACHE_TTL_SECONDS",
    "POOL_TEACHING",
    "POOL_ATTRIBUTION",
    "GroundingQuery",
    "GroundingCandidate",
    "table_exists",
    "search_candidates",
    "build_grounding_materials",
    "render_grounding_segment",
    "grounding_enabled",
    "clear_grounding_cache",
]

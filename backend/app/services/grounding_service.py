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
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import case, inspect, or_, select
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


def _to_uuid(value: Optional[str]) -> Optional[Any]:
    """把 kp_id 字符串转成 UUID 对象（转不了就返回 ``None``）。

    为什么必须转：``questions.kp_id`` 与 ``knowledge_points.id`` 都是
    ``GUID`` 列（SQLite 落 ``CHAR(36)``、PG 落 ``UUID``），SQLAlchemy 的
    ``GUID.process_bind_param`` 会对传入值做 ``uuid.UUID(str(value))``——
    传一个非 UUID 字符串会直接抛 ``ValueError``，把整条检索带崩。
    转不了（脏数据 / 调用方传了裸 key）就返回 ``None``，由调用方决定
    是「不过滤」还是「查不到」。
    """
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _build_candidates_query(
    *,
    pool: str,
    window: Sequence[int],
    kp_uuid: Optional[Any],
    question_type: Optional[str],
    difficulty: Optional[int],
    like_words: Sequence[str],
):
    """组装结构化粗筛 + LIKE 精排的 SQL（**不执行**，返回可执行语句）。

    结构化粗筛字段：``status`` / ``grade`` / ``kp_id`` / ``type`` / ``difficulty``。
    LIKE 精排：目标串里区分度最高的几个词做「或」匹配（无索引，全表扫，
    实测 median 0.391ms，4166 行规模完全够用）。

    ⚠️ **LIKE 只做排序加权，绝不做硬过滤**（这是 A-1 期的关键修正）：
    早期版本把 LIKE 放进 ``WHERE``，结果「当前题面没场景 → 借兄弟题的场景」
    这条核心链路**完全失效** —— 当前题面是「在括号里填上 > < 或 =」，
    兄弟题是「在文具店买了 3 支铅笔」，两者没有任何 3 字以上的共同片段，
    LIKE 硬过滤把兄弟题（也就是唯一有生活场景的题）全部筛掉了，
    素材包直接空掉。
    现在的口径：**结构化字段负责「谁能进候选」，LIKE 负责「谁排前面」**，
    相关性最终由 Python 层的 bigram 覆盖度 + 相关性下限判定。

    ⚠️ **讲解素材池不按 kp_id 收窄**（口径铁律）：
    讲解池 = 全量 ``active`` 题，**必须含 kp_id 为空的题**（实测 864 道
    scenario 题 —— shopping / cake / distance / time 这类生活场景题大量集中
    在这一批，它们的场景词最丰富，正是「接地气」的来源）。
    归因池才强制 ``kp_id IS NOT NULL``。
    """
    stmt = select(Question).where(
        Question.status == "active",
        Question.grade.in_(list(window)),
    )
    if pool == POOL_ATTRIBUTION:
        # 归因类素材池：必须挂得上知识点
        stmt = stmt.where(Question.kp_id.isnot(None))
        if kp_uuid is not None:
            stmt = stmt.where(Question.kp_id == kp_uuid)
    if question_type:
        stmt = stmt.where(Question.type_ == question_type)
    if difficulty is not None:
        stmt = stmt.where(Question.difficulty == difficulty)
    if like_words:
        # LIKE 命中排前面（**不排除**未命中的行）
        like_hit = or_(*[Question.content.like(f"%{word}%") for word in like_words])
        stmt = stmt.order_by(case((like_hit, 0), else_=1))
    # 次序键固定，保证同样的输入永远得到同样的候选集（可复现）
    return stmt.order_by(Question.id).limit(SQL_CANDIDATE_LIMIT)


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
    """检索候选题（结构化粗筛 + LIKE 加权 + bigram 打分）。

    相关性口径（两条，缺一不可）：

    1. **bigram 覆盖度 > 0**：候选题面必须与检索目标串有共同片段，
       否则「完全不相关的题」会被当成素材送给模型；
    2. **同 KP 命中**：``kp_id`` 明确指定时，同知识点的兄弟题**即使
       文本毫不相干也要保留** —— 「借兄弟题的场景骨架」这条链路
       靠的就是同 KP 信号（「在括号里填上 > < 或 =」与
       「在文具店买了 3 支铅笔」没有任何共同片段）。

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

    kp_uuid = _to_uuid(query.kp_id)
    # 「同 KP」结构化信号：只在归因池 + 明确指定 kp 时成立
    kp_scoped = pool == POOL_ATTRIBUTION and kp_uuid is not None

    try:
        like_words = like_terms(query.text or "", limit=3)
        stmt = _build_candidates_query(
            pool=pool,
            window=window,
            kp_uuid=kp_uuid,
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
        candidate = _row_to_candidate(row, pool, score)
        # 相关性下限：既没有共同片段、又没命中同 KP 的题一律丢弃
        if candidate.score <= 0.0 and not kp_scoped:
            continue
        candidates.append(candidate)

    # 稳定排序：分数降序，同分保持 SQL 返回顺序（可复现）
    candidates.sort(key=lambda item: item.score, reverse=True)
    top = candidates[: max(1, query.top_k)]
    _cache_put(key, top)
    return top


# =============================================================================
# 素材包组装
# =============================================================================


def _fetch_kp_description(db: Session, kp_id: Optional[str]) -> str:
    """按 kp_id 取知识点释义（取不到 / 超纲一律返回空串）。

    ⚠️ **必须做年级校验**（实测发现过的真实泄漏）：
    题库里存在「小学题挂到初中知识点上」的脏数据 —— 实测构造一条
    ``grade=3`` 的题指向 ``grade=9`` 的知识点（释义「二次方程的解法与应用」），
    小学孩子的素材包里就出现了「二次方程」。
    所以这里除了取释义，还要看一眼**知识点自己的年级**：
    超过 :data:`PRIMARY_MAX_GRADE` 一律当「取不到」处理（返回空串），
    由上层继续找别的候选。

    Args:
        db: 调用方传入的数据库会话。
        kp_id: 知识点 id（字符串或 UUID）。

    Returns:
        知识点释义；取不到 / 超纲 → ``""``。
    """
    kp_uuid = _to_uuid(kp_id)
    if kp_uuid is None:
        return ""
    try:
        row = db.execute(
            select(KnowledgePoint.description, KnowledgePoint.grade).where(
                KnowledgePoint.id == kp_uuid
            )
        ).first()
    except Exception as exc:
        logger.warning("取知识点释义失败(kp_id=%s): %s", kp_id, exc)
        return ""
    if row is None:
        return ""
    description, kp_grade = row[0], row[1]
    if kp_grade is not None and kp_grade > PRIMARY_MAX_GRADE:
        logger.warning(
            "知识点 %s 年级 %s 超过小学上限 %s，已丢弃",
            kp_id,
            kp_grade,
            PRIMARY_MAX_GRADE,
        )
        return ""
    return (description or "").strip()


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

    # 3) 知识点释义（**带年级校验**：超纲知识点一律丢弃）
    kp_description = ""
    if query.kp_id:
        kp_description = _fetch_kp_description(db, query.kp_id)
    if not kp_description:
        for candidate in search_candidates(db, query, pool=POOL_ATTRIBUTION):
            if not candidate.kp_id:
                continue
            description = _fetch_kp_description(db, candidate.kp_id)
            if description:
                kp_description = description
                break

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
        # ⚠️ 必须把 db 传进去：开关要在**调用方的事务内**读，
        # 否则同一事务里刚改的开关值读不到（旧实现漏传 db，
        # 走 get_config 的独立连接 + 进程内缓存 → 读到过期值）。
        if not grounding_enabled(db):
            return ""
        materials = build_grounding_materials(db, query)
        return render_materials_for_prompt(materials)
    except Exception as exc:
        logger.warning("接地气素材渲染失败: %s", exc)
        return ""


def _load_question_context(
    db: Session, question_id: Optional[Any]
) -> Tuple[str, Optional[str]]:
    """取会话关联题的「题面 + 知识点 id」（取不到返回空）。"""
    qid = _to_uuid(question_id)
    if qid is None:
        return "", None
    try:
        row = db.execute(
            select(Question.content, Question.kp_id).where(Question.id == qid)
        ).first()
    except Exception as exc:
        logger.warning("取题面失败(question_id=%s): %s", question_id, exc)
        return "", None
    if row is None:
        return "", None
    return (row[0] or ""), (str(row[1]) if row[1] else None)


def _load_student_grade(db: Session, student_id: Optional[Any]) -> Optional[int]:
    """取学生年级（取不到返回 ``None`` = 未知 → 年级窗口取小学全段）。

    年级未知**不是**事故：窗口上限恒为 6，拿不到年级时最多只能拿到
    小学题，绝不会漏进初高中内容。
    """
    sid = _to_uuid(student_id)
    if sid is None:
        return None
    try:
        from app.models.user import Student

        grade = db.execute(
            select(Student.grade).where(Student.id == sid)
        ).scalar_one_or_none()
    except Exception as exc:
        logger.warning("取学生年级失败(student_id=%s): %s", student_id, exc)
        return None
    if grade is None:
        return None
    try:
        return int(grade)
    except (TypeError, ValueError):
        return None


def render_session_grounding_segment(
    db: Session,
    *,
    mode: Optional[str],
    question_id: Optional[Any] = None,
    student_id: Optional[Any] = None,
    fallback_text: str = "",
) -> str:
    """按「会话上下文」检索并渲染接地气素材段（tutor 注入用）。

    调用方（orchestrator）只管传会话 id，**不碰检索细节**。

    三道闸门（任一不满足 → 返回空串 = 不注入）：

    1. **模式闸门**：只有 ``DEMO`` / ``JUDGE`` 注入。这两个模式在
       ``app.ai.intent`` 里整段跳过答案泄漏检查；``GUIDE`` / ``HINT``
       严格查 ``has_answer_leak``，素材段一旦把答案带出来就会触发
       重生成，白白多一次模型往返。
    2. **开关闸门**：``RAG_GROUNDING_ENABLED`` 关闭 → 完全不注入，
       线上输出与改造前**逐字一致**。
    3. **年级闸门**：由 :func:`build_grounding_materials` 负责
       （学生年级 > 6 → 空包；候选题年级恒 ≤ 6）。

    Args:
        db: 调用方传入的数据库会话。
        mode: 本轮响应模式（``GUIDE`` / ``HINT`` / ``DEMO`` / ``JUDGE``）。
        question_id: 会话关联的题目 id（可为空）。
        student_id: 学生 id（可为空 → 年级未知）。
        fallback_text: 没有关联题时，用它当检索目标串。

    Returns:
        渲染文本；任何闸门不满足 / 检索失败 → ``""``。
    """
    try:
        from app.ai.intent import UNGUARDED_MODES

        if mode not in UNGUARDED_MODES:
            return ""
        content, kp_id = _load_question_context(db, question_id)
        grade = _load_student_grade(db, student_id)
        query = GroundingQuery(
            text=content or (fallback_text or ""),
            kp_id=kp_id,
            student_grade=grade,
        )
        return render_grounding_segment(db, query)
    except Exception as exc:
        logger.warning("接地气素材段构建失败: %s", exc)
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
    "render_session_grounding_segment",
    "grounding_enabled",
    "clear_grounding_cache",
]

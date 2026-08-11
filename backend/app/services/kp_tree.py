"""知识树索引服务（KF 知识树 L2 · Q5 读侧 L1 rollup）。

背景：
    知识树从「13 个平铺 L1」升级为「13 L1 + 80 L2」两层树后，**写侧**（backfill）
    会把题目的 ``questions.kp_id`` 从 L1 下沉到 L2；而**读侧**（掌握度雷达、
    推荐、练习抽题、错题本筛选）如果不做聚合，会立刻出两类事故：

    * 统计炸桶：雷达图从 13 桶变成 93 桶，每桶 1~2 个样本，掌握度全是噪声；
    * 抽题落空：前端按 L1 请求练习题，而题目已挂 L2 → ``kp_id == L1`` 查不到任何题。

    本模块提供**两个方向**的换算，是全项目唯一的知识树换算收敛点：

    * ``rollup(kp_id)``      —— 向上：L2 → 其 L1（统计聚合用），等价 SQL 的
      ``COALESCE(parent_id, id)``；
    * ``subtree_ids(kp_id)`` —— 向下：L1 → ``[自身] + 全部 L2``（取题筛选用）。

设计约束（详见 docs/design-kf-tree-l2.md §K5 / §K8）：
    * **树深恒为 2**，因此禁止 ``WITH RECURSIVE``：一次
      ``SELECT id, parent_id FROM knowledge_points``（93 行）全量载入内存即可，
      既比递归 CTE 快，又天然 dialect-agnostic（SQLite / PostgreSQL 行为一致）。
    * 纯标准库 + SQLAlchemy，**零新增第三方包**。
    * 索引带进程内缓存；缓存有效性用「知识点行数」做廉价校验，
      seed 跑批把 13 行写成 93 行后会自动失效重建，无需人工干预。

线程安全：
    缓存读写用 ``threading.Lock`` 保护；构建过程本身是纯函数，
    并发下最多重复构建一次，不会产生脏索引。
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.content import QUESTION_STATUS_ACTIVE, KnowledgePoint, Question

logger = logging.getLogger("app.services.kp_tree")

#: L2 样本数阈值：某个 L2 名下 ``status='active'`` 的题数 ≥ 本阈值时，
#: 推荐/抽题才允许下钻到该 L2；不足则回退到整个 L1 子树。
#:
#: 单点定义，禁止在业务代码里散落魔数（design §3.4）。
L2_MIN_SAMPLES: int = 5


# ==================================================================== 索引对象
@dataclass(frozen=True, eq=False)
class KpTreeIndex:
    """知识树的进程内只读索引（树深恒为 2）。

    Attributes:
        parent_of: ``{kp_id: parent_id | None}``，L1 的值恒为 ``None``。
        children_of: ``{l1_id: [l2_id, ...]}``，按 ``sort`` 升序；L2 不作为键出现。
        l1_ids: 全部 L1 的 id 集合。
        code_of: ``{kp_id: code}``，仅用于日志与运维校验，业务逻辑不依赖。
    """

    parent_of: Dict[uuid.UUID, Optional[uuid.UUID]]
    children_of: Dict[uuid.UUID, List[uuid.UUID]]
    l1_ids: FrozenSet[uuid.UUID]
    code_of: Dict[uuid.UUID, Optional[str]]

    # ------------------------------------------------------------ 向上聚合
    def rollup(self, kp_id: Any) -> Optional[uuid.UUID]:
        """把任意知识点 id 归并到它的 L1（统计口径）。

        语义等价于 SQL ``COALESCE(parent_id, id)``：

        * L2 → 返回其父 L1 的 id；
        * L1 → 返回自身；
        * ``None`` → 返回 ``None``；
        * **索引里不存在的 id** → 原样返回（降级为「不做聚合」，
          保证索引滞后时行为退回升级前，绝不吞数据）。

        Args:
            kp_id: 知识点 id（``uuid.UUID`` 或其字符串形式）。

        Returns:
            L1 的 ``uuid.UUID``，或 ``None``。
        """
        key = _norm_id(kp_id)
        if key is None:
            return None
        if key not in self.parent_of:
            return key
        return self.parent_of[key] or key

    def rollup_key(self, kp_id: Any) -> Optional[str]:
        """``rollup`` 的字符串版本，方便直接做 dict 键。"""
        rolled = self.rollup(kp_id)
        return str(rolled) if rolled is not None else None

    # ------------------------------------------------------------ 向下展开
    def subtree_ids(self, kp_id: Any) -> List[uuid.UUID]:
        """把 L1 展开成「自身 + 全部 L2」（取题筛选口径）。

        * L1 → ``[自身, 子1, 子2, ...]``（子节点按 ``sort`` 升序）；
        * L2 → ``[自身]``；
        * 索引里不存在的 id → ``[自身]``（降级为升级前的等值筛选）；
        * ``None`` → ``[]``。

        为什么 L1 自身也要留在结果里：存量题里仍有约 20~30% 停在 L1
        （规则层没吃到、也没走方舟），去掉自身会让这批题彻底抽不到。

        Args:
            kp_id: 知识点 id。

        Returns:
            id 列表（首元素恒为入参自身），不含重复。
        """
        key = _norm_id(kp_id)
        if key is None:
            return []
        result: List[uuid.UUID] = [key]
        for child in self.children_of.get(key, ()):
            if child != key:
                result.append(child)
        return result

    def expand_many(self, kp_ids: Iterable[Any]) -> List[uuid.UUID]:
        """批量展开多个知识点的子树并去重（保持首次出现顺序）。

        Args:
            kp_ids: 知识点 id 集合。

        Returns:
            去重后的 id 列表。
        """
        seen: Dict[uuid.UUID, None] = {}
        for raw in kp_ids:
            for kid in self.subtree_ids(raw):
                seen.setdefault(kid, None)
        return list(seen.keys())

    # ------------------------------------------------------------ 辅助判定
    def is_l1(self, kp_id: Any) -> bool:
        """该 id 是否为 L1（一级知识点）。"""
        key = _norm_id(kp_id)
        return key is not None and key in self.l1_ids

    def is_l2(self, kp_id: Any) -> bool:
        """该 id 是否为 L2（二级知识点）。"""
        key = _norm_id(kp_id)
        return key is not None and self.parent_of.get(key) is not None

    @property
    def size(self) -> int:
        """索引里的节点总数（用于缓存校验与运维日志）。"""
        return len(self.parent_of)

    def l2_sample_counts(self, db: Session) -> Dict[uuid.UUID, int]:
        """代理到模块级 :func:`l2_sample_counts`，方便链式调用。"""
        return l2_sample_counts(db)


#: 空索引（数据库里一个知识点都没有时返回，避免调用方到处判空）
EMPTY_INDEX: KpTreeIndex = KpTreeIndex(
    parent_of={},
    children_of={},
    l1_ids=frozenset(),
    code_of={},
)


# ==================================================================== 内部工具
def _norm_id(raw: Any) -> Optional[uuid.UUID]:
    """把任意形态的知识点 id 归一成 ``uuid.UUID``（失败返回 None）。"""
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError, AttributeError):
        return None


def _bind_key(db: Session) -> int:
    """缓存分片键：同一进程里可能同时存在多个引擎（生产库 / 测试内存库）。"""
    try:
        return id(db.get_bind())
    except Exception:  # pragma: no cover - Session 未绑定引擎的极端情况
        return 0


def _build_index(db: Session) -> KpTreeIndex:
    """一次查询建索引（不走缓存）。

    Args:
        db: 数据库会话。

    Returns:
        新建的 :class:`KpTreeIndex`。
    """
    rows = db.execute(
        select(KnowledgePoint.id, KnowledgePoint.parent_id, KnowledgePoint.code)
        .order_by(KnowledgePoint.sort, KnowledgePoint.id)
    ).all()

    parent_of: Dict[uuid.UUID, Optional[uuid.UUID]] = {}
    code_of: Dict[uuid.UUID, Optional[str]] = {}
    for row in rows:
        kid = _norm_id(row[0])
        if kid is None:
            continue
        parent_of[kid] = _norm_id(row[1])
        code_of[kid] = row[2]

    # 防御：父节点已被删除（parent_id 指向不存在的行）时，把它当作 L1 处理，
    # 否则 rollup 会返回一个悬空 id，下游 JOIN 恒空。
    for kid, pid in list(parent_of.items()):
        if pid is not None and pid not in parent_of:
            logger.warning("知识点 %s 的 parent_id=%s 不存在，降级为 L1", kid, pid)
            parent_of[kid] = None

    children_of: Dict[uuid.UUID, List[uuid.UUID]] = {}
    for kid, pid in parent_of.items():
        if pid is None:
            children_of.setdefault(kid, [])
    for row in rows:
        kid = _norm_id(row[0])
        pid = parent_of.get(kid) if kid is not None else None
        if kid is None or pid is None:
            continue
        children_of.setdefault(pid, []).append(kid)

    l1_ids = frozenset(kid for kid, pid in parent_of.items() if pid is None)

    return KpTreeIndex(
        parent_of=parent_of,
        children_of=children_of,
        l1_ids=l1_ids,
        code_of=code_of,
    )


# ==================================================================== 缓存入口
#: ``{bind_key: (节点行数, 索引)}``
_CACHE: Dict[int, Tuple[int, KpTreeIndex]] = {}
_CACHE_LOCK = threading.Lock()


def load_kp_tree(db: Session) -> KpTreeIndex:
    """取知识树索引（进程内缓存，按知识点行数做廉价有效性校验）。

    为什么用「行数」而不是纯 ``lru_cache``：
        seed 跑批会把 13 行写成 93 行。若进程常驻（uvicorn worker）且缓存永不失效，
        跑批后的读侧仍会按旧树聚合 → 雷达图错位。行数校验只多一条
        ``SELECT COUNT(*)``（93 行的计数，微秒级），换来「跑批即生效」。

    Args:
        db: 数据库会话。

    Returns:
        :class:`KpTreeIndex`；库里没有知识点时返回 :data:`EMPTY_INDEX`。
    """
    key = _bind_key(db)
    try:
        total = int(db.execute(select(func.count(KnowledgePoint.id))).scalar_one() or 0)
    except Exception as exc:  # pragma: no cover - 表不存在等极端情况
        logger.warning("知识树计数失败，返回空索引: %s", exc)
        return EMPTY_INDEX

    if total <= 0:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
        return EMPTY_INDEX

    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None and cached[0] == total:
        return cached[1]

    index = _build_index(db)
    with _CACHE_LOCK:
        _CACHE[key] = (total, index)
    logger.debug("知识树索引重建：%d 个节点（L1=%d）", index.size, len(index.l1_ids))
    return index


def invalidate() -> None:
    """清空全部知识树缓存。

    调用时机：
        * ``seed_db.py`` 跑批结束后（同进程内立即生效）；
        * 单元测试 fixture 里，避免跨用例共享陈旧索引。
    """
    with _CACHE_LOCK:
        _CACHE.clear()


# ============================================================== L2 样本数统计
def l2_sample_counts(db: Session) -> Dict[uuid.UUID, int]:
    """统计每个知识点名下可用题（``status='active'``）的数量。

    供「L2 样本 ≥ :data:`L2_MIN_SAMPLES` 才允许下钻出 L2 题」的判定使用。

    Args:
        db: 数据库会话。

    Returns:
        ``{kp_id: 题数}``；没有题的知识点不出现在结果里（调用方用 ``.get(kid, 0)``）。
    """
    rows = db.execute(
        select(Question.kp_id, func.count(Question.id))
        .where(Question.kp_id.isnot(None), Question.status == QUESTION_STATUS_ACTIVE)
        .group_by(Question.kp_id)
    ).all()

    counts: Dict[uuid.UUID, int] = {}
    for kp_id, cnt in rows:
        key = _norm_id(kp_id)
        if key is None:
            continue
        counts[key] = int(cnt or 0)
    return counts


def preferred_targets(
    index: KpTreeIndex,
    kp_id: Any,
    counts: Dict[uuid.UUID, int],
    *,
    min_samples: int = L2_MIN_SAMPLES,
) -> List[uuid.UUID]:
    """给定一个 L1，决定实际用于抽题的 id 集合（Q5 下钻策略）。

    策略（design §4.4 时序图 C）：
        * 该 L1 下存在样本数 ≥ ``min_samples`` 的 L2 → 只用这些「够厚」的 L2，
          外加 L1 自身（承接尚未下沉的存量题）；
        * 否则 → 回退到整个 L1 子树。

    Args:
        index: 知识树索引。
        kp_id: L1（传 L2 也可，此时恒返回 ``[自身]``）。
        counts: :func:`l2_sample_counts` 的结果。
        min_samples: 下钻阈值。

    Returns:
        用于 ``Question.kp_id.in_(...)`` 的 id 列表（至少含入参自身）。
    """
    subtree = index.subtree_ids(kp_id)
    if len(subtree) <= 1:
        return subtree

    root = subtree[0]
    thick = [kid for kid in subtree[1:] if counts.get(kid, 0) >= min_samples]
    if not thick:
        return subtree
    return [root] + thick


__all__ = [
    "L2_MIN_SAMPLES",
    "KpTreeIndex",
    "EMPTY_INDEX",
    "load_kp_tree",
    "invalidate",
    "l2_sample_counts",
    "preferred_targets",
]

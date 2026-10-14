"""知识点读侧服务（Sprint 1 · KG-003 详情 / KG-004 前置 / KG-005 关系）。

    * ``get_knowledge_point`` —— 按 id 取知识点详情（KG-003）。
    * ``get_prerequisites``  —— 按 prerequisite 边查前置知识，可递归一层
      且防环（KG-004）。
    * ``get_relations``      —— 按 id 取相关知识，prerequisite / related /
      next 三类分组（KG-005）。

设计约定（与 ``kp_tree.py`` 分工）：
    * ``kp_tree.py`` 管**归类树**（parent_id 自引用：rollup / subtree / 抽题筛选）；
    * 本模块管**详情读 / 关系读**（KG-003~005 均落在此 service 文件）。

主键派生红线（与 seed 脚本 / 测试共用，一个字符都不能改）：
    ``kp_uuid(code) = uuid5(NS_QUESTION_BANK, "kp:" + code)``
    ``NS_QUESTION_BANK = 6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21``
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.content import KnowledgePoint, KnowledgeRelation

__all__ = [
    "get_knowledge_point",
    "get_prerequisites",
    "get_relations",
    "NS_QUESTION_BANK",
    "kp_uuid",
]

#: 主键派生命名空间（KG-001/002 共存冻结值，见红线）
NS_QUESTION_BANK = uuid.UUID("6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21")


def kp_uuid(code: str) -> uuid.UUID:
    """知识点主键派生（与 scripts/seed_kp_v65.py 同公式）。"""
    return uuid.uuid5(NS_QUESTION_BANK, f"kp:{code}")


def _norm_id(raw: str) -> Optional[uuid.UUID]:
    """把请求里的 id 字符串归一成 ``uuid.UUID``；非法返回 None。"""
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError, AttributeError):
        return None


def get_knowledge_point(db: Session, kp_id: str) -> Optional[KnowledgePoint]:
    """KG-003 · 按 id 取知识点详情。

    Args:
        db: 数据库会话。
        kp_id: 知识点 id 字符串（合法 UUID 形态）。

    Returns:
        ``KnowledgePoint`` ORM 对象；id 非法或不存在返回 ``None``
        （由 API 层统一转 404，避免泄露资源存在性）。
    """
    key = _norm_id(kp_id)
    if key is None:
        return None
    return db.execute(
        select(KnowledgePoint).where(KnowledgePoint.id == key)
    ).scalar_one_or_none()


def _direct_prerequisite_ids(db: Session, kp_id: uuid.UUID) -> List[uuid.UUID]:
    """查某个知识点的**直接**前置（``prerequisite`` 边，source -> target）。"""
    rows = db.execute(
        select(KnowledgeRelation.source_id).where(
            KnowledgeRelation.target_id == kp_id,
            KnowledgeRelation.relation_type == "prerequisite",
        )
    ).all()
    return [row[0] for row in rows]


def get_prerequisites(
    db: Session, kp_id: str, *, recursive: bool = True
) -> List[KnowledgePoint]:
    """KG-004 · 按 id 查前置知识（``prerequisite`` 边）。

    语义：
        * 直接前置 = 所有 ``relation_type == 'prerequisite'`` 指向 ``kp_id``
          边的 ``source`` 知识点；
        * ``recursive=True``（默认）时**递归一层**：再取直接前置各自的
          直接前置；用 ``seen`` 集合去重，**环状边（A→B→A）不死循环**；
        * 返回顺序：直接前置在前、间接前置在后（去重后）。

    Args:
        db: 数据库会话。
        kp_id: 知识点 id 字符串（合法 UUID 形态）。
        recursive: 是否递归一层（默认 True）。

    Returns:
        前置知识点 ORM 列表（按直接→间接排序去重）；
        id 非法、知识点不存在或无前置时返回**空列表**（API 层恒 200，
        不给 404，符合规划「空关系返回空数组」同理的读取语义）。
    """
    key = _norm_id(kp_id)
    if key is None:
        return []
    kp_exists = db.execute(
        select(KnowledgePoint.id).where(KnowledgePoint.id == key)
    ).scalar_one_or_none()
    if kp_exists is None:
        return []

    # 防环基底：查询目标自身视为「已见过」——任何路径（含递归一层）
    # 都不能把目标节点自己加回来（A→B→A 环在此被吸收）。
    seen: set = {key}
    result: List[KnowledgePoint] = []

    def _add(node_id: uuid.UUID) -> None:
        if node_id in seen:
            return
        node = db.get(KnowledgePoint, node_id)
        if node is None:
            return
        seen.add(node_id)
        result.append(node)

    # 第一层：直接前置
    direct_ids = _direct_prerequisite_ids(db, key)
    for pid in direct_ids:
        _add(pid)

    # 第二层：递归一层（间接前置）。环状边由 seen 吸收，绝不重复入列。
    if recursive:
        for pid in direct_ids:
            for gid in _direct_prerequisite_ids(db, pid):
                _add(gid)

    return result


def _relation_target_ids(
    db: Session, kp_id: uuid.UUID, relation_type: str
) -> List[uuid.UUID]:
    """查 ``relation_type`` 边中与 ``kp_id`` 相邻的对端 id。

    * prerequisite / next 是**有向**边：prerequisite 只读「指向我」的
      source（谁是我前置），next 只读「从我发出」的 target（谁是后续）；
    * related 是**无向**语义：source 或 target 任一方向命中都算相邻。
    """
    rel = KnowledgeRelation
    if relation_type == "prerequisite":
        rows = db.execute(
            select(rel.source_id).where(
                rel.target_id == kp_id, rel.relation_type == relation_type
            )
        ).all()
        return [row[0] for row in rows]
    if relation_type == "next":
        rows = db.execute(
            select(rel.target_id).where(
                rel.source_id == kp_id, rel.relation_type == relation_type
            )
        ).all()
        return [row[0] for row in rows]
    if relation_type == "related":
        rows = db.execute(
            select(rel.source_id, rel.target_id).where(
                rel.relation_type == relation_type,
                or_(rel.source_id == kp_id, rel.target_id == kp_id),
            )
        ).all()
        return [
            (t if s == kp_id else s)
            for s, t in rows
            if (t if s == kp_id else s) != kp_id
        ]
    return []


def get_relations(db: Session, kp_id: str) -> Dict[str, List[KnowledgePoint]]:
    """KG-005 · 按 id 查相关知识，返回 prerequisite/related/next 三类分组。

    语义：
        * ``prerequisite`` —— ``prerequisite`` 边的 source（谁是我前置），
          只取直接关系，不做递归（递归视角由 KG-004 提供）；
        * ``related``      —— ``related`` 边的对端（无向，双向都算）；
        * ``next``         —— ``next`` 边的 target（学完之后的下一个）。

    Args:
        db: 数据库会话。
        kp_id: 知识点 id 字符串（合法 UUID 形态）。

    Returns:
        ``{"prerequisite": [...], "related": [...], "next": [...]}``，
        每组为 ``KnowledgePoint`` ORM 列表（去重）；
        id 非法、知识点不存在或无关系边时对应组返回**空列表**
        （API 层恒 200，不给 404，符合规划「空关系返回空数组而非报错」）。
    """
    groups: Dict[str, List[KnowledgePoint]] = {
        "prerequisite": [],
        "related": [],
        "next": [],
    }
    key = _norm_id(kp_id)
    if key is None:
        return groups
    kp_exists = db.execute(
        select(KnowledgePoint.id).where(KnowledgePoint.id == key)
    ).scalar_one_or_none()
    if kp_exists is None:
        return groups

    for rel_type in ("prerequisite", "next", "related"):
        ids = _relation_target_ids(db, key, rel_type)
        seen: set = set()
        nodes: List[KnowledgePoint] = []
        for nid in ids:
            if nid in seen:
                continue
            node = db.get(KnowledgePoint, nid)
            if node is None:
                continue
            seen.add(nid)
            nodes.append(node)
        groups[rel_type] = nodes
    return groups

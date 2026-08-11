"""知识点自动归因批处理脚本（离线，不入请求链路）。

三级方案（按序尝试）：
    1. 规则词典（主力）：加载 ``seed/kp_keywords.json``（v2 = 13 L1 + 80 L2 共 93 条），
       遍历统计命中数，命中唯一桶返回 kp_code；多桶命中按 priority 消歧；
       无命中返回 None。L2 的 priority 恒 ≥100 而 L1 恒 ≤30，因此**同时命中父子桶时
       必定落到更细的 L2**。
    2. 方舟兜底（两段式）：段① 在 13 个 L1 里选 1；段② 在该 L1 的 children（≤8）里选 1。
       段②无候选或未命中 → 降级返回段①的 L1 code（保 recall，不返回 None）。
    3. 失败留空：两级都未命中 → kp_id 留空（默认模式）或保持原值（``--relabel-l1``）。

归因安全：所有自动归因结果一律 ``kp_verified=0``（不污染可归因池）。
离线批处理：脚本由运维手动执行，不在 HTTP 请求链路里跑方舟。

用法::

    cd backend
    python app/scripts/backfill_kp.py                           # 默认：只补 kp_id 为空的题（规则+方舟）
    python app/scripts/backfill_kp.py --dry-run                 # 只输出归因分布，不落库
    python app/scripts/backfill_kp.py --limit 100               # 只处理前 100 道
    python app/scripts/backfill_kp.py --dry-run --relabel-l1    # 存量 L1 题重打标预演（仅规则层）
    python app/scripts/backfill_kp.py --relabel-l1              # 存量 L1 题重打标落库（仅规则层）
    python app/scripts/backfill_kp.py --relabel-l1 --force      # 强制重打标含 verified=1 的 L1 题

``--relabel-l1`` 为什么强制 ``--no-ark``：
    规则层是纯字符串匹配（确定性、可复现），方舟层是 LLM（非确定性）。
    若重打标带方舟，每跑一次存量题的归因都会抖动，掌握度曲线会莫名跳变、
    事故无法复现。**幂等性优先于覆盖率**（design §4.2）。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import or_, select, text, update
from sqlalchemy.orm import Session

# 允许以脚本方式直接运行：backend/app/scripts/backfill_kp.py → 上溯 3 级到 backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.ai.ark_client import ark_respond  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.content import KnowledgePoint, Question  # noqa: E402

logger = logging.getLogger("backfill_kp")

#: 规则词典文件路径
RULES_PATH: Path = BACKEND_DIR / "seed" / "kp_keywords.json"

#: 方舟兜底分类超时（秒）
ARK_TIMEOUT: float = 30.0

# ------------------------------------------------------------------ priority 分层
#: L2 规则的 priority 下界（design §K3：L1 ∈ [0,30]，L2 ∈ [100,199]）
L2_PRIORITY_MIN: int = 100
#: L2 规则的 priority 上界
L2_PRIORITY_MAX: int = 199
#: L2 「兜底子桶」的 priority 上界：priority ≤ 该值的 L2 属于父桶内的兜底分类
#: （如 KP_WORD_GENERAL / KP_MIX_OTHER），验收时要单独扣掉（决策 A2 双口径）
L2_FALLBACK_PRIORITY_MAX: int = 120

# ------------------------------------------------------------------ 验收阈值（决策 A2）
#: 口径一：L2 命中数 / 待处理题总数
ACCEPT_L2_RATIO: float = 0.70
#: 口径二：非兜底 L2 命中数 / 待处理题总数
ACCEPT_L2_STRICT_RATIO: float = 0.50


# ---------------------------------------------------------------- 规则加载
def _load_rules() -> List[Dict[str, Any]]:
    """从 ``seed/kp_keywords.json`` 加载规则词典。

    Returns:
        规则列表，每条形如 ``{"kp_code": ..., "kp_name": ..., "keywords": [...], "priority": ...}``。

    Raises:
        FileNotFoundError: 词典文件不存在。
        ValueError: 词典文件格式非法。
    """
    if not RULES_PATH.exists():
        raise FileNotFoundError(f"规则词典文件不存在: {RULES_PATH}")

    with open(RULES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"规则词典格式非法：rules 字段缺失或为空: {RULES_PATH}")

    return rules


def _fallback_codes(rules: Sequence[Dict[str, Any]]) -> Set[str]:
    """从词典里挑出「兜底型 L2」的编码集合（priority ∈ [100, 120]）。

    Args:
        rules: 规则列表。

    Returns:
        兜底 L2 的 ``kp_code`` 集合。
    """
    return {
        str(rule.get("kp_code"))
        for rule in rules
        if L2_PRIORITY_MIN <= int(rule.get("priority", 0)) <= L2_FALLBACK_PRIORITY_MAX
    }


def _load_kp_list(db: Session) -> List[Dict[str, Any]]:
    """从数据库加载全部知识点（按 sort 排序），供方舟 prompt 和 kp_code→id 映射使用。

    Returns:
        知识点列表，每条含 ``id`` / ``code`` / ``name`` / ``description`` /
        ``parent_id`` / ``parent_code``（L1 的 ``parent_code`` 为 ``None``）。
    """
    rows = db.execute(
        select(
            KnowledgePoint.id,
            KnowledgePoint.code,
            KnowledgePoint.name,
            KnowledgePoint.description,
            KnowledgePoint.parent_id,
        )
        .where(KnowledgePoint.code.isnot(None))
        .order_by(KnowledgePoint.sort, KnowledgePoint.code)
    ).all()

    code_by_id: Dict[Any, str] = {row.id: row.code for row in rows}

    return [
        {
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "description": row.description or "",
            "parent_id": row.parent_id,
            "parent_code": code_by_id.get(row.parent_id) if row.parent_id else None,
        }
        for row in rows
    ]


def _l1_ids(kp_list: Sequence[Dict[str, Any]]) -> List[Any]:
    """从知识点列表里取出全部 L1（``parent_id`` 为空）的主键。"""
    return [kp["id"] for kp in kp_list if not kp.get("parent_id")]


# ---------------------------------------------------------------- Level 1: 规则词典
def classify_by_rules(content: str, rules: list[dict]) -> Optional[str]:
    """规则词典匹配（纯 Python 字符串匹配，不依赖任何 NLP 库）。

    遍历 93 个桶的 keywords，统计每桶命中数：
      * 命中唯一桶 → 返回该桶的 ``kp_code``。
      * 多桶命中 → 按 ``priority`` 降序消歧；priority 相同则按命中数降序；
        两者均相同（不可消歧）→ 返回 None（低置信度，交方舟兜底）。
      * 无命中 → 返回 None。

    因为 L2 的 priority（100~199）恒高于 L1（0~30），一道题同时命中
    「父桶通用词 + 子桶专有词」时会稳定落到子桶，无需额外分支。

    Args:
        content: 题干文本。
        rules: 规则列表（来自 ``_load_rules()``）。

    Returns:
        命中的 ``kp_code``（如 ``"KP_CALC_ADD"``），或 None。
    """
    if not content or not rules:
        return None

    # 统计每桶命中数
    scored: List[Dict[str, Any]] = []
    for rule in rules:
        keywords = rule.get("keywords", [])
        if not keywords:
            continue
        hit_count = sum(1 for kw in keywords if kw and kw in content)
        if hit_count > 0:
            scored.append(
                {
                    "kp_code": rule["kp_code"],
                    "priority": rule.get("priority", 0),
                    "hits": hit_count,
                }
            )

    if not scored:
        return None

    if len(scored) == 1:
        return scored[0]["kp_code"]

    # 多桶命中：按 (priority desc, hits desc) 排序消歧
    scored.sort(key=lambda x: (x["priority"], x["hits"]), reverse=True)

    top = scored[0]
    second = scored[1]

    # priority 严格更高 → 直接返回
    if top["priority"] > second["priority"]:
        return top["kp_code"]

    # priority 相同但命中数严格更多 → 返回
    if top["priority"] == second["priority"] and top["hits"] > second["hits"]:
        return top["kp_code"]

    # 不可消歧（priority 和 hits 都相同）→ 返回 None，交方舟兜底
    return None


# ---------------------------------------------------------------- Level 2: 方舟兜底
def _pick_code(reply: str, valid_codes: Iterable[str]) -> Optional[str]:
    """从模型回复里解析知识点编码：精确匹配优先，模糊匹配按**最长优先**。

    为什么必须最长优先：
        ``valid_codes`` 原本是 ``set``，遍历顺序由哈希决定。当封闭集合里同时存在
        ``KP_CALC`` 与 ``KP_CALC_ADD`` 时，模型回 ``"KP_CALC_ADD"`` 也可能被
        ``KP_CALC`` 抢先命中，同一份输入在不同进程里结果不同 —— 归因不可复现。
        按长度降序遍历后，最具体的编码必然先被匹配到。

    Args:
        reply: 模型原始回复。
        valid_codes: 合法编码集合。

    Returns:
        命中的编码，或 None。
    """
    if not reply:
        return None
    codes = [c for c in valid_codes if c]
    if not codes:
        return None

    cleaned = reply.strip()
    if cleaned in codes:
        return cleaned

    for code in sorted(codes, key=len, reverse=True):
        if code in cleaned:
            return code
    return None


def _normalize_kp_tree(
    kp_tree: Any,
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """把入参归一成 ``(L1 节点列表, {L1 code: [L2 节点]})``。

    兼容两种入参形态：
        * ``list[dict]``：``_load_kp_list()`` 的扁平输出，按 ``parent_code`` 自动分层；
          若列表里没有任何 ``parent_code``（升级前的 13 桶），则全部视为 L1、无子节点，
          两段式自动退化为单段式（与旧行为完全一致）。
        * ``dict``：``{"l1": [...], "children": {code: [...]}}`` 的显式树。

    Args:
        kp_tree: 扁平列表或显式树。

    Returns:
        ``(l1_nodes, children_by_code)``。
    """
    if isinstance(kp_tree, dict):
        l1_nodes = list(kp_tree.get("l1") or [])
        raw_children = kp_tree.get("children") or {}
        children = {str(k): list(v or []) for k, v in raw_children.items()}
        return l1_nodes, children

    nodes = list(kp_tree or [])
    l1_nodes = [kp for kp in nodes if not kp.get("parent_code")]
    children: Dict[str, List[Dict[str, Any]]] = {}
    for kp in nodes:
        parent_code = kp.get("parent_code")
        if parent_code:
            children.setdefault(str(parent_code), []).append(kp)
    return l1_nodes, children


def _ark_single_choice(content: str, options: Sequence[Dict[str, Any]], hint: str) -> Optional[str]:
    """向方舟发一次封闭单选，返回命中的 ``kp_code``（失败/未命中返回 None）。

    Args:
        content: 题干文本。
        options: 候选知识点（含 code/name/description）。
        hint: 追加到 system 提示里的一句范围说明。

    Returns:
        ``kp_code`` 或 None。

    Raises:
        不抛异常：方舟任何失败都被吞掉并返回 None（绝不炸批处理）。
    """
    if not options:
        return None

    valid_codes = {opt["code"] for opt in options if opt.get("code")}
    if not valid_codes:
        return None

    options_lines: List[str] = []
    for i, kp in enumerate(options, 1):
        desc = kp.get("description") or ""
        options_lines.append(f"{i}. {kp['code']} - {kp['name']}: {desc}")
    options_text = "\n".join(options_lines)

    system_prompt = (
        "你是一个小学数学知识点分类助手。给定一道数学题的题干，"
        "请从以下知识点列表中选出最合适的一个。"
        "只回答知识点的编码（如 KP_CALC），不要输出任何其他内容。"
        f"{hint}"
    )

    user_msg = (
        f"可选知识点：\n{options_text}\n\n"
        f"题干：{content}\n\n"
        f"请只回答一个知识点编码（如 KP_CALC）："
    )

    try:
        reply = ark_respond(
            messages=[{"role": "user", "content": user_msg}],
            system=system_prompt,
            timeout=ARK_TIMEOUT,
        )
    except Exception as exc:  # 含 ArkError；方舟任何失败都不许炸批处理
        logger.warning("方舟兜底分类失败: %s", exc)
        return None

    if not reply:
        return None

    picked = _pick_code(reply, valid_codes)
    if picked is None:
        logger.warning("方舟返回的编码不在封闭集合内: %s", str(reply)[:100])
    return picked


def classify_by_ark(content: str, kp_tree: Any) -> Optional[str]:
    """方舟兜底分类（两段式）。

    * 段①：在 13 个 L1 里做封闭单选，确定大类；
    * 段②：在该 L1 的 children（≤8 个）里再做一次封闭单选，下钻到 L2；
      **段②没有候选或没命中时，降级返回段①的 L1 code**（保 recall，不返回 None）。

    方舟调用失败（网络异常 / 返回非合法编码）时返回 None，绝不抛异常。

    Args:
        content: 题干文本。
        kp_tree: ``_load_kp_list()`` 的扁平列表，或 ``{"l1": [...], "children": {...}}``。
            升级前的 13 桶扁平列表会自动退化为单段式（旧行为不变）。

    Returns:
        ``kp_code``（优先 L2，降级 L1）或 None。
    """
    if not content:
        return None

    l1_nodes, children = _normalize_kp_tree(kp_tree)
    if not l1_nodes:
        return None

    # 段① —— 13 选 1，定 L1
    l1_code = _ark_single_choice(content, l1_nodes, hint="")
    if l1_code is None:
        return None

    # 段② —— 该 L1 的 children 里选 L2；无候选则直接用 L1
    l2_options = children.get(l1_code) or []
    if not l2_options:
        return l1_code

    l2_code = _ark_single_choice(
        content,
        l2_options,
        hint=f"这道题已经确定属于「{l1_code}」大类，现在请在它的子类里选最贴切的一个。",
    )
    return l2_code or l1_code


# ---------------------------------------------------------------- 待处理集合
def _select_targets(
    db: Session,
    *,
    relabel_l1: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    l1_ids: Optional[Sequence[Any]] = None,
) -> List[Any]:
    """挑出本次要处理的题目行。

    * 默认模式：``Question.kp_id IS NULL``；
    * ``--relabel-l1``：``kp_id IS NULL`` **或**
      （``kp_id`` 落在 13 个 L1 上 **且** ``kp_verified IS FALSE``）。
    * ``--relabel-l1 --force``：``kp_id IS NULL`` **或**
      （``kp_id`` 落在 13 个 L1 上）——不检查 ``kp_verified``，
      把人工复核过（``kp_verified=1``）的 L1 题也纳入重打标。
      **仅当 ``--relabel-l1`` 时生效**；调用方须保证 ``force=True``
      时 ``relabel_l1`` 也为 ``True``。

    ⚠️ 红线：``--force`` 会打破 ``kp_verified=1`` 的人工锁定，
    重打标后这些题的 ``kp_verified`` 会被设为 ``0``（规则层标的，需要复核）。
    不带 ``--relabel-l1`` 的 ``--force`` 由 CLI 层拦截报错。

    Args:
        db: 数据库会话。
        relabel_l1: 是否把停在 L1 的未复核题也纳入重打标。
        force: 是否打破 ``kp_verified`` 锁定（仅 ``relabel_l1=True`` 时有意义）。
        limit: 只取前 N 行（None 表示全部）。
        l1_ids: 13 个 L1 的主键；``relabel_l1=True`` 时必填。

    Returns:
        行列表，每行含 ``id`` / ``content`` / ``kp_id``。
    """
    query = select(Question.id, Question.content, Question.kp_id)

    if relabel_l1 and l1_ids:
        if force:
            # --force：不检查 kp_verified，把 verified=1 的 L1 题也纳入
            query = query.where(
                or_(
                    Question.kp_id.is_(None),
                    Question.kp_id.in_(list(l1_ids)),
                )
            )
        else:
            # 默认：只纳入 kp_verified=0 的 L1 题（保护人工复核结论）
            query = query.where(
                or_(
                    Question.kp_id.is_(None),
                    Question.kp_id.in_(list(l1_ids)) & Question.kp_verified.is_(False),
                )
            )
    else:
        query = query.where(Question.kp_id.is_(None))

    query = query.order_by(Question.id)
    if limit is not None and limit > 0:
        query = query.limit(limit)

    return db.execute(query).all()


# ---------------------------------------------------------------- mistakes 同步
#: 错题本 kp_id 同步 SQL（design §4.3，dialect-agnostic：无 PG/SQLite 专属语法）。
#:
#: 除了「错题还没有 kp_id」，重打标后还要处理「题目已升到 L2 而错题仍停在 L1」的漂移。
#: 权威源头是 ``questions.kp_verified``——题目侧人工复核过的，错题侧不许覆盖。
#: 刻意**不给 mistakes 加 kp_verified 列**（加列 = 违反零迁移红线）。
MISTAKE_SYNC_SQL: str = (
    "UPDATE mistakes SET kp_id = ("
    "    SELECT q.kp_id FROM questions q "
    "    WHERE q.id = mistakes.question_id"
    ") "
    "WHERE mistakes.kp_id IS NULL "
    "   OR EXISTS ("
    "        SELECT 1 FROM questions q "
    "        WHERE q.id = mistakes.question_id "
    "          AND q.kp_verified = 0 "
    "          AND q.kp_id IS NOT NULL "
    "          AND mistakes.kp_id <> q.kp_id"
    "   )"
)


# ---------------------------------------------------------------- Level 3: 批处理回填
def backfill(
    db: Session,
    *,
    dry_run: bool = False,
    limit: Optional[int] = None,
    relabel_l1: bool = False,
    force: bool = False,
    no_ark: bool = False,
) -> dict:
    """批处理回填：对待处理题目做自动归因。

    三级方案按序尝试：规则词典 → 方舟兜底（``no_ark=True`` 时跳过）→ 留空/保持原值。
    归因结果一律 ``kp_verified=0``（不污染可归因池）。
    脚本末尾同步错题本 kp_id（见 :data:`MISTAKE_SYNC_SQL`）。

    Args:
        db: 数据库会话。
        dry_run: True 时只输出归因分布不落库。
        limit: 只处理前 N 道题（None 表示全部）。
        relabel_l1: True 时把「停在 L1 且未人工复核」的存量题也纳入重打标。
        force: True 时打破 ``kp_verified`` 锁定，把 verified=1 的 L1 题也纳入重打标。
            **仅当 ``relabel_l1=True`` 时有意义**；重打标后 ``kp_verified`` 设为 ``0``。
        no_ark: True 时只跑规则层，完全不调用方舟（确定性、可复现）。

    Returns:
        归因分布统计::

            {
                "total_empty": int,        # 待处理题目总数（沿用旧键名，含重打标目标）
                "total_targets": int,      # 同上，语义更准确的别名
                "rule_matched": int,       # 规则词典命中数
                "ark_matched": int,        # 方舟兜底命中数
                "unmatched": int,          # 两级都未命中数
                "kept_original": int,      # 未命中但保留了原有 L1（仅 relabel 模式）
                "changed": int,            # 归因结果与原值不同、实际会写库的行数
                "l1_matched": int,         # 命中结果落在 L1 的数量
                "l2_matched": int,         # 命中结果落在 L2 的数量
                "l2_fallback_matched": int,# 其中落在「兜底型 L2」的数量
                "distribution": dict,      # {kp_code: count} 归因分布
                "relabel_l1": bool,
                "no_ark": bool,
            }
    """
    rules = _load_rules()
    kp_list = _load_kp_list(db)

    # kp_code → kp_id 映射 / L2 判定 / 兜底桶判定
    kp_map: Dict[str, Any] = {kp["code"]: kp["id"] for kp in kp_list}
    l2_codes: Set[str] = {kp["code"] for kp in kp_list if kp.get("parent_id")}
    fallback_codes: Set[str] = _fallback_codes(rules)

    rows = _select_targets(
        db,
        relabel_l1=relabel_l1,
        force=force,
        limit=limit,
        l1_ids=_l1_ids(kp_list),
    )

    stats: Dict[str, Any] = {
        "total_empty": len(rows),
        "total_targets": len(rows),
        "rule_matched": 0,
        "ark_matched": 0,
        "unmatched": 0,
        "kept_original": 0,
        "changed": 0,
        "l1_matched": 0,
        "l2_matched": 0,
        "l2_fallback_matched": 0,
        "distribution": {},
        "relabel_l1": relabel_l1,
        "force": force,
        "no_ark": no_ark,
    }

    for row in rows:
        question_id = row.id
        content = row.content or ""
        old_kp_id = row.kp_id

        # Level 1: 规则词典
        kp_code = classify_by_rules(content, rules)
        source = "rule"

        # Level 2: 方舟兜底（--no-ark 时跳过）
        if kp_code is None and not no_ark:
            kp_code = classify_by_ark(content, kp_list)
            source = "ark"

        # Level 3: 失败 → 留空（默认）/ 保持原值（重打标）
        if kp_code is None or kp_code not in kp_map:
            stats["unmatched"] += 1
            if old_kp_id is not None:
                stats["kept_original"] += 1
            continue

        kp_id = kp_map[kp_code]
        stats["distribution"][kp_code] = stats["distribution"].get(kp_code, 0) + 1

        if kp_code in l2_codes:
            stats["l2_matched"] += 1
            if kp_code in fallback_codes:
                stats["l2_fallback_matched"] += 1
        else:
            stats["l1_matched"] += 1

        if source == "rule":
            stats["rule_matched"] += 1
        else:
            stats["ark_matched"] += 1

        if old_kp_id == kp_id:
            continue  # 结果与原值一致：不写库、不计入 changed（幂等重跑友好）
        stats["changed"] += 1

        if not dry_run:
            # 归因安全：kp_verified=0（不污染可归因池）
            db.execute(
                update(Question)
                .where(Question.id == question_id)
                .values(kp_id=kp_id, kp_verified=False)
            )

    # 末尾同步错题 kp_id（PRD Q3 + design §4.3）
    if not dry_run:
        db.execute(text(MISTAKE_SYNC_SQL))
        db.commit()

    return stats


# ---------------------------------------------------------------- CLI
def _ratio(part: int, whole: int) -> float:
    """安全求百分比（分母为 0 时返回 0.0）。"""
    return (part / whole * 100.0) if whole else 0.0


def _print_report(stats: dict, dry_run: bool) -> None:
    """打印归因分布报告（含决策 A2 的双口径验收数字）。"""
    mode = "[DRY-RUN] " if dry_run else ""
    tag = "重打标(--relabel-l1)" if stats.get("relabel_l1") else "补空(默认)"
    if stats.get("relabel_l1") and stats.get("force"):
        tag = "重打标(--relabel-l1 --force)"
    engine = "仅规则层" if stats.get("no_ark") else "规则层 + 方舟兜底"

    total = int(stats.get("total_targets", stats.get("total_empty", 0)))
    l2 = int(stats.get("l2_matched", 0))
    l2_fb = int(stats.get("l2_fallback_matched", 0))
    l2_strict = l2 - l2_fb

    print(f"\n{'=' * 66}")
    print(f"  {mode}知识点自动归因报告  ·  模式={tag}  ·  引擎={engine}")
    print(f"{'=' * 66}")
    print(f"  待处理题目总数: {total}")
    print(f"  规则词典命中:   {stats['rule_matched']}")
    print(f"  方舟兜底命中:   {stats['ark_matched']}")
    print(f"  未命中:         {stats['unmatched']}"
          f"（其中保留原 L1: {stats.get('kept_original', 0)}）")
    print(f"  实际会改动:     {stats.get('changed', 0)}")

    total_matched = stats["rule_matched"] + stats["ark_matched"]
    if total_matched > 0:
        print(f"  规则占比:       {_ratio(stats['rule_matched'], total_matched):.1f}%")
        print(f"  方舟占比:       {_ratio(stats['ark_matched'], total_matched):.1f}%")

    print(f"\n  —— 双口径验收（决策 A2，分母 = 待处理题目总数）——")
    print(f"  L1 命中:        {stats.get('l1_matched', 0)}")
    print(f"  L2 命中:        {l2}（兜底型 L2: {l2_fb}）")
    ok1 = _ratio(l2, total) >= ACCEPT_L2_RATIO * 100
    ok2 = _ratio(l2_strict, total) >= ACCEPT_L2_STRICT_RATIO * 100
    print(f"  口径①  L2 总占比:      {_ratio(l2, total):5.1f}%  "
          f"(目标 ≥{ACCEPT_L2_RATIO * 100:.0f}%)  {'PASS' if ok1 else 'FAIL'}")
    print(f"  口径②  非兜底 L2 占比: {_ratio(l2_strict, total):5.1f}%  "
          f"(目标 ≥{ACCEPT_L2_STRICT_RATIO * 100:.0f}%)  {'PASS' if ok2 else 'FAIL'}")

    distribution = stats.get("distribution", {})
    if distribution:
        print(f"\n  归因分布（按数量降序）:")
        for kp_code, count in sorted(distribution.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {kp_code:24s}  {count:5d}   {_ratio(count, total):5.1f}%")

    if dry_run:
        print(f"\n  (dry-run 模式，未落库)")
    else:
        print(f"\n  已落库，归因结果一律 kp_verified=0")
        print(f"  已同步错题本 kp_id（未复核题目侧的漂移一并纠正）")
    print(f"{'=' * 66}\n")


def main(argv: Optional[Sequence[str]] = None) -> None:
    """命令行入口。

    Args:
        argv: 命令行参数（None 表示取 ``sys.argv[1:]``），便于单测注入。

    Raises:
        SystemExit: 参数组合非法（如 ``--relabel-l1 --ark``）时退出码 2。
    """
    parser = argparse.ArgumentParser(
        description="知识点自动归因批处理脚本（离线，不入请求链路）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只输出归因分布，不落库")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 道题")
    parser.add_argument(
        "--relabel-l1",
        action="store_true",
        help="把「停在 L1 且未人工复核」的存量题一并重打标（隐含 --no-ark，保证幂等）",
    )
    parser.add_argument("--no-ark", action="store_true", help="只跑规则层，不调用方舟")
    parser.add_argument(
        "--force",
        action="store_true",
        help="打破 kp_verified 锁定，把 verified=1 的 L1 题也纳入重打标"
        "（仅与 --relabel-l1 搭配使用，单独使用会报错）",
    )
    parser.add_argument(
        "--ark",
        action="store_true",
        help="显式要求启用方舟（与 --relabel-l1 互斥，仅用于报错提示）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.relabel_l1 and args.ark:
        parser.error(
            "--relabel-l1 与 --ark 互斥：重打标必须只跑规则层，"
            "否则每次跑批结果都会抖动、事故无法复现。"
        )

    if args.force and not args.relabel_l1:
        parser.error(
            "--force 必须与 --relabel-l1 搭配使用：单独 --force 无意义"
            "（默认模式只补 kp_id 为空的题，不存在 verified 锁定问题）。"
        )

    no_ark = bool(args.no_ark or args.relabel_l1)
    if args.relabel_l1 and not args.no_ark:
        logger.info("--relabel-l1 已自动启用 --no-ark（仅规则层，保证幂等）")

    with SessionLocal() as db:
        stats = backfill(
            db,
            dry_run=args.dry_run,
            limit=args.limit,
            relabel_l1=args.relabel_l1,
            force=args.force,
            no_ark=no_ark,
        )

    _print_report(stats, args.dry_run)


if __name__ == "__main__":
    main()

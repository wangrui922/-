# -*- coding: utf-8 -*-
"""同伴视角语料库（peer_view_corpus）的入库 / 人工放行门控 / 检索服务。

设计来源：``07-项目文档/规格-同伴视角语料库-20261029.md``（PRD1 P0-5b）。

职责边界（对齐 grounding_service 的「唯一碰 ORM 处」约定）
---------------------------------------------------------
* 本模块是语料库的**唯一数据访问层**，所有 SQL 都在此处，方便被审计；
* **纯函数层**（``grounding_materials`` / ``grounding_retrieval``）不 import 本模块、
  不查库；本模块只被 ``grounding_service`` 调用。
* 表 ``peer_view_corpus`` **无 ORM 模型**（参照 025 student_mastery_synthesis 范式），
  全表用 raw SQL 维护，靠 ``schema_reconcile.py`` 与生产库对账。

流程与铁律
----------
生成 → 规则过滤(禁止项) → archived → 人工抽检放行 → released。
只有 ``status='released'`` 的语料可被讲解层检索引用；**未放行语料进入线上讲解
0 例外**（``retrieve_released`` 只 SELECT ``status='released'``，天然保证）。

禁止项（硬红线，机器可扫，见 ``forbidden_scan``）：
  * 不得出现真实姓名、班级、小区校名（运营侧软审核，机器只扫明显 token）；
  * 不得把错误指向「你」（与 tutor 提示词铁律「不用『你错了』『不对』」冲突）；
  * 不得出现 LaTeX 源码（分数写 5/13、根号写 √3、乘号写 ×、除号写 ÷、次方写 x²）；
  * 不得出现初高中内容（年级 > 6 直接拦截）；
  * 不得出现评价性贬义词（笨、差、没脑子、废、错了别做）；
  * 单句 ≤ 40 字；不连用感叹号；不出现「老师/客服/AI」自称。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

#: 表名（与 grounding_service.PEER_CORPUS_TABLE 必须一致）。
TABLE: str = "peer_view_corpus"

#: 允许的错因类型（须与 tutor 提示词的错误诊断五类严格对齐）。
ERROR_TYPES: Tuple[str, ...] = ("读题错", "方法错", "计算错", "知识点缺", "检查漏")

#: 允许的场景标签。
SCENES: Tuple[str, ...] = (
    "购物", "时间", "距离", "吃", "穿", "游戏", "校园", "家务", "手工",
)

#: 允许的入库来源。
SOURCES: Tuple[str, ...] = ("seed", "template", "llm_rewrite")

#: 软审核扫描只覆盖「机器可无歧义判定」的红线；姓名/班级/校名这种靠运营人工抽检。
_BANNED_SELF_REF = ("老师", "客服")          # 不出现「老师/客服/AI」自称（AI 以大写字母出现）
_BANNED_DEROGATORY = ("笨", "没脑子", "废", "错了别做")
_BANNED_SELF_ACCUSE = ("你错了", "你不对", "你做错了")  # 把错误指向「你」
_LATEX_HINTS = ("\\frac", "\\sqrt", "\\cdot", "\\times", "${", "^{")  # LaTeX 源码痕迹


def _now() -> str:
    """UTC 时间 ISO8601 字符串（秒精度，带 Z）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def forbidden_scan(sentence: str, grade: Optional[int] = None) -> List[str]:
    """机器扫描一条语料的禁止项，返回命中的违规 code 列表（空 = 干净）。

    Args:
        sentence: 语料句本体。
        grade: 年级（1-6）；高于 6 视为初高中内容，单独记一条。

    Returns:
        违规 code 列表；顺序稳定（按检测顺序）。每条 code 含义：
        ``too_long`` 超 40 字 / ``latex`` 含 LaTeX 源码 / ``self_ref`` 出现自称 /
        ``self_accuse`` 把错误指向「你」/ ``derogatory`` 评价性贬义词 /
        ``grade_too_high`` 初高中内容 / ``multi_exclaim`` 连用感叹号。
    """
    hits: List[str] = []
    s = sentence or ""
    if len(s) > 40:
        hits.append("too_long")
    if any(h in s for h in _LATEX_HINTS) or "$" in s:
        hits.append("latex")
    if any(w in s for w in _BANNED_SELF_REF) or re.search(r"\bAI\b", s):
        hits.append("self_ref")
    if any(w in s for w in _BANNED_SELF_ACCUSE):
        hits.append("self_accuse")
    if any(w in s for w in _BANNED_DEROGATORY):
        hits.append("derogatory")
    if grade is not None and grade > 6:
        hits.append("grade_too_high")
    if "！！" in s or s.count("!") > 1 or s.count("！") > 1:
        hits.append("multi_exclaim")
    return hits


def _validate(
    kp_code: str, grade: int, scene: Optional[str],
    error_type: Optional[str], sentence: str, source: str,
) -> None:
    """入库前的参数合法性校验（在扫描之前）。"""
    if not (kp_code and kp_code.strip()):
        raise ValueError("kp_code 不能为空")
    if not (isinstance(grade, int) and 1 <= grade <= 6):
        raise ValueError(f"grade 必须 1-6 整数，实际 {grade!r}")
    if scene is not None and scene not in SCENES:
        raise ValueError(f"scene 必须是 {SCENES} 之一，实际 {scene!r}")
    if error_type is not None and error_type not in ERROR_TYPES:
        raise ValueError(f"error_type 必须是 {ERROR_TYPES} 之一，实际 {error_type!r}")
    if source not in SOURCES:
        raise ValueError(f"source 必须是 {SOURCES} 之一，实际 {source!r}")
    if not (sentence and sentence.strip()):
        raise ValueError("sentence 不能为空")


def ingest_sentence(
    db: Session,
    *,
    kp_code: str,
    grade: int,
    sentence: str,
    scene: Optional[str] = None,
    error_type: Optional[str] = None,
    source: str = "seed",
    seed_batch: Optional[str] = None,
    reviewer: Optional[str] = None,
) -> Dict[str, object]:
    """入库一条语料：先参数校验 → 再禁止项扫描 → 干净则 archived，否则 draft（拦截）。

    注意：**本函数只生成 + 过滤 + 入库到 archived（待人工抽检）**；
    真正的「人工放行」是单独的 :func:`release` / :func:`reject`。
    被禁止项拦截（draft）的语料不进入检索池，但仍留痕（``forbidden_scan`` 记原因），
    方便运营修正后重提。

    Args:
        db: 调用方会话。
        kp_code / grade / sentence / scene / error_type / source / seed_batch:
            见规格字段表。
        reviewer: 预留（生成阶段通常无审核人）。

    Returns:
        ``{"id", "status", "violations", "forbidden_scan"}``。
    """
    _validate(kp_code, grade, scene, error_type, sentence, source)
    violations = forbidden_scan(sentence, grade)
    status = "archived" if not violations else "draft"
    now = _now()
    row_id = str(uuid.uuid4())
    db.execute(
        text(
            f"INSERT INTO {TABLE} "
            "(id, kp_code, grade, scene, error_type, sentence, source, status, "
            "reviewed_by, reviewed_at, forbidden_scan, seed_batch, created_at, updated_at) "
            "VALUES (:id, :kp, :g, :sc, :et, :se, :so, :st, :rb, :ra, :fs, :sb, :ca, :ua)"
        ),
        {
            "id": row_id, "kp": kp_code.strip(), "g": grade, "sc": scene,
            "et": error_type, "se": sentence, "so": source, "st": status,
            "rb": reviewer, "ra": None,
            "fs": json.dumps(violations, ensure_ascii=False),
            "sb": seed_batch, "ca": now, "ua": now,
        },
    )
    return {
        "id": row_id,
        "status": status,
        "violations": violations,
        "forbidden_scan": violations,
    }


def release(db: Session, row_id: str, reviewer: str) -> bool:
    """人工抽检放行（archived/draft → released）。

    Args:
        db: 调用方会话。
        row_id: 语料 id。
        reviewer: 审核人（留痕）。

    Returns:
        是否成功（行存在才 True）。
    """
    now = _now()
    res = db.execute(
        text(
            f"UPDATE {TABLE} SET status='released', reviewed_by=:rb, "
            f"reviewed_at=:ra, updated_at=:ua WHERE id=:id"
        ),
        {"rb": reviewer, "ra": now, "ua": now, "id": row_id},
    )
    return bool(res.rowcount)


def reject(db: Session, row_id: str, reviewer: str) -> bool:
    """人工抽检退回（archived/draft → rejected，永不进入检索池）。"""
    now = _now()
    res = db.execute(
        text(
            f"UPDATE {TABLE} SET status='rejected', reviewed_by=:rb, "
            f"reviewed_at=:ra, updated_at=:ua WHERE id=:id"
        ),
        {"rb": reviewer, "ra": now, "ua": now, "id": row_id},
    )
    return bool(res.rowcount)


def list_for_review(
    db: Session, statuses: Tuple[str, ...] = ("archived",)
) -> List[Dict[str, object]]:
    """列出待人工抽检的语料（默认 archived）。"""
    placeholders = ",".join(f":s{i}" for i in range(len(statuses)))
    rows = db.execute(
        text(
            f"SELECT id, kp_code, grade, scene, error_type, sentence, source, "
            f"status, forbidden_scan, seed_batch, created_at "
            f"FROM {TABLE} WHERE status IN ({placeholders}) ORDER BY created_at"
        ),
        {f"s{i}": s for i, s in enumerate(statuses)},
    ).fetchall()
    out: List[Dict[str, object]] = []
    for r in rows:
        out.append(
            {
                "id": r[0], "kp_code": r[1], "grade": r[2], "scene": r[3],
                "error_type": r[4], "sentence": r[5], "source": r[6],
                "status": r[7], "forbidden_scan": r[8], "seed_batch": r[9],
                "created_at": r[10],
            }
        )
    return out


def retrieve_released(
    db: Session,
    *,
    kp_code: str,
    max_grade: int,
    error_type: Optional[str] = None,
    scene: Optional[str] = None,
    limit: int = 1,
) -> List[str]:
    """从「已放行」语料里按知识点 + 年级窗口取若干条同伴视角句。

    这是讲解层唯一允许的入口：**只 SELECT ``status='released'``**，未放行语料
    天然不会被取到（0 例外）。年级窗口上限由调用方算好传入（小学 6 封顶）。

    Args:
        db: 调用方会话。
        kp_code: 知识点 code。
        max_grade: 年级窗口上限（学生年级 + 半宽，封顶 6）。
        error_type: 可选，优先匹配同错因；不匹配不拦截（放宽召回）。
        scene: 可选，优先匹配同场景。
        limit: 返回条数（默认 1；讲解层一次一条即可）。

    Returns:
        命中的语料句列表（可能为空的）；**检索失败返回空列表，不抛错**。
    """
    if not kp_code or not isinstance(max_grade, int):
        return []
    try:
        clauses = ["status='released'", "kp_code=:kp", "grade<=:mg"]
        params: Dict[str, object] = {"kp": kp_code.strip(), "mg": max_grade}
        if error_type:
            clauses.append("error_type=:et")
            params["et"] = error_type
        if scene:
            clauses.append("scene=:sc")
            params["sc"] = scene
        sql = (
            f"SELECT sentence FROM {TABLE} WHERE {' AND '.join(clauses)} "
            f"ORDER BY RANDOM() LIMIT :lim"
        )
        params["lim"] = int(limit)
        rows = db.execute(text(sql), params).fetchall()
        return [r[0] for r in rows]
    except Exception:  # noqa: BLE001 - 检索失败绝不断辅导主流程
        return []


def count_released(db: Session) -> int:
    """已放行语料总数（验收：≥ 200 条）。"""
    row = db.execute(
        text(f"SELECT COUNT(*) FROM {TABLE} WHERE status='released'")
    ).fetchone()
    return int(row[0]) if row else 0


def coverage_stats(db: Session) -> Dict[str, int]:
    """放行语料的覆盖统计（验收：KP≥60 / 错因各类≥25 / 年级各≥20）。"""
    kp_rows = db.execute(
        text(
            f"SELECT COUNT(DISTINCT kp_code) FROM {TABLE} "
            f"WHERE status='released'"
        )
    ).fetchone()
    err_rows = db.execute(
        text(
            f"SELECT error_type, COUNT(*) FROM {TABLE} "
            f"WHERE status='released' GROUP BY error_type"
        )
    ).fetchall()
    grade_rows = db.execute(
        text(
            f"SELECT grade, COUNT(*) FROM {TABLE} "
            f"WHERE status='released' GROUP BY grade"
        )
    ).fetchall()
    return {
        "released": count_released(db),
        "kp_count": int(kp_rows[0]) if kp_rows else 0,
        "error_type": {r[0]: int(r[1]) for r in err_rows},
        "grade": {int(r[0]): int(r[1]) for r in grade_rows},
    }

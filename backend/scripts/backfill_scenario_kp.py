# -*- coding: utf-8 -*-
"""生活场景题知识点回填脚本（B-0 存量回填）。

背景
----
``backend/scripts/generate_scenario_bank.py`` 把场景题的 ``kp_id`` 硬编码成
``None``，864 道 ``source_doc='scenario_bank'`` 的题全部落在知识点体系之外，
导致 **归因断链**：``student_dna_service`` 的六维 / 遗忘事件在 ``kp_id is None``
时直接 ``continue``，学生做过这些题也不会产生带 ``knowledge_point_id`` 的 L1 事件。

本脚本按 ``app/engines/scenario_kp_mapping.py`` 的 24 条子模板映射，
把 ``questions.kp_id`` 回填成对应知识点的 id。

安全设计（沿用 ``scripts/cleanup_prod_data.py`` 的生产安全范式）
--------------------------------------------------------------
* **默认 dry-run**：不带 ``--confirm`` 时只打印意图，**零写入**；
* **备份**：真写前用 **sqlite3 backup API** 复制一份（**禁止 cp**——WAL 下
  直接复制会得到残缺库），备份路径写进报告；
* **幂等**：已回填成目标 id 的题跳过并单独计数；``kp_id`` 非空但与映射不符的题
  **不覆盖**，只计入 ``mismatched`` 并写进报告（人工处理）；
* **外键校验前置**：映射涉及的每个 ``kp_code`` 都必须在库中解析到一条
  ``status='active'`` 的知识点，否则**整批中止**并以非零退出码返回
  （不静默跳过任何一条映射）；
* **只写一列**：唯一被修改的列是 ``questions.kp_id``，其余列保持字节级一致
  （由 ``tests/test_backfill_scenario_kp.py`` 逐列比对守卫）；
* **不新增任何配置键**：批次大小等参数走 CLI 开关或模块常量，
  **不碰** ``app/engines/config.py`` 的 ``CONFIG_SPECS``（31 键有 golden 守卫）。

用法
----
    # 1) 预演（零写入）
    python backend/scripts/backfill_scenario_kp.py backend/dev.db

    # 2) 真写（先自动备份，再写 kp_id）
    python backend/scripts/backfill_scenario_kp.py backend/dev.db --confirm

    # 3) 产出审计报告
    python backend/scripts/backfill_scenario_kp.py backend/dev.db --report out.json

退出码
------
    0  正常完成（dry-run 预演 或 写入成功）
    2  命令行用法错误（argparse 默认）
    3  外键校验失败——映射的 kp_code 缺失或非 active，整批中止、零写入
    4  数据库 / IO 异常
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.engines.gap_ranking import (  # noqa: E402  (需先补 sys.path)
    GapCandidate,
    candidate_from_mapping,
    rank_gaps,
)
from app.engines.scenario_kp_mapping import (  # noqa: E402  (需先补 sys.path)
    ANNOTATION_ISSUES,
    EXPECTED_QUESTION_COUNT,
    SCENARIO_KP_MAPPING,
    TARGET_KP_CODES,
    export_mapping_json,
    validate_mapping_table,
)

# ============================================================ 常量
#: 退出码
EXIT_OK = 0
EXIT_FK_VALIDATION_FAILED = 3
EXIT_DB_ERROR = 4

#: 目标场景题来源（与 ``generate_scenario_bank.py`` 写入的一致）
TARGET_SOURCE_DOC = "scenario_bank"

#: 默认批次大小（每批一个事务）。**刻意做成模块常量 + CLI 开关**，
#: 而不是 ``CONFIG_SPECS`` 键——``CONFIG_SPECS`` 被 31 键 golden 测试锁死，
#: 且与并行分支冲突。
DEFAULT_BATCH_SIZE = 500

#: 备份文件名前缀
BACKUP_PREFIX = "backfill-scenario-kp-backup"

#: 缺口榜取前 N 条（写进 JSON 报告的 ``gap_ranking.items``）。
#:
#: ⚠️ 为什么是常量而不是 ``CONFIG_SPECS`` 键：``CONFIG_SPECS`` 被 31 键
#: golden 测试锁死，且与并行 rag 分支冲突；缺口榜条数属于**报告呈现参数**，
#: 走模块常量即可。
GAP_RANKING_TOP_N = 50

#: 缺口榜段的口径说明（**必须如实写明两个缺席因子**，不许伪造数据填它们）。
GAP_RANKING_NOTE = (
    "「前置被依赖」因子未启用（knowledge_relations 4 行 / knowledge_prerequisite 0 行，"
    "依赖边数据不足，该因子在引擎里 enabled=False 只留接口位）；"
    "错因命中数因子恒 0（错因富化埋点未上线）。"
    "本榜口径为：错因命中数 > 重要度 > 难度 > 零题量时长（字典序）。"
    "「零题量时长」取该知识点建档至今的天数（库里没有题量历史快照表，"
    "这是可得的最近似口径，非精确的『连续零题量天数』）。"
)


# ============================================================ 数据结构
@dataclass(frozen=True)
class KnowledgePointRow:
    """知识点的最小只读视图（供外键校验与报告使用）。"""

    id: str
    code: str
    name: str
    status: str
    grade: Optional[int]
    importance: Optional[int]
    difficulty: Optional[int]


@dataclass
class TargetRow:
    """一条待回填的题目行。"""

    id: str
    scenario_template: str
    kp_code: str
    kp_id_before: Optional[str]
    kp_id_after: str
    action: str = "pending"  # write | skip_same | skip_mismatch | skip_no_mapping
    reason: str = ""


@dataclass
class BackfillOutcome:
    """一次回填（或预演）的执行结论。"""

    batch_id: str
    mode: str
    db_path: str
    backup_path: Optional[str]
    report_path: Optional[str]
    exit_code: int
    targets: List[TargetRow] = field(default_factory=list)
    kp_counts_before: Dict[str, int] = field(default_factory=dict)
    kp_counts_after: Dict[str, int] = field(default_factory=dict)
    zero_kp_before: int = 0
    zero_kp_after: int = 0
    errors: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)
    planned: int = 0
    written: int = 0
    skipped_same: int = 0
    skipped_mismatch: int = 0
    skipped_no_mapping: int = 0
    gap_ranking: Dict[str, Any] = field(default_factory=dict)


# ============================================================ 工具函数
def utcnow_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串（秒精度，带 Z）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_batch_id() -> str:
    """生成一个批次号：``bkp-<UTC时间>-<6位随机>``。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"bkp-{stamp}-{uuid.uuid4().hex[:6]}"


def backup_database(source_path: Path, destination_path: Path) -> Path:
    """用 **sqlite3 backup API** 复制数据库。

    ⚠️ **禁止**用 ``shutil.copy`` / ``cp``：源库若处于 WAL 模式，直接复制主文件
    会漏掉 WAL 里的页，得到残缺库。``sqlite3.Connection.backup`` 会把整库
    （含 WAL 内容）一致地拷过去。

    Args:
        source_path: 源库路径。
        destination_path: 备份落盘路径（父目录需已存在）。

    Returns:
        备份文件路径。

    Raises:
        sqlite3.Error: 备份失败。
    """
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path))
    try:
        destination = sqlite3.connect(str(destination_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return destination_path


def default_backup_path(db_path: Path, batch_id: str) -> Path:
    """推导默认备份路径：与源库同目录的 ``<库名>.<批次号>.bak``。"""
    return db_path.with_name(f"{db_path.name}.{batch_id}.{BACKUP_PREFIX}.bak")


# ============================================================ 数据库读取
def fetch_knowledge_points(
    connection: sqlite3.Connection,
) -> Dict[str, KnowledgePointRow]:
    """按 ``code`` 索引知识点（只读）。

    Args:
        connection: 已打开的 SQLite 连接。

    Returns:
        ``code`` → ``KnowledgePointRow``；``code`` 为 NULL / 空串的知识点不入索引。
    """
    rows = connection.execute(
        """
        SELECT id, code, name, status, grade, importance, difficulty
          FROM knowledge_points
        """
    ).fetchall()
    index: Dict[str, KnowledgePointRow] = {}
    for row in rows:
        code = (row[1] or "").strip()
        if not code:
            continue
        index[code] = KnowledgePointRow(
            id=row[0],
            code=code,
            name=row[2],
            status=row[3],
            grade=row[4],
            importance=row[5],
            difficulty=row[6],
        )
    return index


def validate_kp_codes(
    kp_codes: Iterable[str],
    kp_index: Dict[str, KnowledgePointRow],
) -> List[str]:
    """外键校验：每个 code 必须解析到一条 **active** 的知识点。

    Args:
        kp_codes: 待校验的知识点 code。
        kp_index: ``code`` → 知识点行。

    Returns:
        错误描述列表；**为空表示校验通过**。任一条错误都意味着整批必须中止。
    """
    errors: List[str] = []
    for code in sorted(set(kp_codes)):
        row = kp_index.get(code)
        if row is None:
            errors.append(f"知识点不存在: code={code}")
            continue
        if row.status != "active":
            errors.append(f"知识点非 active: code={code} status={row.status}")
    return errors


def fetch_targets(
    connection: sqlite3.Connection,
) -> List[TargetRow]:
    """读取待回填的题目行（**只读**）。

    取数范围：``source_doc='scenario_bank'`` 的**全部**题目（含 ``kp_id`` 已非空的），
    以便幂等重跑时能区分「已回填」与「映射不符」两种跳过。

    Args:
        connection: 已打开的 SQLite 连接。

    Returns:
        ``TargetRow`` 列表（``action`` 字段仍为 ``pending``）。
    """
    rows = connection.execute(
        """
        SELECT id, scenario_template, kp_id
          FROM questions
         WHERE source_doc = ?
         ORDER BY scenario_template, id
        """,
        (TARGET_SOURCE_DOC,),
    ).fetchall()
    targets: List[TargetRow] = []
    for question_id, template, kp_id in rows:
        template_key = (template or "").strip()
        kp_code = SCENARIO_KP_MAPPING.get(template_key, "")
        targets.append(
            TargetRow(
                id=question_id,
                scenario_template=template_key,
                kp_code=kp_code,
                kp_id_before=kp_id,
                kp_id_after="",
            )
        )
    return targets


def count_kp_questions(
    connection: sqlite3.Connection,
    kp_codes: Sequence[str],
    active_only: bool = True,
) -> Dict[str, int]:
    """统计每个目标知识点下的题目数。

    Args:
        connection: 已打开的 SQLite 连接。
        kp_codes: 目标知识点 code 序列。
        active_only: 是否只统计 ``status='active'`` 的题（与
            ``kp_tree.l2_sample_counts`` / 缺口报告口径一致）。

    Returns:
        ``code`` → 题目数（没有题的 code 也给 0，保证键集完整）。
    """
    counts = {code: 0 for code in kp_codes}
    placeholders = ",".join("?" for _ in kp_codes)
    if not kp_codes:
        return counts
    sql = (
        f"SELECT q.kp_id, COUNT(*) FROM questions q "
        f"JOIN knowledge_points kp ON kp.id = q.kp_id "
        f"WHERE kp.code IN ({placeholders})"
    )
    if active_only:
        sql += " AND q.status = 'active'"
    sql += " GROUP BY q.kp_id"
    by_id = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT id, code FROM knowledge_points WHERE code IN (%s)"
            % placeholders,
            tuple(kp_codes),
        ).fetchall()
    }
    for kp_id, amount in connection.execute(sql, tuple(kp_codes)).fetchall():
        code = by_id.get(kp_id)
        if code is not None:
            counts[code] = int(amount)
    return counts


def count_zero_question_kps(connection: sqlite3.Connection) -> int:
    """统计「零题量」知识点数。

    口径与缺口报告一致：``status='active'`` 的知识点，且**没有**任何
    ``status='active'`` 的题目挂在它下面。

    Args:
        connection: 已打开的 SQLite 连接。

    Returns:
        零题量知识点数量。
    """
    row = connection.execute(
        """
        SELECT COUNT(*)
          FROM knowledge_points kp
         WHERE kp.status = 'active'
           AND NOT EXISTS (
                SELECT 1 FROM questions q
                 WHERE q.kp_id = kp.id
                   AND q.status = 'active'
           )
        """
    ).fetchone()
    return int(row[0]) if row else 0


# ============================================================ 缺口榜（脚本层查库 → 引擎层纯排序）
def fetch_gap_candidates(connection: sqlite3.Connection) -> List[GapCandidate]:
    """取「零题量 active 知识点」候选集（**脚本层查库**）。

    ⚠️ 职责边界：``app/engines/gap_ranking.py`` 是**纯函数层**，不查库。
    所以"哪些知识点算缺口"这件事必须由**本脚本**（唯一碰 SQLite 的地方）
    查出来，再喂给引擎排序。

    「零题量时长」取 ``knowledge_points.created_at`` 到今天的天数——
    库里**没有**题量历史快照表，所以这是可得的最近似口径
    （"建档至今一直零题量"），不是精确的"连续零题量天数"。
    ``created_at`` 为空时记 0。

    Args:
        connection: 已打开的 SQLite 连接。

    Returns:
        候选知识点列表（``GapCandidate``）。
    """
    rows = connection.execute(
        """
        SELECT kp.id AS kp_id,
               kp.code AS kp_code,
               kp.name AS kp_name,
               kp.grade AS grade,
               kp.subject AS subject,
               kp.importance AS importance,
               kp.difficulty AS difficulty,
               CAST(julianday('now') - julianday(kp.created_at) AS INTEGER)
                   AS zero_duration_days,
               (SELECT COUNT(*) FROM questions q
                 WHERE q.kp_id = kp.id AND q.status = 'active') AS question_count
          FROM knowledge_points kp
         WHERE kp.status = 'active'
         ORDER BY kp.code
        """
    ).fetchall()

    candidates: List[GapCandidate] = []
    for row in rows:
        if int(row["question_count"] or 0) > 0:
            continue  # 有题 → 不是缺口
        candidates.append(
            candidate_from_mapping(
                {
                    "knowledge_point_id": row["kp_id"],
                    "kp_code": row["kp_code"] or "",
                    "kp_name": row["kp_name"] or "",
                    "grade": row["grade"],
                    "subject": row["subject"] or "",
                    "importance": row["importance"] or 0,
                    "difficulty": row["difficulty"] or 0,
                    "question_count": 0,
                    "zero_duration_days": row["zero_duration_days"] or 0,
                }
            )
        )
    return candidates


def build_gap_ranking_section(
    connection: sqlite3.Connection,
    top_n: int = GAP_RANKING_TOP_N,
) -> Dict[str, Any]:
    """产出 JSON 报告里的 ``gap_ranking`` 段。

    结构::

        {
          "note": "…两个缺席因子的原因…",
          "total_zero_question_kp": 244,
          "ranked_count": 180,
          "deferred_count": 64,
          "top_n": 50,
          "items": [ {kp_code, kp_name, grade, importance, difficulty,
                  zero_days, rank, reason}, ... ]
        }

    Args:
        connection: 已打开的 SQLite 连接。
        top_n: ``items`` 取前 N 条。

    Returns:
        可 ``json.dumps`` 的 dict。
    """
    candidates = fetch_gap_candidates(connection)
    ranking = rank_gaps(candidates)
    items: List[Dict[str, Any]] = []
    for result in ranking.ranked[: max(0, int(top_n))]:
        items.append(
            {
                "rank": result.rank,
                "kp_id": result.knowledge_point_id,
                "kp_code": result.kp_code,
                "kp_name": result.kp_name,
                "grade": result.grade,
                "band": result.band,
                "importance": int(result.factors.get("importance", 0)),
                "difficulty": int(result.factors.get("difficulty", 0)),
                "zero_days": int(result.factors.get("zero_duration_days", 0)),
                "reason": result.reason,
            }
        )
    return {
        "note": GAP_RANKING_NOTE,
        "total_zero_question_kp": len(candidates),
        "ranked_count": len(ranking.ranked),
        "deferred_count": len(ranking.deferred),
        "top_n": int(top_n),
        "items": items,
    }


# ============================================================ 计划与写入
def plan_targets(
    targets: Sequence[TargetRow],
    kp_index: Dict[str, KnowledgePointRow],
) -> Tuple[List[TargetRow], int, int, int]:
    """给每条目标行标出动作（不写库）。

    动作口径：

        * ``write``           —— ``kp_id IS NULL``，需要回填；
        * ``skip_same``       —— ``kp_id`` 已等于映射目标 id（幂等跳过）；
        * ``skip_mismatch``   —— ``kp_id`` 非空但与映射目标不符（**不覆盖**）；
        * ``skip_no_mapping`` —— 映射表没覆盖该子模板（理论不应出现）。

    Args:
        targets: 目标行。
        kp_index: ``code`` → 知识点行。

    Returns:
        ``(targets, write_count, skip_same, skip_mismatch)``。
    """
    write_count = 0
    skip_same = 0
    skip_mismatch = 0
    for target in targets:
        if not target.kp_code:
            target.action = "skip_no_mapping"
            target.reason = "映射表未覆盖该 scenario_template"
            continue
        kp_row = kp_index.get(target.kp_code)
        if kp_row is None:
            target.action = "skip_no_mapping"
            target.reason = f"知识点不存在: {target.kp_code}"
            continue
        target.kp_id_after = kp_row.id
        if target.kp_id_before is None:
            target.action = "write"
            target.reason = "kp_id 为空，回填"
            write_count += 1
        elif target.kp_id_before == kp_row.id:
            target.action = "skip_same"
            target.reason = "已回填为同一知识点，幂等跳过"
            skip_same += 1
        else:
            target.action = "skip_mismatch"
            target.reason = "已有其它知识点归属，不覆盖，交人工处理"
            skip_mismatch += 1
    return list(targets), write_count, skip_same, skip_mismatch


def apply_writes(
    connection: sqlite3.Connection,
    targets: Sequence[TargetRow],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """执行回填写入（**只写 ``questions.kp_id`` 一列**）。

    Args:
        connection: 已打开的 SQLite 连接。
        targets: 目标行（只处理 ``action == 'write'`` 的）。
        batch_size: 每批事务的条数。

    Returns:
        实际写入条数。

    Raises:
        sqlite3.Error: 写入失败（事务会回滚）。
    """
    pending = [target for target in targets if target.action == "write"]
    written = 0
    step = max(1, int(batch_size))
    for start in range(0, len(pending), step):
        chunk = pending[start : start + step]
        with connection:  # 每批一个事务
            for target in chunk:
                cursor = connection.execute(
                    "UPDATE questions SET kp_id = ? WHERE id = ? AND kp_id IS NULL",
                    (target.kp_id_after, target.id),
                )
                written += int(cursor.rowcount or 0)
    return written


# ============================================================ 报告
def build_report(
    outcome: BackfillOutcome,
    mapping_json: Dict[str, Any],
) -> Dict[str, Any]:
    """组装 JSON 报告。

    Args:
        outcome: 执行结论。
        mapping_json: ``export_mapping_json()`` 的输出。

    Returns:
        可 ``json.dumps`` 的报告 dict。
    """
    per_kp_delta = {
        code: int(outcome.kp_counts_after.get(code, 0))
        - int(outcome.kp_counts_before.get(code, 0))
        for code in sorted(set(outcome.kp_counts_before) | set(outcome.kp_counts_after))
    }
    return {
        "batch_id": outcome.batch_id,
        "generated_at": utcnow_iso(),
        "mode": outcome.mode,
        "db_path": outcome.db_path,
        "backup_path": outcome.backup_path,
        "report_path": outcome.report_path,
        "exit_code": outcome.exit_code,
        "source_doc": TARGET_SOURCE_DOC,
        "mapping": {
            "template_count": mapping_json.get("template_count"),
            "kp_code_count": mapping_json.get("kp_code_count"),
            "expected_question_count": mapping_json.get("expected_question_count"),
            "records": mapping_json.get("records"),
        },
        "stats": {
            "target_total": len(outcome.targets),
            "to_backfill": outcome.planned,
            "written": outcome.written,
            "skipped_same": outcome.skipped_same,
            "skipped_mismatch": outcome.skipped_mismatch,
            "skipped_no_mapping": outcome.skipped_no_mapping,
            "errors": len(outcome.errors),
            "validation_errors": len(outcome.validation_errors),
        },
        "kp_question_counts": {
            "before": outcome.kp_counts_before,
            "after": outcome.kp_counts_after,
            "delta": per_kp_delta,
        },
        "zero_question_kp": {
            "before": outcome.zero_kp_before,
            "after": outcome.zero_kp_after,
            "delta": outcome.zero_kp_after - outcome.zero_kp_before,
        },
        "gap_ranking": outcome.gap_ranking,
        "questions": [
            {
                "id": target.id,
                "scenario_template": target.scenario_template,
                "kp_code": target.kp_code,
                "kp_id_before": target.kp_id_before,
                "kp_id_after": target.kp_id_after or None,
                "action": target.action,
                "reason": target.reason,
            }
            for target in outcome.targets
        ],
        "annotation_issue": [dict(issue) for issue in ANNOTATION_ISSUES],
        "errors": list(outcome.errors),
        "validation_errors": list(outcome.validation_errors),
    }


# ============================================================ 主流程
def run_backfill(
    db_path: Path,
    confirm: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_id: Optional[str] = None,
    backup_dir: Optional[Path] = None,
) -> BackfillOutcome:
    """执行一次回填（或 dry-run 预演）。

    Args:
        db_path: 目标 SQLite 文件路径。
        confirm: ``True`` 才真正写库；``False`` 为 dry-run（零写入）。
        batch_size: 每批事务条数。
        batch_id: 批次号（不传则自动生成）。
        backup_dir: 备份落盘目录（默认与源库同目录）。

    Returns:
        ``BackfillOutcome``。
    """
    resolved_batch_id = batch_id or new_batch_id()
    outcome = BackfillOutcome(
        batch_id=resolved_batch_id,
        mode="confirm" if confirm else "dry-run",
        db_path=str(db_path),
        backup_path=None,
        report_path=None,
        exit_code=EXIT_OK,
    )

    mapping_report = validate_mapping_table()
    if not mapping_report["ok"]:
        outcome.validation_errors.extend(mapping_report["issues"])
        outcome.exit_code = EXIT_FK_VALIDATION_FAILED
        return outcome

    connection = sqlite3.connect(str(db_path))
    try:
        connection.row_factory = sqlite3.Row
        kp_index = fetch_knowledge_points(connection)
        validation_errors = validate_kp_codes(TARGET_KP_CODES, kp_index)

        # 只读地取一遍目标行与计划（**不写库**），让中止时的报告仍能说明
        # 「本来打算回填多少条、被哪几个 KP 卡住」。
        targets = fetch_targets(connection)
        targets, write_count, skip_same, skip_mismatch = plan_targets(targets, kp_index)
        outcome.targets = targets
        outcome.planned = write_count
        outcome.skipped_same = skip_same
        outcome.skipped_mismatch = skip_mismatch
        outcome.skipped_no_mapping = sum(
            1 for target in targets if target.action == "skip_no_mapping"
        )

        if validation_errors:
            # 整批中止：外键校验不过，一条都不写
            outcome.validation_errors.extend(validation_errors)
            outcome.exit_code = EXIT_FK_VALIDATION_FAILED
            # 中止时也要给出缺口榜（只读，不影响"零写入"）
            outcome.gap_ranking = build_gap_ranking_section(connection)
            return outcome

        outcome.kp_counts_before = count_kp_questions(connection, TARGET_KP_CODES)
        outcome.zero_kp_before = count_zero_question_kps(connection)

        if confirm:
            backup_target = (
                (backup_dir or db_path.parent) / f"{db_path.name}.{resolved_batch_id}.bak"
            )
            backup_database(db_path, backup_target)
            outcome.backup_path = str(backup_target)
            outcome.written = apply_writes(connection, targets, batch_size=batch_size)
        else:
            # dry-run：只报告「打算写多少条」，一个字都不落
            outcome.written = 0

        outcome.kp_counts_after = count_kp_questions(connection, TARGET_KP_CODES)
        outcome.zero_kp_after = count_zero_question_kps(connection)
        # 缺口榜：在**写完之后**取，反映报告生成时刻的库状态
        # （dry-run 时库没变，取到的就是当前状态）。
        outcome.gap_ranking = build_gap_ranking_section(connection)
    finally:
        connection.close()

    return outcome


def write_report(report: Dict[str, Any], report_path: Path) -> Path:
    """把报告写成 JSON 文件。

    Args:
        report: 报告 dict。
        report_path: 落盘路径。

    Returns:
        落盘路径。
    """
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report_path


def print_summary(outcome: BackfillOutcome) -> None:
    """打印人读摘要（dry-run 也打印「打算做什么」）。"""
    print("=" * 72)
    print(f"[题库回填 B-0] 批次 {outcome.batch_id}  模式={outcome.mode}")
    print(f"  数据库      : {outcome.db_path}")
    if outcome.backup_path:
        print(f"  备份        : {outcome.backup_path}")
    elif outcome.validation_errors:
        print("  备份        : (外键校验失败，整批中止，未备份未写入)")
    else:
        print("  备份        : (dry-run 不备份)")
    print(f"  目标题目    : {len(outcome.targets)} 条")
    print(f"  待回填      : {outcome.planned} 条")
    print(f"  实际写入    : {outcome.written} 条")
    print(f"  幂等跳过    : {outcome.skipped_same} 条")
    print(f"  映射不符跳过: {outcome.skipped_mismatch} 条")
    print(f"  无映射跳过  : {outcome.skipped_no_mapping} 条")
    print(f"  映射覆盖    : {len(SCENARIO_KP_MAPPING)} 个子模板 / "
          f"{len(TARGET_KP_CODES)} 个知识点 / 预期 {EXPECTED_QUESTION_COUNT} 题")
    print(f"  零题量知识点: {outcome.zero_kp_before} → {outcome.zero_kp_after}")
    if outcome.validation_errors:
        print("  ❌ 外键校验失败（整批中止，零写入）：")
        for message in outcome.validation_errors:
            print(f"     - {message}")
    if outcome.errors:
        print("  ❌ 错误：")
        for message in outcome.errors:
            print(f"     - {message}")
    if outcome.mode == "dry-run" and not outcome.validation_errors:
        print("  ℹ️ dry-run：未写入任何数据。确认真实回填请加 --confirm。")
    print("=" * 72)


def build_arg_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="生活场景题知识点回填（B-0 存量回填，默认 dry-run）",
    )
    parser.add_argument(
        "db_path",
        nargs="?",
        default=str(BACKEND_DIR / "dev.db"),
        help="目标 SQLite 文件路径（默认 backend/dev.db）",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="真正写库（默认 dry-run，零写入）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批事务条数（默认 {DEFAULT_BATCH_SIZE}）",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="JSON 报告落盘路径（不传则只打印摘要）",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="备份文件落盘目录（默认与源库同目录）",
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="指定批次号（默认自动生成）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """脚本入口。

    Args:
        argv: 命令行参数（默认取 ``sys.argv[1:]``）。

    Returns:
        进程退出码。
    """
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    db_path = Path(args.db_path).expanduser()
    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}", file=sys.stderr)
        return EXIT_DB_ERROR

    # 生产安全：路径里带 prod 字样时必须显式二次确认
    if args.confirm and "prod" in str(db_path).lower():
        if not os.environ.get("ALLOW_PROD_BACKFILL"):
            print(
                "❌ 目标路径含 'prod' 字样；如确认是安全的生产副本，"
                "请设置环境变量 ALLOW_PROD_BACKFILL=1 后重试。",
                file=sys.stderr,
            )
            return EXIT_DB_ERROR

    try:
        outcome = run_backfill(
            db_path=db_path,
            confirm=bool(args.confirm),
            batch_size=int(args.batch_size),
            batch_id=args.batch_id,
            backup_dir=Path(args.backup_dir).expanduser() if args.backup_dir else None,
        )
    except sqlite3.Error as exc:  # pragma: no cover - 依赖真实 IO 故障
        print(f"❌ 数据库错误: {exc}", file=sys.stderr)
        return EXIT_DB_ERROR

    print_summary(outcome)

    if args.report:
        outcome.report_path = str(Path(args.report).expanduser())

    report = build_report(outcome, export_mapping_json())
    if outcome.report_path:
        report_path = write_report(report, Path(outcome.report_path))
        print(f"  报告已写入: {report_path}")

    if outcome.validation_errors:
        return EXIT_FK_VALIDATION_FAILED
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

"""残缺选择题治理 · 脚本共用工具（T01 · 2026-08-12）。

提供两批脚本（``20260812_fix_screenshot_four.py`` / ``20260812_archive_residual_choices.py``）
共用的纯函数与 DB 助手：

* :func:`dumps_like_ormtype` —— 与 ``app.models.base.JSONType`` 逐字节一致的 JSON 序列化；
* :data:`REMOVED_BACKUP_TABLE` / :data:`OPTIONS_BACKUP_TABLE` —— 两张独立备份表名；
* :func:`create_backup_tables` —— 幂等建表（零迁移，不进 ``migrations/``）；
* :func:`ensure_removed_backup` / :func:`ensure_options_backup` —— 幂等留痕（NOT EXISTS 去重）；
* :func:`export_removed_csv` / :func:`export_patched_csv` —— CSV 导出（列序对齐既有 CSV）；
* :func:`resolve_uuid_prefix` —— UUID 前缀解析（防误伤，命中 ≠1 即抛错）；
* :func:`utc_isoformat` —— 统一 UTC 时间戳。

⚠️ 零迁移红线：备份表一律 ``CREATE TABLE IF NOT EXISTS``，不修改 ``questions`` 表结构、
不新增 ``migrations/`` 文件。留痕全走独立备份表 + CSV。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import text  # noqa: E402

#: 归档留痕表（N0.2）：与既有 ``questions_options_backup_20260811`` 同构，列序不同。
#: 列：``(id, old_status, old_kp_id, removed_at, reason)``。
REMOVED_BACKUP_TABLE: str = "questions_removed_backup_20260812"

#: 补 D 留痕表（N0.1）：记录原/新 options，便于回滚补丁。
#: 列：``(id, old_options, new_options, patched_at, reason)``。
OPTIONS_BACKUP_TABLE: str = "questions_options_backup_20260812"

#: 本期归档原因（N0.2）。
REASON_RESIDUAL_CHOICE_MISSING_ABCD: str = "residual_choice_missing_abcd"
#: 本期补 D 原因（N0.1）。
REASON_RESIDUAL_CHOICE_PATCH_D: str = "residual_choice_patch_d"
#: 9cf5259e 逻辑损坏（OCR 误读 ÷ 为 -，无法补 D）。
REASON_RESIDUAL_CHOICE_LOGIC_BROKEN: str = "residual_choice_logic_broken"


def dumps_like_ormtype(value: Any) -> str:
    """与 ``JSONType.process_bind_param`` 逐字节一致。

    ``json.dumps(value, ensure_ascii=False, separators=(",", ":"))``。脚本写回
    ``options`` 必须复用本函数，否则 ORM 读回虽 OK 但 diff 噪音大、后续比对全废。
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def utc_isoformat(moment: Optional[datetime] = None) -> str:
    """UTC ISO8601 时间戳（带时区），与 ``app.models.base.utcnow`` 对齐。"""
    return (moment or datetime.now(timezone.utc)).isoformat()


def create_backup_tables(conn) -> None:
    """幂等建两张备份表（``CREATE TABLE IF NOT EXISTS``，零迁移）。

    Args:
        conn: SQLAlchemy ``Connection``（``engine.begin()`` 产物）。
    """
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {REMOVED_BACKUP_TABLE} (
                id          TEXT NOT NULL,
                old_status  TEXT NOT NULL,
                old_kp_id   TEXT,
                removed_at  TEXT NOT NULL,
                reason      TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {OPTIONS_BACKUP_TABLE} (
                id          TEXT NOT NULL,
                old_options TEXT,
                new_options TEXT,
                patched_at  TEXT NOT NULL,
                reason      TEXT NOT NULL
            )
            """
        )
    )


def ensure_removed_backup(conn, reason: str, rows: Iterable[Dict[str, Any]]) -> int:
    """幂等把归档留痕写入 :data:`REMOVED_BACKUP_TABLE`。

    按 ``(id, reason)`` 去重（``NOT EXISTS`` 子查询），避免与既有 154 行
    ``audit_error`` 行冲突，也保证脚本可重复运行。返回**新写入**的行数。

    Args:
        conn: SQLAlchemy ``Connection``。
        reason: 归档原因（本期恒为 ``residual_choice_missing_abcd``）。
        rows: 留痕行，每行为 ``{"id", "old_status", "old_kp_id", "removed_at"}``。
    """
    inserted = 0
    for row in rows:
        qid = row["id"]
        exists = conn.execute(
            text(
                f"SELECT 1 FROM {REMOVED_BACKUP_TABLE} "
                f"WHERE id = :id AND reason = :reason"
            ),
            {"id": qid, "reason": reason},
        ).first()
        if exists is not None:
            continue
        conn.execute(
            text(
                f"INSERT INTO {REMOVED_BACKUP_TABLE} "
                f"(id, old_status, old_kp_id, removed_at, reason) "
                f"VALUES (:id, :old_status, :old_kp_id, :removed_at, :reason)"
            ),
            {
                "id": qid,
                "old_status": row.get("old_status"),
                "old_kp_id": row.get("old_kp_id"),
                "removed_at": row.get("removed_at"),
                "reason": reason,
            },
        )
        inserted += 1
    return inserted


def ensure_options_backup(conn, rows: Iterable[Dict[str, Any]]) -> int:
    """幂等把补 D 留痕写入 :data:`OPTIONS_BACKUP_TABLE`。

    按 id 去重（reason 恒为 ``residual_choice_patch_d``），返回**新写入**的行数。

    Args:
        conn: SQLAlchemy ``Connection``。
        rows: 留痕行，每行为 ``{"id", "old_options", "new_options", "patched_at"}``。
    """
    inserted = 0
    for row in rows:
        qid = row["id"]
        exists = conn.execute(
            text(f"SELECT 1 FROM {OPTIONS_BACKUP_TABLE} WHERE id = :id"),
            {"id": qid},
        ).first()
        if exists is not None:
            continue
        conn.execute(
            text(
                f"INSERT INTO {OPTIONS_BACKUP_TABLE} "
                f"(id, old_options, new_options, patched_at, reason) "
                f"VALUES (:id, :old_options, :new_options, :patched_at, :reason)"
            ),
            {
                "id": qid,
                "old_options": row.get("old_options"),
                "new_options": row.get("new_options"),
                "patched_at": row.get("patched_at"),
                "reason": row.get("reason", REASON_RESIDUAL_CHOICE_PATCH_D),
            },
        )
        inserted += 1
    return inserted


def export_removed_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    """写 ``docs/residual-choice-removed-20260812.csv``。

    列顺序（对齐既有 ``removed-questions-20260812.csv``）：
    ``question_id, old_status, old_kp_id, reason, removed_at``。

    ⚠️ 幂等保护：若 ``rows`` 为空（重跑且本批次无新增归档），**不覆盖**既有 CSV，
    保留历史审计证据，避免把已写好的 308 行 CSV 截断成只剩表头。

    Args:
        path: 目标 CSV 路径（父目录自动创建）。
        rows: 留痕行，字段同 :func:`ensure_removed_backup` 入参。
    """
    materialized = list(rows)
    if not materialized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question_id", "old_status", "old_kp_id", "reason", "removed_at"])
        for row in rows:
            writer.writerow(
                [
                    row.get("id"),
                    row.get("old_status"),
                    row.get("old_kp_id") or "",
                    row.get("reason"),
                    row.get("removed_at"),
                ]
            )


def export_patched_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    """写 ``docs/residual-choice-patched-20260812.csv``。

    统一 4 列：``question_id, action, old_d, new_d``。
    补 D 行 ``action='patch_d'``，``old_d``/``new_d`` 为 D 选项内容；
    archive 行 ``action='archive'``，``old_d`` 留空、``new_d`` 填 status（如 ``archived``）。

    ⚠️ 幂等保护：若 ``rows`` 为空（重跑且无新增），不覆盖既有 CSV。

    Args:
        path: 目标 CSV 路径（父目录自动创建）。
        rows: 留痕行，补 D 行含 ``{id, old_d, new_d}``，archive 行含 ``{id, status}``。
    """
    materialized = list(rows)
    if not materialized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question_id", "action", "old_d", "new_d"])
        for row in rows:
            action = row.get("action")
            if action == "archive":
                writer.writerow([row.get("id"), action, "", row.get("status", "")])
            else:
                writer.writerow(
                    [row.get("id"), action or "patch_d", row.get("old_d", ""), row.get("new_d", "")]
                )


def resolve_uuid_prefix(conn, prefix: str) -> str:
    """用 ``LIKE 'prefix%'`` 解析题目 UUID。

    命中恰好 1 行返回全 UUID；命中 0 行或 ≥2 行立即抛 ``ValueError``（防误伤——
    前缀漂移命中多行会批量改错库）。

    Args:
        conn: SQLAlchemy ``Connection``。
        prefix: UUID 前缀（如 ``9a4f1461``）。

    Returns:
        命中的完整 UUID 字符串。
    """
    matches = [
        r["id"]
        for r in conn.execute(
            text("SELECT id FROM questions WHERE id LIKE :pat"),
            {"pat": f"{prefix}%"},
        )
        .mappings()
        .all()
    ]
    if len(matches) == 0:
        raise ValueError(f"UUID 前缀 {prefix!r} 未命中任何题目")
    if len(matches) > 1:
        raise ValueError(
            f"UUID 前缀 {prefix!r} 命中 {len(matches)} 行（应为 1）：{matches[:5]}"
        )
    return str(matches[0])


__all__ = [
    "REMOVED_BACKUP_TABLE",
    "OPTIONS_BACKUP_TABLE",
    "REASON_RESIDUAL_CHOICE_MISSING_ABCD",
    "REASON_RESIDUAL_CHOICE_PATCH_D",
    "REASON_RESIDUAL_CHOICE_LOGIC_BROKEN",
    "dumps_like_ormtype",
    "utc_isoformat",
    "create_backup_tables",
    "ensure_removed_backup",
    "ensure_options_backup",
    "export_removed_csv",
    "export_patched_csv",
    "resolve_uuid_prefix",
]

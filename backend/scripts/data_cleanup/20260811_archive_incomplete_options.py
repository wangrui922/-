"""P0-A · 缺选项归档（T2 · 2026-08-11 重构期）。

判断口径（与设计文档 §1.1 / §3 一致）：

* 只扫 ``status='active' AND type_='选择'``。
* Python 侧调 ``judge_engine.normalize_options()`` 归一化（唯一真相源）。
  ``normalize_options`` 兼容 dict / list / list-of-dict 三种真实形态；
  归一后长度 **< 2** 就视为「选项不完整」。
* 命中行 ``UPDATE status='archived', updated_at=utcnow()``，**不动 options 内容 / correct_answer**。

零迁移红线：仅 UPDATE 现有行；备份表用 ``CREATE TABLE … AS SELECT``，不属于
schema 改动（参考 PRD §7.2 注脚）。

用法::

    cd backend && .venv/Scripts/python.exe ../scripts/data_cleanup/20260811_archive_incomplete_options.py            # 实际归档
    cd backend && .venv/Scripts/python.exe ../scripts/data_cleanup/20260811_archive_incomplete_options.py --dry-run   # 只统计

退出码：成功 0；脚本本身错误 1（命中数 0 不是错误——可能数据库已干净）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# 把 backend/ 放进 sys.path，让 ``from app...`` 可以工作（脚本由 ``../scripts/...`` 启动）
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from app.models.content import QUESTION_STATUS_ACTIVE, QUESTION_STATUS_ARCHIVED  # noqa: E402
from app.services.judge_engine import normalize_options  # noqa: E402

from scripts.data_cleanup._common import (  # noqa: E402
    append_log,
    build_log_block,
    get_engine,
    utc_isoformat,
)


BACKUP_TABLE: str = "questions_options_backup_20260811"


def _normalize_raw_options(raw: Any) -> List[Any]:
    """把 JSON 列读出来的 ``str / None / list / dict`` 还原成原始结构。

    SQLite 的 JSON 列在大多数驱动下返回的是 ``str``，需要先 ``json.loads``。
    PG 直接是 ``list / dict``。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return []
    return raw


def _is_incomplete_choices(type_: Optional[str], options_raw: Any) -> bool:
    """归一化后长度 < 2 即「不完整」。非选择题直接 False。"""
    if (type_ or "").strip() != "选择":
        return False
    return len(normalize_options(_normalize_raw_options(options_raw))) < 2


def _scan_candidates(conn) -> List[Dict[str, Any]]:
    """拉回所有 active 选择题，由 Python 侧做最终归档判定。"""
    rows = conn.execute(
        text(
            """
            SELECT id, type, options
            FROM questions
            WHERE status = :active AND type = :choice
            """
        ),
        {"active": QUESTION_STATUS_ACTIVE, "choice": "选择"},
    ).mappings().all()
    return [dict(row) for row in rows]


def _backup_table_sql() -> str:
    """构造 SQLite 兼容的备份表语句。``IF NOT EXISTS`` 让脚本可重复运行。"""
    return (
        f"CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} AS "
        "SELECT id, type, options FROM questions "
        f"WHERE status = '{QUESTION_STATUS_ACTIVE}' AND type = '选择'"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="归档选项不完整的选择题")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args(argv)

    engine = get_engine()
    stamp = utc_isoformat(datetime.now(timezone.utc))

    with engine.begin() as conn:
        # 1. 备份表（仅 dry-run 不写）
        if not args.dry_run:
            conn.execute(text(_backup_table_sql()))

        # 2. 扫候选题 + Python 侧判定
        candidates = _scan_candidates(conn)
        to_archive: List[str] = []
        for row in candidates:
            if _is_incomplete_choices(row.get("type"), row.get("options")):
                to_archive.append(str(row["id"]))

        scanned = len(candidates)
        matched = len(to_archive)

        # 3. 实际 UPDATE
        if to_archive and not args.dry_run:
            placeholders = ", ".join(f":id{i}" for i in range(len(to_archive)))
            params = {f"id{i}": qid for i, qid in enumerate(to_archive)}
            params["archived"] = QUESTION_STATUS_ARCHIVED
            conn.execute(
                text(
                    f"UPDATE questions SET status = :archived "
                    f"WHERE id IN ({placeholders})"
                ),
                params,
            )

    # 4. 日志
    log_block = build_log_block(
        "阶段 A · 缺选项归档",
        [
            ("脚本", "scripts/data_cleanup/20260811_archive_incomplete_options.py"),
            ("时间", stamp),
            ("备份表", f"{BACKUP_TABLE}（{'已跳过' if args.dry_run else '已创建'}）"),
            ("模式", "dry-run" if args.dry_run else "实际执行"),
            ("扫描", f"active 选择题 {scanned} 道"),
            ("命中归档", f"{matched} 道"),
            (
                "不动字段",
                "options 内容、correct_answer",
            ),
        ],
    )
    append_log(log_block)

    print(
        f"[{'dry-run' if args.dry_run else '归档'}] 阶段 A 完成："
        f"扫描 {scanned} 道 active 选择题，命中归档 {matched} 道"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

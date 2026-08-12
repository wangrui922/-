"""P0-B · 题干 E2E 前缀剥离（T3 · 2026-08-11 重构期）。

入库管道把内部代号写到了题干前面（``E2E 23+45=?``），必须剥掉（Q2 拍板：
不保留源信息）。

剥法（与设计文档 §1.2 一致）：``r'^\\s*E2E\\s+'``（不区分大小写）；
命中后从匹配结束位置起留下剩余内容，并 ``lstrip()`` 去前导空白。

不动 ``correct_answer`` / ``options`` / 任何非 content 字段。

零迁移红线：仅 UPDATE ``content``；备份表用 ``CREATE TABLE … AS SELECT``
临时存放 ``id, content``（非 DDL 改动）。

用法::

    cd backend && .venv/Scripts/python.exe ../scripts/data_cleanup/20260811_strip_e2e_prefix.py            # 实际剥离
    cd backend && .venv/Scripts/python.exe ../scripts/data_cleanup/20260811_strip_e2e_prefix.py --dry-run   # 只统计
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from scripts.data_cleanup._common import (  # noqa: E402
    append_log,
    build_log_block,
    get_engine,
    utc_isoformat,
)


BACKUP_TABLE: str = "questions_content_backup_20260811"

#: 兼容 Python 正则：不区分大小写 + 任意前导空白 + E2E + 至少一个空白。
E2E_PREFIX_RE: "re.Pattern[str]" = re.compile(r"^\s*E2E\s+", re.IGNORECASE)


def _strip_e2e(content: Optional[str]) -> Optional[str]:
    """剥掉 E2E 前缀（无命中原样返回）。"""
    if not content:
        return content
    return E2E_PREFIX_RE.sub("", content).lstrip()


def _scan_all_contents(conn) -> List[Dict[str, Any]]:
    """全表扫描；Python 侧正则判定（跨方言稳）。"""
    rows = conn.execute(text("SELECT id, content FROM questions")).mappings().all()
    return [dict(row) for row in rows]


def _backup_table_sql() -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} AS "
        "SELECT id, content FROM questions"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="剥离题干里的 E2E 内部代号前缀")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args(argv)

    engine = get_engine()
    stamp = utc_isoformat(datetime.now(timezone.utc))

    hits: List[Dict[str, Any]] = []
    with engine.begin() as conn:
        if not args.dry_run:
            conn.execute(text(_backup_table_sql()))

        for row in _scan_all_contents(conn):
            original = row.get("content") or ""
            new_content = _strip_e2e(original)
            if new_content != original:
                hits.append({"id": str(row["id"]), "before": original, "after": new_content})

        for entry in hits:
            if args.dry_run:
                break  # dry-run 不写库
            conn.execute(
                text(
                    "UPDATE questions SET content = :after "
                    "WHERE id = :id"
                ),
                {"after": entry["after"], "id": entry["id"]},
            )

    # 全表行数通过独立轻量查询拿（连接已归还，简单计数不会重）
    with engine.connect() as meta_conn:
        total_rows = meta_conn.execute(text("SELECT COUNT(*) FROM questions")).scalar_one()

    log_block = build_log_block(
        "阶段 B · E2E 前缀剥离",
        [
            ("脚本", "scripts/data_cleanup/20260811_strip_e2e_prefix.py"),
            ("时间", stamp),
            ("备份表", f"{BACKUP_TABLE}（{'已跳过' if args.dry_run else '已创建'}）"),
            ("模式", "dry-run" if args.dry_run else "实际执行"),
            ("扫描", f"questions 全表 {int(total_rows)} 行"),
            ("命中剥离", f"{len(hits)} 行"),
            ("不动字段", "correct_answer、options"),
            ("正则", r"^\s*E2E\s+ (re.IGNORECASE)"),
        ],
    )
    append_log(log_block)

    print(
        f"[{'dry-run' if args.dry_run else '剥离'}] 阶段 B 完成："
        f"全表 {int(total_rows)} 行扫描，命中 E2E 前缀 {len(hits)} 行"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""P0-C · 人工标注完成（或放弃）后跑：标不上 + 不完整归档（T4 · 收尾）。

口径（与设计文档 §1.3 一致）：

* 标不上：``status='active' AND kp_id IS NULL AND kp_verified=0``。
* 题型不完整：复用 ``judge_engine.is_l1_safe`` 复核 —— 该函数是 L1 安全池
  唯一真相源。同时覆盖「缺答案」「复合多小问」「选择题少选项」
  「非支持题型」等所有条件（参考 ``judge_engine.is_l1_safe`` docstring）。
* 仅归档「标不上 + 题型不完整」的题；标不上但题型完整的题**保留**
  ``active``，靠 ``attributable_only=False`` 进宽松池（不进掌握度，可
  抽题）。

零迁移红线：仅 UPDATE ``status``，不引入 schema 改动。

用法::

    cd backend && .venv/Scripts/python.exe ../scripts/data_cleanup/20260811_archive_unresolvable.py            # 实际归档
    cd backend && .venv/Scripts/python.exe ../scripts/data_cleanup/20260811_archive_unresolvable.py --dry-run   # 只统计
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from app.models.content import QUESTION_STATUS_ACTIVE, QUESTION_STATUS_ARCHIVED  # noqa: E402

from scripts.data_cleanup._common import (  # noqa: E402
    append_log,
    build_log_block,
    get_engine,
    utc_isoformat,
)


def _scan_unresolved(conn) -> List[Dict[str, Any]]:
    """拉所有「标不上 + active」的题（含 kp_verified=0 的子集）。"""
    rows = conn.execute(
        text(
            """
            SELECT id, type, options, kp_verified
            FROM questions
            WHERE status = :active AND (kp_id IS NULL OR kp_verified = 0)
            """
        ),
        {"active": QUESTION_STATUS_ACTIVE},
    ).mappings().all()
    return [dict(row) for row in rows]


def _normalize_raw_options(raw: Any) -> Any:
    """把 JSON 列读出的字符串还原。"""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None
    return raw


def _is_unresolvable(row: Dict[str, Any]) -> bool:
    """标不上 + 题型不完整 → 归档。

    题型完整则保留 ``active``（即便没有 kp），进宽松池。不完整判据复用
    ``judge_engine.is_l1_safe`` 的对偶：``not is_l1_safe`` 即「不可被
    L1 自动判分」，这等价于「题型不完整 / 不支持」。

    ⚠️ 不动 ``correct_answer`` —— 本函数只用 ``is_l1_safe`` 的判定口径，
    不直接读 Answer 表，避免对答案列加 IO。
    """
    from app.services.judge_engine import is_l1_safe

    return not is_l1_safe(row.get("type"), _normalize_raw_options(row.get("options")), None)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="标不上 + 不完整的题归档")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args(argv)

    engine = get_engine()
    stamp = utc_isoformat(datetime.now(timezone.utc))

    to_archive: List[str] = []
    total_unresolved = 0
    with engine.begin() as conn:
        unresolved = _scan_unresolved(conn)
        total_unresolved = len(unresolved)
        for row in unresolved:
            if _is_unresolvable(row):
                to_archive.append(str(row["id"]))

        if to_archive and not args.dry_run:
            placeholders = ", ".join(f":id{i}" for i in range(len(to_archive)))
            params = {f"id{i}": qid for i, qid in enumerate(to_archive)}
            params["archived"] = QUESTION_STATUS_ARCHIVED
            conn.execute(
                text(
                    f"UPDATE questions SET status = :archived, "
                    f"updated_at = :stamp WHERE id IN ({placeholders})"
                ),
                {**params, "stamp": stamp},
            )

    log_block = build_log_block(
        "阶段 C-2 · 标不上 + 不完整归档",
        [
            ("脚本", "scripts/data_cleanup/20260811_archive_unresolvable.py"),
            ("时间", stamp),
            ("模式", "dry-run" if args.dry_run else "实际执行"),
            ("标不上扫描", f"{total_unresolved} 题（status=active & kp_id IS NULL / kp_verified=0）"),
            ("命中归档（标不上 + 不完整）", f"{len(to_archive)} 题"),
            ("保留 active（标不上但题型完整）", f"{total_unresolved - len(to_archive)} 题，进宽松池"),
        ],
    )
    append_log(log_block)

    print(
        f"[{'dry-run' if args.dry_run else '归档'}] 阶段 C-2 完成："
        f"标不上 {total_unresolved} 题，命中归档 {len(to_archive)} 题"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""P0 · N0.2 全库残缺选择题批量归档（2026-08-12）。

判定口径严格复用 :func:`app.services.importers.quality_gate.validate_choice_options`
（不各自写 SQL ``LIKE '%"D"%'``，与设计 §1.1 D1 单点真相一致）：

``残缺选择题 ⟺ type='选择' 且 set(option_keys(options)) != {A,B,C,D}``

处置流程：
1. ``SELECT`` 全部 ``active`` 选择题，Python 侧逐题判定；
2. 双保险跳过 :data:`OPTIONS_BACKUP_TABLE` 中已补 D 的 id（D6 顺序铁律兜底）；
3. ``--expect N`` 量级熔断：实际命中与期望偏差 > 5% 即 abort（exit 2）并打印明细，
   绝不静默执行（设计 §1.1 D3）；
4. 执行：① 先把命中行拷入备份表 :data:`REMOVED_BACKUP_TABLE`
   （reason=``residual_choice_missing_abcd``，NOT EXISTS 去重）② 再
   ``UPDATE questions SET status='archived' WHERE id IN (...)``；
5. 导出 ``docs/residual-choice-removed-20260812.csv``
   （列序对齐既有 ``removed-questions-20260812.csv``：
   ``question_id, old_status, old_kp_id, reason, removed_at``）。

⚠️ 顺序铁律（D6）：**必须**在 ``20260812_fix_screenshot_four.py`` 之后运行，
否则那 3 道待补 D 题会被先归档。

用法::

    cd backend && .venv/Scripts/python.exe scripts/data_cleanup/20260812_archive_residual_choices.py --dry-run --expect 312   # 先核对命中数
    cd backend && .venv/Scripts/python.exe scripts/data_cleanup/20260812_archive_residual_choices.py --expect 312            # 实际归档
    cd backend && .venv/Scripts/python.exe scripts/data_cleanup/20260812_archive_residual_choices.py --expect 312 --limit 50 # 小批量试跑

退出码：成功 0；量级熔断 abort 2；脚本错误 1。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import exc, text  # noqa: E402

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.content import QUESTION_STATUS_ACTIVE, QUESTION_STATUS_ARCHIVED  # noqa: E402
from app.services.importers.quality_gate import validate_choice_options  # noqa: E402
from scripts.data_cleanup._common import (  # noqa: E402
    append_log,
    build_log_block,
    get_engine,
)
from scripts.data_cleanup._residual_common import (  # noqa: E402
    OPTIONS_BACKUP_TABLE,
    REMOVED_BACKUP_TABLE,
    REASON_RESIDUAL_CHOICE_MISSING_ABCD,
    create_backup_tables,
    ensure_removed_backup,
    export_removed_csv,
    resolve_uuid_prefix,
    utc_isoformat,
)

#: 项目根（backend/ 的上一级），CSV 落 ``docs/``。
_PROJECT_ROOT = _BACKEND_ROOT.parent
_REMOVED_CSV = _PROJECT_ROOT / "docs" / "residual-choice-removed-20260812.csv"

#: 量级熔断阈值：实际命中与 ``--expect`` 偏差 > 5% 即 abort。
EXPECT_TOLERANCE: float = 0.05


def _parse_options(raw: Any) -> Any:
    """把库里读出的 options 原始值解析成 dict/list（解析不了就原样返回）。"""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        import json as _json

        try:
            return _json.loads(text)
        except (TypeError, ValueError):
            return raw
    return raw


def _patched_ids(conn) -> set[str]:
    """已补 D 的 id 集合（OPTIONS_BACKUP_TABLE），双保险跳过。

    该表由 T02（``20260812_fix_screenshot_four.py``）创建。若本脚本在 T02 之前
    被单独执行（例如 ``--expect`` 量级熔断自检），表还不存在，此时按「无已补 D」
    处理（返回空集），让后续扫描/熔断逻辑正常跑，而不是因缺表直接崩（exit 1）。
    """
    try:
        rows = conn.execute(
            text(f"SELECT id FROM {OPTIONS_BACKUP_TABLE}")
        ).mappings().all()
    except exc.OperationalError:
        return set()
    return {str(r["id"]) for r in rows}


def _scan_candidates(conn) -> List[Dict[str, Any]]:
    """拉回所有 active 选择题（id, status, kp_id, options）。"""
    rows = conn.execute(
        text(
            """
            SELECT id, status, kp_id, options
            FROM questions
            WHERE status = :active AND type = :choice
            """
        ),
        {"active": QUESTION_STATUS_ACTIVE, "choice": "选择"},
    ).mappings().all()
    return [dict(r) for r in rows]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="N0.2 全库残缺选择题批量归档")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument(
        "--expect",
        type=int,
        default=None,
        help="期望命中数；实际命中与期望偏差 > 5%% 即 abort（exit 2）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制实际归档/统计数量（小批量试跑）",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="CSV 输出目录（默认 backend/../docs）。CI/测试可指向临时目录隔离产物",
    )
    args = parser.parse_args(argv)

    engine = get_engine()
    stamp = utc_isoformat(datetime.now(timezone.utc))

    # CSV 落盘路径：给了 --out-dir 用覆盖值；否则沿用模块级常量（指向真实 docs/，
    # 与既有测试 monkeypatch 兼容）。
    removed_csv = (
        Path(args.out_dir) / "residual-choice-removed-20260812.csv"
        if args.out_dir
        else _REMOVED_CSV
    )

    with engine.begin() as conn:
        # 0. 双保险依赖的 options 备份表必须先存在（幂等建表，零迁移）。
        #    否则裸跑 T03（未先跑 T02）时 _patched_ids 会查不存在的表而报错。
        if not args.dry_run:
            create_backup_tables(conn)

        # 1. 扫描候选题
        candidates = _scan_candidates(conn)
        patched = _patched_ids(conn) if not args.dry_run else set()

        # 2. Python 侧判定（严格复用 validate_choice_options，不传答案）
        to_archive: List[Dict[str, Any]] = []
        for row in candidates:
            qid = str(row["id"])
            # 双保险：已补 D 的 id 跳过（即便它仍在 active 池里）
            if qid in patched:
                continue
            if not validate_choice_options(_parse_options(row["options"])):
                to_archive.append(
                    {
                        "id": qid,
                        "old_status": row.get("status") or QUESTION_STATUS_ACTIVE,
                        "old_kp_id": row.get("kp_id"),
                        "removed_at": stamp,
                    }
                )

        scanned = len(candidates)
        matched = len(to_archive)

        # 3. 量级熔断
        if args.expect is not None:
            expected = args.expect
            if expected <= 0:
                print(f"[熔断] --expect 必须为正数，收到 {expected}")
                return 2
            deviation = abs(matched - expected) / expected
            if deviation > EXPECT_TOLERANCE:
                print(
                    f"[熔断] 实际命中 {matched} 与期望 {expected} 偏差 "
                    f"{deviation:.1%} > {EXPECT_TOLERANCE:.0%}，中止执行（exit 2）"
                )
                print(f"  命中明细（前 20）：{[r['id'] for r in to_archive[:20]]}")
                return 2

        # 4. 限制数量（小批量试跑）
        if args.limit is not None and args.limit >= 0:
            to_archive = to_archive[: args.limit]
            matched = len(to_archive)

        # 5. 执行：先备份，再归档
        if to_archive and not args.dry_run:
            create_backup_tables(conn)
            ensure_removed_backup(conn, REASON_RESIDUAL_CHOICE_MISSING_ABCD, to_archive)
            placeholders = ", ".join(f":id{i}" for i in range(len(to_archive)))
            params = {f"id{i}": r["id"] for i, r in enumerate(to_archive)}
            params["archived"] = QUESTION_STATUS_ARCHIVED
            conn.execute(
                text(
                    f"UPDATE questions SET status = :archived "
                    f"WHERE id IN ({placeholders})"
                ),
                params,
            )

    # 6. 出口 CSV
    if to_archive and not args.dry_run:
        export_removed_csv(removed_csv, to_archive)

    # 7. 日志
    log_block = build_log_block(
        "N0.2 · 全库残缺选择题批量归档",
        [
            ("脚本", "scripts/data_cleanup/20260812_archive_residual_choices.py"),
            ("时间", stamp),
            ("模式", "dry-run" if args.dry_run else "实际执行"),
            ("扫描", f"active 选择题 {scanned} 道"),
            ("命中归档", f"{matched} 道"),
            (
                "期望核对",
                f"--expect {args.expect}" if args.expect is not None else "未设（跳过熔断）",
            ),
            ("跳过的已补D id", f"{len(patched)} 个（双保险）" if patched else "N/A（dry-run）"),
            ("备份表", f"{REMOVED_BACKUP_TABLE}（reason={REASON_RESIDUAL_CHOICE_MISSING_ABCD}）"),
            ("CSV", "（未写出）" if args.dry_run else str(removed_csv)),
        ],
    )
    append_log(log_block)

    print(
        f"[{'dry-run' if args.dry_run else '归档'}] N0.2 完成："
        f"扫描 {scanned} 道 active 选择题，命中归档 {matched} 道"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

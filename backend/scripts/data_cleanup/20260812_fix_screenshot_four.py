"""P0 · N0.1 截图 4 题即时修复（2026-08-12）。

主理人已定位 4 道截图题，按 UUID 前缀处置：

* ``9a4f1461`` 比 40 大、比 60 小、个位是 5 —— 补 D = ``65``（正确 = B）
* ``a3275f03`` 小阳 28 张，小强比小阳多得多 —— 补 D = ``30张``（正确 = C）
* ``855ae9b6`` 妈妈和奶奶共做 13 个包子 —— 补 D = ``5-4``（正确 = C）
* ``9cf5259e`` 要使 1□6 ÷ 6 得数是一位数 —— **逻辑错误**（OCR 误读 ÷ 为 -），无法补 D，直接 ``archive``

处置口径（设计 §3 / PRD N0.1）：
* 补 D：保留原 A/B/C 顺序与内容，仅追加 D 键到末位；``correct_answer`` 指向**绝不改动**；
  写回 ``options`` 用 :func:`_residual_common.dumps_like_ormtype`，与 ORM ``JSONType`` 逐字节一致。
* archive：``status='archived'``，移出抽题池；``correct_answer`` 同样不动。
* 留痕：①备份表 :data:`OPTIONS_BACKUP_TABLE`（仅补 D 的 3 道，记 old/new options）；
  ②UUID ``9cf5259e`` 的归档也进 :data:`REMOVED_BACKUP_TABLE`（reason=``residual_choice_logic_broken``）；
  ③CSV ``docs/residual-choice-patched-20260812.csv``（列：question_id, action, old_d, new_d）。

⚠️ 顺序铁律（D6）：本脚本**必须先于** ``20260812_archive_residual_choices.py`` 运行，
否则这 3 道待补 D 题会被先归档。

用法::

    cd backend && .venv/Scripts/python.exe scripts/data_cleanup/20260812_fix_screenshot_four.py            # 实际执行
    cd backend && .venv/Scripts/python.exe scripts/data_cleanup/20260812_fix_screenshot_four.py --dry-run   # 只统计不写库

退出码：成功 0；命中 UUID 校验失败 / 脚本错误 1。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text  # noqa: E402

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.content import QUESTION_STATUS_ACTIVE, QUESTION_STATUS_ARCHIVED  # noqa: E402
from scripts.data_cleanup._common import (  # noqa: E402
    append_log,
    build_log_block,
    get_engine,
)
from scripts.data_cleanup._residual_common import (  # noqa: E402
    OPTIONS_BACKUP_TABLE,
    REMOVED_BACKUP_TABLE,
    REASON_RESIDUAL_CHOICE_LOGIC_BROKEN,
    REASON_RESIDUAL_CHOICE_PATCH_D,
    create_backup_tables,
    dumps_like_ormtype,
    ensure_options_backup,
    ensure_removed_backup,
    export_patched_csv,
    resolve_uuid_prefix,
    utc_isoformat,
)

#: 项目根（backend/ 的上一级），CSV 落 ``docs/``。
_PROJECT_ROOT = _BACKEND_ROOT.parent
_PATCHED_CSV = _PROJECT_ROOT / "docs" / "residual-choice-patched-20260812.csv"

#: 3 道待补 D 题（前缀 → 补 D 内容）。D 追加末位，正确选项不变。
PATCH_SPECS: Dict[str, str] = {
    "9a4f1461": "65",
    "a3275f03": "30张",
    "855ae9b6": "5-4",
}

#: 1 道逻辑错误题（前缀），直接 archive。
ARCHIVE_SPEC: str = "9cf5259e"


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
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return raw
    return raw


def _build_new_options(parsed: Any, d_value: str) -> Any:
    """在保留原 A/B/C 顺序前提下，把 D 追加到末位。

    兼容 dict / list / list-of-dict 三种物理形态；无法识别形态则按原样 + D 兜底。
    """
    if isinstance(parsed, dict):
        new = dict(parsed)
        new["D"] = d_value
        return new
    if isinstance(parsed, list):
        # list-of-dict：追加 {"key":"D","text":...}
        if parsed and isinstance(parsed[0], dict):
            new = list(parsed)
            new.append({"key": "D", "text": d_value})
            return new
        # 纯 list：按下标映射 A/B/C/D，D 追加末位
        new = list(parsed)
        new.append(d_value)
        return new
    # 兜底：原样包成 dict 追加 D（不应触发，仅防御）
    return {"D": d_value}


def _extract_d(parsed: Any) -> str:
    """取原 options 里若已含 D 的内容（补 D 前应为空）。"""
    if isinstance(parsed, dict):
        return str(parsed.get("D", ""))
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        for item in parsed:
            if str(item.get("key", "")).upper() == "D":
                return str(item.get("text", ""))
    return ""


def _existing_old_d(conn, qid: str) -> Optional[str]:
    """已补 D 的 id 在备份表里的原始 D 值（溯源用，修复 P2 重跑 CSV 污染）。

    若 id 已在 :data:`OPTIONS_BACKUP_TABLE`（非首次执行），直接读备份表
    ``old_options`` 里的 D —— 那是补 D 前的真值（D 键不存在 → 空串）。
    这样重跑脚本也不会把 ``old_d`` 误写成已补上的新 D（``65,65``）。

    若 id 不在备份表（首次执行），返回 ``None``，调用方回退到读当前库。
    """
    row = conn.execute(
        text(f"SELECT old_options FROM {OPTIONS_BACKUP_TABLE} WHERE id = :id"),
        {"id": qid},
    ).mappings().first()
    if row is None:
        return None
    parsed_old = _parse_options(row["old_options"])
    return _extract_d(parsed_old)


def _scan(conn, qid: str) -> Optional[Dict[str, Any]]:
    """拉单题当前 (type, status, options, kp_id)。"""
    row = conn.execute(
        text("SELECT id, type, status, options, kp_id FROM questions WHERE id = :id"),
        {"id": qid},
    ).mappings().first()
    return dict(row) if row else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="N0.1 截图 4 题即时修复（3 补 D + 1 archive）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
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
    patched_csv = (
        Path(args.out_dir) / "residual-choice-patched-20260812.csv"
        if args.out_dir
        else _PATCHED_CSV
    )

    patch_rows: List[Dict[str, Any]] = []   # CSV / options 备份（补 D）
    archive_rows: List[Dict[str, Any]] = []  # CSV（archive）
    removed_backup_rows: List[Dict[str, Any]] = []  # removed 备份表（9cf5259e）

    with engine.begin() as conn:
        # 1. 解析前缀 → 全 UUID（命中 ≠1 即 abort，防误伤）
        resolved_patch: Dict[str, str] = {}
        for prefix in PATCH_SPECS:
            qid = resolve_uuid_prefix(conn, prefix)
            resolved_patch[prefix] = qid
        archive_qid = resolve_uuid_prefix(conn, ARCHIVE_SPEC)

        # 2. 备份表（仅 dry-run 不写）
        if not args.dry_run:
            create_backup_tables(conn)

        # 3. 补 D：保留 A/B/C，D 末位
        for prefix, qid in resolved_patch.items():
            row = _scan(conn, qid)
            if row is None:
                print(f"[跳过] 前缀 {prefix} 解析到的 {qid} 在 questions 中不存在")
                continue
            parsed = _parse_options(row["options"])
            old_options_json = dumps_like_ormtype(parsed) if parsed is not None else None
            d_value = PATCH_SPECS[prefix]
            new_options = _build_new_options(parsed, d_value)
            new_options_json = dumps_like_ormtype(new_options)
            # old_d 溯源：dry-run 不查备份表（表尚未建，且 CSV 不落盘，无需 old_d）；
            # 实跑时优先读备份表历史真值，避免重跑把已补的 D 误写回 old_d（修复 P2 CSV 污染）。
            if args.dry_run:
                old_d = _extract_d(parsed)
            else:
                existing_old_d = _existing_old_d(conn, qid)
                old_d = existing_old_d if existing_old_d is not None else _extract_d(parsed)

            patch_rows.append(
                {
                    "id": qid,
                    "old_options": old_options_json,
                    "new_options": new_options_json,
                    "patched_at": stamp,
                    "reason": REASON_RESIDUAL_CHOICE_PATCH_D,
                    # 仅 CSV 用
                    "old_d": old_d,
                    "new_d": d_value,
                }
            )
            if not args.dry_run:
                conn.execute(
                    text("UPDATE questions SET options = :opts WHERE id = :id"),
                    {"opts": new_options_json, "id": qid},
                )

        # 4. archive：9cf5259e（逻辑错误，无法补 D）
        arch = _scan(conn, archive_qid)
        if arch is not None:
            archive_rows.append(
                {
                    "id": archive_qid,
                    "status": QUESTION_STATUS_ARCHIVED,
                    "action": "archive",
                }
            )
            removed_backup_rows.append(
                {
                    "id": archive_qid,
                    "old_status": arch.get("status") or QUESTION_STATUS_ACTIVE,
                    "old_kp_id": arch.get("kp_id"),
                    "removed_at": stamp,
                }
            )
            if not args.dry_run:
                conn.execute(
                    text("UPDATE questions SET status = :st WHERE id = :id"),
                    {"st": QUESTION_STATUS_ARCHIVED, "id": archive_qid},
                )

        # 5. 落备份表
        if not args.dry_run:
            if patch_rows:
                ensure_options_backup(
                    conn,
                    [
                        {
                            k: v
                            for k, v in r.items()
                            if k in ("id", "old_options", "new_options", "patched_at", "reason")
                        }
                        for r in patch_rows
                    ],
                )
            if removed_backup_rows:
                ensure_removed_backup(
                    conn, REASON_RESIDUAL_CHOICE_LOGIC_BROKEN, removed_backup_rows
                )

    # 6. 出口 CSV（统一列：question_id, action, old_d, new_d）
    csv_rows: List[Dict[str, Any]] = []
    for r in patch_rows:
        csv_rows.append({"id": r["id"], "action": "patch_d", "old_d": r["old_d"], "new_d": r["new_d"]})
    for r in archive_rows:
        csv_rows.append({"id": r["id"], "action": "archive", "status": r["status"]})
    if not args.dry_run:
        export_patched_csv(patched_csv, csv_rows)

    # 7. 日志
    log_block = build_log_block(
        "N0.1 · 截图 4 题即时修复",
        [
            ("脚本", "scripts/data_cleanup/20260812_fix_screenshot_four.py"),
            ("时间", stamp),
            ("模式", "dry-run" if args.dry_run else "实际执行"),
            ("补 D", f"{len(patch_rows)} 道（{', '.join(PATCH_SPECS)} → 末位补 D）"),
            ("归档", f"{len(archive_rows)} 道（{ARCHIVE_SPEC} 逻辑错误）"),
            ("备份表", f"{OPTIONS_BACKUP_TABLE}（补D） / {REMOVED_BACKUP_TABLE}（archive）"),
            ("CSV", "（未写出）" if args.dry_run else str(patched_csv)),
            ("不动字段", "correct_answer"),
        ],
    )
    append_log(log_block)

    print(
        f"[{'dry-run' if args.dry_run else '执行'}] N0.1 完成："
        f"补 D {len(patch_rows)} 道，归档 {len(archive_rows)} 道"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

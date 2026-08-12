"""P0-C · KP 自动打标 + 人工标注导出（T4 · 2026-08-11 重构期）。

阶段 1：从 ``knowledge_points`` 拉全部 L1 知识点名作为关键词表（不取 L2）
做子串匹配，命中率高且无歧义的 ``UPDATE kp_id, kp_verified=1``。
阶段 2：不能自动匹配的导出 CSV —— ``docs/unmapped-human-triage-20260811.csv``，
给运营同学人工标注。
阶段 3（可选）：支持 ``--replay CSV`` 读取人工填好的 CSV 回填 ``kp_id``。

零迁移红线：仅 UPDATE 现有行的 ``kp_id`` / ``kp_verified`` / ``updated_at``，
不引入 schema 改动。

用法::

    # 阶段 1+2：自动打标 + 导出待人工标注
    cd backend && .venv/Scripts/python.exe ../scripts/data_cleanup/20260811_autotag_unmapped.py
    cd backend && .venv/Scripts/python.exe ../scripts/data_cleanup/20260811_autotag_unmapped.py --dry-run

    # 阶段 3：人工标注完成后回填
    cd backend && .venv/Scripts/python.exe ../scripts/data_cleanup/20260811_autotag_unmapped.py --replay ../docs/unmapped-human-triage-20260811.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from app.models.content import QUESTION_STATUS_ACTIVE  # noqa: E402

from scripts.data_cleanup._common import (  # noqa: E402
    append_log,
    build_log_block,
    get_engine,
    utc_isoformat,
)


PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
EXPORT_CSV: Path = PROJECT_ROOT / "docs" / "unmapped-human-triage-20260811.csv"

#: 短关键词 → 知识点名的内置优先表（与知识树脱钩时也能给题目一个挂靠点）。
#: 子串命中即算成功。值就是 ``knowledge_points.name``。
#: 优先级：更具体的关键词排在前面，避免「时分」先于「时」抢命中。
BUILTIN_KEYWORD_HINTS: List[Tuple[str, str]] = [
    ("分数加减", "分数加减法"),
    ("小数加减", "小数加减法"),
    ("整数加减", "整数加减法"),
    ("乘除法", "乘除法运算"),
    ("加减法", "加减法运算"),
    ("元角分", "元角分"),
    ("时分秒", "时分秒"),
    ("周长", "周长"),
    ("面积", "面积"),
    ("小数", "小数"),
    ("分数", "分数"),
    ("除法", "除法"),
    ("乘法", "乘法"),
    ("加法", "加法"),
    ("减法", "减法"),
    ("应用题", "应用题"),
]


def _load_kp_keyword_table(conn) -> List[Tuple[str, uuid.UUID]]:
    """从 ``knowledge_points`` 表拉 (name, id) 作为关键词基础。

    ⚠️ 短关键词会严重模糊，本函数只取实际存在的 name；
    命中歧义时仍由人工标注兜底（阶段 3），不做强匹配。
    """
    rows = conn.execute(text("SELECT id, name FROM knowledge_points")).all()
    result: List[Tuple[str, uuid.UUID]] = []
    for kid, name in rows:
        if not name:
            continue
        result.append((str(name).strip(), kid))
    return result


def _auto_match(content: Optional[str], kp_table: List[Tuple[str, uuid.UUID]]) -> Optional[uuid.UUID]:
    """子串命中：返回首个命中的 ``kp_id``；多关键词同时命中返回 None 让人工判。

    这里不实现「唯一最具体命中」之类的复杂语义，留给运营同学通过 CSV 兜底。
    """
    if not content:
        return None
    matches: List[uuid.UUID] = []
    # 优先看内置 hint，再看知识树同名（同名高优先级在先）
    for kw, name_or_label in [(kw, name) for kw, name in BUILTIN_KEYWORD_HINTS]:
        if kw in content:
            # 在 kp_table 里找 name == name_or_label 的 kp；找不到时丢弃（不影响其它）
            for kp_name, kp_id in kp_table:
                if kp_name == name_or_label:
                    matches.append(kp_id)
                    break
            else:
                continue
            if len(matches) > 1:
                return None  # 多个内置 keyword 同命中——交给人工
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        # 退路：知识树 name 子串直接命中（同名短词不要使用，避免锁死）
        for kp_name, kp_id in kp_table:
            if len(kp_name) >= 3 and kp_name in content:
                return kp_id
    return None


def _scan_unmapped(conn) -> List[Dict[str, Any]]:
    """拉所有 ``status='active' AND kp_id IS NULL AND kp_verified=0`` 题。"""
    rows = conn.execute(
        text(
            """
            SELECT id, type, content
            FROM questions
            WHERE status = :active AND kp_id IS NULL AND kp_verified = 0
            """
        ),
        {"active": QUESTION_STATUS_ACTIVE},
    ).mappings().all()
    return [dict(row) for row in rows]


def _unmapped_active_count(conn) -> int:
    """全库 active 且无 kp 的题数（统计用）。"""
    return int(
        conn.execute(
            text(
                "SELECT COUNT(*) FROM questions WHERE status = :active "
                "AND kp_id IS NULL"
            ),
            {"active": QUESTION_STATUS_ACTIVE},
        ).scalar_one()
    )


def _write_export_csv(rows: List[Dict[str, Any]]) -> Path:
    """导出 ``unmapped-human-triage-20260811.csv``（运营同学人工标注用）。

    列：``question_id, type, stem, suggested_kp, human_kp, note``。
    """
    EXPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with EXPORT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question_id", "type", "stem", "suggested_kp", "human_kp", "note"])
        for r in rows:
            stem = (r.get("content") or "").replace("\n", " ")[:80]
            writer.writerow([str(r["id"]), r.get("type") or "", stem, "", "", ""])
    return EXPORT_CSV


def _replay_from_csv(csv_path: Path, conn, dry_run: bool) -> int:
    """读取人工标注 CSV 回填 ``kp_id``。

    human_kp 列允许两种写法：

    1. ``L1 name`` —— 在 ``knowledge_points`` 里按 name 找 id；
    2. ``uuid:<uuid>`` —— 直接给 UUID。
    留空 / ``SKIP`` 视为跳过该题。
    """
    if not csv_path.exists():
        print(f"[replay] CSV 不存在：{csv_path}", file=sys.stderr)
        return 0

    kp_index: Dict[str, uuid.UUID] = {
        str(name).strip(): kid
        for kid, name in conn.execute(text("SELECT id, name FROM knowledge_points")).all()
        if name
    }

    updated = 0
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            qid_raw = (row.get("question_id") or "").strip()
            human_raw = (row.get("human_kp") or "").strip()
            if not qid_raw or not human_raw or human_raw.upper() == "SKIP":
                continue
            try:
                qid = uuid.UUID(qid_raw)
            except (TypeError, ValueError):
                continue

            if human_raw.startswith("uuid:"):
                try:
                    kp_uuid = uuid.UUID(human_raw.split(":", 1)[1])
                except (TypeError, ValueError):
                    continue
            else:
                kp_uuid = kp_index.get(human_raw)
                if kp_uuid is None:
                    continue

            if not dry_run:
                conn.execute(
                    text(
                        "UPDATE questions SET kp_id = :kp, kp_verified = 1 "
                        "WHERE id = :id"
                    ),
                    {"kp": str(kp_uuid), "id": str(qid)},
                )
            updated += 1
    return updated


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="KP 自动打标 + 人工标注导出")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库 / 写 CSV")
    parser.add_argument("--replay", type=str, default=None, help="回填人工标注 CSV")
    args = parser.parse_args(argv)

    engine = get_engine()
    stamp = utc_isoformat(datetime.now(timezone.utc))

    if args.replay:
        # 阶段 3：回填模式
        replay_path = Path(args.replay).resolve()
        with engine.begin() as conn:
            updated = _replay_from_csv(replay_path, conn, dry_run=args.dry_run)
        log_block = build_log_block(
            "阶段 C-3 · 人工标注回填",
            [
                ("脚本", "scripts/data_cleanup/20260811_autotag_unmapped.py --replay"),
                ("时间", stamp),
                ("CSV", str(replay_path)),
                ("模式", "dry-run" if args.dry_run else "实际执行"),
                ("回填 kp_id", f"{updated} 题"),
            ],
        )
        append_log(log_block)
        print(
            f"[{'dry-run' if args.dry_run else 'replay'}] 阶段 C 回填完成："
            f"读 CSV {updated} 条有效标注"
        )
        return 0

    # 阶段 1+2：自动打标 + 导出待标注 CSV
    auto_tagged = 0
    export_rows: List[Dict[str, Any]] = []
    total_unmapped: int = 0
    with engine.begin() as conn:
        kp_table = _load_kp_keyword_table(conn)
        candidates = _scan_unmapped(conn)
        total_unmapped = _unmapped_active_count(conn)
        for row in candidates:
            match = _auto_match(row.get("content"), kp_table)
            if match is not None:
                if not args.dry_run:
                    conn.execute(
                        text(
                            "UPDATE questions SET kp_id = :kp, kp_verified = 1 "
                            "WHERE id = :id"
                        ),
                        {"kp": str(match), "id": str(row["id"])},
                    )
                auto_tagged += 1
            else:
                export_rows.append(row)

    exported_csv: Optional[Path] = None
    if not args.dry_run and export_rows:
        exported_csv = _write_export_csv(export_rows)

    log_block = build_log_block(
        "阶段 C · KP 自动打标 + 人工标注导出",
        [
            ("脚本", "scripts/data_cleanup/20260811_autotag_unmapped.py"),
            ("时间", stamp),
            ("模式", "dry-run" if args.dry_run else "实际执行"),
            ("active 无 kp 全库", f"{total_unmapped} 题"),
            ("本轮候选扫描", f"{len(candidates)} 题"),
            ("自动打标", f"{auto_tagged} 题"),
            ("待人工标注", f"{len(export_rows)} 题"),
            ("导出 CSV", str(exported_csv) if exported_csv else "（已跳过 dry-run）"),
            ("内置关键词数", len(BUILTIN_KEYWORD_HINTS)),
        ],
    )
    append_log(log_block)

    print(
        f"[{'dry-run' if args.dry_run else '执行'}] 阶段 C 完成："
        f"自动打标 {auto_tagged} 题，待人工标注 {len(export_rows)} 题"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

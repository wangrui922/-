"""修复或下架存量题库中的公式压扁题。

原则：
- 只有能由题干、选项和答案共同验算的题才修复；
- 无法确认原公式的题只做可逆归档，不猜题；
- 正式写库前使用 SQLite backup API 生成一致性备份；
- 每道原记录写入独立审计表，脚本可重复执行。

用法::

    python scripts/data_cleanup/20260819_repair_flattened_formulas.py --db dev.db --dry-run
    python scripts/data_cleanup/20260819_repair_flattened_formulas.py --db dev.db
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, NamedTuple, Optional


BACKUP_TABLE = "questions_formula_cleanup_backup_20260819"


class FormulaRepair(NamedTuple):
    old_content: str
    new_content: str
    reason: str


class FormulaArchive(NamedTuple):
    old_content: str
    reason: str


REPAIRS: Dict[str, FormulaRepair] = {
    "5731304f-ca9d-5e18-a5ae-d8af8260e8ba": FormulaRepair(
        old_content=(
            "古希腊学者认为：如果一个数恰好等于它的所有因数（本身除外）相加之和，"
            "那么这个数就是“完全数”。例如6有4个因数1，2，3，6，除本身6以外，"
            "还有1，2，3三个因数。6123=++，所以6是“完全数”。下面的数中是“完全数”的是（"
        ),
        new_content=(
            "古希腊学者认为：如果一个数恰好等于它的所有因数（本身除外）相加之和，"
            "那么这个数就是“完全数”。例如，6 有 4 个因数：1、2、3、6。"
            "除本身 6 以外，1 + 2 + 3 = 6，所以 6 是“完全数”。"
            "下面的数中是“完全数”的是（ ）。"
        ),
        reason="等式顺序可由上下文唯一恢复为 1 + 2 + 3 = 6",
    ),
    "a1928c6f-ca46-5ad1-8e2c-fa7d5de4fba1": FormulaRepair(
        old_content="若11114nnn＝++（n为非0自然数），则n＝（",
        new_content="若 1/n + 1/n + 1/n = 1/4（n 为非 0 自然数），则 n =（ ）。",
        reason="三个同分母项相加等于 1/4，解得 n=12，与选项 C 和标准答案一致",
    ),
    "2949e748-7f40-5c50-ae3a-61e6199af16c": FormulaRepair(
        old_content="当a是小于1且大于0的数时，3a5()3a5",
        new_content="当 a 是大于 0 且小于 1 的数时，a × 3/5（ ）a ÷ 3/5。",
        reason="两侧同为 3/5，乘除关系与标准答案“小于”一致",
    ),
}


ARCHIVES: Dict[str, FormulaArchive] = {
    "4ceed29e-09ab-548e-ba12-1291dfaa49ae": FormulaArchive(
        old_content="（）里填（）时，式子()1371113能进行简便计算",
        reason="分数结构和运算顺序均丢失，无法依据选项唯一恢复",
    ),
    "eeeef13a-3e58-56a4-a169-0e2d1f286edd": FormulaArchive(
        old_content=(
            "下面说法正确的有（）个。①计算5332533285858855+++=+++，"
            "可以用到加法交换律和结合律。②一个数既是8的倍数，又是48的因数，这个数一定是16。"
            "③10以内的合数有：2，4，6，8，10。④计算2154+时，分母不同，也就是分数单位不同，"
            "要先通分，然后按照同分母分数加减法进行计算"
        ),
        reason="两个分数算式结构严重丢失，无法确认四个说法的真伪",
    ),
    "83a27613-6156-5558-9b9a-906dc50b0139": FormulaArchive(
        old_content="若a、b、c、d都大于0，且3134432abcd===，则a、b、c、d中最小的是()",
        reason="四个量的乘除系数顺序丢失，无法依据答案唯一恢复",
    ),
}


def _create_audit_table(con: sqlite3.Connection) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} (
            id TEXT PRIMARY KEY,
            old_content TEXT NOT NULL,
            old_status TEXT NOT NULL,
            old_source_tag TEXT,
            old_ship_ready INTEGER,
            action TEXT NOT NULL,
            new_content TEXT,
            reason TEXT NOT NULL,
            cleaned_at TEXT NOT NULL
        )
        """
    )


def _backup_row(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    action: str,
    new_content: Optional[str],
    reason: str,
    cleaned_at: str,
) -> None:
    con.execute(
        f"""
        INSERT OR IGNORE INTO {BACKUP_TABLE}
        (id, old_content, old_status, old_source_tag, old_ship_ready,
         action, new_content, reason, cleaned_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["id"],
            row["content"],
            row["status"],
            row["source_tag"],
            row["ship_ready"],
            action,
            new_content,
            reason,
            cleaned_at,
        ),
    )


def apply_cleanup(con: sqlite3.Connection) -> Dict[str, int]:
    """在一个事务内应用清洗计划，返回本次实际变更数量。"""
    con.row_factory = sqlite3.Row
    cleaned_at = datetime.now(timezone.utc).isoformat()
    repaired = 0
    archived = 0

    with con:
        _create_audit_table(con)

        for qid, patch in REPAIRS.items():
            row = con.execute(
                "SELECT id, content, status, source_tag, ship_ready "
                "FROM questions WHERE id=?",
                (qid,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"待修复题不存在：{qid}")
            if row["content"] == patch.new_content:
                continue
            if row["content"] != patch.old_content:
                raise RuntimeError(f"待修复题原文与审计基线不一致：{qid}")
            _backup_row(
                con,
                row,
                action="repair",
                new_content=patch.new_content,
                reason=patch.reason,
                cleaned_at=cleaned_at,
            )
            con.execute(
                "UPDATE questions SET content=? WHERE id=?",
                (patch.new_content, qid),
            )
            repaired += 1

        for qid, patch in ARCHIVES.items():
            row = con.execute(
                "SELECT id, content, status, source_tag, ship_ready "
                "FROM questions WHERE id=?",
                (qid,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"待归档题不存在：{qid}")
            if row["status"] == "archived":
                continue
            if row["content"] != patch.old_content:
                raise RuntimeError(f"待归档题原文与审计基线不一致：{qid}")
            _backup_row(
                con,
                row,
                action="archive",
                new_content=None,
                reason=patch.reason,
                cleaned_at=cleaned_at,
            )
            con.execute(
                "UPDATE questions "
                "SET status='archived', source_tag='red', ship_ready=0 "
                "WHERE id=?",
                (qid,),
            )
            archived += 1

    return {"repaired": repaired, "archived": archived}


def backup_database(db_path: Path) -> Path:
    """使用 SQLite 在线备份 API 生成包含 WAL 内容的一致性备份。"""
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}-before-formula-cleanup-{stamp}.db"
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="修复或下架公式压扁题")
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument("--dry-run", action="store_true", help="只核对目标，不写库")
    args = parser.parse_args(argv)

    db_path = Path(args.db).resolve()
    if not db_path.is_file():
        print(f"数据库不存在：{db_path}")
        return 1

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        missing = [
            qid
            for qid in (*REPAIRS.keys(), *ARCHIVES.keys())
            if con.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone() is None
        ]
        if missing:
            print("缺少目标题：" + "、".join(missing))
            return 1
        if args.dry_run:
            print(f"核对通过：计划修复 {len(REPAIRS)} 道，下架 {len(ARCHIVES)} 道；未写库。")
            return 0
    finally:
        con.close()

    backup_path = backup_database(db_path)
    con = sqlite3.connect(db_path)
    try:
        result = apply_cleanup(con)
    finally:
        con.close()
    print(
        f"完成：修复 {result['repaired']} 道，下架 {result['archived']} 道。"
        f"一致性备份：{backup_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

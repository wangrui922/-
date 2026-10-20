#!/usr/bin/env python
"""生产库 schema 对账（2026-10-20，幂等，只读 + 可选补建）。

**为什么需要它**：生产库是 SQLite，**没有迁移台账表**（不像 Alembic 那样
能查「已应用到哪一版」）。ORM 的 ``create_all`` **只建缺失表、绝不 ALTER
已有表**，而本项目还有一批**无 ORM 模型、由 raw SQL 维护的表**
（``review_cards`` / ``review_logs`` 由 FSRS 模块维护，见
``app/services/fsrs_scheduler.py``）——``create_all`` 永远不会建它们。

2026-10-20 实测踩坑：生产库（2026-09-12 版本）缺 ``review_cards``，
``GET /api/progress/adaptive-plan`` 直接 500/400，而 ``/api/health``
仍 200——**只有打真实业务链路才暴露**。

本脚本做三件事：
    1. 对账：ORM 期望的表 + 迁移文件里定义的表 vs 生产库实际有的表；
    2. 对账：ORM 期望的**列** vs 生产库实际有的列（缺列逐个报出来，
       对应「铁律 4」——跨版本部署必须做列级 diff）；
    3. ``--fix``：把**迁移文件里定义但库里没有的表**用对应 ``*.sql`` 幂等
       补建（``CREATE TABLE IF NOT EXISTS``），并补缺列。

⚠️ **只补「迁移文件里明确写了 CREATE TABLE IF NOT EXISTS」的表**——
不猜结构、不手写 DDL。缺列只**报不改**（列定义涉及回填语义，必须人工
确认，见 ``migrations/0NN_*_sqlite.sql`` 里的 UPDATE 段）。

用法（服务器容器内）：
    python /app/scripts/schema_reconcile.py            # 只对账，报告
    python /app/scripts/schema_reconcile.py --fix      # 对账 + 补建缺表
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

#: 容器内 backend 根目录
APP_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = APP_ROOT / "migrations"
# 同目录模块（``_sqlite_url``）——直接 ``python scripts/xxx.py`` 运行时
# ``scripts/`` 不在 sys.path，要先补上
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def db_path_from_url(url: str) -> str:
    """从 ``DATABASE_URL`` 解析 SQLite 文件路径（实现见 ``_sqlite_url``）。"""
    from _sqlite_url import sqlite_path_from_url

    return sqlite_path_from_url(url)


# ---------------------------------------------------------------- 迁移解析
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)", re.IGNORECASE
)


def parse_migration_tables() -> dict[str, Path]:
    """从 ``migrations/*_sqlite.sql`` 解析出「表名 → 定义所在文件」。

    ⚠️ 只认 **SQLite 方言** 的迁移文件（``*_sqlite.sql``）——PostgreSQL
    方言的 SQL 语法与 SQLite 不兼容（``SERIAL`` / ``TIMESTAMPTZ`` 等）。
    """
    out: dict[str, Path] = {}
    if not MIGRATIONS_DIR.exists():
        return out
    for path in sorted(MIGRATIONS_DIR.glob("*_sqlite.sql")):
        text = path.read_text(encoding="utf-8")
        for m in _CREATE_TABLE_RE.finditer(text):
            name = m.group(2).lower()
            # 010 里 ``students_grade_1_12`` 是中间表名（随后 RENAME 成
            # ``students``），不是最终表——不计入期望集。
            if name.endswith("_1_12"):
                continue
            out.setdefault(name, path)
    return out


def orm_expected_tables() -> set[str]:
    """ORM 模型定义的表集合。"""
    sys.path.insert(0, str(APP_ROOT))
    from app.models.base import Base

    import app.models  # noqa: F401  —— 触发所有模型注册

    return {t.lower() for t in Base.metadata.tables}


def orm_expected_columns() -> dict[str, set[str]]:
    """ORM 模型期望的「表 → 列集合」。"""
    sys.path.insert(0, str(APP_ROOT))
    from app.models.base import Base

    import app.models  # noqa: F401

    out: dict[str, set[str]] = {}
    for name, table in Base.metadata.tables.items():
        out[name.lower()] = {c.name for c in table.columns}
    return out


# ---------------------------------------------------------------- 对账
def actual_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0].lower()
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def actual_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1].lower() for r in conn.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return set()


def reconcile(conn: sqlite3.Connection) -> tuple[list[str], list[str], dict[str, set[str]]]:
    """返回 (缺表, 缺列明细, 期望表集)。"""
    want_tables = orm_expected_tables() | set(parse_migration_tables())
    have = actual_tables(conn)

    missing_tables = sorted(want_tables - have)

    missing_cols: dict[str, set[str]] = {}
    for table, cols in orm_expected_columns().items():
        if table not in have:
            continue  # 缺表已单独报，不重复报列
        actual = actual_columns(conn, table)
        gap = {c for c in cols if c.lower() not in actual}
        if gap:
            missing_cols[table] = gap

    return missing_tables, missing_cols, want_tables


def fix_missing_tables(conn: sqlite3.Connection, missing: list[str]) -> list[str]:
    """用对应迁移文件的 SQL 幂等补建缺表。

    只执行**整份**迁移文件（``executescript``）——迁移文件本身是幂等
    的（``CREATE TABLE IF NOT EXISTS`` / ``INSERT OR IGNORE``），但一份
    文件里可能有 ``ALTER TABLE`` 语句对已存在的表会报错，所以逐个文件
    单独执行并捕获错误，不影响其它表。
    """
    fixed: list[str] = []
    src = parse_migration_tables()
    for table in missing:
        path = src.get(table)
        if path is None:
            print(f"[fix] 跳过 {table}：找不到对应的迁移文件")
            continue
        try:
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.commit()
            fixed.append(table)
            print(f"[fix] 已补建 {table}（来自 {path.name}）")
        except sqlite3.Error as exc:
            print(f"[fix] {table} 补建失败（{exc}）——可能含 ALTER 语句，需人工处理")
    return fixed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="补建迁移文件里定义但库里没有的表")
    args = ap.parse_args()

    import os

    db_path = db_path_from_url(
        os.environ.get("DATABASE_URL", "sqlite:////app/data/coach.db")
    )
    if not Path(db_path).exists():
        print(f"[fatal] 数据库不存在：{db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        missing_tables, missing_cols, want = reconcile(conn)
        have = actual_tables(conn)

        print(f"=== schema 对账：{db_path} ===")
        print(f"生产库表数：{len(have)}   期望表数：{len(want)}")

        print("\n--- 缺表 ---")
        if missing_tables:
            for t in missing_tables:
                src = parse_migration_tables().get(t)
                print(f"  MISSING  {t}" + (f"  (迁移文件 {src.name})" if src else "  (无 ORM 模型 / 无迁移文件)"))
        else:
            print("  （无）")

        print("\n--- 缺列（对应「铁律 4：跨版本部署必做列级 diff」）---")
        if missing_cols:
            for t, cols in sorted(missing_cols.items()):
                print(f"  {t}: 缺 {sorted(cols)}")
            print("  ⚠️ 缺列只报告不改：列定义涉及回填语义，须人工照抄 migrations/0NN_*_sqlite.sql 的 UPDATE 段")
        else:
            print("  （无）")

        if args.fix and missing_tables:
            print("\n--- 补建缺表 ---")
            fixed = fix_missing_tables(conn, missing_tables)
            print(f"  已补建 {len(fixed)} 张：{fixed}")

        # 复检
        after_missing, after_cols, _ = reconcile(conn)
        if after_missing:
            print(f"\n[warn] 仍有缺表：{after_missing}")
        if after_cols:
            print(f"\n[warn] 仍有缺列：{ {k: sorted(v) for k, v in after_cols.items()} }")
        if not after_missing and not after_cols:
            print("\nSCHEMA_OK")
            return 0
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

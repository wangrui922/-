"""按 ``DATABASE_URL`` 方言初始化数据库。

分流策略：
    * 以 ``sqlite`` 开头  → 使用 ORM ``Base.metadata.create_all(engine)`` 建表（便携）；
    * 否则(PostgreSQL)    → 读取并执行 ``migrations/001_init.sql``。

用法::

    DATABASE_URL=sqlite:///./dev.db python backend/scripts/init_db.py
    DATABASE_URL=postgresql+psycopg://coach:coach@localhost:5433/coach \\
        python backend/scripts/init_db.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 让脚本能 import app：把 backend/ 目录(本文件的祖父目录)加入 sys.path。
# 目录结构: <root>/backend/scripts/init_db.py  →  BACKEND_DIR = <root>/backend
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import DATABASE_URL, engine  # noqa: E402
from app.models import Base  # noqa: E402


def _init_sqlite() -> None:
    """SQLite：用 ORM 元数据直接建表。"""
    Base.metadata.create_all(engine)
    print("[init_db] SQLite 模式：使用 Base.metadata.create_all 建表完成")


def _init_postgres() -> None:
    """PostgreSQL：读取并执行迁移文件。"""
    sql_path = BACKEND_DIR / "migrations" / "001_init.sql"
    if not sql_path.exists():
        raise FileNotFoundError(f"找不到迁移文件: {sql_path}")

    sql = sql_path.read_text(encoding="utf-8")
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    print(f"[init_db] PostgreSQL 模式：执行迁移文件 {sql_path.name} 完成（{len(statements)} 条语句）")


def _count_tables() -> int:
    """返回当前库中用户表数量（排除系统表）。"""
    return len(inspect(engine).get_table_names())


def main() -> None:
    url = (DATABASE_URL or os.getenv("DATABASE_URL", "")).strip()
    if not url:
        url = "sqlite:///./dev.db"
        print(f"[init_db] 未检测到 DATABASE_URL，回退到 {url}")

    if url.lower().startswith("sqlite"):
        _init_sqlite()
    else:
        _init_postgres()

    table_count = _count_tables()
    print(f"[init_db] 建表成功！当前数据库表数量: {table_count}")
    print(f"[init_db] DATABASE_URL = {url}")


if __name__ == "__main__":
    main()

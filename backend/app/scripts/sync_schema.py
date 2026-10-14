"""幂等 schema 同步脚本：把真实库对齐到 ORM 模型定义（修复历史漂移）。

背景：
    dev.db 是 gitignore 的历史库，app 启动不调 ``create_all``，模型演进后库不会
    自愈。本脚本补上「模型有、库没有」的表与列，使练习结算 / 诊 / 报整条链路可跑。

设计要点（与 ``seed_db.py`` 同风格）：
    * 直接 ``import app.db.engine``，复用应用既有的**方言感知**引擎；
    * 缺表 → ``Base.metadata.create_all(engine, tables=[缺的])``，只建缺失表，
      对已有表无副作用（``create_all`` 自带 ``checkfirst``）；
    * 缺列 → ``ALTER TABLE ... ADD COLUMN``，列定义严格来自模型（含 nullable / default）；
    * 幂等：先 ``inspect`` 检查再操作，重复跑不报错、不重复加列；
    * 跨方言：用 SQLAlchemy DDL 编译器按当前方言渲染类型；``NOT NULL`` 且无
      ``server_default`` 的列补一个与模型 ``default`` 等价的 DB 默认值
      （SQLite 的 ``ALTER ADD COLUMN`` 对 NOT NULL 列必须带默认值，否则报错）；
    * 跑完自检全表比对，漂移清单应为空。

用法：
    cd backend
    python app/scripts/sync_schema.py                                   # 对齐当前 DATABASE_URL 指向的库
    DATABASE_URL=postgresql+psycopg://u:p@host:5432/db python ...       # 对齐 PostgreSQL 生产库

⚠️ 本脚本只「让库对齐模型」，绝不反向改模型迁就旧库；属于修复既有漂移，不是改
业务表结构。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许以脚本方式直接运行：backend/app/scripts/sync_schema.py → 上溯 3 级到 backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import inspect, literal, text  # noqa: E402
from sqlalchemy.schema import CreateColumn  # noqa: E402

from app.db import DATABASE_URL, engine  # noqa: E402
from app.models import Base  # noqa: E402  (import 触发全部模型登记到 Base.metadata)

# 显式 import 各模型子模块兜底，确保 Base.metadata 完整（__init__ 已 import）
from app.models import (  # noqa: E402,F401
    ai,
    content,
    learning,
    student_mastery,
    activation,
    user,
    weekly_report,
)


def _default_literal_for_add(column, dialect) -> "str | None":
    """为「NOT NULL 且无 server_default」的列，推导 ALTER 用的 DB 默认值字面量。

    模型里这些是 client-side default（Python 侧），SQLite 的 ``ALTER ADD COLUMN``
    对 NOT NULL 列要求必须有默认值，否则报错。这里把模型 ``default`` 等价翻译成
    DB 侧默认值：

        * 字面量（str / int / float / bool）→ 用 SQLAlchemy ``literal`` 按方言正确
          渲染（含引号 / 转义），SQLite 与 PostgreSQL 均合法；
        * callable（如 ``utcnow``）→ ``CURRENT_TIMESTAMP``（双方言都支持）；
        * 都拿不到 → ``None``（交给上层报错，不静默放行）。

    这样补齐后的列与模型「NOT NULL + 默认取值」语义完全一致。
    """
    default = column.default
    if default is None:
        return None
    arg = getattr(default, "arg", default)
    if callable(arg):
        return "CURRENT_TIMESTAMP"
    try:
        # 关键：DDL 里不能出现绑定参数（? / %s），必须渲染成裸字面量。
        # compile 默认会把它当成可执行表达式、生成占位符，故用 literal_binds=True
        # 强制内联字面量（含引号 / 转义），SQLite 与 PostgreSQL 均合法。
        return str(literal(arg).compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
    except Exception:
        return None


def _add_column_ddl(table_name: str, column, dialect) -> str:
    """渲染单条 ``ALTER TABLE ... ADD COLUMN ...``（方言正确）。"""
    spec = str(CreateColumn(column).compile(dialect=dialect))
    # 补 DB 默认值：NOT NULL 且无 server_default 时 SQLite 必须带 DEFAULT
    if (not column.nullable) and column.server_default is None:
        default_sql = _default_literal_for_add(column, dialect)
        if default_sql:
            spec = f"{spec} DEFAULT {default_sql}"
    quoted_table = dialect.identifier_preparer.quote(table_name)
    return f"ALTER TABLE {quoted_table} ADD COLUMN {spec}"


def compute_drift(engine) -> "tuple[list[str], list[tuple[str, str]]]":
    """返回 ``(缺失表名列表, 缺失(表,列)列表)``。

    只检查「模型期望、库缺失」这一**破坏性方向**——库里多出来的表 / 列是向前兼容的，
    不会让应用崩，故不计入漂移。
    """
    insp = inspect(engine)
    missing_tables: list[str] = []
    missing_cols: list[tuple[str, str]] = []
    for table_name, table in Base.metadata.tables.items():
        if not insp.has_table(table_name):
            missing_tables.append(table_name)
            continue
        existing = {c["name"] for c in insp.get_columns(table_name)}
        for col in table.columns:
            if col.name not in existing:
                missing_cols.append((table_name, col.name))
    return missing_tables, missing_cols


def sync(engine) -> None:
    """执行一次幂等同步：缺表建表、缺列加列。"""
    dialect = engine.dialect
    missing_tables, missing_cols = compute_drift(engine)

    # 1) 缺表：只建缺失的表（create_all 自带 checkfirst，对已有表无副作用）
    if missing_tables:
        tables = [Base.metadata.tables[name] for name in missing_tables]
        Base.metadata.create_all(engine, tables=tables, checkfirst=True)
        print(f"[sync] 新建表: {missing_tables}")

    # 2) 缺列：ALTER TABLE ADD COLUMN（先检查再执行，幂等）
    if missing_cols:
        with engine.begin() as conn:
            for table_name, col_name in missing_cols:
                column = Base.metadata.tables[table_name].columns[col_name]
                ddl = _add_column_ddl(table_name, column, dialect)
                print(f"[sync] ALTER: {ddl}")
                conn.execute(text(ddl))
        print(f"[sync] 补齐列: {[f'{t}.{c}' for t, c in missing_cols]}")

    if not missing_tables and not missing_cols:
        print("[sync] 无漂移，schema 已与模型一致")


def self_verify(engine) -> None:
    """跑完自检全表比对，漂移不为 0 直接非零退出。"""
    missing_tables, missing_cols = compute_drift(engine)
    if missing_tables or missing_cols:
        lines = ["[sync] 自检失败，仍有漂移："]
        for t in missing_tables:
            lines.append(f"  缺表: {t}")
        for t, c in missing_cols:
            lines.append(f"  缺列: {t}.{c}")
        raise SystemExit("\n".join(lines))
    print("[sync] 自检通过：全表比对漂移 = 0")


def main() -> None:
    print(f"[sync] 目标库: {DATABASE_URL}  dialect={engine.dialect.name}")
    sync(engine)
    self_verify(engine)
    print("[sync] 完成。")


if __name__ == "__main__":
    main()

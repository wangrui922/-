"""Schema 守卫测试：模型定义必须与真实 dev.db 对齐（根治 schema 漂移）。

背景：
    dev.db 是 gitignore 的历史库，app 启动不调 ``create_all``，模型演进后库不会
    自愈。一旦「模型有、库没有」的表 / 列出现，练习结算（finish→record_mistake）、
    「诊」（读 student_mastery）、「报」（写 weekly_reports）整条链路会 500。

本测试在每次 ``pytest`` 跑真实 dev.db 时全表比对，发现漂移立即红，并把修复路径
写进报错信息，把「下次加模型又漂」的回归挡在 CI / 本地：

    * 只读连接（``mode=ro``），绝不修改 dev.db；
    * dev.db 不存在时 ``skipif``，不拖累无库环境（如纯净 CI）；
    * 只检查「模型期望、库缺失」这一破坏性方向——库里多出来的表 / 列是向前兼容的，
      不会让应用崩，故不计入漂移。

用法（报错信息会直接给出）：
    cd backend && python app/scripts/sync_schema.py
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from app.models import Base

#: 真实题库（开发用 SQLite）。不存在时自动 skip。
_DEV_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dev.db")
_FIX_SCRIPT = "cd backend && python app/scripts/sync_schema.py"


@pytest.mark.skipif(not os.path.exists(_DEV_DB), reason="真实题库 dev.db 不存在，跳过 schema 守卫")
def test_models_and_dev_db_in_sync() -> None:
    """``Base.metadata`` 里的每张表、每个列，dev.db 都必须有；否则即 schema 漂移。"""
    uri = f"file:{_DEV_DB.replace(os.sep, '/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        db_tables = {row[0] for row in cur.fetchall()}

        missing_tables: list[str] = []
        missing_cols: list[tuple[str, str]] = []

        for table_name, table in Base.metadata.tables.items():
            if table_name not in db_tables:
                missing_tables.append(table_name)
                continue
            cur.execute(f'PRAGMA table_info("{table_name}")')
            db_cols = {row[1] for row in cur.fetchall()}
            for col in table.columns:  # 只检查「模型有、库没有」的方向
                if col.name not in db_cols:
                    missing_cols.append((table_name, col.name))
    finally:
        conn.close()

    assert not missing_tables and not missing_cols, _format_drift(missing_tables, missing_cols)


def _format_drift(missing_tables: list[str], missing_cols: list[tuple[str, str]]) -> str:
    lines = ["模型与真实 dev.db 出现 schema 漂移（模型期望、库缺失）："]
    for t in missing_tables:
        lines.append(f"  - 缺表: {t}")
    for t, c in missing_cols:
        lines.append(f"  - 缺列: {t}.{c}")
    lines.append(f"修复: 在 backend/ 下执行 `{_FIX_SCRIPT}`")
    return "\n".join(lines)

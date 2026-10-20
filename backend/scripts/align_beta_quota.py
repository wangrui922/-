#!/usr/bin/env python
"""生产库「内测全免费不限额」对齐（2026-10-20，幂等）。

做三件事：
    1. ``plans`` 表 free 档的配额同步为无限（-1 / -1 / 99999）+ 文案对齐；
    2. ``system_config`` 里的额度相关键检查（若有）；
    3. 打印对齐后的实际值，供人工确认。

⚠️ 只在**备份已做**的前提下运行（脚本会自己再备份一次到 runtime/data/）。
⚠️ 生产库是 SQLite，有 WAL；脚本用 sqlite3 backup API 做备份（WAL 场景下
   直接 ``cp`` 会得到 malformed 库）。

用法（在服务器上）：
    sudo docker exec deploy-backend-1 python /app/scripts/align_beta_quota.py
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

def _db_path_from_url(url: str) -> str:
    """从 ``DATABASE_URL`` 解析出 SQLite 文件绝对路径。

    实现见 ``scripts/_sqlite_url.py``（与 schema_reconcile.py 共用一份，
    避免两处各写一遍、各自漂移）。
    """
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _sqlite_url import sqlite_path_from_url

    return sqlite_path_from_url(url)


DB_PATH = _db_path_from_url(
    os.environ.get("DATABASE_URL", "sqlite:////app/data/coach.db")
)

BACKUP_DIR = Path("/app/runtime/data")


def backup_db() -> Path | None:
    """用 sqlite3 backup API 备份（不能 cp，WAL 下会 malformed）。"""
    if not BACKUP_DIR.exists():
        print(f"[backup] 目录不存在，跳过：{BACKUP_DIR}")
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = BACKUP_DIR / f"coach.db.bak-betaquota-{stamp}"
    src = sqlite3.connect(DB_PATH)
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src.close()
    print(f"[backup] 已备份 → {dst} ({dst.stat().st_size / 1024:.0f} KB)")
    return dst


def align_free_plan(conn: sqlite3.Connection) -> None:
    """把 free 档对齐到"内测全免费不限额"口径。"""
    before = conn.execute(
        "SELECT ai_daily_limit, practice_daily_limit, max_mistake_storage, description "
        "FROM plans WHERE slug='free'"
    ).fetchone()
    print(f"[free] 改前: {before}")
    if before is None:
        print("[free] free 档不存在，跳过")
        return

    conn.execute(
        "UPDATE plans SET ai_daily_limit=-1, practice_daily_limit=-1, "
        "max_mistake_storage=99999, name='内测免费版', "
        "description='内测期全免费：AI 对话与练习均不限次数' "
        "WHERE slug='free'"
    )
    conn.commit()
    after = conn.execute(
        "SELECT ai_daily_limit, practice_daily_limit, max_mistake_storage, description "
        "FROM plans WHERE slug='free'"
    ).fetchone()
    print(f"[free] 改后: {after}")


def report(conn: sqlite3.Connection) -> None:
    """打印关键配置与数据量，供人工确认。"""
    print("\n--- plans 全表 ---")
    for row in conn.execute(
        "SELECT slug, ai_daily_limit, practice_daily_limit, max_mistake_storage FROM plans"
    ):
        print("   ", row)

    print("\n--- 门控/额度 system_config ---")
    rows = conn.execute(
        "SELECT key, value FROM system_config "
        "WHERE key LIKE '%CONSENT%' OR key LIKE '%FREE%' OR key LIKE '%THRESHOLD%'"
    ).fetchall()
    print("    ", rows if rows else "(无)")

    print("\n--- 关键表行数 ---")
    for t in ("users", "students", "ai_sessions", "attempts", "mistakes",
              "subscriptions", "payment_transactions", "activation_codes"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"    {t}: {n}")
        except sqlite3.Error as exc:
            print(f"    {t}: 查询失败（{exc}）")


def main() -> None:
    if not Path(DB_PATH).exists():
        print(f"[fatal] 数据库不存在：{DB_PATH}")
        sys.exit(1)

    backup_db()

    conn = sqlite3.connect(DB_PATH)
    try:
        align_free_plan(conn)
        report(conn)
    finally:
        conn.close()
    print("\nDONE")


if __name__ == "__main__":
    main()

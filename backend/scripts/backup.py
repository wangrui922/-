#!/usr/bin/env python3
"""数学教练 SQLite 数据库宿主机自动备份脚本（生产环境）。

部署方式：宿主机 crontab，每日 02:00 触发（``python3 backup.py``）。

与 ``backend/scripts/backup_sqlite.py`` 的关系：
    本脚本为**宿主机生产版**，是 backup_sqlite.py（容器侧 minimal 版）的功能
    超集：VACUUM INTO 一致性冷备份（失败降级 shutil.copy2）、完整性校验、
    7 天 daily 清理、周日 weekly 周备、日志落盘。两者运行面不同（宿主机 vs
    容器），互不替代：容器侧备份请继续使用 backup_sqlite.py。

用法::

    python3 backup.py                                  # 默认即生产路径
    COACH_BASE=/data/coach python3 backup.py           # 任意服务器复用

路径规则（环境变量覆盖，默认值与生产宿主机完全一致，不设变量时行为不变）：
    - ``COACH_BASE``       项目根，默认 ``/home/ubuntu/coach``
    - DB 文件              ``{COACH_BASE}/runtime/data/coach.db``（可用 ``COACH_DB`` 覆盖）
    - 备份目录              ``{COACH_BASE}/backups``（可用 ``COACH_BACKUP_DIR`` 覆盖）
    - 日志文件              ``{COACH_BASE}/logs/backup.log``（可用 ``COACH_BACKUP_LOG`` 覆盖）

保留策略：最近 7 天 daily 备份；周日额外保留一份周备（``weekly/`` 目录，永久保留）。
"""
import os, shutil, sqlite3, sys
from datetime import datetime, timedelta

BASE = os.environ.get("COACH_BASE", "/home/ubuntu/coach")
DB_FILE = os.environ.get("COACH_DB", os.path.join(BASE, "runtime", "data", "coach.db"))
BACKUP_DIR = os.environ.get("COACH_BACKUP_DIR", os.path.join(BASE, "backups"))
WEEKLY_DIR = os.path.join(BACKUP_DIR, "weekly")
LOG = os.environ.get("COACH_BACKUP_LOG", os.path.join(BASE, "logs", "backup.log"))

os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG), exist_ok=True)

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
today = datetime.now().strftime("%Y%m%d")
week_num = datetime.now().strftime("%Y-W%U")
dow = datetime.now().weekday()   # Monday=0...Sunday=6

# 1. 一致性冷备份
backup_file = os.path.join(BACKUP_DIR, f"coach-{today}.db")
tmpfile = backup_file + ".tmp"

try:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    with conn:
        cur = conn.cursor()
        cur.execute(f"VACUUM INTO '{tmpfile}'")
    conn.close()
except Exception as e:
    # 降级: 用 shutil.copy2
    shutil.copy2(DB_FILE, tmpfile)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{now} WARN VACUUM INTO failed, fallback to shutil.copy: {e}\n")

# 校验文件生成
if not os.path.exists(tmpfile) or os.path.getsize(tmpfile) == 0:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{now} ALERT backup write failed: {tmpfile}\n")
    sys.exit(1)

# 2. 完整性校验
ok = False
err_msg = ""
try:
    conn = sqlite3.connect(tmpfile)
    cur = conn.execute("PRAGMA integrity_check;")
    result = cur.fetchone()[0]
    conn.close()
    ok = (result == "ok")
    if not ok:
        err_msg = f"integrity_check={result}"
except Exception as e:
    err_msg = str(e)

if not ok:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{now} ALERT backup validation failed {err_msg} file={tmpfile}\n")
    os.remove(tmpfile)
    sys.exit(1)

shutil.move(tmpfile, backup_file)
size = os.path.getsize(backup_file)
with open(LOG, "a", encoding="utf-8") as f:
    f.write(f"{now} OK backup success {backup_file} size={size} integrity=ok\n")

# 3. 周日额外保留周备
if dow == 6:  # Sunday
    os.makedirs(WEEKLY_DIR, exist_ok=True)
    weekly = os.path.join(WEEKLY_DIR, f"coach-{week_num}.db")
    shutil.copy2(backup_file, weekly)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{now} OK weekly backup kept {weekly}\n")

# 4. 清理超过 7 天的 daily 备份
deleted = 0
for fn in os.listdir(BACKUP_DIR):
    fp = os.path.join(BACKUP_DIR, fn)
    if not os.path.isfile(fp) or not fn.startswith("coach-"):
        continue
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(fp))
        if datetime.now() - mtime > timedelta(days=7):
            os.remove(fp)
            deleted += 1
    except Exception:
        pass
if deleted:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{now} OK cleaned old daily backups count={deleted}\n")

sys.exit(0)

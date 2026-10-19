#!/usr/bin/env python3
"""清理测试账号数据。先备份到 JSON，再按依赖顺序删除。

测试账号条件：phone LIKE '1390000%'，或 nickname 含「测试」/「异步」。
执行前会把所有关联数据导出为 JSON 备份，请谨慎确认账号列表。

用法::

    python3 cleanup_test_data.py                      # 默认即生产路径
    COACH_BASE=/data/coach python3 cleanup_test_data.py

路径规则（环境变量覆盖，默认值与生产宿主机完全一致，不设变量时行为不变）：
    - ``COACH_BASE``       项目根，默认 ``/home/ubuntu/coach``
    - DB 文件              ``{COACH_BASE}/runtime/data/coach.db``（可用 ``COACH_DB`` 覆盖）
    - JSON 备份目录        ``{COACH_BASE}/backups``（可用 ``COACH_BACKUP_DIR`` 覆盖）
"""
import json, os, sqlite3, sys
from datetime import datetime

BASE = os.environ.get("COACH_BASE", "/home/ubuntu/coach")
DB = os.environ.get("COACH_DB", os.path.join(BASE, "runtime", "data", "coach.db"))
BACKUP_DIR = os.environ.get("COACH_BACKUP_DIR", os.path.join(BASE, "backups"))
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP_FILE = os.path.join(BACKUP_DIR, f"test_data_backup_{TIMESTAMP}.json")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 测试账号条件
cur.execute("SELECT id, phone, nickname, created_at FROM users WHERE phone LIKE '1390000%' OR nickname LIKE '%测试%' OR nickname LIKE '%异步%'")
rows = cur.fetchall()
if not rows:
    print("未找到测试账号。")
    conn.close()
    sys.exit(0)

print(f"发现 {len(rows)} 个测试账号:")
user_ids = []
for r in rows:
    print(f"  {r['phone']} ({r['nickname']}) uid={r['id']}")
    user_ids.append(r["id"])

# 获取 students.id（与 users.id 一致）
student_ids = user_ids[:]  # 根据数据，students.id = users.id

# 备份所有关联数据
backup = {"users": [dict(r) for r in rows]}

# 定义要查询的表（表名, 外键列）
tables_to_check = [
    ("ai_messages", "student_id"),
    ("ai_sessions", "student_id"),
    ("attempts", "student_id"),
    ("mistakes", "student_id"),
    ("weekly_reports", "student_id"),
    ("student_profile", "student_id"),
    ("student_adaptive_level", "student_id"),
    ("student_mastery", "student_id"),
    ("parent_child", "student_id"),
    ("subscriptions", "user_id"),
    ("payment_transactions", "user_id"),
    ("learning_tasks", "student_id"),
    ("review_schedule", "student_id"),
]

for table, fk_col in tables_to_check:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if not cur.fetchone():
        print(f"  表 {table} 不存在，跳过")
        continue
    # 检查列是否存在
    cur.execute(f"PRAGMA table_info({table})")
    cols = [c["name"] for c in cur.fetchall()]
    if fk_col not in cols:
        print(f"  表 {table} 无列 {fk_col}，跳过")
        continue

    ids = student_ids if fk_col == "student_id" else user_ids
    if not ids:
        continue
    placeholders = ",".join(["?"] * len(ids))
    cur.execute(f"SELECT * FROM {table} WHERE {fk_col} IN ({placeholders})", ids)
    data = [dict(r) for r in cur.fetchall()]
    backup[table] = data
    print(f"  {table}: {len(data)} 条")

# 保存备份
os.makedirs(BACKUP_DIR, exist_ok=True)
with open(BACKUP_FILE, "w", encoding="utf-8") as f:
    json.dump(backup, f, ensure_ascii=False, indent=2, default=str)
print(f"\n备份已保存: {BACKUP_FILE} ({os.path.getsize(BACKUP_FILE)} bytes)")

# 按依赖顺序删除（子表 -> 父表）
# 外键是 student_id 的表先删，再删 students，最后删 users
# 注意：user_id 关联的表（subscriptions, payment_transactions）也要删

delete_order = [
    ("ai_messages", "student_id", student_ids),
    ("ai_sessions", "student_id", student_ids),
    ("attempts", "student_id", student_ids),
    ("mistakes", "student_id", student_ids),
    ("weekly_reports", "student_id", student_ids),
    ("student_profile", "student_id", student_ids),
    ("student_adaptive_level", "student_id", student_ids),
    ("student_mastery", "student_id", student_ids),
    ("parent_child", "student_id", student_ids),
    ("subscriptions", "user_id", user_ids),
    ("payment_transactions", "user_id", user_ids),
    ("learning_tasks", "student_id", student_ids),
    ("review_schedule", "student_id", student_ids),
    ("students", "id", student_ids),
    ("users", "id", user_ids),
]

total_deleted = 0
for table, pk_col, ids in delete_order:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if not cur.fetchone():
        continue
    if not ids:
        continue
    placeholders = ",".join(["?"] * len(ids))
    try:
        cur.execute(f"DELETE FROM {table} WHERE {pk_col} IN ({placeholders})", ids)
        deleted = cur.rowcount
        total_deleted += deleted
        print(f"  删除 {table}: {deleted} 条")
    except sqlite3.Error as e:
        print(f"  删除 {table} 失败: {e}")

conn.commit()
conn.close()
print(f"\n清理完成，共删除 {total_deleted} 条关联数据。")
print(f"备份文件: {BACKUP_FILE}")

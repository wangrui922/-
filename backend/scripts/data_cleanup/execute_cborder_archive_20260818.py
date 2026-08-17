# -*- coding: utf-8 -*-
"""归档第三批：C_BORDER 45 道待核题（用户决策：全删）。

红线纪律：
- 删除 = UPDATE status='archived'（可逆，无需真删）
- 留痕 = 独立备份表 questions_cborder_archive_20260818 + CSV
- 幂等：备份表存在则跳过重建；已在 archived 的题跳过 UPDATE
- 同步处理引用这些题的 doing 老 session
"""
import csv
import sqlite3
import os
import datetime

DB = os.path.join(os.path.dirname(__file__), "..", "..", "dev.db")
DB = os.path.abspath(DB)
CSV_IN = os.path.join(os.path.dirname(__file__), "20260818_C_BORDER_verdict.csv")
CSV_OUT = os.path.join(os.path.dirname(__file__), "20260818_C_BORDER_archived_20260818.csv")
BACKUP_TBL = "questions_cborder_archive_20260818"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

# 1) 读入 45 条待核 UUID
ids = []
with open(CSV_IN, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        ids.append(r["id"].strip())
print(f"待核清单读取: {len(ids)} 条")

# 2) 备份表（仅首次建，保留全字段，可一键拉回）
COLS = ["id", "content", "image_url", "subject", "grade", "difficulty",
        "kp_id", "type", "options", "status", "created_at", "edition",
        "source_type", "section", "source_doc", "kp_verified",
        "source_tag", "ship_ready"]
cur.execute(f"DROP TABLE IF EXISTS {BACKUP_TBL}")  # 清掉首次失败残留的旧 schema
cur.execute(f"""
CREATE TABLE IF NOT EXISTS {BACKUP_TBL} (
    id TEXT, content TEXT, image_url TEXT, subject TEXT, grade TEXT,
    difficulty TEXT, kp_id TEXT, type TEXT, options TEXT, status TEXT,
    created_at TEXT, edition TEXT, source_type TEXT, section TEXT,
    source_doc TEXT, kp_verified INTEGER, source_tag TEXT, ship_ready INTEGER,
    archived_at TEXT
)
""")
cur.execute(f"SELECT COUNT(*) FROM {BACKUP_TBL}")
existing = cur.fetchone()[0]
print(f"备份表已存在行数: {existing}")

backup_rows = []
update_ids = []
for qid in ids:
    q = cur.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not q:
        print(f"  [跳过] 未找到 {qid[:8]}")
        continue
    if existing == 0:
        backup_rows.append(tuple(
            (q[c] if c in q.keys() else None) for c in COLS) + (
            datetime.datetime.now().isoformat(timespec="seconds"),))
    if q["status"] == "active":
        update_ids.append(qid)

if backup_rows:
    cur.executemany(
        f"INSERT INTO {BACKUP_TBL} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", backup_rows)
    print(f"  备份写入: {len(backup_rows)} 行")

if update_ids:
    ph = ",".join("?" * len(update_ids))
    cur.execute(
        f"UPDATE questions SET status='archived' WHERE id IN ({ph})", update_ids)
    print(f"  UPDATE archived: {cur.rowcount} 行")
else:
    print("  无 active 需更新（已归档过）")

# 3) 关联 doing 老 session（learning_tasks.items 含这些题的快照）
dead = []
for r in cur.execute("SELECT id, items FROM learning_tasks WHERE status='doing' AND items IS NOT NULL"):
    if any(qid in (r["items"] or "") for qid in ids):
        dead.append(r["id"])
if dead:
    ph = ",".join("?" * len(dead))
    cur.execute(
        f"UPDATE learning_tasks SET status='done', finished_at=CURRENT_TIMESTAMP WHERE id IN ({ph})", dead)
    print(f"  关联 doing session 归档: {cur.rowcount} 个")
else:
    print("  无关联 doing session")

# 4) 导出归档 CSV（含判留/判删原标注，便于追溯）
with open(CSV_OUT, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["id", "type", "grade", "verdict", "reason", "content_preview"])
    verdict_map = {}
    with open(CSV_IN, encoding="utf-8-sig") as fin:
        for r in csv.DictReader(fin):
            verdict_map[r["id"].strip()] = (r.get("verdict", ""), r.get("reason", ""))
    for qid in ids:
        q = cur.execute(
            "SELECT type,grade,content FROM questions WHERE id=?", (qid,)).fetchone()
        v, reason = verdict_map.get(qid, ("", ""))
        w.writerow([qid, q["type"], q["grade"], v, reason,
                    (q["content"] or "").replace("\n", " ")[:120]])

con.commit()

# 5) 最终核对
print("\n=== 最终核对 ===")
print("  active:", con.execute(
    "SELECT COUNT(*) FROM questions WHERE status='active'").fetchone()[0])
print("  archived:", con.execute(
    "SELECT COUNT(*) FROM questions WHERE status='archived'").fetchone()[0])
print("  备份表:", con.execute(f"SELECT COUNT(*) FROM {BACKUP_TBL}").fetchone()[0])
# 抽题 SQL 是否还能抽到这批题
still = con.execute(
    f"SELECT COUNT(*) FROM questions WHERE id IN ({','.join('?'*len(ids))}) AND status='active'",
    ids).fetchone()[0]
print("  抽题 SQL 仍会抽到已删题:", still)
con.close()
print("\nDONE. CSV:", CSV_OUT)

# -*- coding: utf-8 -*-
"""执行归档：A(6) + C_HARD(49) = 55 道铁定残缺题。
红线：删除=UPDATE status='archived'（可逆）；留痕=独立备份表 + CSV。
幂等：备份表已存在 / 题已 archived 则跳过。
"""
import sqlite3, csv, os, datetime

DB='dev.db'
OUT=os.path.dirname(os.path.abspath(__file__))
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
cur=con.cursor()

def load_ids(fname):
    p=os.path.join(OUT,fname)
    ids=[]
    with open(p,encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            ids.append(r['id'].strip())
    return ids

a_ids=load_ids('20260818_full_A.csv')
c_ids=load_ids('20260818_full_C_HARD.csv')
all_ids=list(dict.fromkeys(a_ids+c_ids))
print('A:',len(a_ids),' C_HARD:',len(c_ids),' 并集:',len(all_ids))

# 去重已 archived
live=[]
for qid in all_ids:
    st=cur.execute("SELECT status FROM questions WHERE id=?",(qid,)).fetchone()
    if st and st['status']=='active':
        live.append(qid)
print('其中仍 active(待归档):',len(live))

bk='questions_full_audit_backup_20260818'
if not cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{bk}'").fetchone():
    cur.execute(f"""CREATE TABLE {bk} AS SELECT * FROM questions WHERE 0=1""")
    print('创建备份表',bk)
# 备份原始行
placeholders=','.join('?'*len(live))
cur.execute(f"INSERT INTO {bk} SELECT * FROM questions WHERE id IN ({placeholders})", live)
print('备份表写入行数:',cur.rowcount)

# 归档
if live:
    cur.execute(f"UPDATE questions SET status='archived' WHERE id IN ({placeholders})", live)
    print('UPDATE archived 行数:',cur.rowcount)

# 关联 doing session：items 含这些题的 → done
cur.execute("SELECT id, items FROM learning_tasks WHERE status='doing' AND items IS NOT NULL")
dead=[]
for r in cur.fetchall():
    items=r['items'] or ''
    if any(qid in items for qid in live):
        dead.append(r['id'])
if dead:
    ph=','.join('?'*len(dead))
    cur.execute(f"UPDATE learning_tasks SET status='done', finished_at=CURRENT_TIMESTAMP WHERE id IN ({ph})", dead)
    print('关联 doing session 归档:',cur.rowcount)

con.commit()
print('active 剩余:',cur.execute("SELECT COUNT(*) FROM questions WHERE status='active'").fetchone()[0])
print('archived 总数:',cur.execute("SELECT COUNT(*) FROM questions WHERE status='archived'").fetchone()[0])
con.close()

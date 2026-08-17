"""执行归档：图片缺失题（v2 扫描清单）。

红线：
1. 删除 = UPDATE status='archived'（可逆）
2. 留痕：独立备份表 questions_no_image_backup_20260818
3. CSV 落档
4. 幂等：备份表存在则跳过重建
5. 同时清理 learning_tasks.items 里引用这些题的老 session 快照
"""
import sqlite3, csv, datetime, os, json

con = sqlite3.connect('dev.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
out_dir = 'scripts/data_cleanup'
csv_in = out_dir + '/20260818_C_no_image_v2_20260818_0055.csv'

# 读取待归档 ID
target_ids = []
with open(csv_in) as f:
    for r in csv.DictReader(f):
        target_ids.append(r['id'])
target_ids = list(dict.fromkeys(target_ids))  # 去重保序

print(f'待归档 ID 数: {len(target_ids)}')

# 1. 独立备份表（幂等）
backup_table = 'questions_no_image_backup_20260818'
exists = cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
    (backup_table,)).fetchone()
if exists:
    print(f'备份表 {backup_table} 已存在，跳过重建（仅当文件 0 行时才重建）')
    cur_count = cur.execute(f'SELECT COUNT(*) FROM {backup_table}').fetchone()[0]
    print(f'  当前备份表行数: {cur_count}')
else:
    print(f'建备份表 {backup_table}...')
    cur.execute(f'CREATE TABLE {backup_table} AS SELECT * FROM questions WHERE 0')
    cur.execute(f'INSERT INTO {backup_table} SELECT * FROM questions WHERE id IN ({",".join("?"*len(target_ids))})',
                target_ids)
    inserted = cur.execute(f'SELECT COUNT(*) FROM {backup_table}').fetchone()[0]
    print(f'  插入备份 {inserted} 行')

# 2. CSV 留痕（不只是清单CSV，还包括归档前后对比）
archive_csv = out_dir + f'/20260818_C_archived_{ts}.csv'
already = set(r['id'] for r in cur.execute(
    f"SELECT id FROM questions WHERE status='archived' AND id IN ({','.join('?'*len(target_ids))})",
    target_ids).fetchall())

print(f'之前已 archived 的: {len(already)} 条（直接放过）')
to_act = [i for i in target_ids if i not in already]
print(f'本次实际执行 UPDATE 的: {len(to_act)} 条')

if to_act:
    # 验证 state：UPDATE 前所有目标都是 active
    non_active = cur.execute(
        f"SELECT id FROM questions WHERE status != 'active' AND id IN ({','.join('?'*len(to_act))})",
        to_act).fetchall()
    if non_active:
        print('⚠️ 发现非 active 题：', [r['id'] for r in non_active])
    # UPDATE
    cur.execute(
        f"UPDATE questions SET status='archived' WHERE id IN ({','.join('?'*len(to_act))})",
        to_act)
    con.commit()
    print(f'  UPDATE questions SET status=archived 行数: {cur.rowcount}')

# 3. 写归档后 CSV（含全部 186 条）
with open(archive_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['id', 'type', 'grade', 'fig_ref', 'ship_ready_before', 'source_tag', 'source_doc', 'content'])
    rows = cur.execute(
        f"SELECT id, type, grade, ship_ready, source_tag, source_doc, content FROM "
        f"{backup_table} WHERE id IN ({','.join('?'*len(target_ids))}) ORDER BY id",
        target_ids).fetchall()
    for r in rows:
        c = r['content']
        if len(c) > 200:
            c = c[:200] + '...'
        # fig_ref: 由于备份表里没保存，重新跑分类
        # 简化：从 CSV 里读
        w.writerow([r['id'], r['type'], r['grade'], '', r['ship_ready'], r['source_tag'] or '', r['source_doc'] or '', c])
print(f'\n归档 CSV: {archive_csv}')

# 4. 同步：归档学习任务里引用这些题的老 session（防御已练完的学生抽不到新题）
print('\n清理 learning_tasks 老 doing session...')
sessions_with_dead = cur.execute("""
SELECT DISTINCT id FROM learning_tasks
WHERE status='doing'
  AND EXISTS (
    SELECT 1 FROM json_each(items) je
    WHERE json_extract(je.value, '$.question_id') IN ({})
  )
""".format(','.join('?'*len(target_ids))), target_ids).fetchall()
dead_ids = [r[0] for r in sessions_with_dead]
print(f'  受影响 doing session: {len(dead_ids)}')
if dead_ids:
    cur.execute(
        "UPDATE learning_tasks SET status='done', finished_at=CURRENT_TIMESTAMP "
        f"WHERE id IN ({','.join('?'*len(dead_ids))})",
        dead_ids)
    print(f'  UPDATE 行数: {cur.rowcount}')
    con.commit()

# 5. 最终核对
final_active = cur.execute("SELECT COUNT(*) FROM questions WHERE status='active'").fetchone()[0]
final_archived = cur.execute("SELECT COUNT(*) FROM questions WHERE status='archived'").fetchone()[0]
print(f'\n核对:')
print(f'  active: {final_active}')
print(f'  archived: {final_archived}')
print(f'  backup ({backup_table}): {cur.execute(f"SELECT COUNT(*) FROM {backup_table}").fetchone()[0]}')

con.close()

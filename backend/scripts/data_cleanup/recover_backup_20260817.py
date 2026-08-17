"""补救：完整重建 A 类 + B 类原始 content 备份表。

A 类 6 条原始 content 仍在 questions 表（归档不修改 content），从 status='archived'
按 6 UUID 拉回。

B 类 24 条从 B_cleaned.csv (content_before) 重建。
"""
import sqlite3, csv, os

con = sqlite3.connect('dev.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

# 6 个 A 类 UUID（前缀）
a_prefixes = ['7d23581d', '12aecef7', 'ce712f27', 'adddbb3d', 'ee11d646', '5bf3aa6f']
a_reasons = {
    '7d23581d': '题干截断:动词后停',
    '12aecef7': '题干为占位符',
    'ce712f27': "选项value为空:['D']",
    'adddbb3d': '题干含HTML标签',
    'ee11d646': '题干含HTML标签',
    '5bf3aa6f': '题干含HTML标签',
}

# 重建 A 类备份
a_recover = 'questions_incomplete_recover_20260817'
cur.execute(f'DROP TABLE IF EXISTS {a_recover}')
cur.execute(f"""
CREATE TABLE {a_recover} (
    id TEXT PRIMARY KEY,
    type TEXT, grade INTEGER,
    reason TEXT,
    content TEXT, options TEXT,
    source_doc TEXT
)
""")

n_a = 0
for p in a_prefixes:
    r = cur.execute("SELECT id, type, grade, content, options, source_doc FROM questions WHERE id LIKE ? AND status='archived'",
                    (p + '%',)).fetchone()
    if r:
        cur.execute(f'INSERT INTO {a_recover} VALUES (?,?,?,?,?,?,?)',
                    (r['id'], r['type'], r['grade'], a_reasons[p], r['content'], r['options'] or '', r['source_doc'] or ''))
        n_a += 1
con.commit()
print(f'{a_recover}: {n_a} 行')

# 重建 B 类备份
b_recover = 'questions_polluted_recover_20260817'
cur.execute(f'DROP TABLE IF EXISTS {b_recover}')
cur.execute(f"""
CREATE TABLE {b_recover} (
    id TEXT PRIMARY KEY,
    type TEXT, grade INTEGER,
    reason TEXT,
    content_before TEXT,
    source_doc TEXT
)
""")

n_b = 0
for csv_path in ['scripts/data_cleanup/20260817_B_cleaned_20260817_2355.csv',
                 'scripts/data_cleanup/20260817_B2_cleaned_20260817_2356.csv']:
    if not os.path.exists(csv_path):
        continue
    with open(csv_path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            content_before = row.get('content_before', row.get('content', ''))
            cur.execute(f'INSERT OR REPLACE INTO {b_recover} VALUES (?,?,?,?,?,?)',
                        (row['id'], row.get('type', ''), int(row.get('grade', 0)) if row.get('grade') else 0,
                         row.get('reason', ''), content_before, row.get('source_doc', '')))
            n_b += 1
con.commit()
print(f'{b_recover}: {n_b} 行')

# 验证
print()
print('=== A 类 6 条 ===')
for r in cur.execute(f'SELECT id, type, reason, substr(content,1,60) FROM {a_recover}'):
    print(f'  {r[0][:8]} {r[1]} {r[2]}')
    print(f'    content: {r[3]!r}')

print()
print('=== B 类前 5 条 ===')
for r in cur.execute(f'SELECT id, type, reason, substr(content_before,1,50) FROM {b_recover} LIMIT 5'):
    print(f'  {r[0][:8]} {r[1]} {r[2]}')
    print(f'    before: {r[3]!r}')

print()
print(f'✅ 备份已恢复：A={n_a}, B={n_b}')
print()
print('说明：A 类 6 条原始 content 从 questions.archived 拉回（归档不修改 content）')
print('      B 类 24 条原始 content_before 从 B_cleaned.csv + B2_cleaned.csv 重建')
print('      缺口：第一次末尾清洗的 58 条 content_before 未在 CSV 中备份（被立即覆盖）—— 那些题已合法清洗，但原始 content 不可恢复')

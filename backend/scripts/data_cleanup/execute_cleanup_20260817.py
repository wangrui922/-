"""题库清洗执行 — A 类归档 + B 类格式污染就地清洗。

红线：
- 删除 = UPDATE status='archived'（可逆）
- 留痕：先建独立备份表 + 写 CSV
- 不加列/CHECK，仅修改 content（B 类）
"""
import sqlite3, json, csv, re, os, datetime, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.data_cleanup.scan_incomplete import (
    classify, is_truncated, HTML_TAG, PAPER_NUM_HEAD, PAPER_NUM_INLINE, CTRL,
)


con = sqlite3.connect('dev.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute("""
SELECT id, content, options, type, grade, ship_ready, source_tag, source_doc
FROM questions WHERE status='active'
""").fetchall()

a_hits = []
b_hits = []
for r in rows:
    rules = classify(r)
    if not rules:
        continue
    rec = {'id': r['id'], 'type': r['type'], 'grade': r['grade'],
           'content': r['content'], 'options': r['options'],
           'source_doc': r['source_doc'], 'source_tag': r['source_tag'],
           'reasons': rules}
    if any(c.startswith('A') for c, _ in rules):
        a_hits.append(rec)
    else:
        b_hits.append(rec)

a_ids = sorted(set(r['id'] for r in a_hits))
b_ids = sorted(set(r['id'] for r in b_hits))

print('=' * 60)
print('题库清洗执行')
print('=' * 60)
print(f'A 类（真残缺）: {len(a_ids)} 条 → 归档')
print(f'B 类（格式污染）: {len(b_ids)} 条 → 清洗 content')
print()

# === 第 1 步：A 类建备份表 ===
backup_a_name = 'questions_incomplete_backup_20260817'
print(f'[1/6] 建 A 类备份表 {backup_a_name} ...')
# 已存在则跳过 DROP（保护原始备份不被覆盖）
exists_a = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (backup_a_name,)).fetchone()
if exists_a:
    n_a_backup = cur.execute(f'SELECT COUNT(*) FROM {backup_a_name}').fetchone()[0]
    print(f'  备份表已存在（含 {n_a_backup} 行），跳过重建以保护原始备份')
else:
    placeholders = ','.join('?' * len(a_ids))
    cur.execute(f"""
    CREATE TABLE {backup_a_name} AS
    SELECT * FROM questions WHERE id IN ({placeholders})
    """, a_ids)
    n_a_backup = cur.execute(f'SELECT COUNT(*) FROM {backup_a_name}').fetchone()[0]
    print(f'  备份行数: {n_a_backup}')

# === 第 2 步：B 类建备份表 ===
backup_b_name = 'questions_polluted_backup_20260817'
print(f'[2/6] 建 B 类备份表 {backup_b_name} ...')
exists_b = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (backup_b_name,)).fetchone()
if exists_b:
    n_b_backup = cur.execute(f'SELECT COUNT(*) FROM {backup_b_name}').fetchone()[0]
    print(f'  备份表已存在（含 {n_b_backup} 行），跳过重建')
else:
    placeholders_b = ','.join('?' * len(b_ids))
    cur.execute(f"""
    CREATE TABLE {backup_b_name} AS
    SELECT * FROM questions WHERE id IN ({placeholders_b})
    """, b_ids)
    n_b_backup = cur.execute(f'SELECT COUNT(*) FROM {backup_b_name}').fetchone()[0]
    print(f'  备份行数: {n_b_backup}')

# === 第 3 步：写 CSV ===
out_dir = 'scripts/data_cleanup'
ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
csv_a = f'{out_dir}/20260817_A_archived_{ts}.csv'
csv_b = f'{out_dir}/20260817_B_cleaned_{ts}.csv'

with open(csv_a, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['id', 'type', 'grade', 'reason', 'content', 'options', 'source_doc'])
    # 按 a_ids 顺序写（已排序），保证每个 UUID 写一次
    a_recs = {r['id']: r for r in a_hits}
    for uid in a_ids:
        if uid not in a_recs:
            continue
        r = a_recs[uid]
        reason = '; '.join(rr for _, rr in r['reasons'] if _.startswith('A'))
        c = r['content']
        if len(c) > 200:
            c = c[:200] + '...'
        w.writerow([r['id'], r['type'], r['grade'], reason, c, r['options'] or '', r['source_doc'] or ''])
print(f'[3/6] A 类 CSV 清单: {csv_a}')

with open(csv_b, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['id', 'type', 'grade', 'reason', 'content_before', 'source_doc'])
    b_recs = {r['id']: r for r in b_hits}
    for uid in b_ids:
        if uid not in b_recs:
            continue
        r = b_recs[uid]
        reason = '; '.join(rr for _, rr in r['reasons'])
        w.writerow([r['id'], r['type'], r['grade'], reason, r['content'], r['source_doc'] or ''])
print(f'       B 类 CSV 清单: {csv_b}')

# === 第 4 步：执行 A 类归档（UPDATE status='archived'）===
print(f'[4/6] 归档 A 类 {len(a_ids)} 条 (status="archived") ...')
placeholders = ','.join('?' * len(a_ids))
cur.execute(f"""
UPDATE questions SET status='archived'
WHERE id IN ({placeholders})
""", a_ids)
con.commit()
print(f'  归档完成')

# === 第 5 步：执行 B 类清洗（UPDATE content）===
print(f'[5/6] 清洗 B 类 {len(b_ids)} 条 (去末尾"(N分)"分值残留) ...')
# 正则：只去掉末尾的 "(N分)" 或 "（N分）"，中间不动
SCORE_TAIL = re.compile(r'\s*[（(]\s*\d+\s*分\s*[)）]\s*$')
HEAD_NUM = re.compile(r'^\s*\d+\s*[.、．)）]\s+')  # 开头题号

# 逐条 UPDATE（SQLite 的 REPLACE 不支持 regex）
cleaned_count = 0
samples = []
for bid in b_ids:
    r = cur.execute('SELECT content FROM questions WHERE id=?', (bid,)).fetchone()
    if not r:
        continue
    content = r['content'] or ''
    new_content = content
    # 1) 剥末尾 "(N分)"
    new_content2 = SCORE_TAIL.sub('', new_content)
    # 2) 剥开头题号 "1. " "5. " 等（仅当题号后是空格+正文，不是 "5.8" 这种小数）
    if HEAD_NUM.match(new_content2):
        # 确认去掉题号后首字符不是数字（避免误伤 "5.8" 这种小数开头）
        rest = HEAD_NUM.sub('', new_content2)
        if rest and not rest[0].isdigit():
            new_content2 = rest
    if new_content2 != content:
        cur.execute('UPDATE questions SET content=? WHERE id=?', (new_content2, bid))
        cleaned_count += 1
        if len(samples) < 5:
            samples.append((bid, content[:60], new_content2[:60]))
con.commit()
print(f'  清洗完成: {cleaned_count} 条 content 已更新')
print('  示例（前 5 条）：')
for bid, b, a in samples:
    print(f'    {bid[:8]}:')
    print(f'      before: {b!r}')
    print(f'      after:  {a!r}')

# === 第 6 步：不变式验证（active 选择题必须含合法 ABCD）===
print('[6/6] 不变式验证 ...')
from app.services.importers import quality_gate as qg
from app.services import judge_engine as je

bad = []
for r in cur.execute("""
SELECT id, options, type FROM questions WHERE status='active' AND type='选择'
"""):
    try:
        opts = json.loads(r['options']) if r['options'] else None
    except Exception:
        opts = None
    if not qg.validate_choice_options(opts):
        bad.append((r['id'], '选项不完整'))
    if not opts:
        bad.append((r['id'], '选项为空'))
print(f'  active 选择题总: {cur.execute("SELECT COUNT(*) FROM questions WHERE status=? AND type=?", ("active", "选择")).fetchone()[0]}')
print(f'  不变式失败: {len(bad)}')
if bad:
    print('  ⚠️ 失败明细:')
    for b in bad[:10]:
        print(f'    {b}')

# 回归：再次扫库，确认 A/B 类都清空
print()
print('=== 回归扫库 ===')
rows2 = cur.execute("""
SELECT id, content, options, type, grade, ship_ready, source_tag, source_doc
FROM questions WHERE status='active'
""").fetchall()
a2 = 0; b2 = 0
for r in rows2:
    rules = classify(r)
    if not rules:
        continue
    if any(c.startswith('A') for c, _ in rules):
        a2 += 1
    else:
        b2 += 1
print(f'A 类残留: {a2}')
print(f'B 类残留: {b2}')

print()
print('=' * 60)
print(f'✅ 清洗完成：A 类归档 {len(a_ids)} 条，B 类清洗 {cleaned_count} 条')
print(f'   备份表：{backup_a_name} ({n_a_backup} 行) / {backup_b_name} ({n_b_backup} 行)')
print(f'   CSV：{csv_a} / {csv_b}')
print('=' * 60)

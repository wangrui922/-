"""题库 B 类补丁清洗 — 剥中间位置的 "(N分)" 分值残留。

不动开头题号（避免误伤 "5.8＞5.□" 这种小数开头真题目）。
不动"分钟"/"分钟"等单位（用括号形式限定）。
"""
import sqlite3, csv, re, os, datetime
from scripts.data_cleanup.scan_incomplete import classify

con = sqlite3.connect('dev.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

# 1) 找出当前 active 中 B 类残留（重新扫）
rows = cur.execute("""
SELECT id, content, options, type, grade, ship_ready, source_tag, source_doc
FROM questions WHERE status='active'
""").fetchall()

b_residual = []
for r in rows:
    rules = classify(r)
    if rules and all(not c.startswith('A') for c, _ in rules):
        b_residual.append({'id': r['id'], 'content': r['content'], 'type': r['type'],
                          'grade': r['grade'], 'source_doc': r['source_doc'], 'reasons': rules})

print(f'B 类残留: {len(b_residual)} 条')

# 2) 仅处理"中间位置有 (N分)"的；开头题号不动
SCORE_INLINE = re.compile(r'\s*[（(]\s*\d+\s*分\s*[)）]\s*')
SCORE_GROUP = re.compile(r'\s*[（(]\s*共\s*\d+\s*分\s*[)）]\s*')
HEAD_NUM_DANGER = re.compile(r'^\s*\d+\s*[.、．)）]\s+')  # 开头题号 — 谨慎跳过

candidates = []
for r in b_residual:
    content = r['content'] or ''
    has_inline_score = bool(SCORE_INLINE.search(content)) or bool(SCORE_GROUP.search(content))
    # 检查开头题号风险：若以小数 "5.8" "1.2" 开头 → 不动
    is_decimal_head = bool(re.match(r'^\s*\d+\.\d', content))
    is_pure_num_list = bool(re.match(r'^\s*\d+[、，]', content))
    is_safe_to_clean = has_inline_score and not is_decimal_head and not is_pure_num_list
    r['is_safe_to_clean'] = is_safe_to_clean
    r['has_inline_score'] = has_inline_score
    candidates.append(r)

cleanable = [r for r in candidates if r['is_safe_to_clean']]
print(f'其中可安全清洗（仅剥中间 (N分)）: {len(cleanable)} 条')
print(f'其中仅开头题号（不动）: {len(candidates) - len(cleanable)} 条')

# 显示不动的那几条
not_clean = [r for r in candidates if not r['is_safe_to_clean']]
for r in not_clean:
    short = (r['content'][:80] + '...') if len(r['content']) > 80 else r['content']
    print(f'  跳过: {r["id"][:8]} - {short!r}')
print()

# 3) 建备份表（追加模式）
backup_name = 'questions_polluted2_backup_20260817'
print(f'建备份表 {backup_name} ...')
cur.execute(f'DROP TABLE IF EXISTS {backup_name}')
ids_to_backup = [r['id'] for r in cleanable]
if ids_to_backup:
    placeholders = ','.join('?' * len(ids_to_backup))
    cur.execute(f"""
    CREATE TABLE {backup_name} AS
    SELECT * FROM questions WHERE id IN ({placeholders})
    """, ids_to_backup)
n_backup = cur.execute(f'SELECT COUNT(*) FROM {backup_name}').fetchone()[0]
print(f'  备份行数: {n_backup}')

# 4) 写 CSV
ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
csv_path = f'scripts/data_cleanup/20260817_B2_cleaned_{ts}.csv'
with open(csv_path, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['id', 'type', 'grade', 'reason', 'content_before', 'source_doc'])
    for r in cleanable:
        reason = '; '.join(rr for _, rr in r['reasons'])
        w.writerow([r['id'], r['type'], r['grade'], reason, r['content'], r['source_doc'] or ''])
print(f'CSV 清单: {csv_path}')

# 5) 执行清洗
print(f'清洗 {len(cleanable)} 条 (剥中间 (N分)) ...')
samples = []
for r in cleanable:
    bid = r['id']
    content = r['content']
    new_content = content
    # 剥 "(共N分)"
    new_content = SCORE_GROUP.sub('', new_content)
    # 剥 "(N分)"
    new_content = SCORE_INLINE.sub('', new_content)
    # 收尾清理：可能末尾有空格
    new_content = new_content.rstrip()
    if new_content != content:
        cur.execute('UPDATE questions SET content=? WHERE id=?', (new_content, bid))
        if len(samples) < 8:
            samples.append((bid, content[:80], new_content[:80]))
con.commit()
print(f'  完成')
for bid, b, a in samples:
    print(f'    {bid[:8]}:')
    print(f'      before: {b!r}')
    print(f'      after:  {a!r}')

# 6) 回归扫库
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

# 不变式验证
from app.services.importers import quality_gate as qg
import json
bad = 0
for r in cur.execute("SELECT options FROM questions WHERE status='active' AND type='选择'"):
    try:
        opts = json.loads(r['options']) if r['options'] else None
    except:
        opts = None
    if not qg.validate_choice_options(opts):
        bad += 1
print(f'选择题不变式失败: {bad}')
print()
print(f'✅ 补丁完成')

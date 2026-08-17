"""题库残缺扫描+分类 — 重写截断规则后版本。

判定 A 真残缺（建议删）：
- 选项 value 为空（选择题）
- 题干为占位符
- 题干含 HTML 标签
- 字串过短（选择题<6/应用<10等）
- 题干精确截断（叙述在开放性虚词/动词后突然停止）

判定 B 格式污染（题目完整，但 PDF 试卷格式没清洗）：
- 试卷编号残留（开头 "1. "）
- "(N分)" 分值标记
- 含控制字符
"""
import sqlite3, json, csv, re, os, datetime
from app.services.importers import quality_gate as qg
from collections import Counter

con = sqlite3.connect('dev.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

UNITS = tuple('米 厘米 分米 吨 千克 公斤 斤 克 元 角 分 块 个 只 条 张 把 根 朵 颗 棵 架 台 辆 艘 盏 盒 箱 包 袋 瓶 杯 碗 盘 碟 副 套 件 双 对 群 堆 排 列 行 间 栋 座 层 步 圈 周 遍 次 回 趟 场 节 段 章 首 句 字 页 幅 面'.split())
STRONG = set('如果则即得求还共每因为所以但是但而或与和可至对')  # Python set() 按字符拆开
# 单字动作动词——"花/想/买/卖/买/卖/拿"在数学题里常作名词/量词，剔除
WEAK_VERB = set('玩 走 看 坐 跑 跳 爬 游 滑 唱 送 收 借'.replace(' ', ''))
QUANTITY_TAILS = tuple('）' + u for u in '张 个 只 条 人 块 元 角 分 倍 种 厘米 米 号 位 片 本 辆 台 架 盏 枝 朵 根 颗 棵 群 堆 排 行 间 层 圈 面 条 处 部'.split())
HTML_TAG = re.compile(r'<[^>]+>')
CTRL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
PAPER_NUM_HEAD = re.compile(r'^\s*\d{1,2}[.、．)）]\s*(\(\d+分\))?\s*')
PAPER_NUM_INLINE = re.compile(r'[\(（]\s*\d+分\s*[\)）]')
# 助词/量词结尾 → 末尾单字更可能是名词，不算动词截断
HELPER_TAIL_BEFORE = set('的了着过们里上下来中是在和与把将被让给用往向')
HTML_TAG = re.compile(r'<[^>]+>')
CTRL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
PAPER_NUM_HEAD = re.compile(r'^\s*\d{1,2}[.、．)）]\s*(\(\d+分\))?\s*')
PAPER_NUM_INLINE = re.compile(r'[\(（]\s*\d+分\s*[\)）]')


def is_truncated(content, qtype):
    """精确截断检测：叙述在开放性虚词/动词后突然停止。"""
    c = (content or '').strip()
    if not c:
        return True
    if qtype == '选择':
        # 选择题合法结尾
        if c.endswith('（') or c.endswith(')'):
            return False
        if c[-1] in '？?。.！!；;':
            return False
        if c[-1].isdigit() or c[-1] in '%）)]':
            return False
        if c.endswith(QUANTITY_TAILS):
            return False
        if c.endswith(('的是', '的是？', '处', '里', '呢')):
            return False
        if c.endswith(('哪个', '哪种', '哪个？', '哪种？')):
            return False
        if c.endswith(('去', '来', '做')):
            return False
        # 强截断
        if c.endswith(tuple(STRONG)) or c[-2:] in STRONG or c[-1] in STRONG:
            return True
        # 弱动词：要求 c[-1] 真作动词，且 c[-2] 不是助词（否则更像名词）
        if (c.endswith(tuple(WEAK_VERB)) or c[-1] in WEAK_VERB) and len(c) >= 2 and c[-2] not in HELPER_TAIL_BEFORE:
            return True
        return False
    if qtype in ('应用', '计算', '解答'):
        # 题目里任何位置含问号 → 至少有完整问题，不算截断
        if '?' in c or '？' in c:
            return False
        if c[-1] in '？?。.！!；;':
            return False
        if c[-1].isdigit() or c[-1] in '%）)]':
            return False
        if c.endswith(UNITS):
            return False
        # 答案混入题干（"答："/"解："/"【点拨】"）→ 视为题组/答案粘合，不算截断
        if '答：' in c or '解：' in c or '【点拨】' in c:
            return False
        if c.endswith(tuple(STRONG)) or c[-2:] in STRONG or c[-1] in STRONG:
            return True
        if c.endswith(tuple('玩 走 看 坐 爬'.split())):
            return True
        return False
    return False


def truncate_reason(c, qtype):
    c = (c or '').strip()
    if c.endswith(tuple(STRONG)) or c[-2:] in STRONG or c[-1] in STRONG:
        return '末尾虚词"' + c[-6:] + '"'
    return '动词后停'


def classify(q):
    cid = q['id']
    content = q['content'] or ''
    options_str = q['options']
    qtype = q['type']
    options = None
    if options_str:
        try:
            options = json.loads(options_str)
        except Exception:
            pass
    ans_row = cur.execute('SELECT correct_answer FROM answers WHERE question_id=?', (cid,)).fetchone()
    ans = ans_row['correct_answer'] if ans_row else None
    rules = []

    # A1：选项 value 为空
    if qtype == '选择' and options and isinstance(options, dict):
        empties = [k for k, v in options.items() if not (v or '').strip()]
        if empties:
            rules.append(('A.真残缺', '选项value为空:' + str(empties)))

    # A2：题干为占位符
    if qtype in ('选择', '应用', '计算', '解答') and (qg.is_placeholder_only(content) if content else True):
        rules.append(('A.真残缺', '题干为占位符'))

    # A3：HTML 标签
    if HTML_TAG.search(content):
        rules.append(('A.真残缺', '题干含HTML标签'))

    # A4：字串过短
    MIN_LEN = {'选择': 6, '填空': 5, '判断': 4, '应用': 10, '计算': 6, '解答': 10, '图形': 8}
    minl = MIN_LEN.get(qtype, 5)
    if 0 < len(content.strip()) < minl:
        rules.append(('A.真残缺', '字串过短(' + str(len(content.strip())) + '<' + str(minl) + ')'))

    # A5：精确截断
    if qtype in ('选择', '应用', '计算', '解答') and is_truncated(content, qtype):
        rules.append(('A.真残缺', '题干截断:' + truncate_reason(content, qtype)))

    # B 类
    if PAPER_NUM_HEAD.match(content):
        rules.append(('B.格式污染', '试卷编号残留(开头)'))
    if PAPER_NUM_INLINE.search(content):
        rules.append(('B.格式污染', '含"(N分)"分值标记'))
    if CTRL.search(content):
        rules.append(('B.格式污染', '含控制字符'))

    return rules


rows = cur.execute("""
SELECT id, content, options, type, grade, ship_ready, source_tag, source_doc
FROM questions WHERE status='active'
""").fetchall()

a_hits = []
b_hits = []
for r in rows:
    rules = classify(r)
    if rules:
        rec = {'id': r['id'], 'type': r['type'], 'grade': r['grade'],
               'content': r['content'], 'options': r['options'],
               'source_doc': r['source_doc'], 'source_tag': r['source_tag'],
               'reasons': rules}
        if any(c.startswith('A') for c, _ in rules):
            a_hits.append(rec)
        else:
            b_hits.append(rec)

a_ids = set(r['id'] for r in a_hits)
b_ids = set(r['id'] for r in b_hits)

print('A. 真残缺 去重 UUID 数:', len(a_ids))
print('B. 格式污染 去重 UUID 数:', len(b_ids))
print()

# 按 reason 分类
rule_counter = Counter()
for r in a_hits:
    for cat, reason in r['reasons']:
        if cat.startswith('A'):
            rule_counter[reason.split(':')[0]] += 1
            break

print('A 类 reason 分布:')
for r, n in rule_counter.most_common():
    print('  ', n, ' ', r)
print()

# 写 CSV
out_dir = 'scripts/data_cleanup'
ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
csv_path = out_dir + '/20260817_A_incomplete_' + ts + '.csv'
with open(csv_path, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['id', 'type', 'grade', 'reason_text', 'content', 'options', 'source_doc'])
    seen = set()
    for r in a_hits:
        if r['id'] in seen:
            continue
        seen.add(r['id'])
        all_reasons = '; '.join(rr for _, rr in r['reasons'] if _.startswith('A'))
        c = r['content']
        if len(c) > 200:
            c = c[:200] + '...'
        w.writerow([r['id'], r['type'], r['grade'], all_reasons, c, r['options'] or '', r['source_doc'] or ''])
print('  A CSV:', csv_path, '(' + str(len(seen)) + ' 行)')

# A 类全部样本
print()
print('=== A 类全部样本（按 reason 分组）===')
groups = {}
for r in a_hits:
    if r['id'] in groups:
        continue
    reason_key = r['reasons'][0][1].split(':')[0] if r['reasons'] else '?'
    groups.setdefault(reason_key, []).append(r)

for reason, items in groups.items():
    print('\n--- ' + reason + ' (' + str(len(items)) + ' 条) ---')
    for r in items[:15]:
        c = r['content']
        if len(c) > 80:
            c = c[:80] + '...'
        all_reasons = '; '.join(rr for _, rr in r['reasons'] if _.startswith('A'))
        print('  ' + r['id'][:8] + ' [' + r['type'] + '/g' + str(r['grade']) + '] ' + all_reasons)
        print('    content:', repr(c))
    if len(items) > 15:
        print('  ... 共', len(items), '条')

# B 类按 source 分布
print()
print('=== B 类 source_doc 分布（前 10）===')
src_counter = Counter()
for r in b_hits:
    src_counter[(r['source_doc'] or 'None', r['type'])] += 1
for k, v in src_counter.most_common(10):
    print('  ', v, ' ', k)

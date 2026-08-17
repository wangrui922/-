"""C 类扫描 v2：题干引向图但 content/options 都无图 → 图片缺失。

白名单（不归档）：
- INSTR：指令题（用/列竖式计算、画一画、圈一圈、画线段、作图题）
- TABLE：数据/表/名单题（下面是 价格表/统计表/成绩/名单/身份证/数量/小票/情况表）
- FORMULA：算式题（在...算式中、下列算式、填入算式）
- PROCESS：解答过程题（下面是...解答过程、解法一/二、谁做得对）

真缺（归档）：
- 其他命中 FIG_REF_RE + content/options 都没图标记的。
"""
import sqlite3, csv, datetime, re, json, sys

con = sqlite3.connect('dev.db')
con.row_factory = sqlite3.Row
cur = con.cursor()

FIG_REF_RE = re.compile(
    r'如图|如下图|上图|下图|看图|右图|左图|右面|左面|图示|右上图|右下图|左上图|左下图|'
    r'图中所示|图中是|图中|'
    r'下面是|上面是|'
    r'竖式中的|竖式里|竖式中|竖式|'
    r'式中|式子中|式子里|'
    r'右图中|左图中|'
    r'下面图|上面图|'
    r'如图所示|'
    r'读图'
)

# 白名单 —— 只放行"不用图也能做题"的纯指令题/数据表题/算式题/解法题。
# 注意：'看图列式/看图填一填/读图/在图中画/圈一圈分一分/画出'等都是要看图才能做题，归档。
INSTRUCTION_RE = re.compile(
    r'(?<!看图)(?<!如图)用竖式计算|(?<!看图)(?<!如图)列竖式计算|(?<!看图)(?<!如图)用横式计算|(?<!看图)(?<!如图)列式计算|'
    r'直接写出\b'
)

TABLE_RE = re.compile(
    r'价格表|价目表|统计表|信息表|成绩(表|单|统计|汇总)|'
    r'身份证|身份号码|公民身份|公民身份证|'
    r'购物小票|购物清单|账单|'
    r'下面是.*?(价格|数量|成绩|分数|人数|金额|名字|日期|身份证|信息|统计|表格|纪录|数据|情况|清单|名单|店铺|规格|单价|姓名)'
)

FORMULA_RE = re.compile(
    r'在这道算式中|在.*这.*算式(?!.*?如图|.*?下图|.*?右图)|'
    r'下列算式|下列算术|把.*填入.*算式|填入.*算式|填入.*下面的.*□|'
    r'把.*填入下面的.*?[=＋]'
)

PROCESS_RE = re.compile(
    r'下面是.*解答过程|下面是他们|下列解法|两种解法|解法一|解法二|'
    r'谁做得对|您认为谁|她的发现|两种思路|'
    r'下面是小丽和小刚|小红和小明|下面是小亮的|'
    r'小明和.*?|小刚和.*?|小红和.*?小亮和'
)

IMG_TAG_RE = re.compile(r'<img\b|<svg\b|data:image/|!\[.*?\]\([^)]+\)')
IMG_URL_RE = re.compile(r'https?://[^\s\'"]+\.(?:jpg|jpeg|png|gif|svg|webp|bmp)', re.I)


def has_any_image(text):
    if not text:
        return False
    if IMG_TAG_RE.search(text):
        return True
    if IMG_URL_RE.search(text):
        return True
    return False


def classify(c, qtype):
    """返回 (是缺图, 标签, 原因)。
    标签: 'NO_IMAGE' / 'INSTR' / 'TABLE' / 'FORMULA' / 'PROCESS'
    白名单匹配范围限制在题面前 150 字符（避开题尾的"请直接写出..."等子问）。
    """
    if not c:
        return False, '', ''
    m = FIG_REF_RE.search(c)
    if not m:
        return False, '', ''

    # 白名单只匹配题面前段，避免题尾子问/广告命中
    head = c[:150]

    if INSTRUCTION_RE.search(head):
        return False, 'INSTR', '指令题(用/列/直接写出)'
    if TABLE_RE.search(head):
        return False, 'TABLE', '数据/表题'
    if FORMULA_RE.search(head):
        return False, 'FORMULA', '算式题'
    if PROCESS_RE.search(head):
        return False, 'PROCESS', '解答过程题'

    # 真缺图
    return True, 'NO_IMAGE', '图引词=' + m.group(0)


rows = cur.execute("""
SELECT id, content, options, type, grade, ship_ready, source_tag, source_doc
FROM questions WHERE status='active'
""").fetchall()

whitelist_hits = []
no_image_hits = []
for r in rows:
    flag, label, reason = classify(r['content'] or '', r['type'])
    if flag:
        no_image_hits.append({**dict(r), 'fig_ref': reason})
    elif label:
        whitelist_hits.append({**dict(r), 'whitelist': label, 'reason': reason})

print('NO_IMAGE 真缺图:', len(no_image_hits))
print('WHITELIST 误判过滤:', len(whitelist_hits))
print()

print('--- 真缺图（建议归档）：', len(no_image_hits), '条 ---')
for r in no_image_hits:
    c = r['content']
    if len(c) > 100:
        c = c[:100] + '...'
    print('  ' + r['id'][:8] + ' [' + r['type'] + '/g' + str(r['grade']) + '] fig=' + r['fig_ref'] + ' src=' + (r['source_doc'] or 'None'))
    print('    ' + repr(c))
print()

print('--- 白名单过滤（应保留）：', len(whitelist_hits), '条 ---')
for r in whitelist_hits:
    c = r['content']
    if len(c) > 100:
        c = c[:100] + '...'
    print('  ' + r['id'][:8] + ' [' + r['type'] + '/g' + str(r['grade']) + '] wh=' + r['whitelist'])
    print('    ' + repr(c))

# 写 CSV
out_dir = 'scripts/data_cleanup'
ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
csv_path = out_dir + '/20260818_C_no_image_v2_' + ts + '.csv'
with open(csv_path, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['id', 'type', 'grade', 'fig_ref', 'ship_ready', 'source_tag', 'source_doc', 'content'])
    seen = set()
    for r in no_image_hits:
        if r['id'] in seen:
            continue
        seen.add(r['id'])
        c = r['content']
        if len(c) > 200:
            c = c[:200] + '...'
        w.writerow([r['id'], r['type'], r['grade'], r['fig_ref'], r['ship_ready'], r['source_tag'] or '', r['source_doc'] or '', c])
print()
print('归档清单 CSV:', csv_path, '(' + str(len(seen)) + ' 行)')

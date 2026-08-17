# -*- coding: utf-8 -*-
"""全量体检最终扫描器（2026-08-18 复检 v2）
space-normalize 后再匹配，避免 '上 图' 漏抓。
产出：
  C_HARD : 明确需要外部图（全库 image_url 空）→ 必删
  A      : 表述不完整（占位符/括号不平衡/截断/HTML）
  C_BORDER: 待核（图形/数/折/拼/轴对称/平移…），逐个标 recommendation
其余（选择题选项完整性 D）已单独验证为 0。
"""
import sqlite3, re, json, csv, os

DB = 'dev.db'
OUT = os.path.dirname(os.path.abspath(__file__))
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

def norm(s):
    return re.sub(r'\s+', '', s or '')

# ---------- A 类 ----------
A_CONT = re.compile(r'<[a-zA-Z/][^>]*>')
A_TRUNC_TAIL = set('如果则即得求还共每因为所以但是但而或与和可至对到给使让把被比从当于')
# 真占位符（注意：选择题/填空题题干里的 全角（） 是正常填空位，不算占位符）
A_PLACE = re.compile(r'答案略|（略）|略\s*$|待补|待定|此处|\?{3,}|？{3,}|【\s*】|……\s*$|…{2,}$')
# 括号不平衡只对"不用（）做题"的题型生效（选择/填空的（）是正常空位）
BRACKET_TYPES = ('应用','解答','计算','判断','图形')
def check_A(c, qtype):
    c = c.strip()
    if not c: return 'A_EMPTY','content空'
    if A_CONT.search(c): return 'A_HTML','残留HTML标签'
    if A_PLACE.search(c): return 'A_PLACE','占位符'
    if qtype in BRACKET_TYPES and (c.count('（')!=c.count('）') or c.count('(')!=c.count(')')):
        return 'A_BRACKET','括号不平衡'
    if len(c)>=6 and c[-1] in A_TRUNC_TAIL and not c.rstrip().endswith(('？','?','。','）',')','”','"')):
        if not (qtype=='判断' and c[-1] in '对错'):
            return 'A_TRUNC','结尾连词疑似截断'
    if re.search(r'（\s*\d+\s*分\s*）', c): return 'A_SCORE','(N分)残留'
    return None,''

# ---------- C 硬（必删）----------
# 无歧义的强图依赖词（命中即视为缺图，因全库 image_url 为空）
C_HARD = re.compile(
    r'如图|如下图|上图|下图|看图|右图|左图|图示|图中所示|图中是|图中|如图所示|读图|'
    r'竖式|横式|'
    r'钟面|钟表|时钟|表盘|'
    r'数轴|'
    r'展开图|'
    r'统计图|条形图|折线图|饼图|扇形图|'
    r'方格图|方格纸|点子图|'
    r'三视图|'
    r'线段图|'
    r'示意图|'
    r'天平|砝码|'
    r'七巧板|量角器|圆规|直尺|'
    r'被遮住|遮住了一部分|露出|重叠部分|'
    r'平面图形|立体图形|网络图|网格图'
)
C_WHITE = re.compile(
    r'用竖式计算|列竖式计算|用横式计算|列式计算|直接写出|口算下列|'
    r'价格是|价目表|统计表|信息表|成绩(表|单|汇总)|身份证|公民身份|购物小票|购物清单|账单|'
    r'下面是.*?(价格|数量|成绩|分数|人数|金额|名字|日期|身份证|信息|统计|表格|数据|清单|名单|单价|姓名)|'
    r'在这道算式中|下列算式|把.*填入.*算式|填入.*算式|'
    r'下面是.*解答过程|下列解法|两种解法|解法一|解法二|谁做得对|两种思路|'
    r'下面是小丽和小刚|小红和小明|小明和|小刚和|小红和|小亮和'
)

# ---------- C 待核 → 细分 ----------
# 待核信号
SOFT = re.compile(r'图形|画一个|画出|作图|数一数|数图形|拼成|拼一拼|搭一搭|涂色|描红|连线|平面图|立体图')
SELF_SYMBOL = re.compile(r'[○△□☆▲●■◆★✦✧▽◇♢♤♡◻◯]')
STUDENT_ACTION = re.compile(r'请你画|画一画|我会画|自己画|动手画|请你折|动手折|请你拼|动手拼|请你涂|动手涂|请你连|动手连|描一描|请你量|量一量|请你剪|剪一剪')
# 待核里"必须看图"的强信号
BORDER_NEED = re.compile(
    r'数一数|数图形|数线段|数三角形|数长方形|数正方形|'
    r'折成正方体|能折成|不能折成|'
    r'密铺|'
    r'平移|'
    r'拼成图形|拼成下面|'
    r'重叠部分|'
    r'露出|'
    r'指定方向看|从.*面看|从正面看|从上面看|从侧面看|'
    r'添加.*小正方形|'
    r'在图中画|在图上画|图中画|图上画|'
    r'下面的图形.*画|画“✔”|画“×”|画\"✔\"|画\"×\"'
)
# 待核里"文字可解"的强信号（轴对称用字母/汉字举例、纯概念判断）
BORDER_KEEP = re.compile(
    r'轴对称.*[A-Za-z一-龥]|在英文字母|在汉字|下列字母|下列汉字|'
    r'是轴对称图形|不是轴对称图形|有.*条对称轴|对称轴的图形|'
    r'由两个|两个一样|两个完全一样|'
    r'圆环是|半圆|由三条线|由四条线'
)

def classify_c(content):
    n = norm(content)
    m = C_HARD.search(n)
    if m:
        if C_WHITE.search(content[:200]):
            return None, ''
        return 'C_HARD', '图引词=%s' % m.group(0)
    # 待核
    if SOFT.search(content):
        if SELF_SYMBOL.search(content) or STUDENT_ACTION.search(content):
            return None, ''  # 自包含符号或学生自行动手 → 保留
        if BORDER_NEED.search(content):
            return 'C_BORDER_NEED', '疑似需图(数/折/拼/平移/重叠)'
        if BORDER_KEEP.search(content):
            return 'C_BORDER_KEEP', '文字可解(轴对称/概念)'
        return 'C_BORDER_REVIEW', '待人工核'
    return None, ''

rows_hard, rows_a, rows_border = [], [], []
for r in cur.execute("SELECT id,content,type,grade,edition,source_doc,ship_ready,options FROM questions WHERE status='active'"):
    cid=r['id']; content=r['content'] or ''; qtype=r['type']
    f,reason = check_A(content, qtype)
    if f:
        rows_a.append({'id':cid,'type':qtype,'grade':r['grade'],'source_doc':r['source_doc'] or '',
                       'ship_ready':r['ship_ready'],'class':f,'reason':reason,'preview':content[:90].replace('\n','\\n')})
    f,reason = classify_c(content)
    if f in ('C_HARD',):
        rows_hard.append({'id':cid,'type':qtype,'grade':r['grade'],'source_doc':r['source_doc'] or '',
                          'ship_ready':r['ship_ready'],'class':f,'reason':reason,'preview':content[:90].replace('\n','\\n')})
    elif f and f.startswith('C_BORDER'):
        rows_border.append({'id':cid,'type':qtype,'grade':r['grade'],'source_doc':r['source_doc'] or '',
                            'ship_ready':r['ship_ready'],'class':f,'reason':reason,'preview':content[:90].replace('\n','\\n')})

def wc(name, rows):
    p=os.path.join(OUT,name)
    with open(p,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=['id','type','grade','source_doc','ship_ready','class','reason','preview'])
        w.writeheader(); w.writerows(rows)
    return p

pH=wc('20260818_full_C_HARD.csv',rows_hard)
pA=wc('20260818_full_A.csv',rows_a)
pB=wc('20260818_full_C_BORDER.csv',rows_border)
print('C_HARD(必删):',len(rows_hard),'->',pH)
print('A(表述不完整):',len(rows_a),'->',pA)
print('C_BORDER(待核):',len(rows_border),'->',pB)
from collections import Counter
print('  BORDER子类:',Counter(x['class'] for x in rows_border).most_common())
con.close()

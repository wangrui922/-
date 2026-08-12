# -*- coding: utf-8 -*-
"""把 filled CSV 转成自包含 HTML 复核器（数据内嵌，双击浏览器即可看）。
用法: python generate_review_html.py
输出: docs/unmapped-review.html
"""
import csv, json, os, html

SRC = "D:/制作网页/cuotixiaolaoshi/docs/unmapped-human-triage-20260811.filled.csv"
OUT = "D:/制作网页/cuotixiaolaoshi/docs/unmapped-review.html"

rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
for i, r in enumerate(rows):
    r["_idx"] = i

kps = sorted({r["human_kp"] for r in rows if r["human_kp"]})
types = sorted({r["type"] for r in rows if r["type"]})
labeled = sum(1 for r in rows if r["human_kp"])
skipped = len(rows) - labeled

data_js = json.dumps(rows, ensure_ascii=False)

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>练习题库标注复核器</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:-apple-system,"Microsoft YaHei",sans-serif; background:#f5f6f8; color:#222; }}
  header {{ background:#1f6feb; color:#fff; padding:16px 20px; position:sticky; top:0; z-index:10; }}
  header h1 {{ margin:0 0 4px; font-size:18px; }}
  header .sub {{ font-size:12px; opacity:.9; }}
  .stats {{ display:flex; gap:14px; margin-top:10px; flex-wrap:wrap; }}
  .stat {{ background:rgba(255,255,255,.18); padding:6px 12px; border-radius:8px; font-size:13px; }}
  .stat b {{ font-size:16px; }}
  .bar {{ background:#fff; padding:12px 20px; display:flex; gap:10px; flex-wrap:wrap; align-items:center;
          border-bottom:1px solid #e3e6ea; position:sticky; top:84px; z-index:9; }}
  .bar select, .bar input {{ padding:7px 10px; border:1px solid #cdd3da; border-radius:7px; font-size:13px; }}
  .bar input {{ flex:1; min-width:200px; }}
  .bar .btn {{ padding:7px 12px; border:none; background:#eef1f5; border-radius:7px; cursor:pointer; font-size:13px; }}
  main {{ padding:16px 20px; max-width:1000px; margin:0 auto; }}
  .card {{ background:#fff; border:1px solid #e7eaee; border-radius:10px; padding:13px 15px; margin-bottom:11px;
          box-shadow:0 1px 2px rgba(0,0,0,.03); }}
  .card.skip {{ border-color:#f0c2c2; background:#fff7f7; }}
  .card .top {{ display:flex; gap:8px; align-items:center; margin-bottom:6px; }}
  .tag {{ font-size:11px; padding:2px 8px; border-radius:20px; background:#eef1f5; color:#555; }}
  .tag.type {{ background:#e8f0fe; color:#1f6feb; }}
  .tag.kp {{ background:#e7f7ec; color:#1a7f37; font-weight:600; }}
  .tag.suggest {{ background:#fff4e0; color:#9a6700; }}
  .tag.skip {{ background:#fde0e0; color:#b71c1c; font-weight:600; }}
  .card .stem {{ white-space:pre-wrap; line-height:1.6; font-size:14px; }}
  .empty {{ text-align:center; color:#999; padding:40px; }}
</style>
</head>
<body>
<header>
  <h1>练习题库 · 标注复核器</h1>
  <div class="sub">数据来自 unmapped-human-triage-20260811.filled.csv · 双击本页面即可离线查看，无需联网</div>
  <div class="stats">
    <div class="stat">总题数 <b>{len(rows)}</b></div>
    <div class="stat">已标注 <b>{labeled}</b></div>
    <div class="stat">留空待人工 <b>{skipped}</b></div>
  </div>
</header>
<div class="bar">
  <select id="fKp"><option value="">全部知识点</option>{''.join(f'<option>{html.escape(k)}</option>' for k in kps)}</select>
  <select id="fType"><option value="">全部题型</option>{''.join(f'<option>{html.escape(t)}</option>' for t in types)}</select>
  <select id="fState">
    <option value="">全部状态</option>
    <option value="labeled">已标注</option>
    <option value="skip">留空待人工</option>
  </select>
  <input id="q" placeholder="搜题干关键词，例如：计数器 / 元角分 / 圆柱">
  <button class="btn" onclick="render()">筛选</button>
  <button class="btn" onclick="reset()">重置</button>
</div>
<main id="list"></main>
<script>
const DATA = {data_js};
function esc(s){{ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;'); }}
function render() {{
  const kp = document.getElementById('fKp').value;
  const tp = document.getElementById('fType').value;
  const st = document.getElementById('fState').value;
  const q  = document.getElementById('q').value.trim().toLowerCase();
  const out = document.getElementById('list');
  let n = 0; out.innerHTML = '';
  for (const r of DATA) {{
    if (kp && r.human_kp !== kp) continue;
    if (tp && r.type !== tp) continue;
    if (st === 'labeled' && !r.human_kp) continue;
    if (st === 'skip' && r.human_kp) continue;
    if (q && !(r.stem||'').toLowerCase().includes(q)) continue;
    n++;
    const skip = !r.human_kp;
    const kpTag = skip
      ? '<span class="tag skip">未标注 · 待人工</span>'
      : '<span class="tag kp">'+esc(r.human_kp)+'</span>';
    const sug = (r.suggested_kp && r.suggested_kp !== r.human_kp)
      ? '<span class="tag suggest">自动建议: '+esc(r.suggested_kp)+'</span>' : '';
    const tpTag = r.type ? '<span class="tag type">'+esc(r.type)+'</span>' : '';
    out.innerHTML += '<div class="card'+(skip?' skip':'')+'">'
      + '<div class="top">'+tpTag+kpTag+sug+'</div>'
      + '<div class="stem">'+esc(r.stem)+'</div></div>';
  }}
  if (!n) out.innerHTML = '<div class="empty">没有匹配的题目</div>';
}}
function reset() {{
  document.getElementById('fKp').value='';
  document.getElementById('fType').value='';
  document.getElementById('fState').value='';
  document.getElementById('q').value='';
  render();
}}
render();
</script>
</body>
</html>
"""

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(HTML)
print("已生成:", OUT)
print(f"  总 {len(rows)} / 已标 {labeled} / 留空 {skipped}")
print(f"  知识点 {len(kps)} 种 / 题型 {len(types)} 种")

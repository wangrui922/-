# -*- coding: utf-8 -*-
"""重新审核 filled CSV 里的 307 道已标题目，找出高置信标错。
只做分析，不动库。输出 error 清单(强信号冲突) + skip 清单。
强信号规则：只用"几乎专属某知识点"的词，命中强信号但该题 human_kp 不在命中集合 -> 判为错。
"""
import csv, json, re

SRC = "D:/制作网页/cuotixiaolaoshi/docs/unmapped-human-triage-20260811.filled.csv"

# 强信号专属词 -> 知识点。只放几乎只属于该知识点的词，降低误杀。
STRONG = {
    "人民币与货币": ["元", "角", "分", "人民币", "几元几角", "几元", "几角", "找回", "价钱", "买", "付给"],
    "时间单位": ["钟表", "钟面", "时针", "分针", "秒针", "几时", "几刻", "刻度", "上午", "下午",
                  "凌晨", "晚上", "小时", "分钟", "秒", "日历", "星期", "月份", "季度"],
    "长度单位": ["厘米", "毫米", "分米", "千米", "公里", "米"],
    "质量单位": ["克", "千克", "公斤", "吨", "重量", "重"],
    "面积": ["面积", "平方厘米", "平方米", "平方分米", "公顷"],
    "图形与几何": ["三角形", "正方形", "长方形", "圆形", "圆柱", "圆锥", "对称轴", "立体图形",
                   "平面图形", "扇形", "梯形", "平行四边形", "周长"],
    "小数": ["小数", "十分位", "百分位", "小数点后"],
    "分数": ["分数", "几分之几", "分之一", "分子", "分母"],
    "百分数": ["百分数", "百分比", "折扣", "税率", "利率", "成数", "%"],
    "比和比例": ["成正比例", "成反比例", "比例尺", "比值", "化简比"],
    "位置与方向": ["方向", "东面", "西面", "南面", "北面", "东北", "东南", "西北", "西南", "路线图"],
    "统计": ["统计图", "条形图", "折线图", "扇形图", "平均数", "中位数", "众数"],
    "方程": ["方程", "未知数", "等式"],
    "体积": ["体积", "容积", "立方厘米", "立方分米", "立方米"],
}

rows = list(csv.DictReader(open(SRC, encoding="utf-8")))

errors = []   # 高置信标错
skips = []    # 留空待人工
ok = []

for r in rows:
    qid = r["question_id"]
    kp = r["human_kp"].strip()
    stem = (r["stem"] or "")
    if not kp:
        skips.append(r)
        continue
    # 强信号扫描
    hits = []
    for kpname, words in STRONG.items():
        for w in words:
            if w in stem:
                hits.append(kpname)
                break
    hits = list(dict.fromkeys(hits))  # 去重保序
    if hits and kp not in hits:
        # 命中强信号但标成了别的 -> 高置信错
        errors.append({"qid": qid, "type": r["type"], "kp": kp,
                       "hits": hits, "stem": stem})
    else:
        ok.append(r)

print(f"总 {len(rows)} | 已标 {len(rows)-len(skips)} | 留空 {len(skips)}")
print(f">>> 强信号冲突(高置信标错): {len(errors)}")
print(f">>> 无冲突保留: {len(ok)}")
print()
print("===== 高置信标错样例(最多20) =====")
for e in errors[:20]:
    print(f"  [{e['type']}] 现标={e['kp']} | 强信号指向={e['hits']}")
    print(f"      题干: {e['stem'][:70]}")
print()
print("===== 留空待人工(38) 题型分布 =====")
from collections import Counter
print("  ", dict(Counter(r["type"] for r in skips)))

# 导出审核结论 JSON 供后续归档脚本使用
out = {
    "error_ids": [e["qid"] for e in errors],
    "skip_ids": [r["question_id"] for r in skips],
    "errors": errors,
}
with open("D:/制作网页/cuotixiaolaoshi/docs/audit-result-20260812.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\n审核结论已写: docs/audit-result-20260812.json")

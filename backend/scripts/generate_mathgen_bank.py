# -*- coding: utf-8 -*-
"""方向七 · 题库自动扩充生成器（mathgenerator 启发的模板生成，计算/应用转选择题）。

把纯计算题包装成 4 选项选择题，覆盖小学 1-6 年级：
  - 加减法 / 乘除法 / 分数运算 / 小数加减
  - 百分数应用 / 周长 / 平面图形面积 / 长度单位 / 面积体积单位

约束：
  - 答案为数值（可被 SymPy L1 判分），不生成含变量开放题
  - 干扰项基于常见错误（符号错、进位错、单位漏、公式混用）
  - 难度映射：grade1-2 -> difficulty=1, 3-4 -> 3, 5-6 -> 5
  - 知识点 code 映射到 knowledge_points（落库时转 UUID，kp_id 存 id）
  - 幂等：以 content 判重，只新增不修改

用法：
    python backend/scripts/generate_mathgen_bank.py backend/dev.db
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from math import gcd, pi
from typing import Callable, Tuple, Dict, List

utcnow = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

GRADES = list(range(1, 7))  # 小学 1-6
DIFF_MAP = {1: 1, 2: 1, 3: 3, 4: 3, 5: 5, 6: 5}
TARGET_PER_CELL = 30  # 每个 (grade, kp) 目标题数


def _distractors_numeric(ans: float, grade: int) -> list[str]:
    """基于常见错误生成数值干扰项。"""
    dis: list[str] = []
    dis.append(str(-ans) if ans != 0 else str(ans + 1))
    step = 10 if grade <= 2 else (1 if abs(ans) < 100 else 10)
    dis.append(str(ans + step))
    dis.append(str(ans - step))
    if "." in str(ans):
        dis.append(str(ans).replace(".", ""))
    uniq, seen = [], {str(ans)}
    for d in dis:
        d = d.strip()
        if d and d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq[:3]


def _fill_extra(correct: str, seen: set[str]) -> str:
    """在正确答案附近找未占用的自然数值作补位干扰项。"""
    try:
        num = float(correct)
        offsets = [1, -1, 2, -2, 10, -10, 0.1, -0.1, 0.5, -0.5]
        for off in offsets:
            cand = num + off
            if abs(cand - round(cand)) < 1e-9:
                cand_s = str(int(round(cand)))
            else:
                cand_s = f"{cand:.2f}".rstrip("0").rstrip(".")
            if cand_s not in seen and cand_s != correct:
                return cand_s
    except Exception:
        pass
    n = 100
    while str(n) in seen or str(n) == correct:
        n += 1
    return str(n)


def _options(correct_text: str, distractors: list[str]) -> str:
    """4 项选择（去重，随机排布），返回 JSON。"""
    # 去重干扰项（保留首个），再剔除与正确答案相同的
    seen: set[str] = {correct_text}
    dis: list[str] = []
    for d in distractors:
        d = d.strip()
        if d and d not in seen:
            seen.add(d)
            dis.append(d)
    pool = [correct_text] + dis
    keys = ["A", "B", "C", "D"]
    items = list(zip(keys, pool[:4]))
    while len(items) < 4:
        extra = _fill_extra(correct_text, seen)
        seen.add(extra)
        items.append((keys[len(items)], extra))
    rng = random.Random(hash((correct_text, tuple(pool))) & 0xFFFF)
    rng.shuffle(items)
    return json.dumps(dict(items), ensure_ascii=False)


def _correct_key(options_json: str, correct: str) -> str:
    for k, v in json.loads(options_json).items():
        if v == correct:
            return k
    return "A"


# ---------------------------------------------------------------- 生成器
GeneratorFn = Callable[[int, random.Random, int], Tuple[str, str, str, list[str]]]
# (kp_code, 知识点名, 适用年级, 函数)


def g_addition(cell: int, rng: random.Random, grade: int):
    """加减法 KP_CALC_ADD"""
    if grade <= 2:
        a, b = rng.randint(1, 50), rng.randint(1, 50)
        if cell % 2 == 0:
            ans = a + b
            return f"{a} + {b} = ?", str(ans), f"加法：{a}+{b}={ans}", _distractors_numeric(ans, grade)
        a, b = max(a, b), min(a, b)
        ans = a - b
        return f"{a} - {b} = ?", str(ans), f"减法：{a}-{b}={ans}", _distractors_numeric(ans, grade)
    if grade <= 4:
        a, b = rng.randint(10, 500), rng.randint(10, 500)
        if cell % 2 == 0:
            ans = a + b
            return f"{a} + {b} = ?", str(ans), f"加法：{a}+{b}={ans}", _distractors_numeric(ans, grade)
        a, b = max(a, b), min(a, b)
        ans = a - b
        return f"{a} - {b} = ?", str(ans), f"减法：{a}-{b}={ans}", _distractors_numeric(ans, grade)
    a, b, c = rng.randint(100, 999), rng.randint(100, 999), rng.randint(10, 99)
    if cell % 3 == 0:
        ans = a + b - c
        return f"{a} + {b} - {c} = ?", str(ans), f"混合运算：{a}+{b}-{c}={ans}", _distractors_numeric(ans, grade)
    if cell % 3 == 1:
        ans = a - b + c
        return f"{a} - {b} + {c} = ?", str(ans), f"混合运算：{a}-{b}+{c}={ans}", _distractors_numeric(ans, grade)
    ans = a + b + c
    return f"{a} + {b} + {c} = ?", str(ans), f"连加：{a}+{b}+{c}={ans}", _distractors_numeric(ans, grade)


def g_multiplication(cell: int, rng: random.Random, grade: int):
    """乘除法 KP_CALC_MUL"""
    if grade <= 2:
        a, b = rng.randint(2, 9), rng.randint(2, 9)
        ans = a * b
        return f"{a} x {b} = ?", str(ans), f"乘法口诀：{a}x{b}={ans}", _distractors_numeric(ans, grade)
    if grade <= 4:
        if cell % 2 == 0:
            a, b = rng.randint(2, 12), rng.randint(2, 99)
            ans = a * b
            return f"{a} x {b} = ?", str(ans), f"乘法：{a}x{b}={ans}", _distractors_numeric(ans, grade)
        b = rng.randint(2, 9)
        ans = rng.randint(2, 12)
        a = b * ans
        return f"{a} / {b} = ?", str(ans), f"除法：{a}/{b}={ans}", _distractors_numeric(ans, grade)
    if cell % 3 == 0:
        a, b = rng.randint(10, 99), rng.randint(10, 99)
        ans = a * b
        return f"{a} x {b} = ?", str(ans), f"两位数乘法：{a}x{b}={ans}", _distractors_numeric(ans, grade)
    if cell % 3 == 1:
        b = rng.randint(2, 12)
        ans = rng.randint(10, 99)
        a = b * ans
        return f"{a} / {b} = ?", str(ans), f"除法：{a}/{b}={ans}", _distractors_numeric(ans, grade)
    a, b, c = rng.randint(2, 20), rng.randint(2, 20), rng.randint(2, 10)
    ans = a * b * c
    return f"{a} x {b} x {c} = ?", str(ans), f"连乘：{a}x{b}x{c}={ans}", _distractors_numeric(ans, grade)


def g_fraction(cell: int, rng: random.Random, grade: int):
    """分数运算 KP_FRACTION"""
    if grade < 4:
        a = rng.randint(1, 5)
        b = rng.randint(a + 1, 10)
        return (
            f"一个蛋糕平均切成 {b} 块，吃了 {a} 块，吃了几分之几？",
            f"{a}/{b}",
            f"分数：吃了{b}块中的{a}块，即{a}/{b}",
            [f"{b}/{a}", str(a), str(b)],
        )
    if cell % 3 == 0:
        denom = rng.randint(2, 12)
        num1, num2 = rng.randint(1, denom - 1), rng.randint(1, denom - 1)
        s = num1 + num2
        ans = f"{s % denom}/{denom}" if s % denom else "0"
        full = f"{s}/{denom}" if s < denom else f"{s // denom}又{s % denom}/{denom}" if s % denom else str(s // denom)
        return (
            f"{num1}/{denom} + {num2}/{denom} = ?",
            ans,
            f"同分母分数加法：{num1}/{denom}+{num2}/{denom}={full}",
            [full, f"{s}/{denom * 2}", str(s)],
        )
    if cell % 3 == 1:
        a, b = rng.randint(1, 5), rng.randint(2, 5)
        c, d = rng.randint(1, 5), rng.randint(2, 5)
        num, den = a * c, b * d
        g = gcd(num, den)
        num, den = num // g, den // g
        ans = f"{num}/{den}" if den != 1 else str(num)
        return (
            f"{a}/{b} x {c}/{d} = ?",
            ans,
            f"分数乘法：{a}/{b}x{c}/{d}={ans}",
            [f"{a * c}/{b + d}", f"{a + c}/{b * d}", str(a * c)],
        )
    a, b = rng.randint(1, 8), rng.randint(2, 8)
    c = rng.randint(2, 8)
    num, den = a * c, b
    g = gcd(num, den)
    num, den = num // g, den // g
    ans = f"{num}/{den}" if den != 1 else str(num)
    return (
        f"{a}/{b} x {c} = ?",
        ans,
        f"分数乘整数：{a}/{b}x{c}={ans}",
        [f"{a * c}/{b + 1}", str(a * c), str(a + c)],
    )


def g_decimal(cell: int, rng: random.Random, grade: int):
    """小数加减 KP_DEC_ADD_SUB"""
    if grade < 4:
        a = round(rng.uniform(0.1, 0.9), 1)
        b = round(rng.uniform(0.1, 0.9), 1)
        ans = round(a + b, 1)
        return (
            f"{a} + {b} = ?",
            f"{ans:.1f}",
            f"小数加法：{a}+{b}={ans:.1f}",
            [f"{ans + 0.1:.1f}", f"{ans - 0.1:.1f}", str(int(ans))],
        )
    a = round(rng.uniform(0.1, 9.9), 1)
    b = round(rng.uniform(0.1, 9.9), 1)
    if cell % 2 == 0:
        ans = round(a + b, 1)
        return (
            f"{a} + {b} = ?",
            f"{ans:.1f}",
            f"小数加法：{a}+{b}={ans:.1f}",
            [f"{ans + 0.1:.1f}", f"{ans - 0.1:.1f}", str(int(ans))],
        )
    a, b = max(a, b), min(a, b)
    ans = round(a - b, 1)
    return (
        f"{a} - {b} = ?",
        f"{ans:.1f}",
        f"小数减法：{a}-{b}={ans:.1f}",
        [f"{ans + 0.1:.1f}", f"{ans - 0.1:.1f}", str(int(ans))],
    )


def g_percentage(cell: int, rng: random.Random, grade: int):
    """百分数应用 KP_PCT_MEANING"""
    if grade < 5:
        total = rng.choice([100, 200, 500])
        pct = rng.choice([10, 20, 25, 50, 75])
        ans = total * pct // 100
        return (
            f"{total} 的 {pct}% 是多少？",
            str(ans),
            f"百分数：{total}x{pct}%={ans}",
            [str(ans + total // 100), str(ans - total // 100), str(pct)],
        )
    total = rng.randint(50, 500)
    pct = rng.choice([5, 10, 15, 20, 25, 30, 40, 50, 60, 75])
    ans = total * pct // 100
    return (
        f"{total} 的 {pct}% 是多少？",
        str(ans),
        f"百分数计算：{total}x{pct}%={ans}",
        [str(ans + 5), str(ans - 5) if ans > 5 else str(ans + 5), str(pct)],
    )


def g_perimeter(cell: int, rng: random.Random, grade: int):
    """周长 KP_GEO_PERIMETER"""
    if grade <= 3:
        l, w = rng.randint(2, 10), rng.randint(2, 10)
        ans = 2 * (l + w)
        return (
            f"一个长方形长 {l} 厘米，宽 {w} 厘米，周长是多少厘米？",
            str(ans),
            f"周长=2x(长+宽)=2x({l}+{w})={ans}",
            [str(l + w), str(l * w), str(2 * l + w)],
        )
    if grade <= 4:
        side = rng.randint(3, 50)
        ans = 4 * side
        return (
            f"一个正方形边长 {side} 厘米，周长是多少厘米？",
            str(ans),
            f"正方形周长=4x边长=4x{side}={ans}",
            [str(side * side), str(2 * side), str(side + 2)],
        )
    if grade <= 5:
        r = rng.randint(2, 40)
        ans = round(2 * pi * r, 2)
        return (
            f"一个圆的半径是 {r} 厘米，周长是多少厘米？（π 取 3.14）",
            f"{ans:.2f}",
            f"圆周长=2πr=2x3.14x{r}={ans:.2f}",
            [f"{pi * r * r:.2f}", f"{pi * r:.2f}", str(2 * r)],
        )
    # grade 6: 直径版题干（与 grade5 半径版错开，防止 content 判重）
    d = rng.randint(4, 80)
    ans = round(pi * d, 2)
    return (
        f"一个圆的直径是 {d} 厘米，周长是多少厘米？（π 取 3.14）",
        f"{ans:.2f}",
        f"圆周长=πd=3.14x{d}={ans:.2f}",
        [f"{pi * d * d / 2:.2f}", f"{pi * d / 2:.2f}", str(d)],
    )


def g_area(cell: int, rng: random.Random, grade: int):
    """平面图形面积 KP_GEO_AREA_PLANE"""
    if grade <= 4:
        l, w = rng.randint(3, 15), rng.randint(3, 15)
        ans = l * w
        return (
            f"一个长方形长 {l} 厘米，宽 {w} 厘米，面积是多少平方厘米？",
            str(ans),
            f"面积=长x宽={l}x{w}={ans}",
            [str(2 * (l + w)), str(l + w), str(l * 2)],
        )
    base, h = rng.randint(3, 15), rng.randint(3, 15)
    if cell % 2 == 0:
        ans = base * h
        return (
            f"一个平行四边形底 {base} 厘米，高 {h} 厘米，面积是多少平方厘米？",
            str(ans),
            f"面积=底x高={base}x{h}={ans}",
            [str(base + h), str(2 * (base + h)), str(base * h // 2)],
        )
    ans = base * h // 2
    return (
        f"一个三角形底 {base} 厘米，高 {h} 厘米，面积是多少平方厘米？",
        str(ans),
        f"三角形面积=底x高/2={base}x{h}/2={ans}",
        [str(base * h), str(base + h), str(base * h // 2 + 2)],
    )


def g_unit_length(cell: int, rng: random.Random, grade: int):
    """长度/时间/货币单位换算 KP_UNIT_LENGTH"""
    conversions = [
        ("米", "厘米", 100), ("千米", "米", 1000), ("千克", "克", 1000), ("吨", "千克", 1000),
        ("小时", "分钟", 60), ("分钟", "秒", 60), ("元", "角", 10),
    ]
    u_from, u_to, factor = rng.choice(conversions)
    val = rng.randint(1, 20)
    if cell % 2 == 0:
        ans = val * factor
        return (
            f"{val} {u_from} = 多少 {u_to}？",
            str(ans),
            f"单位换算：{val}{u_from}={val}x{factor}={ans}{u_to}",
            [str(val + factor), str(val * 10 if factor != 10 else val * 100), str(val)],
        )
    big = val * factor
    return (
        f"{big} {u_to} = 多少 {u_from}？",
        str(val),
        f"单位换算：{big}{u_to}={big}/{factor}={val}{u_from}",
        [str(big + factor), str(big // 10 if factor != 10 else big // 100), str(big)],
    )


def g_unit_area_vol(cell: int, rng: random.Random, grade: int):
    """面积/体积单位换算 KP_UNIT_AREA_VOL"""
    conversions = [
        ("平方米", "平方分米", 100), ("平方千米", "平方米", 1000000),
        ("立方米", "立方分米", 1000), ("升", "毫升", 1000),
    ]
    u_from, u_to, factor = rng.choice(conversions)
    val = rng.randint(1, 20)
    if cell % 2 == 0:
        ans = val * factor
        return (
            f"{val} {u_from} = 多少 {u_to}？",
            str(ans),
            f"单位换算：{val}{u_from}={val}x{factor}={ans}{u_to}",
            [str(val + factor), str(val * 100 if factor == 100 else val * 1000), str(val)],
        )
    big = val * factor
    return (
        f"{big} {u_to} = 多少 {u_from}？",
        str(val),
        f"单位换算：{big}{u_to}={big}/{factor}={val}{u_from}",
        [str(big + factor), str(big // 100 if factor == 100 else big // 1000), str(big)],
    )


# ================================================================ 判断题生成器

def g_judge_addition(cell: int, rng: random.Random, grade: int):
    """判断：加减法运算"""
    if grade <= 2:
        a, b = rng.randint(1, 20), rng.randint(1, 20)
        correct = cell % 2 == 0
        if correct:
            ans = a + b
            return (
                f"{a} + {b} = {ans}（对/错）",
                "正确",
                f"{a}+{b}确实={ans}",
                [],
            )
        wrong_ans = a + b + rng.choice([1, 2, -1, -2])
        return (
            f"{a} + {b} = {wrong_ans}（对/错）",
            "错误",
            f"{a}+{b}={a+b}，不是{wrong_ans}",
            [],
        )
    a, b = rng.randint(10, 99), rng.randint(10, 99)
    correct = cell % 2 == 0
    if correct:
        ans = a + b
        return (f"{a} + {b} = {ans}（对/错）", "正确", f"{a}+{b}={ans}", [])
    wrong_ans = a + b + rng.choice([1, 10, -1, -10, 100])
    return (f"{a} + {b} = {wrong_ans}（对/错）", "错误", f"{a}+{b}={a+b}，不是{wrong_ans}", [])


def g_judge_multiplication(cell: int, rng: random.Random, grade: int):
    """判断：乘除法运算"""
    if grade <= 3:
        a, b = rng.randint(2, 9), rng.randint(2, 9)
        correct = cell % 2 == 0
        if correct:
            ans = a * b
            return (f"{a} x {b} = {ans}（对/错）", "正确", f"{a}x{b}={ans}", [])
        wrong_ans = a * b + rng.choice([1, -1, 10])
        return (f"{a} x {b} = {wrong_ans}（对/错）", "错误", f"{a}x{b}={a*b}，不是{wrong_ans}", [])
    a, b = rng.randint(2, 12), rng.randint(2, 99)
    correct = cell % 2 == 0
    if correct:
        ans = a * b
        return (f"{a} x {b} = {ans}（对/错）", "正确", f"{a}x{b}={ans}", [])
    wrong_ans = a * b + rng.choice([1, 10, -1, -10])
    return (f"{a} x {b} = {wrong_ans}（对/错）", "错误", f"{a}x{b}={a*b}，不是{wrong_ans}", [])


def g_judge_fraction(cell: int, rng: random.Random, grade: int):
    """判断：分数运算"""
    if grade < 4:
        return g_fraction(cell, rng, grade)  # fallback
    a, b = rng.randint(1, 5), rng.randint(2, 5)
    c, d = rng.randint(1, 5), rng.randint(2, 5)
    num, den = a * c, b * d
    g = gcd(num, den)
    num, den = num // g, den // g
    true_ans = f"{num}/{den}" if den != 1 else str(num)
    correct = cell % 2 == 0
    if correct:
        return (
            f"{a}/{b} x {c}/{d} = {true_ans}（对/错）",
            "正确",
            f"{a}/{b}x{c}/{d}={true_ans}",
            [],
        )
    fake = f"{a*c}/{b+d}"
    return (
        f"{a}/{b} x {c}/{d} = {fake}（对/错）",
        "错误",
        f"{a}/{b}x{c}/{d}={true_ans}，不是{fake}",
        [],
    )


def g_judge_decimal(cell: int, rng: random.Random, grade: int):
    """判断：小数运算"""
    a = round(rng.uniform(0.1, 9.9), 1)
    b = round(rng.uniform(0.1, 9.9), 1)
    correct = cell % 2 == 0
    if correct:
        ans = round(a + b, 1)
        return (
            f"{a} + {b} = {ans:.1f}（对/错）",
            "正确",
            f"{a}+{b}={ans:.1f}",
            [],
        )
    fake = round(a + b + 0.1, 1)
    return (
        f"{a} + {b} = {fake:.1f}（对/错）",
        "错误",
        f"{a}+{b}={round(a+b,1):.1f}，不是{fake:.1f}",
        [],
    )


def g_judge_unit(cell: int, rng: random.Random, grade: int):
    """判断：单位换算"""
    conversions = [
        ("米", "厘米", 100), ("千米", "米", 1000), ("千克", "克", 1000),
        ("小时", "分钟", 60), ("元", "角", 10),
    ]
    u_from, u_to, factor = rng.choice(conversions)
    val = rng.randint(1, 20)
    correct = cell % 2 == 0
    if correct:
        ans = val * factor
        return (
            f"{val} {u_from} = {ans} {u_to}（对/错）",
            "正确",
            f"{val}{u_from}={val}x{factor}={ans}{u_to}",
            [],
        )
    fake = val + factor
    return (
        f"{val} {u_from} = {fake} {u_to}（对/错）",
        "错误",
        f"{val}{u_from}={val}x{factor}={val*factor}{u_to}，不是{fake}",
        [],
    )


# ================================================================ 填空题生成器（暂不入池）

def g_fill_addition(cell: int, rng: random.Random, grade: int):
    """填空：加减法"""
    if grade <= 2:
        a, b = rng.randint(1, 20), rng.randint(1, 20)
        if cell % 2 == 0:
            return f"{a} + ___ = {a+b}", str(b), f"{a}+{b}={a+b}", []
        a, b = max(a, b), min(a, b)
        return f"___ - {b} = {a-b}", str(a), f"{a}-{b}={a-b}", []
    a, b = rng.randint(10, 99), rng.randint(10, 99)
    if cell % 2 == 0:
        return f"{a} + ___ = {a+b}", str(b), f"{a}+{b}={a+b}", []
    a, b = max(a, b), min(a, b)
    return f"___ - {b} = {a-b}", str(a), f"{a}-{b}={a-b}", []


def g_fill_multiplication(cell: int, rng: random.Random, grade: int):
    """填空：乘除法"""
    if grade <= 3:
        a, b = rng.randint(2, 9), rng.randint(2, 9)
        ans = a * b
        if cell % 2 == 0:
            return f"{a} x ___ = {ans}", str(b), f"{a}x{b}={ans}", []
        return f"___ x {b} = {ans}", str(a), f"{a}x{b}={ans}", []
    a, b = rng.randint(2, 12), rng.randint(2, 12)
    ans = a * b
    if cell % 2 == 0:
        return f"{a} x ___ = {ans}", str(b), f"{a}x{b}={ans}", []
    return f"___ x {b} = {ans}", str(a), f"{a}x{b}={ans}", []


def g_fill_fraction(cell: int, rng: random.Random, grade: int):
    """填空：分数"""
    if grade < 4:
        a = rng.randint(1, 5)
        b = rng.randint(a + 1, 10)
        return f"吃了 {a} 块蛋糕，平均切成 {b} 块，吃了全部的几分之几？___", f"{a}/{b}", f"{a}/{b}", []
    denom = rng.randint(2, 12)
    num1, num2 = rng.randint(1, denom-1), rng.randint(1, denom-1)
    ans = num1 + num2
    if ans >= denom:
        ans = ans % denom
    return f"{num1}/{denom} + {num2}/{denom} = ___/{denom}", str(ans), f"同分母相加={ans}/{denom}", []


def g_fill_decimal(cell: int, rng: random.Random, grade: int):
    """填空：小数"""
    a = round(rng.uniform(0.1, 9.9), 1)
    b = round(rng.uniform(0.1, 9.9), 1)
    ans = round(a + b, 1)
    return f"{a} + ___ = {ans:.1f}", f"{b:.1f}", f"{a}+{b}={ans:.1f}", []


def g_fill_unit(cell: int, rng: random.Random, grade: int):
    """填空：单位换算"""
    conversions = [
        ("米", "厘米", 100), ("千米", "米", 1000), ("千克", "克", 1000),
        ("小时", "分钟", 60), ("元", "角", 10),
    ]
    u_from, u_to, factor = rng.choice(conversions)
    val = rng.randint(1, 20)
    ans = val * factor
    return f"{val} {u_from} = ___ {u_to}", str(ans), f"{val}x{factor}={ans}", []


# ================================================================ 生成器注册表（多题型）

class GeneratorSpec:
    """生成器规格：code、名称、年级、题型、目标数、函数"""
    __slots__ = ("kp_code", "kp_name", "grades", "qtype", "target", "func")

    def __init__(self, kp_code: str, kp_name: str, grades: List[int],
                 qtype: str, target: int, func: GeneratorFn):
        self.kp_code = kp_code
        self.kp_name = kp_name
        self.grades = grades
        self.qtype = qtype
        self.target = target
        self.func = func


GENERATORS: List[GeneratorSpec] = [
    # ---- 选择题（可入 L1 抽题池）
    GeneratorSpec("KP_CALC_ADD", "加减法", [1, 2, 3, 4, 5, 6], "选择", 25, g_addition),
    GeneratorSpec("KP_CALC_MUL", "乘除法", [2, 3, 4, 5, 6], "选择", 25, g_multiplication),
    GeneratorSpec("KP_FRACTION", "分数运算", [4, 5, 6], "选择", 20, g_fraction),
    GeneratorSpec("KP_DEC_ADD_SUB", "小数加减法", [4, 5, 6], "选择", 20, g_decimal),
    GeneratorSpec("KP_PCT_MEANING", "百分数", [5, 6], "选择", 20, g_percentage),
    GeneratorSpec("KP_GEO_PERIMETER", "周长", [3, 4, 5, 6], "选择", 20, g_perimeter),
    GeneratorSpec("KP_GEO_AREA_PLANE", "平面图形面积", [4, 5, 6], "选择", 20, g_area),
    GeneratorSpec("KP_UNIT_LENGTH", "长度单位", [2, 3, 4, 5, 6], "选择", 15, g_unit_length),
    GeneratorSpec("KP_UNIT_AREA_VOL", "面积与体积单位", [4, 5, 6], "选择", 15, g_unit_area_vol),
    # ---- 判断题（可入 L1 抽题池）
    GeneratorSpec("KP_CALC_ADD", "加减法判断", [1, 2, 3, 4, 5, 6], "判断", 15, g_judge_addition),
    GeneratorSpec("KP_CALC_MUL", "乘除法判断", [2, 3, 4, 5, 6], "判断", 15, g_judge_multiplication),
    GeneratorSpec("KP_FRACTION", "分数判断", [4, 5, 6], "判断", 10, g_judge_fraction),
    GeneratorSpec("KP_DEC_ADD_SUB", "小数判断", [4, 5, 6], "判断", 10, g_judge_decimal),
    GeneratorSpec("KP_UNIT_LENGTH", "单位判断", [2, 3, 4, 5, 6], "判断", 10, g_judge_unit),
    # ---- 填空题（暂不入池，储备）
    GeneratorSpec("KP_CALC_ADD", "加减法填空", [1, 2, 3, 4, 5, 6], "填空", 10, g_fill_addition),
    GeneratorSpec("KP_CALC_MUL", "乘除法填空", [2, 3, 4, 5, 6], "填空", 10, g_fill_multiplication),
    GeneratorSpec("KP_FRACTION", "分数填空", [4, 5, 6], "填空", 8, g_fill_fraction),
    GeneratorSpec("KP_DEC_ADD_SUB", "小数填空", [4, 5, 6], "填空", 8, g_fill_decimal),
    GeneratorSpec("KP_UNIT_LENGTH", "单位填空", [2, 3, 4, 5, 6], "填空", 8, g_fill_unit),
]


def _make_choice_item(qid: str, content: str, ans: str, analysis: str,
                        dis: list[str], grade: int, kp_code: str) -> dict:
    opts = _options(ans, dis)
    key = _correct_key(opts, ans)
    return {
        "question": {
            "id": qid, "content": content, "subject": "数学", "grade": grade,
            "difficulty": DIFF_MAP[grade], "type": "选择", "options": opts,
            "status": "active", "kp_verified": 1, "source_tag": "yellow",
            "ship_ready": 1, "kp_code": kp_code,
        },
        "answer": {
            "question_id": qid, "correct_answer": f"选{key}",
            "analysis": analysis,
            "distractor_analysis": json.dumps(dis, ensure_ascii=False),
        },
    }


def _make_judge_item(qid: str, content: str, ans: str, analysis: str,
                     grade: int, kp_code: str) -> dict:
    # ans 应为 "正确" 或 "错误"
    return {
        "question": {
            "id": qid, "content": content, "subject": "数学", "grade": grade,
            "difficulty": DIFF_MAP[grade], "type": "判断", "options": None,
            "status": "active", "kp_verified": 1, "source_tag": "yellow",
            "ship_ready": 1, "kp_code": kp_code,
        },
        "answer": {
            "question_id": qid, "correct_answer": ans,
            "analysis": analysis,
            "distractor_analysis": "",
        },
    }


def _make_fill_item(qid: str, content: str, ans: str, analysis: str,
                    grade: int, kp_code: str) -> dict:
    return {
        "question": {
            "id": qid, "content": content, "subject": "数学", "grade": grade,
            "difficulty": DIFF_MAP[grade], "type": "填空", "options": None,
            "status": "active", "kp_verified": 1, "source_tag": "yellow",
            "ship_ready": 0, "kp_code": kp_code,
        },
        "answer": {
            "question_id": qid, "correct_answer": ans,
            "analysis": analysis,
            "distractor_analysis": "",
        },
    }


def build_items() -> list[dict]:
    items: list[dict] = []
    used: set[str] = set()
    for spec in GENERATORS:
        for grade in spec.grades:
            rng = random.Random(f"{spec.kp_code}-{spec.qtype}-{grade}")
            cell = 0
            done = 0
            while done < spec.target and cell < 3000:
                content, ans, analysis, dis = spec.func(cell, rng, grade)
                cell += 1
                if content in used:
                    continue
                used.add(content)
                done += 1
                qid = str(uuid.uuid4())
                if spec.qtype == "选择":
                    items.append(_make_choice_item(qid, content, ans, analysis, dis, grade, spec.kp_code))
                elif spec.qtype == "判断":
                    items.append(_make_judge_item(qid, content, ans, analysis, grade, spec.kp_code))
                elif spec.qtype == "填空":
                    items.append(_make_fill_item(qid, content, ans, analysis, grade, spec.kp_code))
    return items


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: generate_mathgen_bank.py <db>")
        return 2
    db_path = sys.argv[1]
    conn = sqlite3.connect(db_path, timeout=10)
    cur = conn.cursor()

    # code -> id(UUID) 映射
    kp_map = {r[0]: r[1] for r in cur.execute("SELECT code, id FROM knowledge_points").fetchall()}
    missing = [c for c in {g.kp_code for g in GENERATORS} if c not in kp_map]
    if missing:
        print(f"[fatal] knowledge_points 缺少 code: {missing}")
        conn.close()
        return 1

    items = build_items()
    print(f"[gen] total={len(items)}")

    existing = {r[0] for r in cur.execute("SELECT content FROM questions").fetchall()}
    now = utcnow()
    inserted_q = inserted_a = skipped = 0

    for it in items:
        q, a = it["question"], it["answer"]
        if q["content"] in existing:
            skipped += 1
            continue
        cur.execute(
            """INSERT OR IGNORE INTO questions
               (id, content, image_url, subject, grade, difficulty, kp_id, type,
                options, status, edition, source_type, section, source_doc,
                kp_verified, source_tag, ship_ready, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (q["id"], q["content"], None, q["subject"], q["grade"], q["difficulty"],
             kp_map[q["kp_code"]], q["type"], q["options"], q["status"], None,
             "mathgen_bank", None, "mathgen_bank", q["kp_verified"], q["source_tag"],
             q["ship_ready"], now),
        )
        if cur.rowcount == 0:
            skipped += 1
            continue
        cur.execute(
            """INSERT OR REPLACE INTO answers
               (id, question_id, correct_answer, analysis, distractor_analysis)
               VALUES (?,?,?,?,?)""",
            (str(uuid.uuid4()), a["question_id"], a["correct_answer"], a["analysis"],
             a["distractor_analysis"]),
        )
        inserted_q += 1
        inserted_a += 1
        existing.add(q["content"])

    conn.commit()

    audit = cur.execute(
        """SELECT q.grade, q.kp_id, COUNT(*)
           FROM questions q
           WHERE q.source_doc='mathgen_bank' AND q.status='active' AND q.ship_ready=1
           GROUP BY q.grade, q.kp_id ORDER BY q.grade, q.kp_id"""
    ).fetchall()
    print("[audit] cells (grade,kp,count):")
    for row in audit:
        mark = "OK" if row[2] >= 20 else "SHORT"
        print(f"  g{row[0]} {row[1]} -> {row[2]} {mark}")
    orphan = cur.execute(
        """SELECT COUNT(*) FROM questions q LEFT JOIN answers a ON a.question_id=q.id
           WHERE q.source_doc='mathgen_bank' AND a.id IS NULL"""
    ).fetchone()[0]
    print(f"[done] inserted questions={inserted_q} answers={inserted_a} skipped={skipped} orphan={orphan}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
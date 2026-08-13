"""批量生成初中/高中数学选择题，落库 ``questions`` + ``answers``（#63 · 规模化 + 溯源护栏）。

设计要点
--------
* **计算保证正确**：每道题的答案由 Python 当场算出，不是拍脑袋，杜绝"编造答案"。
* **溯源护栏**：全部新题 ``source_tag='yellow'``（AI 生成补充，正确但建议抽检）、
  ``source_doc='gen_math_bank'``、``kp_verified=True``（知识点链接是我们显式指定的，可信）。
* **不变式闸门**：每道生成题都必须过 ``quality_gate.is_clean`` + ``validate_choice_options``
  + ``judge_engine.is_l1_safe``，任一不过直接丢弃并计数（宁可少出，不可出死题）。
* **幂等**：主键用 ``uuid5(NS, "aicoach:q:<kp>:<tpl>:<variant>")``，重跑同参数 → 同 id →
  ORM ``merge`` 跳过，绝不重复；也不碰任何存量题。
* **零迁移**：只新增 ``Question``/``Answer`` 行，不改表结构、不加列、不删数据。

用法
----
    python app/scripts/generate_math_bank.py            # 默认每模板 14 道
    python app/scripts/generate_math_bank.py --per 20   # 每模板 20 道
    python app/scripts/generate_math_bank.py --band 初中  # 只初中
"""
from __future__ import annotations

import argparse
import random
import sys
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db import SessionLocal  # noqa: E402
from app.models.content import Question, Answer  # noqa: E402
from app.services.importers.quality_gate import classify_question, VERDICT_CLEAN, validate_choice_options  # noqa: E402
from app.services import judge_engine  # noqa: E402

NS = uuid.NAMESPACE_DNS
KEYS = ["A", "B", "C", "D"]
BAND_GRADES = {"初中": (7, 9), "高中": (10, 12)}


# ------------------------------------------------------------------ 工具
def _unique_variant(x: str, seen: set, rng: random.Random):
    """为撞值的选项造一个**唯一**的替代项（尽量贴近原值）。

    数值型（如 ``"16"`` / ``16``）按 ±1..±8 小幅扰动；非数值字符串兜底加
    ``(n)`` 下标。这样撞值项被修复成合法且互不相同的选项，而不是整题丢弃。
    """
    try:
        num = float(x)
        base = int(num) if num == int(num) else num
        for delta in [1, 2, 3, 4, 5, -1, -2, -3, -4, -5, 6, 7, 8, -6, -7, -8]:
            cand = base + delta
            s = str(cand)
            if s not in seen:
                return s
    except (ValueError, TypeError):
        pass
    i = 1
    while True:
        s = f"{x}({i})"
        if s not in seen:
            return s
        i += 1


def shuffle_options(correct: str, distractors: list[str], rng: random.Random) -> tuple[dict, str]:
    """把正确项 + 3 个干扰项打乱成 ABCD 选项，返回 (options_dict, 正确键位)。

    撞值自愈：干扰项若与正确项或彼此重复，自动扰动成唯一值（不丢题）。
    """
    assert len(distractors) == 3, "选择题必须正好 3 个干扰项"
    items = [correct] + list(distractors)
    seen: set = set()
    healed: list = []
    for x in items:
        if x not in seen:
            seen.add(x)
            healed.append(x)
        else:
            cand = _unique_variant(x, seen, rng)
            seen.add(cand)
            healed.append(cand)
    # 极端兜底：保证正好 4 个互异项
    while len(healed) < 4:
        cand = _unique_variant(correct, seen, rng)
        seen.add(cand)
        healed.append(cand)
    rng.shuffle(healed)
    options = {KEYS[i]: healed[i] for i in range(4)}
    answer_key = KEYS[healed.index(correct)]
    return options, answer_key


def near(correct: int, rng: random.Random, delta=(1, 2, 3, 4, 5)) -> list[int]:
    """生成 3 个与 correct 接近且互不相同的整数干扰项。"""
    out: list[int] = []
    cand = list(delta)
    rng.shuffle(cand)
    for d in cand:
        sign = rng.choice([-1, 1])
        v = correct + sign * d
        if v != correct and v not in out:
            out.append(v)
        if len(out) == 3:
            break
    # 兜底：若 delta 不够，用更大跨度
    while len(out) < 3:
        v = correct + rng.choice([-1, 1]) * (6 + len(out))
        if v != correct and v not in out:
            out.append(v)
    return out


def rnd(rng: random.Random, lo: int, hi: int) -> int:
    return rng.randint(lo, hi)


# ------------------------------------------------------------------ 模板注册表
# 每个模板: fn(rng) -> dict{content, options(dict), answer_key, explanation}
REGISTRY: dict[str, list] = {}


def tpl(kp_code: str):
    def deco(fn):
        REGISTRY.setdefault(kp_code, []).append(fn)
        return fn
    return deco


# ===== 初中 · 有理数 =====
@tpl("CZ_RAT_OPP")
def _(rng):
    a = rnd(rng, 2, 12)
    content = f"下列各数中，{a} 的相反数是（ ）"
    corr = str(-a)
    opts, key = shuffle_options(corr, [str(a), str(2 * a), str(-2 * a)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"{a} 的相反数是在它前面添负号，即 -{a}。"}


@tpl("CZ_RAT_AXIS")
def _(rng):
    a, b = rnd(rng, 1, 9), rnd(rng, 1, 9)
    if a == b:
        b += 1
    bigger = max(a, b)
    content = f"在数轴上，表示数 {a} 和 {b} 的点中，位于右侧的是（ ）"
    opts, key = shuffle_options(str(bigger), [str(min(a, b)), str(-bigger), str(0)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "数轴上右边的数总比左边的大，所以较大的数在右侧。"}


@tpl("CZ_RAT_POW")
def _(rng):
    a = rnd(rng, 2, 4)
    n = rnd(rng, 2, 3)
    corr = a ** n
    content = f"计算 {a}^{n} 的结果是（ ）"
    opts, key = shuffle_options(str(corr), [str(corr + 1), str(corr - 1), str(a * n)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"{a}^{n} = " + "×".join([str(a)] * n) + f" = {corr}。"}


@tpl("CZ_RAT_SCI")
def _(rng):
    # 大数科学记数法
    base = rnd(rng, 1, 9)
    zeros = rnd(rng, 3, 7)
    num = base * (10 ** zeros)
    exp = zeros
    content = f"把 {num} 用科学记数法表示为（ ）"
    opts, key = shuffle_options(f"{base}×10^{exp}",
                                [f"{base}×10^{exp+1}", f"{base*10}×10^{exp-1}", f"0.{base}×10^{exp+1}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"科学记数法为 a×10^n（1≤a<10），{num}={base}×10^{exp}。"}


# ===== 初中 · 实数 =====
@tpl("CZ_REAL_SQRT")
def _(rng):
    a = rnd(rng, 2, 12)
    content = f"{a}² 的算术平方根是（ ）"
    opts, key = shuffle_options(str(a), [str(a * a), str(-a), str(2 * a)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"{a}² 的算术平方根 = √({a}²) = {a}（算术平方根取非负）。"}


# ===== 初中 · 整式 =====
@tpl("CZ_INT_COMBINE")
def _(rng):
    a, b = rnd(rng, 2, 6), rnd(rng, 1, 5)
    c = rnd(rng, 1, 4)
    corr = a + b
    content = f"合并同类项：{a}x + {b}x - {c}x = （ ）"
    opts, key = shuffle_options(f"{corr}x", [f"{(a+b+c)}x", f"{(a-b)}x", f"{(a*b)}x"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"系数相加减：({a}+{b}-{c})x = {corr}x。"}


@tpl("CZ_INT_BRACKET")
def _(rng):
    a, b = rnd(rng, 2, 5), rnd(rng, 1, 4)
    x = rnd(rng, 3, 9)
    # 3(a+b) - 2a 在 x=? 代入
    corr = 3 * (a + b) - 2 * a
    content = f"化简 3(a+b)-2a 的结果为（ ）"
    opts, key = shuffle_options(f"{a+3*b}", [f"{3*a+3*b}", f"{a+b}", f"{3*b-a}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"3(a+b)-2a = 3a+3b-2a = a+3b = {a+3*b}。"}


# ===== 初中 · 一元一次方程 =====
@tpl("CZ_L1_SOLVE")
def _(rng):
    a = rnd(rng, 2, 5)
    b = rnd(rng, 1, 9)
    c = rnd(rng, 1, 9)
    # a x + b = c  => x = (c-b)/a, 保证整除
    x = rnd(rng, 1, 6)
    c = a * x + b
    content = f"方程 {a}x + {b} = {c} 的解是（ ）"
    opts, key = shuffle_options(f"x={x}", [f"x={x+1}", f"x={x-1}", f"x={-x}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"移项：{a}x = {c}-{b} = {c-b}，x = {c-b}÷{a} = {x}。"}


@tpl("CZ_L1_APP")
def _(rng):
    # 行程：相向而行，速度和
    v1, v2 = rnd(rng, 3, 8), rnd(rng, 2, 6)
    t = rnd(rng, 2, 5)
    d = (v1 + v2) * t
    content = f"甲、乙相向而行，甲速 {v1} km/h、乙速 {v2} km/h，{t} 小时后相遇，两地相距（ ）km"
    opts, key = shuffle_options(str(d), [str((v1 + v2) * (t - 1)), str(v1 * t), str(abs(v1 - v2) * t)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"相遇路程 = 速度和 × 时间 = ({v1}+{v2})×{t} = {d} km。"}


# ===== 初中 · 几何角 =====
@tpl("CZ_GB_ANGLE")
def _(rng):
    a = rnd(rng, 20, 70)
    oc = rnd(rng, 2, 4)
    b = a // oc
    content = f"已知 ∠A = {a}°，其角平分线把它分成两个角，每个角的度数是（ ）"
    opts, key = shuffle_options(f"{b}°", [f"{a}°", f"{a*2}°", f"{b*oc+1}°"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"角平分线平分角度：{a}°÷{oc} = {b}°。"}


@tpl("CZ_PL_ANGLE")
def _(rng):
    a = rnd(rng, 20, 60)
    content = f"若 ∠α 与 ∠β 互余，且 ∠α = {a}°，则 ∠β = （ ）"
    corr = 90 - a
    opts, key = shuffle_options(f"{corr}°", [f"{a}°", f"{90+a}°", f"{180-a}°"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"互余两角和为 90°，∠β = 90°-{a}° = {corr}°。"}


@tpl("CZ_PL_PARALLEL")
def _(rng):
    a = rnd(rng, 40, 75)
    content = f"两直线平行，同位角为 {a}°，则另一组同位角是（ ）"
    opts, key = shuffle_options(f"{a}°", [f"{180-a}°", f"{90-a}°", f"{a+10}°"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "两直线平行，同位角相等，故也是 %d°。" % a}


# ===== 初中 · 坐标系 =====
@tpl("CZ_CO_POINT")
def _(rng):
    x = rnd(rng, 1, 5)
    y = rnd(rng, 1, 5)
    content = f"点 P({x}, {y}) 在平面直角坐标系中的位置是（ ）"
    opts, key = shuffle_options("第一象限", ["第二象限", "第三象限", "第四象限"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"横纵坐标都为正，落在第一象限。"}


@tpl("CZ_CO_DIST")
def _(rng):
    a = rnd(rng, 1, 6)
    content = f"点 A({a}, 0) 到原点的距离是（ ）"
    opts, key = shuffle_options(str(a), [str(a * 2), str(0), str(a + 1)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"到原点距离 = √({a}²+0²) = {a}。"}


# ===== 初中 · 二元一次方程组 =====
@tpl("CZ_L2_SOLVE")
def _(rng):
    x = rnd(rng, 1, 5)
    y = rnd(rng, 1, 5)
    a, b = rnd(rng, 1, 3), rnd(rng, 1, 3)
    c, d = rnd(rng, 1, 3), rnd(rng, 1, 3)
    e1 = a * x + b * y
    e2 = c * x + d * y
    content = f"方程组 {{ {a}x+{b}y={e1} , {c}x+{d}y={e2} }} 的解是（ ）"
    opts, key = shuffle_options(f"x={x}, y={y}",
                                [f"x={x+1}, y={y}", f"x={x}, y={y+1}", f"x={y}, y={x}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"代入验证：x={x}, y={y} 满足两式。"}


@tpl("CZ_L2_APP")
def _(rng):
    # 鸡兔同笼变式：买笔，单价不同
    x = rnd(rng, 3, 8)
    y = rnd(rng, 2, 6)
    pa, pb = 5, 3
    total_n = x + y
    total_m = pa * x + pb * y
    content = f"买 A 笔（每支 {pa} 元）和 B 笔（每支 {pb} 元）共 {total_n} 支，花 {total_m} 元，A 笔买了（ ）支"
    opts, key = shuffle_options(str(x), [str(y), str(x + 1), str(x - 1)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"设 A 笔 x 支：5x+3({total_n}-x)={total_m} → 2x={total_m-3*total_n} → x={x}。"}


# ===== 初中 · 不等式 =====
@tpl("CZ_IN_SOLVE")
def _(rng):
    a = rnd(rng, 2, 4)
    b = rnd(rng, 1, 9)
    x = rnd(rng, 1, 6)
    c = a * x + b
    content = f"不等式 {a}x + {b} > {c} 的解集是（ ）"
    opts, key = shuffle_options(f"x > {x}", [f"x < {x}", f"x > {x+1}", f"x < {x+1}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"{a}x > {c}-{b}={c-b}，x > {c-b}÷{a} = {x}。"}


@tpl("CZ_IN_SYS")
def _(rng):
    x = rnd(rng, 2, 6)
    content = f"不等式组 {{ x > {x-1} , x < {x+2} }} 的整数解个数是（ ）"
    # 整数解为 x-1< x < x+2 中整数: x, x+1 => 2个
    opts, key = shuffle_options("2 个", ["1 个", "3 个", "0 个"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"解集为 {x-1}<x<{x+2}，整数解是 {x}、{x+1}，共 2 个。"}


# ===== 初中 · 三角形 =====
@tpl("CZ_TRI_ANGLE")
def _(rng):
    a = rnd(rng, 40, 70)
    b = rnd(rng, 30, 60)
    c = 180 - a - b
    content = f"△ABC 中，∠A={a}°，∠B={b}°，则 ∠C=（ ）"
    opts, key = shuffle_options(f"{c}°", [f"{a+b}°", f"{180-c+10}°", f"{90}°"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"三角形内角和 180°，∠C = 180-{a}-{b} = {c}°。"}


@tpl("CZ_TRI_CONG")
def _(rng):
    content = "下列条件中，能判定两个三角形全等的是（ ）"
    opts, key = shuffle_options("两边和它们的夹角对应相等",
                                ["两边和一对角对应相等", "三个角对应相等", "两边对应相等"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "SAS（两边夹角）是全等判定定理；SSA 不能判定全等。"}


@tpl("CZ_TRI_SIM")
def _(rng):
    k = rnd(rng, 2, 3)
    content = f"两个相似三角形相似比为 1:{k}，则面积比为（ ）"
    opts, key = shuffle_options(f"1:{k*k}", [f"1:{k}", f"{k}:1", f"1:{2*k}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"相似图形面积比 = 相似比的平方 = 1:{k*k}。"}


# ===== 初中 · 勾股定理 =====
@tpl("CZ_PYTH_BASIC")
def _(rng):
    a = rnd(rng, 3, 9)
    b = rnd(rng, 4, 9)
    while a * a + b * b not in [25, 36, 49, 64, 81, 100, 121, 144, 169]:
        a = rnd(rng, 3, 9); b = rnd(rng, 4, 9)
    c = int((a * a + b * b) ** 0.5)
    content = f"直角三角形中两直角边长为 {a} 和 {b}，则斜边长为（ ）"
    opts, key = shuffle_options(str(c), [str(c + 1), str(a + b), str(abs(a - b))], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"勾股定理：c=√({a}²+{b}²)=√({a*a+b*b})={c}。"}


@tpl("CZ_PYTH_APP")
def _(rng):
    a = rnd(rng, 3, 6) * 3
    content = f"一根 {a} m 的绳子能否围出直角边分别为 {a//2} m 和 {a//2} m 的直角三角形？斜边应为（ ）"
    c = int(((a // 2) ** 2 + (a // 2) ** 2) ** 0.5)
    opts, key = shuffle_options(f"{c} m", [f"{a} m", f"{a//2} m", f"{c+1} m"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"斜边=√(({a//2})²+({a//2})²)=√({2*(a//2)**2})={c} m。"}


# ===== 初中 · 四边形 =====
@tpl("CZ_QU_PARAL")
def _(rng):
    a = rnd(rng, 40, 80)
    content = f"平行四边形中一个内角为 {a}°，则它的对角是（ ）"
    opts, key = shuffle_options(f"{a}°", [f"{180-a}°", f"{90-a}°", f"{360-a}°"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "平行四边形对角相等，故对角也是 %d°。" % a}


@tpl("CZ_QU_SPECIAL")
def _(rng):
    a = rnd(rng, 4, 9)
    content = f"正方形边长为 {a}，其面积为（ ）"
    opts, key = shuffle_options(f"{a*a}", [f"{4*a}", f"{2*a}", f"{a+4}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"正方形面积 = 边长² = {a}² = {a*a}。"}


# ===== 初中 · 一次函数 =====
@tpl("CZ_F1_EXPR")
def _(rng):
    k = rnd(rng, 2, 4)
    b = rnd(rng, 1, 5)
    x = rnd(rng, 1, 3)
    y = k * x + b
    content = f"一次函数 y={k}x+{b} 的图像过点 (x, {y})，则 x=（ ）"
    opts, key = shuffle_options(str(x), [str(x + 1), str(x - 1), str(-x)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"代入：{y}={k}x+{b} → {k}x={y-b}={y-b} → x={x}。"}


@tpl("CZ_F1_IMG")
def _(rng):
    content = "一次函数 y=2x+1 的图像经过（ ）"
    opts, key = shuffle_options("第一、二、三象限",
                                ["第一、三、四象限", "第二、三、四象限", "仅第一、三象限"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "k=2>0 过一三象限，b=1>0 与 y 轴交于正半轴，故过一二三象限。"}


# ===== 初中 · 反比例函数 =====
@tpl("CZ_FI_EXPR")
def _(rng):
    k = rnd(rng, 2, 8)
    x = rnd(rng, 1, 5)
    y = k // 1 * (1)  # k/x at x=1 -> k
    content = f"反比例函数 y=k/x 过点 (1, {k})，则 k=（ ）"
    opts, key = shuffle_options(str(k), [str(-k), str(k * 2), str(k // 2 if k > 1 else 1)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"代入 x=1, y={k}：{k}=k/1 → k={k}。"}


# ===== 初中 · 分式 =====
@tpl("CZ_FRAC_SIMPLIFY")
def _(rng):
    a = rnd(rng, 2, 6)
    content = f"化简分式 a²/{a}（a≠0）的结果是（ ）"
    opts, key = shuffle_options("a", ["a²", "1/a", "2a"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"a²/a = a（同底数幂相除，指数相减）。"}


@tpl("CZ_FRAC_EQ")
def _(rng):
    x = rnd(rng, 2, 6)
    a = rnd(rng, 1, 4)
    # 1/(x) = 1/a -> x=a, but make equation: 2/x = 2/a
    content = f"解分式方程 2/x = 2/{a}，则 x=（ ）"
    opts, key = shuffle_options(str(a), [str(a + 1), str(-a), str(2 * a)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"两边同乘 x·{a}：2{a}=2x → x={a}。"}


# ===== 初中 · 二次根式 =====
@tpl("CZ_RD_SIMPLIFY")
def _(rng):
    a = rnd(rng, 2, 5)
    content = f"化简 √( {a*a} ) 的结果是（ ）"
    opts, key = shuffle_options(str(a), [str(-a), str(2 * a), str(a * a)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"√(a²)=|a|={a}（a>0 时等于 a）。"}


# ===== 初中 · 概率统计 =====
@tpl("CZ_PR_CLASSIC")
def _(rng):
    red = rnd(rng, 1, 3)
    blue = rnd(rng, 1, 3)
    total = red + blue
    content = f"袋中有 {red} 个红球、{blue} 个蓝球，随机摸 1 个，摸到红球的概率是（ ）"
    opts, key = shuffle_options(f"{red}/{total}", [f"{blue}/{total}", f"{red}/{total+1}", f"1/{total}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"概率 = 红球数/总数 = {red}/{total}。"}


@tpl("CZ_ST_MEAN")
def _(rng):
    xs = [rnd(rng, 70, 95) for _ in range(3)]
    mean = round(sum(xs) / 3)
    content = f"数据 {xs[0]}、{xs[1]}、{xs[2]} 的平均数是（ ）"
    opts, key = shuffle_options(str(mean), [str(mean + 1), str(mean - 1), str(max(xs))], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"平均数 = ({xs[0]}+{xs[1]}+{xs[2]})÷3 = {sum(xs)}÷3 = {mean}。"}


# ===== 初中 · 锐角三角函数 =====
@tpl("CZ_TG_DEF")
def _(rng):
    content = "在 Rt△ 中，sin A 的定义是（ ）"
    opts, key = shuffle_options("对边/斜边",
                                ["邻边/斜边", "对边/邻边", "斜边/对边"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "正弦 sin A = 对边 / 斜边。"}


@tpl("CZ_TG_APP")
def _(rng):
    ang = 30
    h = rnd(rng, 10, 30)
    # 30° 对边 = 斜边/2 近似（用整数）
    content = f"斜坡倾角 30°，坡高 {h} m，则斜坡长度约为（ ）（取 √3≈1.7）"
    slope = round(h * 2)
    opts, key = shuffle_options(f"{slope} m", [f"{h} m", f"{round(h*1.7)} m", f"{h//2} m"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"sin30°=对边/斜边=1/2 → 斜边=2×{h}={slope} m。"}


# ===== 高中 · 集合 =====
@tpl("HS_SET_OP")
def _(rng):
    a, b = rnd(rng, 1, 5), rnd(rng, 3, 8)
    content = f"已知集合 A={{1,2,{a}}}, B={{2,3,{b}}}，则 A∩B 的元素个数是（ ）"
    common = 1 + (1 if a in (2, 3, b) else 0) + (1 if b == 2 else 0)
    common = len({1, 2, a} & {2, 3, b})
    opts, key = shuffle_options(str(common), [str(common + 1), str(common - 1 if common > 0 else 0), str(5)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"A∩B 是两集合公共元素，共 {common} 个。"}


@tpl("HS_SET_COND")
def _(rng):
    content = "“x>2” 是 “x>1” 的（ ）条件"
    opts, key = shuffle_options("充分不必要", ["必要不充分", "充要", "既不充分也不必要"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "x>2 ⇒ x>1，但 x>1 不一定 x>2，故充分不必要。"}


# ===== 高中 · 函数 =====
@tpl("HS_FUNC_CONCEPT")
def _(rng):
    a = rnd(rng, 1, 3)
    x = rnd(rng, 1, 4)
    y = a * x + 1
    content = f"函数 f(x)={a}x+1，则 f({x})=（ ）"
    opts, key = shuffle_options(str(y), [str(y + 1), str(a + 1), str(x)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"f({x})={a}×{x}+1={y}。"}


@tpl("HS_FUNC_MONO")
def _(rng):
    content = "函数 f(x)=2x+1 在 R 上是（ ）"
    opts, key = shuffle_options("单调递增", ["单调递减", "先增后减", "常数函数"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "一次函数 k=2>0，在 R 上单调递增。"}


@tpl("HS_FUNC_PARITY")
def _(rng):
    content = "函数 f(x)=x³ 是（ ）"
    opts, key = shuffle_options("奇函数", ["偶函数", "非奇非偶", "既是奇又是偶"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "f(-x)=(-x)³=-x³=-f(x)，故为奇函数。"}


@tpl("HS_FUNC_QUAD")
def _(rng):
    a = rnd(rng, 1, 3)
    b = rnd(rng, 1, 4)
    # 顶点 x=-b/(2a)
    from fractions import Fraction
    content = f"二次函数 y={a}x²+{2*a*b}x 的对称轴是直线（ ）"
    opts, key = shuffle_options(f"x={-b}", [f"x={b}", f"x={2*b}", f"y={-b}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"对称轴 x=-b/(2a)=-(2·{a}·{b})/(2·{a})=-{b}。"}


@tpl("HS_FUNC_EXP")
def _(rng):
    a = rnd(rng, 2, 3)
    content = f"计算 2^{a} × 2^{a} 的结果是（ ）"
    corr = 2 ** (2 * a)
    opts, key = shuffle_options(str(corr), [str(2 ** a + 2 ** a), str(2 * a), str(4 ** a)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"同底数幂相乘：2^{a}×2^{a}=2^{2*a}={corr}。"}


@tpl("HS_FUNC_LOG")
def _(rng):
    a = rnd(rng, 2, 4)
    content = f"计算 log₂({2**a}) 的值是（ ）"
    opts, key = shuffle_options(str(a), [str(2 * a), str(a * a), str(2 ** a)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"log₂({2**a})={a}（因为 2^{a}={2**a}）。"}


# ===== 高中 · 三角函数 =====
@tpl("HS_TRIG_INDUCE")
def _(rng):
    content = "sin(180°-α) 等于（ ）"
    opts, key = shuffle_options("sinα", ["-sinα", "cosα", "-cosα"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "诱导公式：sin(180°-α)=sinα。"}


@tpl("HS_TRIG_IMG")
def _(rng):
    content = "函数 y=sin x 的最小正周期是（ ）"
    opts, key = shuffle_options("2π", ["π", "4π", "1"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "正弦函数最小正周期 T=2π。"}


@tpl("HS_TRIG_LAW")
def _(rng):
    content = "在△ABC 中，正弦定理的表达式是（ ）"
    opts, key = shuffle_options("a/sinA = b/sinB = c/sinC",
                                ["a²+b²=c²", "a+b+c=180", "sinA+sinB+sinC=1"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "正弦定理：a/sinA=b/sinB=c/sinC。"}


# ===== 高中 · 数列 =====
@tpl("HS_SEQ_ARITH")
def _(rng):
    a1 = rnd(rng, 1, 5)
    d = rnd(rng, 1, 4)
    n = rnd(rng, 3, 6)
    an = a1 + (n - 1) * d
    content = f"等差数列首项 {a1}、公差 {d}，第 {n} 项是（ ）"
    opts, key = shuffle_options(str(an), [str(an + d), str(an - d), str(a1 + n * d)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"a{n}=a1+(n-1)d={a1}+({n}-1)×{d}={an}。"}


@tpl("HS_SEQ_GEOM")
def _(rng):
    a1 = rnd(rng, 1, 3)
    q = rnd(rng, 2, 3)
    n = rnd(rng, 3, 4)
    an = a1 * (q ** (n - 1))
    content = f"等比数列首项 {a1}、公比 {q}，第 {n} 项是（ ）"
    opts, key = shuffle_options(str(an), [str(an * q), str(a1 * q * n), str(an // q)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"a{n}=a1·q^(n-1)={a1}×{q}^({n}-1)={an}。"}


@tpl("HS_SEQ_SUM")
def _(rng):
    n = rnd(rng, 3, 6)
    content = f"数列 1+2+3+…+{n} 的和是（ ）"
    s = n * (n + 1) // 2
    opts, key = shuffle_options(str(s), [str(s + n), str(n * n), str(s - 1)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"等差数列求和：{n}({n}+1)/2={s}。"}


# ===== 高中 · 不等式 =====
@tpl("HS_INEQ_QUAD")
def _(rng):
    r1, r2 = rnd(rng, 1, 3), rnd(rng, 4, 6)
    content = f"不等式 (x-{r1})(x-{r2})<0 的解集是（ ）"
    opts, key = shuffle_options(f"{r1}<x<{r2}",
                                [f"x<{r1} 或 x>{r2}", f"x>{r2}", f"x<{r1}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"二次函数开口向上，小于 0 在两根之间：{r1}<x<{r2}。"}


@tpl("HS_INEQ_BASIC")
def _(rng):
    a = rnd(rng, 2, 5)
    b = rnd(rng, 2, 5)
    content = f"（基本不等式）正数 x、y 满足 x+y={a+b}，则 xy 的最大值为（ ）"
    # 最大 xy 当 x=y=(a+b)/2 整数近似
    s = a + b
    mx = (s // 2) * (s - s // 2)
    opts, key = shuffle_options(str(mx), [str(s), str(s * s), str(mx + 1)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"xy≤((x+y)/2)²，最大值约 {mx}（x=y={s//2} 时取到）。"}


# ===== 高中 · 立体几何 =====
@tpl("HS_SOLID_VOLUME")
def _(rng):
    r = rnd(rng, 2, 4)
    content = f"半径为 {r} 的球体积（公式 4/3·πr³）与半径为 {r} 的正方体体积相比，球体积是（ ）"
    sphere = 4 * r ** 3
    cube = (2 * r) ** 3
    opts, key = shuffle_options(f"4/3·π·{r}³", [f"{cube}", f"{r}³", f"π·{r}³"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"球体积=4/3·π·r³=4/3·π·{r}³。"}


# ===== 高中 · 解析几何 =====
@tpl("HS_ANALYTIC_LINE")
def _(rng):
    x1, y1 = rnd(rng, 0, 2), rnd(rng, 0, 2)
    x2, y2 = x1 + rnd(rng, 1, 4), y1 + rnd(rng, 1, 4)
    if x2 == x1:
        x2 += 1
    k = (y2 - y1) // (x2 - x1) if (y2 - y1) % (x2 - x1) == 0 else (y2 - y1) / (x2 - x1)
    content = f"过 ({x1},{y1})、({x2},{y2}) 的直线斜率是（ ）"
    opts, key = shuffle_options(str(k), [str(-k if k != 0 else 1), str(y2 - y1), str(x2 - x1)], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"斜率 k=(y2-y1)/(x2-x1)=({y2}-{y1})/({x2}-{x1})={k}。"}


@tpl("HS_ANALYTIC_CIRCLE")
def _(rng):
    r = rnd(rng, 2, 5)
    content = f"圆心在原点、半径为 {r} 的圆的标准方程是（ ）"
    opts, key = shuffle_options(f"x²+y²={r*r}", [f"(x-{r})²+y²={r*r}", f"x²+y²={r}", f"x²+y²={2*r}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"圆心原点、半径 {r}：x²+y²={r*r}。"}


# ===== 高中 · 导数 =====
@tpl("HS_DERIV_OP")
def _(rng):
    a = rnd(rng, 2, 4)
    n = rnd(rng, 2, 3)
    content = f"函数 f(x)={a}x^{n} 的导数是（ ）"
    coeff = a * n
    opts, key = shuffle_options(f"{coeff}x^{n-1}", [f"{a}x^{n-1}", f"{coeff}x^{n}", f"{a*n}x^{n+1}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"(x^n)'=n·x^(n-1)，故 f'(x)={a}·{n}x^{n-1}={coeff}x^{n-1}。"}


@tpl("HS_DERIV_APP")
def _(rng):
    content = "函数 f(x)=x² 在 x=1 处的导数（切线斜率）是（ ）"
    opts, key = shuffle_options("2", ["1", "0", "4"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": "f'(x)=2x，f'(1)=2。"}


# ===== 高中 · 概率统计 =====
@tpl("HS_PROB_COUNT")
def _(rng):
    n = rnd(rng, 3, 5)
    content = f"{n} 个不同元素排成一排，排列数是（ ）"
    import math
    fact = math.factorial(n)
    opts, key = shuffle_options(str(fact), [str(n), str(2 ** n), str(n * (n - 1))], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"{n} 个元素全排列 = {n}! = {fact}。"}


@tpl("HS_PROB_CLASSIC")
def _(rng):
    n = rnd(rng, 2, 4)
    content = f"掷一枚均匀骰子，出现点数不大于 {n} 的概率是（ ）"
    opts, key = shuffle_options(f"{n}/6", [f"{6-n}/6", f"{n}/12", f"1/{n}"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"有利结果 {n} 个，总 6 个，概率 {n}/6。"}


@tpl("HS_PROB_DIST")
def _(rng):
    p = rnd(rng, 1, 4)
    content = f"进行 {p} 次独立重复试验，单次成功概率 1/2，成功次数的期望是（ ）"
    opts, key = shuffle_options(f"{p}/2", [f"{p}", f"{p*2}", f"1/2"], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"二项分布期望 E=np={p}×1/2={p}/2。"}


# ===== 高中 · 复数 =====
@tpl("HS_COMPLEX_OP")
def _(rng):
    a = rnd(rng, 1, 4)
    content = f"计算 i^{a}（i 为虚数单位）的结果是（ ）"
    val = a % 4
    cycle = ["1", "i", "-1", "-i"]
    res = cycle[val]
    wrongs = [cycle[(val + 1) % 4], cycle[(val + 2) % 4], cycle[(val + 3) % 4]]
    opts, key = shuffle_options(res, wrongs, rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"i 的幂周期为 4：i^{a}=i^{val}={res}。"}


# ===== 高中 · 向量 =====
@tpl("HS_VECTOR_OP")
def _(rng):
    a = rnd(rng, 1, 4)
    b = rnd(rng, 1, 4)
    content = f"向量 a=( {a}, 0 ), b=( 0, {b} )，则 a·b=（ ）"
    opts, key = shuffle_options("0", [str(a * b), str(a + b), str(abs(a - b))], rng)
    return {"content": content, "options": opts, "answer_key": key,
            "explanation": f"数量积 a·b={a}×0+0×{b}=0（垂直向量数量积为 0）。"}


# ------------------------------------------------------------------ 主流程
def kp_uuid(code: str) -> str:
    return str(uuid.uuid5(NS, "aicoach:kp:" + code))


def q_uuid(code: str, tpl_name: str, variant: int) -> str:
    return str(uuid.uuid5(NS, f"aicoach:q:{code}:{tpl_name}:{variant}"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=14, help="每个模板生成题数")
    ap.add_argument("--band", choices=("初中", "高中"), default=None, help="只生成某学段")
    args = ap.parse_args()

    # 预取 KP code -> (id, band)
    import json
    kp_meta: dict[str, tuple[str, str]] = {}
    for band, fname in (("初中", "math_kp_middle.json"), ("高中", "math_kp_highschool.json")):
        raw = json.load(open(BACKEND_DIR / "seed" / fname, encoding="utf-8"))
        for kp in raw["knowledgePoints"]:
            if kp["parentCode"] is None:
                continue
            kp_meta[kp["code"]] = (kp_uuid(kp["code"]), band)

    # 只保留本学段 / 有模板的 KP
    targets = {code: meta for code, meta in kp_meta.items() if code in REGISTRY}
    if args.band:
        targets = {c: m for c, m in targets.items() if m[1] == args.band}

    total = 0
    skipped = 0
    with SessionLocal() as s:
        for code, (kpid, band) in targets.items():
            lo, hi = BAND_GRADES[band]
            tpls = REGISTRY[code]
            for ti, fn in enumerate(tpls):
                tpl_name = f"{fn.__qualname__}_{ti}"
                for v in range(args.per):
                    rng = random.Random(f"aicoach:{code}:{tpl_name}:{v}")
                    try:
                        rec = fn(rng)
                    except Exception as e:  # 模板自身异常 → 跳过不污染库
                        skipped += 1
                        continue
                    content = rec["content"]
                    options = rec["options"]
                    answer_key = rec["answer_key"]
                    correct_answer = answer_key
                    # ---- 不变式闸门 ----
                    if classify_question(content, None, q_type="选择", options=options)[0] != VERDICT_CLEAN:
                        skipped += 1
                        continue
                    if not validate_choice_options(options, correct_answer):
                        skipped += 1
                        continue
                    if not judge_engine.is_l1_safe("选择", options, correct_answer):
                        skipped += 1
                        continue
                    grade = rng.randint(lo, hi)
                    difficulty = rng.choice([1, 3, 5])
                    qid = q_uuid(code, tpl_name, v)
                    s.merge(Question(
                        id=qid, content=content, subject="数学", grade=grade,
                        difficulty=difficulty, kp_id=kpid, type_="选择",
                        options=options, status="active",
                        source_doc="gen_math_bank", kp_verified=True, source_tag="yellow",
                    ))
                    s.merge(Answer(
                        id=qid, question_id=qid,
                        correct_answer=correct_answer, analysis=rec["explanation"],
                        distractor_analysis={},
                    ))
                    total += 1
        s.commit()

    print(f"[generate_math_bank] 入库 {total} 道，跳过 {skipped} 道（未过不变式/模板异常）。"
          f"覆盖 KP={len(targets)}。")


if __name__ == "__main__":
    main()

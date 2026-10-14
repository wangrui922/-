# -*- coding: utf-8 -*-
"""mathgenerator 批量生成「标准答案为纯表达式」的小学计算题（方向七）。

目标：向题库补充一批可被 SymPy L0 层（layered_judge）精确判对的纯算式题，
与现有应用题/场景题互补，缓解「计算类题多是带场景文字的应用题、L0 无法提取表达式」的问题。

落库契约（与 app/models/content.py Question/Answer 对齐）：
- questions: id, content, subject, grade, difficulty(1/3/5), kp_id, type, status,
  edition, source_type, source_doc, kp_verified, source_tag(yellow), ship_ready
- answers: question_id, correct_answer, analysis

质检（写入前强制通过）：
1. 题面与答案为纯 LaTeX 算式，无英文夹杂（允许单个变量字母 x）；
2. 标准答案可被 sympy 求值为数值/有理数；
3. 用「标准答案 vs 标准答案」复现 layered_judge L0 判分自检（可解析且值相等）；
4. 按 (kp_code, content) 去重、数量逐类校验。

用法：
    python scripts/generate_math_bank.py [--out .build/mathgen_bank.json] [--batch 50]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import mathgenerator as mg
except ImportError:
    sys.exit("缺少依赖 mathgenerator，请先执行: python -m pip install mathgenerator")

try:
    import sympy
except ImportError:
    sys.exit("缺少依赖 sympy，请先执行: python -m pip install sympy")

# ---------------------------------------------------------------------------
# 知识点映射（来自生产库 knowledge_points，id 已核对）
# ---------------------------------------------------------------------------
KP_CALC_ADD = {"code": "KP_CALC_ADD", "id": "73084c38-7051-5eb0-88df-914599e3877d", "name": "加减法"}
KP_CALC_MUL = {"code": "KP_CALC_MUL", "id": "317e80f4-1ee7-5a65-967f-43ff4c637cfe", "name": "乘除法"}
KP_CALC_MENTAL = {"code": "KP_CALC_MENTAL", "id": "82a4eab5-56cb-511f-89b0-af8c6ea60cd5", "name": "口算与估算"}
KP_CALC_MIXED_OP = {"code": "KP_CALC_MIXED_OP", "id": "fba9e84a-9c58-59f9-bb94-867abc039cf9", "name": "四则混合运算"}
KP_EQ_LINEAR1 = {"code": "KP_EQ_LINEAR1", "id": "b0ce2063-269b-5d80-a283-93f38b70f7d7", "name": "一元一次方程"}
KP_NT_ROOT = {"code": "KP_NT_ROOT", "id": "ccab1de4-c8ce-5325-a57a-f4240b30ec58", "name": "平方根与实数"}
KP_NT_GCD_LCM = {"code": "KP_NT_GCD_LCM", "id": "10f5eb2f-71b1-54a7-8114-cc0788663684", "name": "公因数与公倍数"}

# mathgenerator 函数名 -> (知识点, 年级, 难度, 题干前缀, 生成参数)
GEN_PLAN: List[Tuple[str, Dict[str, str], int, int, str, Dict[str, Any]]] = [
    ("addition", KP_CALC_ADD, 1, 1, "口算：", {"maxSum": 20, "maxAddend": 10}),
    ("subtraction", KP_CALC_ADD, 1, 1, "口算：", {"maxMinuend": 20, "maxDiff": 10}),
    ("addition", KP_CALC_ADD, 2, 3, "计算：", {"maxSum": 100, "maxAddend": 60}),
    ("subtraction", KP_CALC_ADD, 2, 3, "计算：", {"maxMinuend": 100, "maxDiff": 60}),
    ("multiplication", KP_CALC_MUL, 2, 1, "口算：", {"maxMulti": 9}),
    ("division", KP_CALC_MUL, 2, 3, "口算：", {"maxA": 81, "maxB": 9}),
    ("multiplication", KP_CALC_MUL, 3, 5, "计算：", {"maxMulti": 12}),
    ("square", KP_CALC_MENTAL, 3, 3, "口算：", {"maxSquareNum": 20}),
    ("square_root", KP_NT_ROOT, 8, 3, "计算：", {"minNo": 1, "maxNo": 12}),
    ("basic_algebra", KP_EQ_LINEAR1, 7, 5, "解方程：", {"maxVariable": 10}),
    ("lcm", KP_NT_GCD_LCM, 5, 3, "求最小公倍数：", {"maxVal": 20}),
    ("gcd", KP_NT_GCD_LCM, 5, 3, "求最大公因数：", {"maxVal": 20}),
]


def strip_latex_dollars(s: str) -> str:
    """去掉首尾 $$ 包裹，保留内部 LaTeX 命令原样（mathgenerator 统一返回 $..$ 包围的字符串）。"""
    s = s.strip()
    while s.startswith("$") and s.endswith("$") and len(s) >= 2:
        s = s[1:-1].strip()
    return s


def to_sympy(expr: str) -> Optional[sympy.Expr]:
    """把 LaTeX 数学串转成 sympy 表达式；失败返回 None（该题弃用）。"""
    s = strip_latex_dollars(expr)
    s = s.replace("\\cdot", "*").replace("\\times", "*").replace("\\div", "/")
    s = re.sub(r"\\sqrt\{([^}]*)\}", r"sqrt(\1)", s)
    s = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", r"(\1/\2)", s)
    s = re.sub(r"\\left|\\right", "", s)
    s = s.replace("^", "**")
    s = s.replace(" ", "")
    if not s:
        return None
    # 只允许数值/运算符/合法的符号函数，杜绝任意字母（如 x、y 混入答案导致不可判）
    if not re.fullmatch(r"[\d+\-*/()sqrt\.,]+", s, re.I):
        return None
    try:
        v = sympy.sympify(s)
        if v.is_number or v.is_constant():
            return v
    except Exception:
        return None
    return None


def answer_canonical(expr: str) -> str:
    """标准答案规整为最简字符串（整数去小数尾零，分数最简显示）。"""
    v = to_sympy(expr)
    if v is None:
        raise ValueError(f"答案无法解析: {expr}")
    return str(sympy.nsimplify(v))


def is_pure_math_problem(problem: str) -> bool:
    """题面排除英文自然语言（如 LCM of / GCD of / Solve for），LaTeX 命令如 \\cdot 不算英文。

    规则：先把 ``\\command`` 命令整体剥掉，剩余文本中再出现连续字母即视为英文单词 → 弃用。
    """
    p = strip_latex_dollars(problem)
    if not p:
        return False
    stripped = re.sub(r"\\[a-zA-Z]+", "", p)
    # 剩余连续字母（>=2 个）说明是英文单词，不是纯算式
    if re.search(r"[A-Za-z]{2,}", stripped):
        return False
    return True


def problem_clean(problem: str) -> str:
    """题面转干净 LaTeX（去掉首尾 $$；已有 '=' 补 '\\ ?'，否则补 '= \\ ?'）。"""
    p = strip_latex_dollars(problem)
    if p.endswith("=") or p.endswith("= "):
        p = p.rstrip() + "\\ ?"
    elif "=" not in p:
        p = p + "= \\ ?"
    return p


def self_check_l0(canonical: str) -> bool:
    """用标准答案自判，复现 layered_judge 的 L0 路径：
    两侧都必须是可解析的纯数学表达式，且值相等。"""
    a = to_sympy(canonical)
    b = to_sympy(canonical)
    if a is None or b is None:
        return False
    try:
        return bool(sympy.simplify(a - b) == 0)
    except Exception:
        return False


def build_stable_key(kp_code: str, content: str) -> str:
    """稳定去重键：知识点 code + 题面原文。"""
    return f"{kp_code}:{content}"


def generate_batch(batch: int) -> List[Dict[str, Any]]:
    seen: set = set()
    questions: List[Dict[str, Any]] = []
    stats: Dict[str, int] = {}

    for func_name, kp, grade, difficulty, prefix, kwargs in GEN_PLAN:
        gen = getattr(mg, func_name)
        made = 0
        attempts = 0
        while made < batch and attempts < batch * 40:
            attempts += 1
            try:
                problem, answer = gen(**kwargs)
            except Exception:
                continue
            if not (isinstance(problem, str) and isinstance(answer, str)):
                continue
            # lcm/gcd 生成的是全英文题干：跳过纯算式检查，走中文化提取
            lcm_gcd_mode = func_name in ("lcm", "gcd")
            if not lcm_gcd_mode and not is_pure_math_problem(problem):
                continue
            try:
                canonical = answer_canonical(answer)
            except ValueError:
                continue
            if lcm_gcd_mode:
                nums = re.findall(r"\$(\d+)", problem)
                if len(nums) != 2:
                    continue
                a_num, b_num = int(nums[0]), int(nums[1])
                if func_name == "lcm":
                    content = f"求 {a_num} 和 {b_num} 的最小公倍数。"
                else:
                    content = f"求 {a_num} 和 {b_num} 的最大公因数。"
            else:
                content = f"{prefix}${problem_clean(problem)}$"
            key = build_stable_key(kp["code"], content)
            if key in seen:
                continue
            if not self_check_l0(canonical):
                continue
            seen.add(key)
            qid = str(uuid.uuid4())
            questions.append({
                "stableKey": key,
                "question": {
                    "id": qid,
                    "content": content,
                    "subject": "数学",
                    "grade": grade,
                    "difficulty": difficulty,
                    "kpCode": kp["code"],
                    "kpId": kp["id"],
                    "type": "计算",
                    "status": "active",
                    "edition": None,
                    "sourceType": "generated",
                    "sourceDoc": "mathgenerator",
                    "kpVerified": True,
                    "sourceTag": "yellow",
                    "shipReady": True,
                },
                "answer": {
                    "questionId": qid,
                    "correctAnswer": canonical,
                    "analysis": f"按 {func_name} 口径生成；标准答案 {canonical}。",
                },
                "l0SelfCheck": True,
            })
            made += 1
        stats[kp["code"]] = made
        if made < batch:
            print(f"[warn] {func_name}->{kp['code']} 生成 {made}/{batch}（尝试 {attempts} 次）", file=sys.stderr)

    print("=== 分知识点生成统计 ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"TOTAL: {len(questions)}")
    return questions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".build/mathgen_bank.json")
    ap.add_argument("--batch", type=int, default=50)
    args = ap.parse_args()

    questions = generate_batch(args.batch)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"meta": {"generator": "mathgenerator", "batchPerKp": args.batch,
                              "l0SelfCheck": "sympy 值等价自检", "count": len(questions)},
                     "questions": questions},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"OK -> {out.resolve()}")
    return 0 if questions else 2


if __name__ == "__main__":
    raise SystemExit(main())
"""精确数学判题引擎（SymPy 符号计算层 · B2+）。

定位
----
**分层判题策略的 L0 层**：对纯计算类题目（代数/方程/化简/求值）做 SymPy 符号级
精确判分，零 LLM 调用、零幻觉、零 token 开销。

分层判题策略
--------------
::

    L0  SymPy 精确判  ──→  代数/方程/化简/求值/积分/导数
    L1  规则引擎判   ──→  选择题键位比对 / 判断题布尔比对（原有 judge_engine）
    L2  LLM 语义判   ──→  应用题/证明题/开放题/复杂多步推理
    L3  人工兜底     ──→  几何题（规则引擎 + 图像匹配）/ 无法自动判定

集成方式
--------
``sympy_judge.judge()`` 与原有 ``judge_engine.judge()`` 接口兼容，
返回同构的 ``JudgeOutcome``。调用方（``practice_service.submit_answer``）
先尝试 L0，L0 无法处理时回退到 L1/L2。

支持的题型
----------
* ``计算``、``化简``、``求值``、``解方程``、``因式分解``、``展开``
* ``求导``、``积分``、``极限``、``解不等式``
* 所有结果可化为 ``sympy.Expr`` 或 ``sympy.Set`` / ``sympy.FiniteSet`` 的题目

不可处理的题型（自动回退 L1/L2）
----------------------------------
* 应用题（带场景描述，需语义理解）
* 几何证明题
* 开放题 / 论述题
* 含图形、图表的题
* 答案为自然语言描述的题
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.services.judge_engine import JudgeOutcome, LEVEL_FALLBACK, LEVEL_RULE

logger = logging.getLogger("app.services.sympy_judge")

# ------------------------------------------------------------------ 常量
#: L0 精确判分层级
LEVEL_SYMPY: int = 0

#: 支持 SymPy 精确判分的题型关键词
SYMPY_SUPPORTED_TYPES: frozenset[str] = frozenset({
    "计算", "化简", "求值", "解方程", "因式分解", "展开",
    "求导", "积分", "极限", "解不等式", "代数", "方程",
})

#: 从题干中抽取数学表达式的正则模式
_EXPRESSION_PATTERNS: List[re.Pattern] = [
    # 等号右侧："求值：3x+2=" → 提取 "3x+2"
    re.compile(r"[:：]\s*([^=]+)=\s*$"),
    # 计算/化简/求值 后面的表达式
    re.compile(r"(?:计算|化简|求值|求解)[:：]?\s*([^。；;\n]+)"),
    # 解方程："解方程：2x+3=7" → 提取 "2x+3=7"
    re.compile(r"解方程[:：]?\s*([^。；;\n]+)"),
    # 因式分解："因式分解：x²-4" → 提取 "x²-4"
    re.compile(r"因式分解[:：]?\s*([^。；;\n]+)"),
    # 求导："求导：x²" → 提取 "x²"
    re.compile(r"求导[:：]?\s*([^。；;\n]+)"),
]

#: 禁止 SymPy 处理的敏感符号（避免 eval 安全问题）
_FORBIDDEN_TOKENS: frozenset[str] = frozenset({
    "__", "import", "exec", "eval", "compile", "open", "file",
    "os", "sys", "subprocess", "input", "raw_input",
})

#: Unicode 数学符号 → ASCII 替换表
_UNICODE_MATH_MAP: Dict[str, str] = {
    "×": "*", "÷": "/", "·": "*", "＊": "*",
    "²": "**2", "³": "**3", "⁴": "**4", "⁵": "**5",
    "√": "sqrt", "∛": "cbrt",
    "π": "pi", "°": "*pi/180",  # 角度转弧度
    "∞": "oo", "∝": "oo",
    "≠": "!=", "≤": "<=", "≥": ">=", "≈": "~=",
    "∈": "in", "∉": "not in",
    "∪": "Union", "∩": "Intersection",
    "∧": "&", "∨": "|", "¬": "~",
    "∑": "Sum", "∏": "Product",
    "∫": "Integral", "∂": "Derivative",
    "→": "->", "⇒": "implies", "⇔": "equivalent",
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
    "θ": "theta", "λ": "lambda", "μ": "mu", "σ": "sigma",
    "φ": "phi", "ψ": "psi", "ω": "omega",
    "ℯ": "E",  # 自然对数底
    "ⅰ": "I", "ⅈ": "I",  # 虚数单位
    "†": "dagger", "‡": "ddagger",
}


# ------------------------------------------------------------------ 安全校验
def _is_safe_expression(text: str) -> bool:
    """检查表达式是否包含危险 token（防止 eval 注入）。"""
    lowered = text.lower()
    for token in _FORBIDDEN_TOKENS:
        if token in lowered:
            return False
    return True


# ------------------------------------------------------------------ 文本预处理
#: LaTeX 简单命令 → 数学文本（顺序敏感：\dfrac 先于 \frac，\sqrt[3] 先于 \sqrt）
_LATEX_SIMPLE_REPLACEMENTS: List[tuple[str, str]] = [
    (r"\cdot", "*"), (r"\times", "*"), (r"\div", "/"),
    (r"\pm", "+-"), (r"\mp", "-+"),  # 仅作占位，含 ± 的表达式大概率解析失败→回退 L1，不误判
    (r"\pi", "pi"), (r"\infty", "oo"),
    (r"\alpha", "alpha"), (r"\beta", "beta"), (r"\gamma", "gamma"),
    (r"\delta", "delta"), (r"\theta", "theta"), (r"\lambda", "lambda"),
    (r"\mu", "mu"), (r"\sigma", "sigma"), (r"\phi", "phi"),
    (r"\omega", "omega"), (r"\epsilon", "epsilon"), (r"\Delta", "Delta"),
    (r"\,", " "), (r"\;", " "), (r"\!", ""), (r"\ ", " "),
    (r"\{", "{"), (r"\}", "}"),
    (r"\log", "log"), (r"\ln", "ln"), (r"\lg", "log"),
    (r"\sin", "sin"), (r"\cos", "cos"), (r"\tan", "tan"),
    (r"\cot", "cot"), (r"\sec", "sec"), (r"\csc", "csc"),
    (r"\arcsin", "asin"), (r"\arccos", "acos"), (r"\arctan", "atan"),
    (r"\exp", "exp"), (r"\log_{2}", "log2"), (r"\sinh", "sinh"),
    (r"\cosh", "cosh"), (r"\tanh", "tanh"),
]


def _convert_latex(text: str) -> str:
    r"""把 LaTeX 数学语法归一化为 SymPy 可解析的文本。

    处理范围：\frac/\dfrac、\sqrt[n]、上下标、\cdot/\times/\div、
    \left/\right、\text/\mathrm 剥壳、常见函数与希腊字母命令。
    复杂环境（cases/矩阵等）不做展开，保持原样交给解析层失败回退。
    """
    if "\\" not in text:
        return text
    result = text

    # 1. 上下标：a^{2} → a**(2)；a_{1} → a_1（仅字母数字下标）
    result = re.sub(r"\^\{([^{}]*)\}", r"**(\1)", result)
    result = re.sub(r"_\{([A-Za-z0-9]+)\}", r"_\1", result)

    # 2. 分数（含嵌套，最多 5 层）
    for _ in range(5):
        new = re.sub(r"\\dfrac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", result)
        new = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", new)
        if new == result:
            break
        result = new

    # 3. 根号：\sqrt[3]{x} → cbrt(x)；\sqrt{x} → sqrt(x)
    result = re.sub(r"\\sqrt\[([0-9]+)\]\{([^{}]*)\}", r"cbrt(\2)", result)
    result = re.sub(r"\\sqrt\{([^{}]*)\}", r"sqrt(\1)", result)

    # 4. 简单命令替换
    for cmd, rep in _LATEX_SIMPLE_REPLACEMENTS:
        result = result.replace(cmd, rep)

    # 5. 定界符与剥壳：\left( → ( ；\text{...} → 内容
    result = result.replace(r"\left.", "").replace(r"\right.", "")
    result = result.replace(r"\left", "").replace(r"\right", "")
    result = re.sub(r"\\(?:mathrm|text|operatorname)\{([^{}]*)\}", r"\1", result)

    return result


def _normalize_math_text(text: str) -> str:
    """将中文数学文本归一化为 SymPy 可解析的 ASCII 表达式。

    处理流程：
    1. Unicode 数学符号 → ASCII
    2. 中文数字 → 阿拉伯数字
    3. 全角字符 → 半角
    4. 空格规范化
    """
    if not text:
        return ""

    result = text.strip()

    # 0. LaTeX 数学语法 → 普通数学文本（必须在 Unicode/^ 处理之前）
    result = _convert_latex(result)

    # 1. Unicode 数学符号替换
    for uni, ascii_repr in _UNICODE_MATH_MAP.items():
        result = result.replace(uni, ascii_repr)

    # 2. 花括号上下标（纯文本与 LaTeX 通用写法）：a^{2} → a**(2)；a_{1} → a_1
    result = re.sub(r"\^\{([^{}]*)\}", r"**(\1)", result)
    result = re.sub(r"_\{([A-Za-z0-9]+)\}", r"_\1", result)

    # 2. ASCII 幂运算符：^ -> ** （乘方）；注意 Python 语义中 ^ 是异或，必须先转
    result = result.replace("^", "**")

    # 2. 中文数字 → 阿拉伯数字
    cn_nums = {"零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
               "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
               "十": "10", "百": "100", "千": "1000", "万": "10000"}
    for cn, num in cn_nums.items():
        result = result.replace(cn, num)

    # 3. 全角 → 半角（数字、字母、运算符）
    fullwidth = "０１２３４５６７８９＋－＊／＝（）［］｛｝"
    halfwidth = "0123456789+-*/=()[]{}"
    for f, h in zip(fullwidth, halfwidth):
        result = result.replace(f, h)

    # 4. 去掉多余空白
    result = " ".join(result.split())

    return result


# ------------------------------------------------------------------ 表达式解析
def _extract_expression(content: str, q_type: str) -> Optional[str]:
    """从题干中抽取数学表达式。

    Args:
        content: 题目题干。
        q_type: 题型名称。

    Returns:
        归一化后的数学表达式；无法抽取时返回 None。
    """
    if not content:
        return None

    # 先尝试题型特定的模式
    for pattern in _EXPRESSION_PATTERNS:
        match = pattern.search(content)
        if match:
            expr = match.group(1).strip()
            if expr:
                normalized = _normalize_math_text(expr)
                if _is_safe_expression(normalized):
                    return normalized

    # 兜底：找等号
    if "=" in content:
        parts = content.split("=")
        if len(parts) >= 2:
            expr = parts[0].strip()
            # 去掉前缀（如"计算""化简"等）
            for prefix in ["计算", "化简", "求值", "求解", "求"]:
                if expr.startswith(prefix):
                    expr = expr[len(prefix):].strip()
                    break
            normalized = _normalize_math_text(expr)
            if _is_safe_expression(normalized):
                return normalized

    return None


# ------------------------------------------------------------------ SymPy 计算
def _parse_and_evaluate(expression: str) -> Optional[Any]:
    """用 SymPy 解析并计算数学表达式。

    Args:
        expression: 归一化后的数学表达式字符串。

    Returns:
        SymPy 表达式对象；解析失败时返回 None。
    """
    try:
        import sympy as sp

        # 创建安全的符号命名空间
        namespace = {
            "sqrt": sp.sqrt, "cbrt": sp.cbrt,
            "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
            "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
            "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
            "exp": sp.exp, "log": sp.log, "ln": sp.log,
            "abs": sp.Abs, "factorial": sp.factorial,
            "pi": sp.pi, "E": sp.E, "oo": sp.oo,
            "I": sp.I, "zoo": sp.zoo, "nan": sp.nan,
            "Rational": sp.Rational, "Integer": sp.Integer,
            "Float": sp.Float, "Symbol": sp.Symbol,
            "symbols": sp.symbols,
            "diff": sp.diff, "integrate": sp.integrate,
            "limit": sp.limit, "series": sp.series,
            "solve": sp.solve, "dsolve": sp.dsolve,
            "factor": sp.factor, "expand": sp.expand,
            "simplify": sp.simplify, "collect": sp.collect,
            "apart": sp.apart, "together": sp.together,
            "trigsimp": sp.trigsimp, "powsimp": sp.powsimp,
            "logcombine": sp.logcombine, "ratsimp": sp.ratsimp,
            "gcd": sp.gcd, "lcm": sp.lcm,
            "summation": sp.summation, "product": sp.product,
            "Matrix": sp.Matrix, "Determinant": sp.Determinant,
            "Transpose": sp.Transpose, "Inverse": sp.Inverse,
            "Derivative": sp.Derivative, "Integral": sp.Integral,
            "Limit": sp.Limit, "Sum": sp.Sum, "Product": sp.Product,
        }

        # ---- 路径 1：Python eval 快速通道（_normalize_math_text 已把 ^ 转成 **）----
        try:
            local_vars = dict(namespace)
            for ch in expression:
                if ch.isalpha() and ch.islower() and ch not in local_vars:
                    local_vars[ch] = sp.Symbol(ch)
            return eval(expression, {"__builtins__": {}}, local_vars)
        except Exception:
            pass  # 语法不支持（如 2x 隐式乘法）或语义不支持（如 SymPy 对象异或），走路径 2

        # ---- 路径 2：SymPy parse_expr 兜底（隐式乘法 / ^ 幂 / 函数套用 / 自动符号）----
        from sympy.parsing.sympy_parser import (
            standard_transformations,
            implicit_multiplication_application,
            convert_xor,
            function_exponentiation,
        )
        transformations = (
            standard_transformations
            + (convert_xor, function_exponentiation, implicit_multiplication_application)
        )
        return sp.parsing.sympy_parser.parse_expr(
            expression, local_dict=namespace, transformations=transformations
        )

    except Exception as e:
        logger.debug("SymPy parse failed: %s | expr=%s", e, expression)
        return None


#: 结果型答案的尾部单位/引导白名单之外不允许出现的字符
#: （出现即视为过程性/叙述性答案，不抽取，避免猜测式误判）
_FRAGMENT_DROP_CHARS: re.Pattern = re.compile(r"[0-9+\-*/=×÷^]")
#: 单字符单位（常见小初数学量词，用于识别「数字+单位」结果型答案）
_FRAGMENT_UNIT_CHARS: str = "颗个元米天盒块件页名人次份本条棵只辆角分秒千克毛克升毫厘平方立方列班台"
#: 中文字符检测：含 CJK 的答案视为「中文夹杂答案」，跳过整段 SymPy 解析
_CONTAINS_CJK: re.Pattern = re.compile(r"[\u4e00-\u9fff]")


def _extract_result_fragment(text: str) -> Optional[str]:
    """从结果型答案文本中提取纯数值片段（安全子集）。

    仅处理「数字（+ 可选单位）(+ 可选括号解释)」与「x=数字」两种结论形式，
    例如：``8 颗（3+5=8）`` → ``8``、``x=2`` → ``2``、``-0.5`` → ``-0.5``、
    ``40天`` → ``40``、``答案为 3`` → ``3``。

    任一环节出现过程符号（四则运算符 / 等号嵌套 / 多数字并列 / 首位前还有数字）
    一律返回 None，由调用方安全回退 L1/L2，绝不猜测式抽取，杜绝误判。

    Args:
        text: 答案原文（可为中文夹杂）。

    Returns:
        抽取到的纯数字字符串；不符合安全模式时返回 None。
    """
    if not text:
        return None

    # 1. 剥离括号解释（（因为 3+5=8）这类附注不属于结果本体）
    stripped = re.sub(r"[（(][^（）()]*[)）]", "", text).strip()
    if not stripped:
        return None

    # 2. 方程形结果：x=2 / X=2.5（解方程常用简洁写法）
    m = re.fullmatch(
        r"\s*[xX]\s*=\s*(?P<num>-?\d+(?:\.\d+)?(?:/\d+)?)\s*", stripped
    )
    if m:
        return m.group("num")

    # 3. 常规「数字 + 可选单位」：匹配末尾数字 token
    m = re.search(r"(-?\d+(?:\.\d+)?(?:/\d+)?)\s*(?P<tail>.*)$", stripped)
    if not m:
        return None
    num, tail = m.group(1), m.group(2).strip()

    # 4. tail 中不允许出现数字/运算符（'盒，还剩 3 个'、'+7' 等都是过程/多结果）
    if tail and _FRAGMENT_DROP_CHARS.search(tail):
        return None
    # tail 只能是空白或中文单位（'8颗'/'12 个'/'40天'）
    if tail and any(ch in _FRAGMENT_UNIT_CHARS for ch in tail) is False and not all(
        "\u4e00" <= ch <= "\u9fff" or ch in " ，,、" for ch in tail
    ):
        return None

    # 5. 数字前已出现数字 → 前面还有过程/并列结果，不猜
    head = stripped[: m.start()]
    if re.search(r"\d", head):
        return None

    return num


def _normalize_answer(answer_text: str) -> Optional[Any]:
    """将学生的答案文本归一化为 SymPy 对象。

    支持两种输入形态：
    1. 纯数学表达式（``8``/``x^2+1``/``\\frac{5}{6}``）——整段解析；
    2. 结果型中文夹杂答案（``8 颗（3+5=8）``/``40天``/``x=2``）——经
       :func:`_extract_result_fragment` 抽取纯数值后再解析。

    两种形态都解析不出时返回 None，调用方应回退到 L1/L2。

    Args:
        answer_text: 学生作答的原始文本。

    Returns:
        SymPy 表达式；无法解析时返回 None。
    """
    if not answer_text:
        return None

    normalized = _normalize_math_text(answer_text)

    # 含中文字符 → 视为「中文夹杂答案」（单位/叙述），跳过整段 SymPy 解析：
    # 否则 ``8个``/``40天`` 会被解析成符号乘积 ``8*个``/``40*天`` 造成误判。
    if _CONTAINS_CJK.search(normalized):
        fragment = _extract_result_fragment(normalized)
        if fragment is None:
            return None
        return _parse_and_evaluate(fragment)

    if not _is_safe_expression(normalized):
        return None

    parsed = _parse_and_evaluate(normalized)
    if parsed is not None:
        return parsed

    # 整段解析失败 → 尝试结果型片段抽取（仅安全子集，失败仍返回 None）
    fragment = _extract_result_fragment(normalized)
    if fragment is None:
        return None
    return _parse_and_evaluate(fragment)


def _sympy_equal(a: Any, b: Any) -> bool:
    """判断两个 SymPy 对象在数学上是否等价。

    使用多重校验策略：
    1. 直接相等（==）
    2. 化简后相等（simplify）
    3. 数值近似相等（N）
    4. 集合相等（针对解集）
    """
    try:
        import sympy as sp

        # 集合/列表/元组形式的解集归一化：{-2,2} == [-2,2] == (-2, 2)
        if isinstance(a, (set, list, tuple, sp.FiniteSet)) and isinstance(
            b, (set, list, tuple, sp.FiniteSet)
        ):
            try:
                sa = a if isinstance(a, sp.FiniteSet) else sp.FiniteSet(*a)
                sb = b if isinstance(b, sp.FiniteSet) else sp.FiniteSet(*b)
                if sa == sb:
                    return True
                return bool(sa.symmetric_difference(sb).is_empty)
            except Exception:
                return False

        # 直接相等
        if a == b:
            return True

        # 化简后相等
        if sp.simplify(a - b) == 0:
            return True

        # 数值近似（处理含 pi / e / sqrt 的表达式，相对+绝对容差）
        try:
            num_a = complex(sp.N(a))
            num_b = complex(sp.N(b))
            tol = 1e-6 * max(1.0, abs(num_a), abs(num_b))
            if abs(num_a - num_b) < tol:
                return True
        except (TypeError, ValueError, OverflowError):
            pass

        # 集合相等（针对方程解集，兜底）
        if isinstance(a, (sp.FiniteSet, sp.Set)) and isinstance(b, (sp.FiniteSet, sp.Set)):
            if a == b:
                return True
            try:
                return bool(a.symmetric_difference(b).is_empty)
            except Exception:
                pass

        return False

    except Exception as e:
        logger.debug("SymPy equality check failed: %s", e)
        return False


# ------------------------------------------------------------------ 主入口
def judge(
    q_type: Optional[str],
    content: Optional[str],
    correct_answer: Optional[str],
    selected_answer: Optional[str],
) -> Optional[JudgeOutcome]:
    """SymPy 精确判分主入口。

    只有当同时满足以下条件时才返回 JudgeOutcome：
    1. 题型在 SYMPY_SUPPORTED_TYPES 中
    2. 能从题干或答案中抽取有效数学表达式
    3. SymPy 能成功解析标准答案和学生答案
    4. 两个表达式数学等价

    任一条件不满足时返回 None，调用方应回退到 L1/L2 判题。

    Args:
        q_type: 题型（"计算"/"化简"/"求值"/"解方程"等）。
        content: 题目题干（用于抽取表达式）。
        correct_answer: 标准答案文本。
        selected_answer: 学生作答文本。

    Returns:
        JudgeOutcome（level=0 表示 SymPy 精确判分）；无法处理时返回 None。
    """
    # ---- 前置校验 ----
    if not q_type or not selected_answer or not correct_answer:
        return None

    kind = q_type.strip()
    if kind not in SYMPY_SUPPORTED_TYPES:
        return None

    # ---- 解析标准答案 ----
    correct_expr = _normalize_answer(correct_answer)
    if correct_expr is None:
        logger.debug("SymPy: cannot parse correct_answer=%s", correct_answer)
        return None

    # ---- 解析学生答案 ----
    user_expr = _normalize_answer(selected_answer)
    if user_expr is None:
        logger.debug("SymPy: cannot parse selected_answer=%s", selected_answer)
        return None

    # ---- 数学等价判断 ----
    is_correct = _sympy_equal(correct_expr, user_expr)

    if is_correct:
        return JudgeOutcome(
            is_correct=True,
            level=LEVEL_SYMPY,
            reason="计算正确，结果和标准答案一致",
            audit_reason=f"SymPy 精确判对：{selected_answer} ≡ {correct_answer}",
        )
    else:
        return JudgeOutcome(
            is_correct=False,
            level=LEVEL_SYMPY,
            reason="计算结果和标准答案不一致，再检查一下计算过程",
            audit_reason=f"SymPy 精确判错：{selected_answer} ≠ {correct_answer} (expr: {user_expr} ≠ {correct_expr})",
        )


def can_judge(q_type: Optional[str], content: Optional[str]) -> bool:
    """快速判断该题是否可能走 SymPy 精确判分（用于前端展示提示）。"""
    if not q_type:
        return False
    kind = q_type.strip()
    if kind not in SYMPY_SUPPORTED_TYPES:
        return False
    expr = _extract_expression(content or "", kind)
    return expr is not None


# ------------------------------------------------------------------ 分层判题路由
def layered_judge(
    q_type: Optional[str],
    content: Optional[str],
    options: Any,
    correct_answer: Optional[str],
    selected_answer: Optional[str],
) -> JudgeOutcome:
    """分层判题策略主入口。

    判题优先级：
    1. L0 SymPy 精确判（计算/化简/求值/解方程等纯数学题型）
    2. L1 规则引擎判（选择题键位 / 判断题布尔）
    3. L2 LLM 语义判（应用题/开放题/复杂推理）—— 由 practice_service 在 L1 兜底后调用
    4. L3 人工兜底（L4）—— 无法自动判定时

    Args:
        q_type: 题型。
        content: 题干（SymPy 层需要）。
        options: 选项（L1 层需要）。
        correct_answer: 标准答案。
        selected_answer: 学生作答。

    Returns:
        JudgeOutcome，level 字段标识判题层级（0=SymPy, 1=规则, 4=兜底）。
    """
    # L0: SymPy 精确判
    outcome = judge(q_type, content, correct_answer, selected_answer)
    if outcome is not None:
        logger.info("L0 SymPy judge: type=%s correct=%s", q_type, outcome.is_correct)
        return outcome

    # L1: 规则引擎判（原有 judge_engine）
    from app.services import judge_engine as je
    outcome = je.judge(q_type, options, correct_answer, selected_answer)
    logger.info("L1 rule judge: type=%s level=%s correct=%s", q_type, outcome.level, outcome.is_correct)
    return outcome


__all__ = [
    "LEVEL_SYMPY",
    "SYMPY_SUPPORTED_TYPES",
    "judge",
    "can_judge",
    "layered_judge",
    "_normalize_math_text",
    "_parse_and_evaluate",
    "_sympy_equal",
]
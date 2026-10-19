"""AI 层共享的轻量数值/文本抽取工具（TASK-002 学生回答结构化地基）。

为什么放在 ``app/ai/`` 下：分层纪律规定 ``app.services`` 可以依赖 ``app.ai``，
反向不许在模块级依赖——学生回答结构化（``response_analyzer``）与判题练习
（``ai_exercise_service``）都要用「有且只有一个数字才抽取」的语义，共享实现
只能落在被依赖方。

与 ``app/services/ai_exercise_service.py::_sole_number`` 的关系：该函数改为
委托本模块（消双份），语义逐字不变——「有且只有一个数字」是关键：``12`` 与
``12元`` 能对上，而 ``7`` 与 ``17`` 不会被误判成相等。

本模块**零 DB、零网络、零第三方依赖**（纯标准库 ``re``），可离线单测。
"""

from __future__ import annotations

import re
from typing import List, Optional

#: 数字（含小数与负号）——与 ai_exercise_service._NUM_RE 逐字一致
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

#: 等式/不等式连接符（全角等号兼容）
_EQ_OPS = "[=＝<>≤≥]+"

#: 表达式「词元」：非空白、非中英文标点、非连接符本身
_TOKEN = r"[^\s，。、；;：:！!？?（）()【】\[\]{}\"'“”‘’=＝<>≤≥]"

#: 算术表达式：词元 + 可重复的「运算符 + 词元」（容忍 ``2x + 3`` 的内部空格）
_EXPR = rf"{_TOKEN}+(?:\s*[+\-*/×÷^]\s*{_TOKEN}+)*"

#: 等式/不等式片段：``3 = 7`` / ``2x + 3 = 7`` / ``x ≤ 5``
_EQUALITY_RE = re.compile(rf"{_EXPR}\s*{_EQ_OPS}\s*{_EXPR}")

#: 噪声字符（与 ai_exercise_service._NOISE_RE 同集合）
_NOISE_RE = re.compile(r"[\s,，。、；;：:！!？?（）()【】\[\]{}\"'“”‘’*_\\]+")

#: 答案标签前缀：「答案/答/结果/等于/得/是/为 + 可选冒号等号」
_ANSWER_LABEL_RE = re.compile(r"^(?:答案|答|结果|等于|得|是|为)\s*[是为:：=＝]?\s*")

#: 单变量等式：``x = 2`` / ``y=3.5``（左侧必须是单个字母，``2x=4`` 不算）
_VAR_EQ_RE = re.compile(r"\b([a-zA-Z])\s*[=＝]\s*(-?\d+(?:\.\d+)?)\b")


def sole_number(text: str) -> Optional[float]:
    """文本里**有且只有一个**数字时返回它，否则返回 ``None``。

    「有且只有一个」是关键：``12`` 与 ``12元`` 能对上，而 ``7`` 与 ``17``
    不会被误判成相等（后者抽出的是 17，与 7 不等）。

    Args:
        text: 任意文本（脏值按空串处理）。

    Returns:
        唯一数字的 float；零个或多个数字 / 无法解析时 ``None``。
    """
    found = _NUM_RE.findall(text or "")
    if len(found) != 1:
        return None
    try:
        return float(found[0])
    except (TypeError, ValueError):
        return None


def clean_text(text: str) -> str:
    """去噪声字符 + NFKC 归一 + 小写 + 去答案标签前缀 + 去尾部句点。"""
    import unicodedata

    value = unicodedata.normalize("NFKC", text or "").strip().lower()
    if not value:
        return ""
    value = _ANSWER_LABEL_RE.sub("", value)
    value = _NOISE_RE.sub("", value)
    return value.rstrip(".")


def extract_equalities(text: str) -> List[str]:
    """抽取文本中的等式/不等式片段（如 ``["2x+3=7", "3 = 7"]``）。

    V1 口径：非空白非标点的连续串 + 连接符 + 连续串。抽不到返回空列表——
    evidence 的消费方（#33）按「空 = 学生没有引用明确数量关系」处理。
    """
    if not text:
        return []
    fragments: List[str] = []
    for match in _EQUALITY_RE.finditer(text):
        fragment = "".join(match.group(0).split())  # 去内部空白：`3 = 7` → `3=7`
        if fragment and fragment not in fragments:
            fragments.append(fragment)
    return fragments


def extract_answer(text: str) -> Optional[str]:
    """从学生回答里抽取**最终答案**（V1 规则口径，拿不到返回 ``None``）。

    抽取顺序（先具体后宽泛，防止误抽）：
        1. 单变量等式 ``x = 2`` → 归一成 ``x=2``（信息最完整，优先）；
        2. 全文本有且只有一个数字（``12`` / ``12元`` / full-width ``１２``）→
           返回该数字字符串（``_sole_number`` 语义，多个数字不猜）；
        3. 都不中 → ``None``（例如「我算到 2x=4 了」含两个数字，属未完成，
           不填 answer）。

    Note:
        本函数只做**抽取**，不做对错判定——判题权威在
        ``app/services/sympy_judge.py``（TASK-002 边界纪律）。
    """
    if not text:
        return None

    var_match = _VAR_EQ_RE.search(text)
    if var_match:
        return f"{var_match.group(1)}={var_match.group(2)}"

    number = sole_number(text)
    if number is None:
        return None
    # 整数不带小数点尾巴（12.0 → "12"），小数原样
    if number == int(number):
        return str(int(number))
    return str(number)


__all__ = [
    "sole_number",
    "clean_text",
    "extract_equalities",
    "extract_answer",
]

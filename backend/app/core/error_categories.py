"""错因分类的**唯一真相源**（T04B · 缺口②统一枚举）。

跨三处打通：
    * 诊断 Agent（``app.ai.agents.diagnose``）—— 输出错因时使用本枚举；
    * 学习画像（``app.services.mastery_engine.classify_error``）—— 同一套；
    * 设计文档《AI Agent 详细Prompt工程设计》—— 口径一致。

枚举（5 类，用户拍板）：
    READ      读题错    审题 / 看错 / 漏条件 / 误解题意
    METHOD    方法错    思路 / 解法 / 策略选错
    CALC      计算错    算错 / 进位 / 退位 / 约分 / 通分 / 符号
    KNOWLEDGE 知识点缺  概念 / 定义 / 性质 / 公式 / 定理不理解
    CHECK     检查漏    没验算 / 漏步骤 / 单位 / 格式不完整

优先级仲裁：``ERROR_CATEGORIES`` 的顺序即优先级（越靠前越根因，命中多类时取最高优先级）。
机器码（如 ``READ``）跨系统稳定，数据库 / JSON 一律用它；中文标签仅用于展示。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# ----------------------------------------------------------------- 稳定机器码
ERROR_READ: Final[str] = "READ"
ERROR_METHOD: Final[str] = "METHOD"
ERROR_CALC: Final[str] = "CALC"
ERROR_KNOWLEDGE: Final[str] = "KNOWLEDGE"
ERROR_CHECK: Final[str] = "CHECK"

#: 无法归入五类的兜底（**不**在 ERROR_CATEGORIES 中，聚合时会被丢弃）
ERROR_UNCLASSIFIED: Final[str] = "UNCLASSIFIED"

#: 顺序 = 优先级仲裁（数值越小越根因，命中多类取最高优先级）
ERROR_CATEGORIES: Final[Tuple[str, ...]] = (
    ERROR_KNOWLEDGE,
    ERROR_METHOD,
    ERROR_READ,
    ERROR_CHECK,
    ERROR_CALC,
)

# ----------------------------------------------------------------- 中文标签
ERROR_LABELS: Final[Dict[str, str]] = {
    ERROR_READ: "读题错",
    ERROR_METHOD: "方法错",
    ERROR_CALC: "计算错",
    ERROR_KNOWLEDGE: "知识点缺",
    ERROR_CHECK: "检查漏",
}

#: 诊断 Agent 提示词里列出的中文枚举（按用户口述顺序）
ERROR_TYPES: Final[Tuple[str, ...]] = (
    ERROR_LABELS[ERROR_READ],
    ERROR_LABELS[ERROR_METHOD],
    ERROR_LABELS[ERROR_CALC],
    ERROR_LABELS[ERROR_KNOWLEDGE],
    ERROR_LABELS[ERROR_CHECK],
)

#: 关键词表（全部小写子串匹配；命中多类按 ERROR_CATEGORIES 优先级仲裁）
ERROR_KEYWORDS: Final[Dict[str, Tuple[str, ...]]] = {
    ERROR_KNOWLEDGE: (
        "概念", "定义", "性质", "公式", "定理", "原理", "知识点",
        "不理解", "没理解", "理解错", "意义不清", "概念错", "概念不清",
        "不知道", "不会", "concept",
    ),
    ERROR_METHOD: (
        "方法", "思路", "解法", "策略", "无从下手", "选错方法",
        "方法错", "思路错", "找不到关系", "数量关系", "建模", "method",
    ),
    ERROR_READ: (
        "审题", "看错", "读错", "漏看", "漏条件", "误解", "没看清",
        "题意", "理解题目", "抄题", "读题", "看错数字", "misread",
    ),
    ERROR_CHECK: (
        "检查", "验算", "没验算", "漏验", "复查", "步骤", "过程",
        "跳步", "漏写", "没写", "少写", "格式", "单位", "没答",
        "漏步骤", "不完整", "检查漏", "check",
    ),
    ERROR_CALC: (
        "计算", "算错", "口算", "笔算", "进位", "退位", "约分", "通分",
        "符号", "变号", "抄错", "写错数", "粗心", "马虎", "计算错",
        "计算失误", "calc",
    ),
}


def error_label(code: str) -> str:
    """取错因中文标签，未知类别回退到「其他」。"""
    return ERROR_LABELS.get(code, "其他")


def classify_error(text: Optional[str]) -> str:
    """把一段错因描述归到五类之一（纯本地规则，OQ2）。

    命中多个类别时按 ``ERROR_CATEGORIES`` 优先级仲裁；无命中返回 ``UNCLASSIFIED``。
    """
    content = (text or "").strip().lower()
    if not content:
        return ERROR_UNCLASSIFIED

    best: Optional[str] = None
    best_priority = len(ERROR_CATEGORIES)
    for category in ERROR_CATEGORIES:
        priority = ERROR_CATEGORIES.index(category)
        if priority >= best_priority:
            continue  # 已有更高优先级命中，这一类即使命中也翻不了盘
        for keyword in ERROR_KEYWORDS.get(category, ()):
            if keyword and keyword in content:
                best = category
                best_priority = priority
                break

    return best or ERROR_UNCLASSIFIED


def normalize_error(raw: Any) -> str:
    """把模型 / 外部给的任意错因文本收敛到五类机器码。

    解析顺序：精确机器码 → 精确中文标签 → 子串命中中文标签 → 关键词扫描。
    """
    value = str(raw or "").strip()
    if value in ERROR_CATEGORIES:
        return value
    for code, label in ERROR_LABELS.items():
        if value == label:
            return code
    low = value.lower()
    for code, label in ERROR_LABELS.items():
        if label and label in value:
            return code
    return classify_error(low)


__all__ = [
    "ERROR_READ",
    "ERROR_METHOD",
    "ERROR_CALC",
    "ERROR_KNOWLEDGE",
    "ERROR_CHECK",
    "ERROR_UNCLASSIFIED",
    "ERROR_CATEGORIES",
    "ERROR_LABELS",
    "ERROR_TYPES",
    "ERROR_KEYWORDS",
    "error_label",
    "classify_error",
    "normalize_error",
]

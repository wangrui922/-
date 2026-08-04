"""缺口②：错因枚举跨 诊断 Agent / 画像 / 文档 三处统一为唯一真相源。

这是「打通」的契约测试——只要任一处改了错因分类，这里会红，逼着同步。
"""
from __future__ import annotations

from app.ai.agents.diagnose import ERROR_TYPES as DIAGNOSE_TYPES
from app.core import constants
from app.core.error_categories import (
    ERROR_CATEGORIES,
    ERROR_LABELS,
    ERROR_TYPES,
    classify_error,
    normalize_error,
)
from app.services.mastery_engine import classify_error as me_classify_error


def test_single_source_of_truth():
    """画像侧（constants 再导出）与唯一真相源完全一致，且只有 5 类。"""
    assert constants.ERROR_CATEGORIES == ERROR_CATEGORIES
    assert len(ERROR_CATEGORIES) == 5


def test_diagnose_uses_same_five_categories():
    """诊断 Agent 提示词里的 5 类中文 == 唯一真相源的中文标签集合。"""
    assert set(DIAGNOSE_TYPES) == set(ERROR_LABELS.values())
    assert len(DIAGNOSE_TYPES) == 5


def test_all_categories_have_label_and_keywords():
    """每个类别都有中文标签，且关键词表（constants 再导出）非空。"""
    for code in ERROR_CATEGORIES:
        assert ERROR_LABELS[code], code
        assert constants.ERROR_KEYWORDS[code], code


def test_classify_consistent_across_layers():
    """同一段文本，诊断归一化与画像关键词分类结论必须一致。"""
    cases = {
        "看错数字，漏看了条件": "READ",
        "方法选错了，找不到关系": "METHOD",
        "进位退位算错了": "CALC",
        "公式定理没记住": "KNOWLEDGE",
        "没验算，步骤漏写": "CHECK",
    }
    for text, expected in cases.items():
        assert normalize_error(text) == expected, text
        assert me_classify_error(text) == expected, text


def test_classify_only_known_or_unclassified():
    """分类结果只能是五类之一，或 UNCLASSIFIED 兜底（不会冒出第六类）。"""
    for text in ["概念不清", "方法不对", "算错了", "没验算", "乱七八糟说不清"]:
        res = classify_error(text)
        assert res in ERROR_CATEGORIES or res == "UNCLASSIFIED", (text, res)

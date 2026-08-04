"""Exercise Agent：按错因生成两道分工明确的练习题（docs §C.1 第 4 个 Agent）。

分工由 ``prompts.exercise.PROMPT`` 固定，顺序不能颠倒：
    1. ``role="fix"``：修复练习，针对孩子真实出错的那一步；
    2. ``role="transfer"``：迁移练习，同知识点换数字/情境/题型。

对外统一成 ``{q, a, type, knowledge, difficulty, role, tag}``，其中
``knowledge`` / ``difficulty`` 以调用方传入的值为准（模型不给就用它兜底），
保证结果可直接落 ``questions`` 表。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.ai.agents._json_utils import extract_json as _extract_json
from app.ai.agents.question import DIFFICULTY_LEVELS, QUESTION_TYPES
from app.ai.ark_client import ArkError, ark_respond
from app.ai.prompts import exercise as exercise_prompt

#: 固定产出题目数（与 prompts.exercise.PRACTICE_COUNT 一致）
PRACTICE_COUNT: int = exercise_prompt.PRACTICE_COUNT

#: 两题的固定分工标签
ROLE_VALUES = ("fix", "transfer")

#: 兜底值
DEFAULT_TYPE: str = "计算"
DEFAULT_DIFFICULTY: int = 3

_USER_TEMPLATE: str = (
    "孩子错因类型：{error_type}；薄弱知识点：{kp}；难度：{difficulty}（1易/3中/5难）。{context}\n"
    "请按你的规则生成两道分工明确的练习题，只输出 JSON 数组。"
)


def _normalize_difficulty(raw: Any, fallback: int) -> int:
    """把难度收敛到 1/3/5，非法值退回 ``fallback``。"""
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return fallback
    if value in DIFFICULTY_LEVELS:
        return value
    return min(DIFFICULTY_LEVELS, key=lambda level: (abs(level - value), level))


def _normalize_type(raw: Any) -> str:
    """把题型收敛到 ``QUESTION_TYPES`` 枚举，不匹配则回落到默认题型。"""
    value = str(raw or "").strip()
    if value in QUESTION_TYPES:
        return value
    for item in QUESTION_TYPES:
        if item and item in value:
            return item
    return DEFAULT_TYPE


def _as_items(parsed: Any) -> List[Dict[str, Any]]:
    """把 ``extract_json`` 的结果统一成 dict 列表。

    兼容三种脏形态：标准数组、被包成 ``{"exercises": [...]}`` 的对象、单个题目对象。
    """
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for value in parsed.values():
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if parsed.get("q"):
            return [parsed]
    return []


def _normalize_item(raw: Dict[str, Any], *, index: int, kp: str, difficulty: int) -> Dict[str, Any]:
    """把模型给的单题收敛成统一结构。"""
    return {
        "q": str(raw.get("q") or "").strip(),
        "a": str(raw.get("a") or "").strip(),
        "type": _normalize_type(raw.get("type")),
        "knowledge": str(raw.get("knowledge") or kp).strip() or kp,
        "difficulty": _normalize_difficulty(raw.get("difficulty"), difficulty),
        "role": str(raw.get("role") or "").strip() or ROLE_VALUES[min(index, len(ROLE_VALUES) - 1)],
        "tag": str(raw.get("tag") or "").strip(),
    }


def gen_exercises(*, error_type: str, kp: str, difficulty: int, context: str = "") -> List[Dict[str, Any]]:
    """生成两道针对性练习题。

    Args:
        error_type: 错因类型（如 ``方法错`` / ``计算错``），来自 Diagnosis Agent。
        kp: 薄弱知识点名称。
        difficulty: 目标难度，1(易)/3(中)/5(难)；非法值按 3 处理。
        context: 附加上下文（原题、辅导对话摘要等），可为空。

    Returns:
        长度恒为 ``PRACTICE_COUNT``(2) 的列表，元素形如
        ``{"q","a","type","knowledge","difficulty","role","tag"}``；
        模型只给一道时复制补足到两道。

    Raises:
        ArkError: 方舟调用失败、返回无法解析为 JSON，或一道有效题都没解析出来。
    """
    knowledge = str(kp or "").strip() or "综合"
    level = _normalize_difficulty(difficulty, DEFAULT_DIFFICULTY)

    user_content = _USER_TEMPLATE.format(
        error_type=str(error_type or "").strip() or "未知",
        kp=knowledge,
        difficulty=level,
        context=(context or "").strip(),
    )

    raw = ark_respond(
        messages=[{"role": "user", "content": user_content}],
        system=exercise_prompt.PROMPT,
    )

    items = [
        _normalize_item(item, index=index, kp=knowledge, difficulty=level)
        for index, item in enumerate(_as_items(_extract_json(raw)))
    ]
    items = [item for item in items if item["q"]]

    if not items:
        raise ArkError("AI 未生成有效练习题")

    # 不足 2 道时复制补足，保证前端"修复+迁移"两栏永远有内容
    while len(items) < PRACTICE_COUNT:
        clone = dict(items[-1])
        clone["role"] = ROLE_VALUES[min(len(items), len(ROLE_VALUES) - 1)]
        items.append(clone)

    return items[:PRACTICE_COUNT]


__all__ = ["gen_exercises", "PRACTICE_COUNT", "ROLE_VALUES"]

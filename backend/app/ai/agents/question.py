"""Question Agent：题目识别与知识点/难度/题型分析（docs §C.1 第 1 个 Agent）。

链路：``图片 → OCR(ocr.PROMPT) → 题目文字`` → ``分析(tutor.PROMPT) → JSON``。
本模块只做"读题+定性"，不做诊断、不做引导。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.ai.agents._json_utils import extract_json as _extract_json
from app.ai.ark_client import ark_respond
from app.ai.prompts import ocr as ocr_prompt
from app.ai.prompts import tutor as tutor_prompt
from app.core.response import ApiError, CODE_BAD_REQUEST

#: 难度档位（与 questions.difficulty 的 CHECK 约束一致）
DIFFICULTY_LEVELS = (1, 3, 5)

#: 允许的题型（覆盖小学到初中的常见题型）
QUESTION_TYPES = ("计算", "应用", "几何", "函数", "证明")

#: 分析失败时的兜底值
DEFAULT_KNOWLEDGE: str = "综合"
DEFAULT_DIFFICULTY: int = 3
DEFAULT_TYPE: str = "计算"

_ANALYZE_INSTRUCTION: str = (
    "请分析以下题目的知识点/难度/题型，只输出JSON，不要任何解释、前后缀或代码块标记。\n"
    '格式严格为：{"knowledge":"知识点名称","difficulty":3,"type":"计算"}\n'
    "其中 difficulty 只能取 1(易)/3(中)/5(难) 三个整数之一；"
    "type 只能取 计算/应用/几何/函数/证明 之一；knowledge 用不超过 12 个字的知识点名。\n\n"
    "题目：\n"
)


def _normalize_difficulty(raw: Any) -> int:
    """把模型给的任意难度值收敛到 1/3/5。"""
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_DIFFICULTY
    if value in DIFFICULTY_LEVELS:
        return value
    # 取最接近的档位（如 2 → 1、4 → 3、9 → 5）
    return min(DIFFICULTY_LEVELS, key=lambda level: (abs(level - value), level))


def _normalize_type(raw: Any) -> str:
    """把模型给的题型收敛到既定枚举，不匹配则回落到默认题型。"""
    value = str(raw or "").strip()
    if value in QUESTION_TYPES:
        return value
    for item in QUESTION_TYPES:
        if item and item in value:
            return item
    return DEFAULT_TYPE


def ocr_image(image_b64: str) -> str:
    """对题目照片做 OCR 转写。

    Args:
        image_b64: 图片 base64 或 data URL。

    Returns:
        转写出的题目文字（可能为空串，表示图中无题）。

    Raises:
        ArkError: 方舟调用失败。
    """
    text = ark_respond(
        messages=[{"role": "user", "content": "请按规则转写这张图片里的数学题。"}],
        system=ocr_prompt.PROMPT,
        image_b64=image_b64,
    )
    return (text or "").strip()


def analyze_question(*, text: Optional[str], image_b64: Optional[str]) -> Dict[str, Any]:
    """识别题目并给出知识点 / 难度 / 题型。

    Args:
        text: 用户输入的题目文字，可为空。
        image_b64: 题目照片 base64 或 data URL，可为空；非空时先做 OCR 并与 ``text`` 合并。

    Returns:
        ``{"knowledge": str, "difficulty": int, "type": str, "question_text": str}``。
        ``question_text`` 是合并后的完整题面，供编排层写入会话上下文。

    Raises:
        ApiError: code=400，文字与图片同时为空。
        ArkError: 方舟调用失败，或返回内容无法解析为 JSON。
    """
    typed_text = (text or "").strip()
    image = (image_b64 or "").strip()

    if not typed_text and not image:
        raise ApiError(CODE_BAD_REQUEST, "题目文字与图片不能同时为空")

    parts = []
    if image:
        ocr_text = ocr_image(image)
        if ocr_text:
            parts.append(ocr_text)
    if typed_text:
        parts.append(typed_text)

    question_text = "\n".join(parts).strip()
    if not question_text:
        raise ApiError(CODE_BAD_REQUEST, "没能从图片里读出题目，换张清楚点的照片试试")

    raw = ark_respond(
        messages=[{"role": "user", "content": _ANALYZE_INSTRUCTION + question_text}],
        system=tutor_prompt.PROMPT,
    )
    data = _extract_json(raw)
    if not isinstance(data, dict):
        data = {}

    return {
        "knowledge": str(data.get("knowledge") or DEFAULT_KNOWLEDGE).strip() or DEFAULT_KNOWLEDGE,
        "difficulty": _normalize_difficulty(data.get("difficulty")),
        "type": _normalize_type(data.get("type")),
        "question_text": question_text,
    }


__all__ = [
    "analyze_question",
    "ocr_image",
    "DIFFICULTY_LEVELS",
    "QUESTION_TYPES",
]
